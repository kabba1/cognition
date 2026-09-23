"""Read-only diagnostics for durable identity, evidence, and cognition invariants."""

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cognition.config.revisions import behavior_hash
from cognition.config.schema import (
    BehaviorConfiguration,
    cognition_protocol_version,
    parse_behavior_config,
)
from cognition.db.autonomy_checks import check_autonomy_state
from cognition.db.exploration_checks import check_exploration_state
from cognition.db.models.attention import Wake
from cognition.db.models.audit import AdminAudit
from cognition.db.models.cognition import (
    AppliedOperation,
    CognitionCycle,
    CognitionTurn,
    ContextSnapshot,
    CycleWake,
    ModelInvocation,
)
from cognition.db.models.evidence import Event, EventContent
from cognition.db.models.governance import GovernanceState
from cognition.db.models.identity import Individual
from cognition.db.models.runtime import RuntimeConfigRevision
from cognition.db.perception_checks import check_perception_state
from cognition.db.personal_checks import check_personal_state
from cognition.db.reflection_checks import check_reflection_state
from cognition.protocols.common import Ref
from cognition.protocols.executive import (
    CognitionDecision,
    ModelRequest,
    parse_decision,
    parse_request,
    parse_result,
    validate_request_contract,
)
from cognition.stores.cognition import canonical_json


@dataclass(frozen=True)
class IntegrityFinding:
    invariant_id: str
    severity: Literal["error", "warning"]
    subject: Ref
    message: str


@dataclass(frozen=True)
class IntegrityReport:
    findings: tuple[IntegrityFinding, ...]
    not_applicable: tuple[str, ...] = ()

    @property
    def healthy(self) -> bool:
        return not any(finding.severity == "error" for finding in self.findings)


def check_database(session: Session) -> IntegrityReport:
    """Observe persisted state without flushing, repairing, or owning a transaction.

    Messages describe the structural problem without copying content, provenance,
    configuration, or credentials into the report.
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
        _check_cognition(session, error)
        check_autonomy_state(session, error)
        check_reflection_state(session, error)
        check_exploration_state(session, error)
        check_perception_state(session, error)
        check_personal_state(session, error)

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


def _check_cognition(
    session: Session, error: Callable[[str, str, UUID, str], None]
) -> None:
    """Read column snapshots, bypassing the caller's dirty ORM identity map."""
    cycles = {
        row.cycle_id: row
        for row in session.execute(select(CognitionCycle.__table__)).mappings()
    }
    turns = {
        row.turn_id: row
        for row in session.execute(select(CognitionTurn.__table__)).mappings()
    }
    wakes = {
        row.wake_id: row for row in session.execute(select(Wake.__table__)).mappings()
    }
    links = session.execute(select(CycleWake.__table__)).mappings().all()
    contexts = session.execute(select(ContextSnapshot.__table__)).mappings().all()
    invocations = session.execute(select(ModelInvocation.__table__)).mappings().all()
    operations = session.execute(select(AppliedOperation.__table__)).mappings().all()
    configurations = {
        row.config_revision_id: row
        for row in session.execute(select(RuntimeConfigRevision.__table__)).mappings()
    }
    valid_configurations: dict[UUID, BehaviorConfiguration] = {}
    for configuration in configurations.values():
        try:
            behavior = parse_behavior_config(configuration.sanitized_config)
            if (
                behavior.config_schema_version != configuration.config_schema_version
                or behavior_hash(behavior) != configuration.content_hash
            ):
                raise ValueError("Configuration identity mismatch")
        except (ValueError, TypeError):
            error(
                "config_revision",
                "config_revision",
                configuration.config_revision_id,
                "Configuration revision has invalid schema, version, or digest.",
            )
        else:
            valid_configurations[configuration.config_revision_id] = behavior

    wake_cycles: dict[UUID, list[UUID]] = defaultdict(list)
    for link in links:
        wake_cycles[link.wake_id].append(link.cycle_id)
        cycle, wake = cycles.get(link.cycle_id), wakes.get(link.wake_id)
        if cycle is None or wake is None or cycle.individual_id != wake.individual_id:
            error(
                "cycle_wake_individual",
                "wake",
                link.wake_id,
                "Cycle wake link has missing or mismatched ownership.",
            )
    for wake in wakes.values():
        if wake.status != "claimed":
            continue
        owners = wake_cycles[wake.wake_id]
        cycle = cycles.get(owners[0]) if len(owners) == 1 else None
        if (
            cycle is None
            or cycle.status != "active"
            or cycle.individual_id != wake.individual_id
        ):
            error(
                "claimed_wake_cycle",
                "wake",
                wake.wake_id,
                "Claimed wake must link to exactly one active cycle of its individual.",
            )

    cycle_turns: dict[UUID, list[UUID]] = defaultdict(list)
    for turn in turns.values():
        cycle_turns[turn.cycle_id].append(turn.turn_id)
    for cycle in cycles.values():
        if cycle.status != "active":
            continue
        members = [turns[identity] for identity in cycle_turns[cycle.cycle_id]]
        unfinished = [
            turn
            for turn in members
            if turn.status in {"prepared", "invoking", "decided"}
        ]
        if len(unfinished) != 1 or unfinished[0].ordinal != max(
            turn.ordinal for turn in members
        ):
            error(
                "cycle_active_turn",
                "cycle",
                cycle.cycle_id,
                "Active cycle needs exactly one unfinished turn at its latest ordinal.",
            )

    decisions: dict[UUID, CognitionDecision] = {}
    for turn in turns.values():
        if turn.decision_json is None and turn.status not in {"decided", "applied"}:
            if turn.decision_id is None and turn.decision_hash is None:
                continue
        try:
            decision = parse_decision(turn.decision_json)
            encoded = json.dumps(
                turn.decision_json,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (ValueError, TypeError):
            error(
                "turn_decision",
                "turn",
                turn.turn_id,
                "Turn requires a structurally valid stored decision.",
            )
            continue
        decisions[turn.turn_id] = decision
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        if (
            decision.decision_id != turn.decision_id
            or turn.decision_hash != digest
            or decision.disposition != turn.disposition
        ):
            error(
                "turn_decision",
                "turn",
                turn.turn_id,
                "Decision identity, disposition, or digest differs from its turn.",
            )
        # A recorded or rejected proposal can honestly contain invalid target IDs.
        if turn.status == "applied" and (
            decision.cycle_id != turn.cycle_id or decision.turn_id != turn.turn_id
        ):
            error(
                "turn_decision",
                "turn",
                turn.turn_id,
                "Applied decision targets a different cycle or turn.",
            )

    requests: dict[UUID, ModelRequest] = {}
    for snapshot in contexts:
        snapshot_turn = turns.get(snapshot.turn_id)
        cycle = (
            cycles.get(snapshot_turn.cycle_id) if snapshot_turn is not None else None
        )
        try:
            request = parse_request(snapshot.request_json)
            rendered = json.loads(snapshot.rendered_context)
            representations_match = canonical_json(rendered) == canonical_json(
                snapshot.request_json
            )
        except (ValueError, TypeError):
            error(
                "context_snapshot",
                "context_snapshot",
                snapshot.snapshot_id,
                "Snapshot requires valid request and rendered JSON representations.",
            )
            continue
        requests[snapshot.turn_id] = request
        linked_configuration = configurations.get(snapshot.config_revision_id)
        linked_behavior = valid_configurations.get(snapshot.config_revision_id)
        incompatible = linked_configuration is None or linked_behavior is None
        controls = [
            section
            for section in request.context_sections
            if section.name == "runtime_control"
        ]
        if len(controls) != 1 or linked_configuration is None:
            incompatible = True
        else:
            control = controls[0]
            incompatible |= (
                control.category != "control"
                or not isinstance(control.content, dict)
                or control.content.get("config_revision_id")
                != str(snapshot.config_revision_id)
                or control.content.get("config_content_hash")
                != linked_configuration.content_hash
            )
        if linked_behavior is not None:
            try:
                validate_request_contract(
                    request,
                    config_schema_version=linked_behavior.config_schema_version,
                    configured_protocol=cognition_protocol_version(linked_behavior),
                )
            except (ValueError, TypeError):
                incompatible = True
            incompatible |= (
                snapshot.model_adapter != linked_behavior.model.adapter
                or snapshot.requested_model != linked_behavior.model.requested_model
            )
        digest = hashlib.sha256(snapshot.rendered_context.encode("utf-8")).hexdigest()
        if (
            incompatible
            or digest != snapshot.content_hash
            or not representations_match
            or request.turn_id != snapshot.turn_id
            or cycle is None
            or request.cycle_id != cycle.cycle_id
            or request.individual_id != cycle.individual_id
            or linked_configuration is None
            or linked_configuration.individual_id != cycle.individual_id
            or request.runtime_contract_version != snapshot.runtime_contract_version
        ):
            error(
                "context_snapshot",
                "context_snapshot",
                snapshot.snapshot_id,
                "Snapshot digest, ownership, configuration, or executive "
                "contract do not match.",
            )
        applied_decision = decisions.get(snapshot.turn_id)
        if (
            snapshot_turn is not None
            and snapshot_turn.status == "applied"
            and applied_decision is not None
            and applied_decision.schema_version != request.schema_version
        ):
            error(
                "turn_decision",
                "turn",
                snapshot.turn_id,
                "Applied decision protocol differs from its frozen request.",
            )

    for invocation in invocations:
        invocation_turn = turns.get(invocation.turn_id)
        if invocation.status == "started" and (
            invocation_turn is None or invocation_turn.status != "invoking"
        ):
            error(
                "invocation_turn",
                "model_invocation",
                invocation.invocation_id,
                "Started invocation must belong to an invoking turn.",
            )
        if invocation.status != "completed":
            continue
        try:
            result = parse_result(invocation.result_json)
        except (ValueError, TypeError):
            error(
                "invocation_result",
                "model_invocation",
                invocation.invocation_id,
                "Completed invocation requires a structurally valid result.",
            )
            continue
        stored_request = requests.get(invocation.turn_id)
        stored_decision = decisions.get(invocation.turn_id)
        if (
            result.status != "completed"
            or stored_request is None
            or result.request_id != stored_request.request_id
            or result.schema_version != stored_request.schema_version
            or stored_decision is None
            or stored_decision.schema_version != stored_request.schema_version
            or result.decision != stored_decision
        ):
            error(
                "invocation_result",
                "model_invocation",
                invocation.invocation_id,
                "Completed result differs from its request or stored decision.",
            )

    for operation in operations:
        operation_turn = turns.get(operation.turn_id)
        cycle = (
            cycles.get(operation_turn.cycle_id) if operation_turn is not None else None
        )
        if cycle is None or operation.individual_id != cycle.individual_id:
            error(
                "applied_operation_individual",
                "operation",
                operation.operation_id,
                "Applied operation must belong to its turn's cycle individual.",
            )
