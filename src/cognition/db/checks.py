"""Read-only diagnostics for implemented Phase 1 durable invariants."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cognition.db.models.audit import AdminAudit
from cognition.db.models.evidence import Event, EventContent
from cognition.db.models.governance import GovernanceState
from cognition.db.models.identity import Individual
from cognition.db.models.runtime import RuntimeConfigRevision
from cognition.protocols.common import Ref


@dataclass(frozen=True)
class IntegrityFinding:
    invariant_id: str
    severity: Literal["error", "warning"]
    subject: Ref
    message: str


@dataclass(frozen=True)
class IntegrityReport:
    findings: tuple[IntegrityFinding, ...]
    not_applicable: tuple[str, ...] = ("claimed_wake_cycle",)

    @property
    def healthy(self) -> bool:
        return not any(finding.severity == "error" for finding in self.findings)


def check_database(session: Session) -> IntegrityReport:
    """Observe Phase 1 state without flushing, repairing, or owning a transaction.

    Messages describe the structural problem without copying content, provenance,
    configuration, or credentials into the report. Phase 2 cycle checks remain
    explicitly unavailable rather than being represented as successful checks.
    """
    findings: list[IntegrityFinding] = []

    def error(invariant: str, kind: str, identity: UUID, message: str) -> None:
        findings.append(
            IntegrityFinding(invariant, "error", Ref(kind=kind, id=identity), message)
        )

    with session.no_autoflush:
        individuals = session.execute(
            select(
                Individual.individual_id,
                Individual.parent_individual_id,
                Individual.fork_event_id,
                Individual.operational_status,
            ).order_by(Individual.individual_id)
        ).all()
        individual_ids = {row.individual_id for row in individuals}
        parents = {row.individual_id: row.parent_individual_id for row in individuals}
        governed = set(session.scalars(select(GovernanceState.individual_id)))
        genesis_counts = dict(
            session.execute(
                select(Event.individual_id, func.count())
                .where(Event.event_type == "individual.born")
                .group_by(Event.individual_id)
            )
            .tuples()
            .all()
        )
        active_counts = dict(
            session.execute(
                select(RuntimeConfigRevision.individual_id, func.count())
                .where(
                    RuntimeConfigRevision.activated_at.is_not(None),
                    RuntimeConfigRevision.superseded_at.is_(None),
                )
                .group_by(RuntimeConfigRevision.individual_id)
            )
            .tuples()
            .all()
        )
        event_owners = dict(
            session.execute(select(Event.event_id, Event.individual_id)).tuples().all()
        )
        audits = session.execute(
            select(AdminAudit.audit_id, AdminAudit.individual_id, AdminAudit.event_id)
        ).all()
        content_ids = session.scalars(select(EventContent.event_id)).all()

    for person in individuals:
        identity = person.individual_id
        if identity not in governed:
            error(
                "individual_governance",
                "individual",
                identity,
                "Individual has no governance row.",
            )
        count = genesis_counts.get(identity, 0)
        if count != 1:
            error(
                "individual_genesis",
                "individual",
                identity,
                f"Expected one individual.born event; found {count}.",
            )
        count = active_counts.get(identity, 0)
        if count != 1:
            error(
                "active_config_revision",
                "individual",
                identity,
                f"Expected one active configuration revision; found {count}.",
            )
        parent, fork_event = person.parent_individual_id, person.fork_event_id
        if (parent is None) != (fork_event is None):
            error(
                "individual_lineage",
                "individual",
                identity,
                "Parent and fork event must be supplied together.",
            )
        if parent is not None and parent not in individual_ids:
            error(
                "individual_lineage",
                "individual",
                identity,
                "Parent individual does not exist.",
            )
        if parent == identity:
            error(
                "individual_lineage",
                "individual",
                identity,
                "An individual cannot be its own parent.",
            )
        if fork_event is not None and fork_event not in event_owners:
            error(
                "individual_lineage",
                "individual",
                identity,
                "Fork event does not exist.",
            )
        if person.operational_status == "retired":
            from cognition.runtime.lifecycle import is_runnable

            if is_runnable(person.operational_status):
                error(
                    "retired_not_runnable",
                    "individual",
                    identity,
                    "Lifecycle considers a retired individual runnable.",
                )

    # Follow each parent edge once; report every member of a cycle, not descendants.
    visited: set[UUID] = set()
    for identity in individual_ids:
        path: list[UUID] = []
        positions: dict[UUID, int] = {}
        current: UUID | None = identity
        while current is not None and current in parents and current not in visited:
            if current in positions:
                cycle = path[positions[current] :]
                if len(cycle) > 1:
                    for member in cycle:
                        error(
                            "individual_lineage",
                            "individual",
                            member,
                            "Parent lineage contains a cycle.",
                        )
                break
            positions[current] = len(path)
            path.append(current)
            current = parents[current]
        visited.update(path)

    for audit in audits:
        owner = event_owners.get(audit.event_id)
        if owner is None:
            error(
                "audit_event_link",
                "admin_audit",
                audit.audit_id,
                "Audit references a missing event.",
            )
        elif owner != audit.individual_id:
            error(
                "audit_event_link",
                "admin_audit",
                audit.audit_id,
                "Audit and evidence event belong to different individuals.",
            )
    for event_id in content_ids:
        if event_id not in event_owners:
            error(
                "event_content_link",
                "event_content",
                event_id,
                "Content references a missing event.",
            )

    return IntegrityReport(
        tuple(
            sorted(
                findings,
                key=lambda finding: (
                    finding.invariant_id,
                    finding.subject.kind,
                    str(finding.subject.id),
                    finding.message,
                ),
            )
        )
    )
