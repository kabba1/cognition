"""Atomic evidence ingestion and redaction-independent source checkpoints."""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from cognition.db.models.attention import Wake
from cognition.db.models.evidence import Event
from cognition.db.models.governance import GovernanceState
from cognition.db.models.identity import Individual
from cognition.db.models.perception import (
    ConnectorBinding,
    InboundWake,
    IngestionReceipt,
    Observation,
)
from cognition.domain.perception import (
    ConnectorBindingSnapshot,
    NormalizedItem,
    NormalizedPage,
    canonical_json_bytes,
    validate_normalized_page,
)
from cognition.protocols.common import Ref, new_id, normalize_utc
from cognition.protocols.events_v1 import EventContent, EventEnvelopeV1, EventSource
from cognition.stores.connectors import load_connector_binding_snapshot
from cognition.stores.evidence import append_event

RECEIPT_MARKER_TYPE = "perception.ingestion_committed"
PERCEPTION_SOURCE = "perception"
INBOUND_MARKER_TYPE = "perception.inbound_wake_created"
INBOUND_PURPOSE = "Attend to incoming observations."
MAX_INBOUND_REFS = 8


@dataclass(frozen=True)
class IngestionResult:
    created_event_ids: tuple[UUID, ...]
    duplicate_count: int
    wake_ids: tuple[UUID, ...]
    cursor_revision: int
    receipt_event_id: UUID | None


def receipt_hash(fields: Mapping[str, Any]) -> str:
    """Commit every immutable receipt column, independently of event content."""
    values: dict[str, Any] = {}
    for column in IngestionReceipt.__table__.columns:
        key = column.name
        if key == "receipt_hash":
            continue
        value = fields[key]
        if isinstance(value, UUID):
            value = str(value)
        elif isinstance(value, datetime):
            value = normalize_utc(value).isoformat()
        values[key] = value
    return hashlib.sha256(canonical_json_bytes(values)).hexdigest()


def validate_receipt(session: Session, event_id: UUID) -> Mapping[str, Any]:
    """Validate retained receipt and its immutable event envelope, without content."""
    with session.no_autoflush:
        row = (
            session.execute(
                select(IngestionReceipt.__table__).where(
                    IngestionReceipt.event_id == event_id
                )
            )
            .mappings()
            .one_or_none()
        )
        event = (
            session.execute(select(Event.__table__).where(Event.event_id == event_id))
            .mappings()
            .one_or_none()
        )
    if row is None or event is None:
        raise ValueError("Missing ingestion receipt or marker")
    if row.receipt_hash != receipt_hash(dict(row)):
        raise ValueError("Ingestion receipt hash mismatch")
    if (
        event.event_type != RECEIPT_MARKER_TYPE
        or event.source_kind != "runtime"
        or event.source_id != PERCEPTION_SOURCE
        or event.source_binding_id != row.connector_binding_id
        or event.individual_id != row.individual_id
        or event.subject_kind != "connector_binding"
        or event.subject_id != row.connector_binding_id
        or event.observed_at != row.observed_at
        or event.recorded_at != row.recorded_at
        or event.occurred_at is not None
        or event.actor_entity_id is not None
        or event.causation_event_id is not None
        or event.correlation_id is not None
    ):
        raise ValueError("Ingestion marker envelope mismatch")
    return dict(row)


def validate_checkpoint(
    session: Session,
    individual_id: UUID,
    binding_id: UUID,
) -> Mapping[str, Any] | None:
    """Require latest retained marker, binding cursor and receipt chain to agree."""
    with session.no_autoflush:
        binding = (
            session.execute(
                select(ConnectorBinding.__table__).where(
                    ConnectorBinding.connector_binding_id == binding_id,
                    ConnectorBinding.individual_id == individual_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        marker = session.scalar(
            select(Event.event_id)
            .where(
                Event.event_type == RECEIPT_MARKER_TYPE,
                or_(
                    Event.source_binding_id == binding_id,
                    (Event.subject_kind == "connector_binding")
                    & (Event.subject_id == binding_id),
                ),
            )
            .order_by(Event.event_sequence.desc())
            .limit(1)
        )
        retained_latest = session.scalar(
            select(IngestionReceipt.event_id)
            .where(IngestionReceipt.connector_binding_id == binding_id)
            .order_by(IngestionReceipt.after_cursor_revision.desc())
            .limit(1)
        )
    if binding is None:
        raise ValueError("Missing owned connector binding")
    if retained_latest != marker:
        raise ValueError("Latest retained ingestion marker and receipt disagree")
    if marker is None:
        if (
            binding.latest_receipt_event_id is not None
            or binding.cursor_revision != 0
            or binding.cursor is not None
        ):
            raise ValueError("Initial connector checkpoint is inconsistent")
        with session.no_autoflush:
            retained = session.scalar(
                select(IngestionReceipt.event_id)
                .where(IngestionReceipt.connector_binding_id == binding_id)
                .limit(1)
            )
        if retained is not None:
            raise ValueError("Ingestion receipt lost its marker classification")
        return None
    row = validate_receipt(session, marker)
    if (
        row["individual_id"] != individual_id
        or row["connector_binding_id"] != binding_id
        or binding.latest_receipt_event_id != marker
        or binding.cursor != row["after_cursor"]
        or binding.cursor_revision != row["after_cursor_revision"]
    ):
        raise ValueError("Latest connector checkpoint is inconsistent")
    previous = row["previous_receipt_event_id"]
    if previous is None:
        if row["before_cursor_revision"] != 0 or row["before_cursor"] is not None:
            raise ValueError("Initial ingestion predecessor is inconsistent")
    else:
        prior = validate_receipt(session, previous)
        if (
            prior["individual_id"] != individual_id
            or prior["connector_binding_id"] != binding_id
            or prior["after_cursor_revision"] != row["before_cursor_revision"]
            or prior["after_cursor"] != row["before_cursor"]
            or prior["recorded_at"] > row["recorded_at"]
        ):
            raise ValueError("Ingestion predecessor is inconsistent")
    return row


def _event(
    session: Session,
    snapshot: ConnectorBindingSnapshot,
    *,
    event_id: UUID,
    event_type: str,
    observed_at: datetime,
    recorded_at: datetime,
    subject: Ref | None = None,
    item: NormalizedItem | None = None,
) -> None:
    append_event(
        session,
        EventEnvelopeV1(
            schema_version=1,
            event_id=event_id,
            individual_id=snapshot.individual_id,
            event_type=event_type,
            occurred_at=None if item is None else item.occurred_at,
            observed_at=observed_at,
            recorded_at=recorded_at,
            source=EventSource(
                kind="runtime" if item is None else "connector",
                source_id=PERCEPTION_SOURCE if item is None else snapshot.adapter_id,
                binding_id=snapshot.connector_binding_id,
            ),
            actor_entity_id=None,
            causation_event_id=None,
            correlation_id=None,
            subject=subject,
            provenance={},
            runtime_version="0.1.0",
            content=EventContent(
                content_type="application/json",
                payload={} if item is None else item.payload,
                text=None if item is None else item.text,
                blob_ref=None,
                content_hash=None,
                sensitivity="internal",
                retention_class="history",
                retain_until=None,
            ),
        ),
    )


def persist_page(
    session: Session,
    snapshot: ConnectorBindingSnapshot,
    page: NormalizedPage,
    *,
    recorded_at: datetime,
) -> IngestionResult:
    """Join caller transaction; preflight the whole page before appending evidence."""
    page = validate_normalized_page(snapshot, page)
    recorded_at = normalize_utc(recorded_at)
    if recorded_at < page.observed_at:
        raise ValueError("Receipt clock precedes observation")
    if session.new or session.dirty or session.deleted:
        raise ValueError("Unflushed ingress state")
    with session.no_autoflush:
        for model in (Individual, GovernanceState):
            if (
                session.execute(
                    select(model.__table__)
                    .where(model.individual_id == snapshot.individual_id)
                    .with_for_update()
                ).first()
                is None
            ):
                raise ValueError("Missing ingress authority state")
        binding = (
            session.execute(
                select(ConnectorBinding.__table__)
                .where(
                    ConnectorBinding.connector_binding_id
                    == snapshot.connector_binding_id,
                    ConnectorBinding.individual_id == snapshot.individual_id,
                )
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        current = load_connector_binding_snapshot(
            session, snapshot.individual_id, snapshot.connector_binding_id
        )
    if (
        binding is None
        or current != snapshot
        or not current.enabled
        or current.operational_status != "active"
    ):
        raise ValueError("Ingress source or lifecycle snapshot is stale or blocked")
    if recorded_at < binding.updated_at or page.observed_at < binding.created_at:
        raise ValueError("Receipt clock precedes connector state")
    validate_checkpoint(session, snapshot.individual_id, snapshot.connector_binding_id)
    with session.no_autoflush:
        open_ids = tuple(
            session.scalars(
                select(InboundWake.wake_id).where(
                    InboundWake.connector_binding_id == snapshot.connector_binding_id,
                    InboundWake.sealed_at.is_(None),
                )
            )
        )
    expected_open = (
        () if binding.pending_wake_id is None else (binding.pending_wake_id,)
    )
    if open_ids != expected_open:
        raise ValueError("Open inbound membership and binding pointer disagree")
    if binding.pending_wake_id is not None:
        from cognition.stores.inbound_scope import validate_inbound_wake

        if (
            validate_inbound_wake(
                session, snapshot.individual_id, binding.pending_wake_id
            )
            is None
        ):
            raise ValueError("Missing open inbound membership")
    keys = [item.dedup_key for item in page.items]
    with session.no_autoflush:
        existing = (
            session.execute(
                select(Observation.__table__).where(
                    Observation.connector_binding_id == snapshot.connector_binding_id,
                    Observation.dedup_key.in_(keys),
                )
            )
            .mappings()
            .all()
        )
    seen = {row.dedup_key: row.content_fingerprint for row in existing}
    if any(row.individual_id != snapshot.individual_id for row in existing):
        raise ValueError("Observation ownership mismatch")
    fresh: list[NormalizedItem] = []
    duplicates = 0
    for item in page.items:
        if item.dedup_key in seen:
            if seen[item.dedup_key] != item.content_fingerprint:
                raise ValueError("Conflicting observation identity")
            duplicates += 1
        else:
            fresh.append(item)
            seen[item.dedup_key] = item.content_fingerprint
    if not fresh and page.next_cursor == snapshot.cursor:
        return IngestionResult((), duplicates, (), snapshot.cursor_revision, None)

    # Pending membership is verified before any receipt/evidence writes.
    pending_id = binding.pending_wake_id
    pending: Wake | None = None
    if pending_id is not None:
        pending = session.get(Wake, pending_id, populate_existing=True)
        if (
            pending is None
            or pending.status != "pending"
            or len(pending.context_refs) >= MAX_INBOUND_REFS
        ):
            raise ValueError("Invalid open inbound wake")
    receipt_id = new_id()
    created: list[UUID] = []
    wake_ids: list[UUID] = []
    observations: list[Observation] = []
    for item in fresh:
        if pending is None:
            wake_id, marker_id = new_id(), new_id()
            _event(
                session,
                snapshot,
                event_id=marker_id,
                event_type=INBOUND_MARKER_TYPE,
                observed_at=recorded_at,
                recorded_at=recorded_at,
                subject=Ref(kind="wake", id=wake_id),
            )
            pending = Wake(
                wake_id=wake_id,
                individual_id=snapshot.individual_id,
                kind="external_event",
                status="pending",
                due_at=recorded_at,
                purpose=INBOUND_PURPOSE,
                cause_event_id=marker_id,
                context_refs=[],
                coalesce_key=None,
                claimed_at=None,
                consumed_at=None,
                revision=1,
            )
            session.add(pending)
            session.flush()
            session.add(
                InboundWake(
                    wake_id=wake_id,
                    individual_id=snapshot.individual_id,
                    connector_binding_id=snapshot.connector_binding_id,
                    creation_event_id=marker_id,
                    created_at=recorded_at,
                    sealed_at=None,
                )
            )
            session.flush()
        event_id = new_id()
        _event(
            session,
            snapshot,
            event_id=event_id,
            event_type="observation.received",
            observed_at=page.observed_at,
            recorded_at=recorded_at,
            item=item,
        )
        created.append(event_id)
        if pending.wake_id not in wake_ids:
            wake_ids.append(pending.wake_id)
        pending.context_refs = [
            *pending.context_refs,
            {"kind": "event", "id": str(event_id)},
        ]
        pending.revision += 1
        observations.append(
            Observation(
                event_id=event_id,
                individual_id=snapshot.individual_id,
                connector_binding_id=snapshot.connector_binding_id,
                external_event_id=item.external_id,
                dedup_key=item.dedup_key,
                fingerprint_version=item.fingerprint_version,
                content_fingerprint=item.content_fingerprint,
                external_content_type=item.content_type,
                raw_content_hash=item.raw_content_hash,
                authentication=item.authentication,
                inbound_wake_id=pending.wake_id,
                receipt_event_id=receipt_id,
            )
        )
        if len(pending.context_refs) == MAX_INBOUND_REFS:
            session.execute(
                update(InboundWake)
                .where(InboundWake.wake_id == pending.wake_id)
                .values(sealed_at=recorded_at)
            )
            session.flush()
            pending = None
    _event(
        session,
        snapshot,
        event_id=receipt_id,
        event_type=RECEIPT_MARKER_TYPE,
        observed_at=page.observed_at,
        recorded_at=recorded_at,
        subject=Ref(kind="connector_binding", id=snapshot.connector_binding_id),
    )
    fields = dict(
        event_id=receipt_id,
        individual_id=snapshot.individual_id,
        connector_binding_id=snapshot.connector_binding_id,
        policy_version=1,
        previous_receipt_event_id=binding.latest_receipt_event_id,
        before_cursor=snapshot.cursor,
        after_cursor=page.next_cursor,
        before_cursor_revision=snapshot.cursor_revision,
        after_cursor_revision=snapshot.cursor_revision + 1,
        authorizing_binding_revision=snapshot.binding_revision,
        observed_at=page.observed_at,
        recorded_at=recorded_at,
        item_count=len(page.items),
        new_count=len(fresh),
        duplicate_count=duplicates,
        source_page_hash=page.source_page_hash,
    )
    session.add(IngestionReceipt(**fields, receipt_hash=receipt_hash(fields)))
    session.flush()
    session.add_all(observations)
    session.flush()
    session.execute(
        update(ConnectorBinding)
        .where(ConnectorBinding.connector_binding_id == snapshot.connector_binding_id)
        .values(
            cursor=page.next_cursor,
            cursor_revision=snapshot.cursor_revision + 1,
            latest_receipt_event_id=receipt_id,
            pending_wake_id=None if pending is None else pending.wake_id,
            revision=snapshot.binding_revision + 1,
            updated_at=recorded_at,
        )
    )
    # Core writes deliberately do not refresh caller-owned ORM objects.
    return IngestionResult(
        tuple(created),
        duplicates,
        tuple(wake_ids),
        snapshot.cursor_revision + 1,
        receipt_id,
    )
