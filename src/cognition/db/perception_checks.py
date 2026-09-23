"""Read-only checks for retained inbound ownership, receipts and wake membership."""

import hashlib
from collections import Counter, defaultdict
from collections.abc import Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from cognition.db.models.attention import Wake
from cognition.db.models.cognition import CycleWake
from cognition.db.models.evidence import Event, EventContent
from cognition.db.models.perception import (
    ConnectorBinding,
    InboundWake,
    IngestionReceipt,
    Observation,
)
from cognition.domain.perception import (
    MAX_AUTH_BYTES,
    canonical_json_bytes,
    validate_cursor,
    validate_json,
    validate_opaque_id,
)
from cognition.protocols.common import normalize_utc
from cognition.protocols.observations_v1 import ObservationAuthentication
from cognition.stores.inbound_scope import (
    INBOUND_MARKER_TYPE,
    get_cycle_inbound,
    validate_inbound_wake,
)
from cognition.stores.perception import (
    RECEIPT_MARKER_TYPE,
    validate_checkpoint,
    validate_receipt,
)

type Finding = Callable[[str, str, UUID, str], None]


def check_perception_state(session: Session, error: Finding) -> None:
    """Observe committed Core values without refreshing or flushing caller objects."""
    with session.no_autoflush:
        bindings = {
            row.connector_binding_id: row
            for row in session.execute(select(ConnectorBinding.__table__)).mappings()
        }
        receipts = {
            row.event_id: row
            for row in session.execute(select(IngestionReceipt.__table__)).mappings()
        }
        observations = {
            row.event_id: row
            for row in session.execute(select(Observation.__table__)).mappings()
        }
        for binding in bindings.values():
            try:
                validate_opaque_id(binding.adapter_id)
                validate_opaque_id(binding.source_id)
                validate_cursor(binding.cursor)
                validate_checkpoint(
                    session, binding.individual_id, binding.connector_binding_id
                )
            except (ValueError, TypeError, LookupError):
                error(
                    "perception_checkpoint",
                    "connector_binding",
                    binding.connector_binding_id,
                    "Connector checkpoint differs from retained ingestion history.",
                )
        _check_receipts(session, bindings, receipts, observations, error)
        _check_observations(session, bindings, receipts, observations, error)
        _check_inbound(session, bindings, observations, error)


def _check_receipts(
    session: Session,
    bindings: dict[UUID, RowMapping],
    receipts: dict[UUID, RowMapping],
    observations: dict[UUID, RowMapping],
    error: Finding,
) -> None:
    counts = Counter(
        (row.individual_id, row.connector_binding_id, row.receipt_event_id)
        for row in observations.values()
    )
    candidates = set(receipts)
    candidates.update(
        session.scalars(
            select(Event.event_id).where(Event.event_type == RECEIPT_MARKER_TYPE)
        )
    )
    grouped: dict[UUID, list[RowMapping]] = defaultdict(list)
    for receipt in receipts.values():
        grouped[receipt.connector_binding_id].append(receipt)
    predecessors: dict[UUID, RowMapping | None] = {}
    for history in grouped.values():
        prior = None
        for receipt in sorted(history, key=lambda row: row.after_cursor_revision):
            predecessors[receipt.event_id] = prior
            prior = receipt
    for identity in sorted(candidates):
        try:
            validate_receipt(session, identity)
            row = receipts[identity]
            binding = bindings.get(row.connector_binding_id)
            prior = predecessors[identity]
            validate_cursor(row.before_cursor)
            validate_cursor(row.after_cursor)
            if (
                binding is None
                or binding.individual_id != row.individual_id
                or row.authorizing_binding_revision > binding.revision
                or row.observed_at < binding.created_at
                or row.recorded_at > binding.updated_at
                or counts[(row.individual_id, row.connector_binding_id, identity)]
                != row.new_count
                or row.new_count == 0
                and row.before_cursor == row.after_cursor
                or prior is None
                and (
                    row.previous_receipt_event_id is not None
                    or row.before_cursor_revision != 0
                    or row.before_cursor is not None
                )
                or prior is not None
                and (
                    row.previous_receipt_event_id != prior.event_id
                    or row.individual_id != prior.individual_id
                    or row.before_cursor_revision != prior.after_cursor_revision
                    or row.before_cursor != prior.after_cursor
                    or row.recorded_at < prior.recorded_at
                )
            ):
                raise ValueError("Invalid owned receipt continuity or count")
        except (ValueError, TypeError, LookupError):
            error(
                "perception_receipt",
                "event",
                identity,
                "Ingestion receipt hash, chain, envelope or owned count is invalid.",
            )


def _check_observations(
    session: Session,
    bindings: dict[UUID, RowMapping],
    receipts: dict[UUID, RowMapping],
    observations: dict[UUID, RowMapping],
    error: Finding,
) -> None:
    candidates = set(observations)
    candidates.update(
        session.scalars(
            select(Event.event_id).where(Event.event_type == "observation.received")
        )
    )
    for identity in sorted(candidates):
        try:
            row = observations.get(identity)
            envelope = (
                session.execute(
                    select(Event.__table__).where(Event.event_id == identity)
                )
                .mappings()
                .one_or_none()
            )
            if row is None or envelope is None:
                raise ValueError("Missing observation or retained envelope")
            binding = bindings.get(row.connector_binding_id)
            receipt = receipts.get(row.receipt_event_id)
            if (
                binding is None
                or receipt is None
                or row.individual_id != binding.individual_id
                or row.individual_id != receipt.individual_id
                or row.connector_binding_id != receipt.connector_binding_id
                or envelope.individual_id != row.individual_id
                or envelope.event_type != "observation.received"
                or envelope.source_kind != "connector"
                or envelope.source_id != binding.adapter_id
                or envelope.source_binding_id != row.connector_binding_id
                or envelope.observed_at != receipt.observed_at
                or envelope.recorded_at != receipt.recorded_at
                or envelope.actor_entity_id is not None
                or envelope.subject_kind is not None
                or envelope.subject_id is not None
                or envelope.causation_event_id is not None
                or envelope.correlation_id is not None
            ):
                raise ValueError("Observation owner, receipt or envelope mismatch")
            ObservationAuthentication.model_validate(row.authentication)
            validate_json(row.authentication)
            if len(canonical_json_bytes(row.authentication)) > MAX_AUTH_BYTES:
                raise ValueError("Observation authentication exceeds metadata limit")
            if row.external_event_id is not None:
                validate_opaque_id(row.external_event_id)
                dedup = hashlib.sha256(
                    canonical_json_bytes(
                        {
                            "version": 1,
                            "kind": "external_id",
                            "value": row.external_event_id,
                        }
                    )
                ).hexdigest()
                if dedup != row.dedup_key:
                    raise ValueError("Observation external identity differs from dedup")
            _check_fingerprint(session, row, envelope)
        except (ValueError, TypeError, LookupError):
            error(
                "perception_observation",
                "event",
                identity,
                "Observation identity, envelope, receipt or fingerprint is invalid.",
            )


def _check_fingerprint(session: Session, row: RowMapping, envelope: RowMapping) -> None:
    content = (
        session.execute(
            select(EventContent.__table__).where(EventContent.event_id == row.event_id)
        )
        .mappings()
        .one_or_none()
    )
    # The retained commitment remains meaningful after redaction, but cannot be
    # reconstructed once its source bytes have intentionally been removed.
    if content is None or content.redacted_at is not None:
        return
    if content.content_type != row.external_content_type:
        raise ValueError("Observation content type differs from retained metadata")
    values = {
        "occurred_at": None
        if envelope.occurred_at is None
        else normalize_utc(envelope.occurred_at).isoformat(),
        "content_type": row.external_content_type,
        "payload": content.payload,
        "text": content.text,
        "authentication": row.authentication,
    }
    if (
        hashlib.sha256(canonical_json_bytes(values)).hexdigest()
        != row.content_fingerprint
    ):
        raise ValueError("Observation fingerprint differs from available content")


def _check_inbound(
    session: Session,
    bindings: dict[UUID, RowMapping],
    observations: dict[UUID, RowMapping],
    error: Finding,
) -> None:
    candidates = {
        (row.individual_id, row.wake_id)
        for row in session.execute(select(InboundWake.__table__)).mappings()
    }
    candidates.update(
        (row.individual_id, row.pending_wake_id)
        for row in bindings.values()
        if row.pending_wake_id is not None
    )
    candidates.update(
        (row.individual_id, row.inbound_wake_id) for row in observations.values()
    )
    for marker in session.execute(
        select(Event.__table__).where(Event.event_type == INBOUND_MARKER_TYPE)
    ).mappings():
        if marker.subject_kind != "wake" or marker.subject_id is None:
            error(
                "perception_inbound_marker",
                "event",
                marker.event_id,
                "Inbound wake marker lacks its retained wake subject.",
            )
        else:
            candidates.add((marker.individual_id, marker.subject_id))
    candidates.update(
        session.execute(
            select(Wake.individual_id, Wake.wake_id)
            .join(Event, Event.event_id == Wake.cause_event_id)
            .where(Event.event_type == INBOUND_MARKER_TYPE)
        ).tuples()
    )
    for owner, identity in sorted(candidates):
        try:
            row = validate_inbound_wake(session, owner, identity)
            if row is None:
                raise ValueError("Missing durable inbound metadata")
            cycle_id = session.scalar(
                select(CycleWake.cycle_id).where(CycleWake.wake_id == identity)
            )
            if cycle_id is not None and get_cycle_inbound(session, cycle_id) is None:
                raise ValueError("Missing inbound cycle membership")
        except (ValueError, TypeError, LookupError):
            error(
                "perception_inbound",
                "wake",
                identity,
                "Inbound wake ownership, membership, pointer or lifecycle is invalid.",
            )
