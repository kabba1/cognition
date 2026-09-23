"""Local administrator control of one explicit internal exploration allowance."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from cognition.domain.exploration import InternalExplorationPolicy
from cognition.policy.governance import AuthenticatedPrincipal, require_admin
from cognition.protocols.common import Clock, JsonObject, Ref, new_id, normalize_utc
from cognition.protocols.events_v1 import EventContent, EventEnvelopeV1, EventSource
from cognition.stores.evidence import append_event, record_admin_audit
from cognition.stores.governance import (
    find_admin_principal,
    set_internal_exploration_policy,
)
from cognition.stores.identity import load_individual

EXPLORATION_ADMIN_OPERATIONS = ("enable_exploration", "disable_exploration")


@dataclass(frozen=True)
class ExplorationAdminResult:
    individual_id: UUID
    operational_status: str
    enabled: bool
    governance_revision: int
    audit_id: UUID
    event_id: UUID


def set_internal_exploration(
    factory: sessionmaker[Session],
    individual_id: UUID,
    principal: AuthenticatedPrincipal,
    enabled: bool,
    reason: str,
    clock: Clock,
) -> ExplorationAdminResult:
    """Change policy only, preserving in-flight results and exact decided turns.

    The runtime separately denies new starts and reconciles pending allowances.
    Global pause does not prevent an authenticated operator from changing policy.
    """
    InternalExplorationPolicy(enabled=enabled)
    if not reason.strip():
        raise ValueError("An administrative reason is required")
    if principal.authn_provider != "local_os":
        raise PermissionError(
            "Administrative operations require local OS authentication"
        )
    operation = "enable_exploration" if enabled else "disable_exploration"
    now = normalize_utc(clock.now())
    event_id, audit_id = new_id(), new_id()
    with factory.begin() as session:
        individual = load_individual(session, individual_id, for_update=True)
        administrator = require_admin(
            find_admin_principal(
                session,
                individual_id,
                authn_provider=principal.authn_provider,
                subject=principal.subject,
            )
        )
        prior, current = set_internal_exploration_policy(
            session, individual_id, enabled
        )
        before: JsonObject = {
            "budget_policy": prior.budget_policy,
            "governance_revision": prior.revision,
        }
        after: JsonObject = {
            "budget_policy": current.budget_policy,
            "governance_revision": current.revision,
        }
        target = Ref(kind="governance", id=individual_id)
        append_event(
            session,
            EventEnvelopeV1(
                schema_version=1,
                event_id=event_id,
                individual_id=individual_id,
                event_type=f"admin.{operation}",
                occurred_at=now,
                observed_at=now,
                recorded_at=now,
                source=EventSource(
                    kind="admin",
                    source_id=str(administrator.admin_principal_id),
                    binding_id=None,
                ),
                actor_entity_id=None,
                causation_event_id=None,
                correlation_id=audit_id,
                subject=target,
                provenance={
                    "admin_principal_id": str(administrator.admin_principal_id)
                },
                content=EventContent(
                    content_type="application/json",
                    payload={
                        "operation": operation,
                        "reason": reason,
                        "before": before,
                        "after": after,
                    },
                    text=None,
                    blob_ref=None,
                    content_hash=None,
                    sensitivity="internal",
                    retention_class="history",
                    retain_until=None,
                ),
                runtime_version="0.1.0",
            ),
        )
        record_admin_audit(
            session,
            individual_id=individual_id,
            admin_principal_id=administrator.admin_principal_id,
            operation=operation,
            target=target,
            reason=reason,
            before_state=before,
            after_state=after,
            created_at=now,
            event_id=event_id,
            audit_id=audit_id,
        )
    return ExplorationAdminResult(
        individual_id,
        individual.operational_status,
        enabled,
        current.revision,
        audit_id,
        event_id,
    )
