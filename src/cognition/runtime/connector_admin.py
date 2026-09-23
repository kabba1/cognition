"""Authenticated, atomic local administration of immutable connector bindings."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from cognition.db.models import GovernanceState
from cognition.policy.governance import AuthenticatedPrincipal, require_admin
from cognition.protocols.common import Clock, JsonObject, Ref, new_id, normalize_utc
from cognition.protocols.events_v1 import EventContent, EventEnvelopeV1, EventSource
from cognition.stores.connectors import ConnectorBindingRecord, create_connector_binding
from cognition.stores.connectors import set_connector_enabled as set_enabled
from cognition.stores.evidence import append_event, record_admin_audit
from cognition.stores.governance import find_admin_principal
from cognition.stores.identity import load_individual


@dataclass(frozen=True)
class ConnectorAdminResult:
    individual_id: UUID
    connector_binding_id: UUID
    enabled: bool
    revision: int
    audit_id: UUID
    event_id: UUID


def _projection(binding: ConnectorBindingRecord | None) -> JsonObject:
    if binding is None:
        return {}
    return {
        "connector_binding_id": str(binding.connector_binding_id),
        "individual_id": str(binding.individual_id),
        "adapter_id": binding.adapter_id,
        "source_id": binding.source_id,
        "enabled": binding.enabled,
        "revision": binding.revision,
        "created_at": binding.created_at.isoformat(),
        "updated_at": binding.updated_at.isoformat(),
    }


def _admin_change(
    factory: sessionmaker[Session],
    individual_id: UUID,
    principal: AuthenticatedPrincipal,
    *,
    operation: str,
    reason: str,
    clock: Clock,
    change: Callable[
        [Session, datetime],
        tuple[ConnectorBindingRecord | None, ConnectorBindingRecord],
    ],
) -> ConnectorAdminResult:
    if not reason.strip():
        raise ValueError("An administrative reason is required")
    if principal.authn_provider != "local_os":
        raise PermissionError(
            "Administrative operations require local OS authentication"
        )
    now = normalize_utc(clock.now())
    event_id, audit_id = new_id(), new_id()
    with factory.begin() as session:
        load_individual(session, individual_id, for_update=True)
        administrator = require_admin(
            find_admin_principal(
                session,
                individual_id,
                authn_provider=principal.authn_provider,
                subject=principal.subject,
            )
        )
        governance = session.scalar(
            select(GovernanceState.individual_id)
            .where(GovernanceState.individual_id == individual_id)
            .with_for_update()
        )
        if governance is None:
            raise LookupError("Governance state is missing")
        prior, current = change(session, now)
        before, after = _projection(prior), _projection(current)
        target = Ref(kind="connector_binding", id=current.connector_binding_id)
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
    return ConnectorAdminResult(
        individual_id,
        current.connector_binding_id,
        current.enabled,
        current.revision,
        audit_id,
        event_id,
    )


def register_connector(
    factory: sessionmaker[Session],
    individual_id: UUID,
    principal: AuthenticatedPrincipal,
    *,
    adapter_id: str,
    source_id: str,
    reason: str,
    clock: Clock,
    enabled: bool = False,
) -> ConnectorAdminResult:
    """Register an exact source identity; disabled unless explicitly allowed."""
    return _admin_change(
        factory,
        individual_id,
        principal,
        operation="connector.register",
        reason=reason,
        clock=clock,
        change=lambda session, now: (
            None,
            create_connector_binding(
                session,
                individual_id,
                adapter_id=adapter_id,
                source_id=source_id,
                enabled=enabled,
                now=now,
            ),
        ),
    )


def set_connector_enabled(
    factory: sessionmaker[Session],
    individual_id: UUID,
    connector_binding_id: UUID,
    principal: AuthenticatedPrincipal,
    *,
    enabled: bool,
    reason: str,
    clock: Clock,
) -> ConnectorAdminResult:
    """Revise source enablement without cancelling or rewriting inbound history."""
    return _admin_change(
        factory,
        individual_id,
        principal,
        operation="connector.enable" if enabled else "connector.disable",
        reason=reason,
        clock=clock,
        change=lambda session, now: set_enabled(
            session, individual_id, connector_binding_id, enabled, now=now
        ),
    )
