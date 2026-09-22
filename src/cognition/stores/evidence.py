"""Append evidence and audit records; redact content without rewriting history."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from cognition.db.models.audit import AdminAudit
from cognition.db.models.evidence import Event, EventContent
from cognition.db.models.governance import AdminPrincipal
from cognition.protocols.common import JsonObject, Ref, new_id, normalize_utc
from cognition.protocols.events_v1 import EventEnvelopeV1


@dataclass(frozen=True)
class StoredEvent:
    envelope: EventEnvelopeV1
    event_sequence: int
    redacted_at: datetime | None
    redaction_audit_id: UUID | None


def append_event(session: Session, envelope: EventEnvelopeV1) -> StoredEvent:
    row = Event(
        event_id=envelope.event_id,
        individual_id=envelope.individual_id,
        event_type=envelope.event_type,
        schema_version=envelope.schema_version,
        occurred_at=envelope.occurred_at,
        observed_at=envelope.observed_at,
        recorded_at=envelope.recorded_at,
        source_kind=envelope.source.kind,
        source_id=envelope.source.source_id,
        source_binding_id=envelope.source.binding_id,
        actor_entity_id=envelope.actor_entity_id,
        causation_event_id=envelope.causation_event_id,
        correlation_id=envelope.correlation_id,
        subject_kind=envelope.subject.kind if envelope.subject else None,
        subject_id=envelope.subject.id if envelope.subject else None,
        provenance=deepcopy(envelope.provenance),
        runtime_version=envelope.runtime_version,
    )
    session.add(row)
    session.flush()
    session.add(
        EventContent(
            event_id=envelope.event_id,
            **envelope.content.model_dump(),
            content_schema_version=1,
            redacted_at=None,
            redaction_audit_id=None,
        )
    )
    session.flush()
    return StoredEvent(envelope.model_copy(deep=True), row.event_sequence, None, None)


def load_event(session: Session, event_id: UUID) -> StoredEvent:
    row = session.get(Event, event_id, populate_existing=True)
    content = session.get(EventContent, event_id, populate_existing=True)
    if row is None or content is None:
        raise LookupError("Event or its content does not exist")
    envelope = EventEnvelopeV1.model_validate(
        {
            "schema_version": row.schema_version,
            "event_id": row.event_id,
            "individual_id": row.individual_id,
            "event_type": row.event_type,
            "occurred_at": row.occurred_at,
            "observed_at": row.observed_at,
            "recorded_at": row.recorded_at,
            "source": {
                "kind": row.source_kind,
                "source_id": row.source_id,
                "binding_id": row.source_binding_id,
            },
            "actor_entity_id": row.actor_entity_id,
            "causation_event_id": row.causation_event_id,
            "correlation_id": row.correlation_id,
            "subject": None
            if row.subject_kind is None
            else {
                "kind": row.subject_kind,
                "id": row.subject_id,
            },
            "provenance": deepcopy(row.provenance),
            "runtime_version": row.runtime_version,
            "content": {
                "content_type": content.content_type,
                "payload": deepcopy(content.payload),
                "text": content.text,
                "blob_ref": content.blob_ref,
                "content_hash": content.content_hash,
                "sensitivity": content.sensitivity,
                "retention_class": content.retention_class,
                "retain_until": content.retain_until,
            },
        }
    )
    return StoredEvent(
        envelope,
        row.event_sequence,
        content.redacted_at,
        content.redaction_audit_id,
    )


def record_admin_audit(
    session: Session,
    *,
    individual_id: UUID,
    admin_principal_id: UUID,
    operation: str,
    target: Ref,
    reason: str,
    before_state: JsonObject,
    after_state: JsonObject,
    created_at: datetime,
    event_id: UUID,
    audit_id: UUID | None = None,
) -> UUID:
    principal = session.get(AdminPrincipal, admin_principal_id)
    event = session.get(Event, event_id)
    if (
        principal is None
        or principal.individual_id != individual_id
        or event is None
        or event.individual_id != individual_id
    ):
        raise ValueError("Audit principal and event must belong to the individual")
    row = AdminAudit(
        audit_id=audit_id or new_id(),
        individual_id=individual_id,
        admin_principal_id=admin_principal_id,
        operation=operation,
        target_kind=target.kind,
        target_id=target.id,
        reason=reason,
        before_state=deepcopy(before_state),
        after_state=deepcopy(after_state),
        created_at=normalize_utc(created_at),
        event_id=event_id,
    )
    session.add(row)
    session.flush()
    return row.audit_id


def redact_event_content(
    session: Session,
    event_id: UUID,
    *,
    audit_id: UUID,
    redacted_at: datetime,
) -> None:
    audit = session.get(AdminAudit, audit_id)
    event = session.get(Event, event_id)
    if (
        audit is None
        or event is None
        or audit.target_kind != "event"
        or audit.target_id != event_id
        or audit.individual_id != event.individual_id
    ):
        raise ValueError("Redaction requires an audit targeting this event")
    content = session.scalar(
        select(EventContent)
        .where(
            EventContent.event_id == event_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if content is None:
        raise LookupError("Event content does not exist")
    if content.redacted_at is not None:
        return
    content.payload = None
    content.text = None
    content.blob_ref = None
    content.redacted_at = normalize_utc(redacted_at)
    content.redaction_audit_id = audit_id
    session.flush()
