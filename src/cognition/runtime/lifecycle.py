"""Explicit local administrative state changes, audit, and evidence."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from cognition.policy.governance import (
    ADMIN_OPERATIONS,
    AuthenticatedPrincipal,
    next_operational_status,
    require_admin,
)
from cognition.protocols.common import Clock, JsonObject, Ref, new_id, normalize_utc
from cognition.protocols.events_v1 import EventContent, EventEnvelopeV1, EventSource
from cognition.stores.evidence import append_event, record_admin_audit
from cognition.stores.governance import (
    find_admin_principal,
    load_governance,
    update_governance,
)
from cognition.stores.identity import load_individual, set_operational_status


def is_runnable(status: str) -> bool:
    """Only active individuals may start new executive work."""
    return status == "active"


@dataclass(frozen=True)
class AdminOperationResult:
    individual_id: UUID
    operational_status: str
    audit_id: UUID
    event_id: UUID


def apply_admin_operation(
    factory: sessionmaker[Session],
    individual_id: UUID,
    principal: AuthenticatedPrincipal,
    operation: str,
    reason: str,
    clock: Clock,
) -> AdminOperationResult:
    if operation not in ADMIN_OPERATIONS:
        raise ValueError("Unknown administrative operation")
    if not reason.strip():
        raise ValueError("An administrative reason is required")
    if principal.authn_provider != "local_os":
        raise PermissionError(
            "Administrative operations require local OS authentication"
        )
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
        governance = load_governance(session, individual_id)
        before: JsonObject = {
            "operational_status": individual.operational_status,
            "external_actions_blocked": governance.external_actions_blocked,
        }
        if operation == "emergency_block":
            governance = update_governance(
                session,
                individual_id,
                external_actions_blocked=True,
                expected_revision=governance.revision,
            )
        else:
            status = next_operational_status(individual.operational_status, operation)
            individual = set_operational_status(
                session,
                individual_id,
                status,
                expected_revision=individual.revision,
            )
        after: JsonObject = {
            "operational_status": individual.operational_status,
            "external_actions_blocked": governance.external_actions_blocked,
        }
        target = Ref(kind="individual", id=individual_id)
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
    return AdminOperationResult(
        individual_id, individual.operational_status, audit_id, event_id
    )
