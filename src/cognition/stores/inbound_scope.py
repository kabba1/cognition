"""Durable inbound grouping and lifecycle, independent of redactable content."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from cognition.db.models import Event, GovernanceState, Individual, Wake
from cognition.db.models.cognition import CognitionCycle, CycleWake
from cognition.db.models.perception import (
    ConnectorBinding,
    InboundWake,
    IngestionReceipt,
    Observation,
)
from cognition.protocols.common import Ref, normalize_utc

INBOUND_MARKER_TYPE = "perception.inbound_wake_created"
PERCEPTION_SOURCE = "perception"
INBOUND_PURPOSE = "Attend to incoming observations."
MAX_INBOUND_REFS = 8


@dataclass(frozen=True)
class InboundWakeRecord:
    wake_id: UUID
    individual_id: UUID
    connector_binding_id: UUID
    creation_event_id: UUID
    member_event_ids: tuple[UUID, ...]
    created_at: datetime
    sealed_at: datetime | None
    status: str


def inbound_wake_predicate() -> ColumnElement[bool]:
    """Classify by any durable association before applying mutable wake fields."""
    return or_(
        select(InboundWake.wake_id)
        .where(InboundWake.wake_id == Wake.wake_id)
        .correlate(Wake)
        .exists(),
        select(Observation.event_id)
        .where(Observation.inbound_wake_id == Wake.wake_id)
        .correlate(Wake)
        .exists(),
        select(ConnectorBinding.connector_binding_id)
        .where(ConnectorBinding.pending_wake_id == Wake.wake_id)
        .correlate(Wake)
        .exists(),
        select(Event.event_id)
        .where(
            Event.event_type == INBOUND_MARKER_TYPE,
            or_(
                and_(Event.subject_kind == "wake", Event.subject_id == Wake.wake_id),
                Event.event_id == Wake.cause_event_id,
            ),
        )
        .correlate(Wake)
        .exists(),
    )


def _owned_association(individual_id: UUID) -> ColumnElement[bool]:
    return or_(
        Wake.individual_id == individual_id,
        select(InboundWake.wake_id)
        .where(
            InboundWake.wake_id == Wake.wake_id,
            InboundWake.individual_id == individual_id,
        )
        .correlate(Wake)
        .exists(),
        select(Observation.event_id)
        .where(
            Observation.inbound_wake_id == Wake.wake_id,
            Observation.individual_id == individual_id,
        )
        .correlate(Wake)
        .exists(),
        select(ConnectorBinding.connector_binding_id)
        .where(
            ConnectorBinding.pending_wake_id == Wake.wake_id,
            ConnectorBinding.individual_id == individual_id,
        )
        .correlate(Wake)
        .exists(),
        select(Event.event_id)
        .where(
            Event.event_type == INBOUND_MARKER_TYPE,
            Event.individual_id == individual_id,
            or_(
                and_(Event.subject_kind == "wake", Event.subject_id == Wake.wake_id),
                Event.event_id == Wake.cause_event_id,
            ),
        )
        .correlate(Wake)
        .exists(),
    )


def validate_inbound_wake(
    session: Session, individual_id: UUID, wake_id: UUID
) -> InboundWakeRecord | None:
    """Return an owned detached group, ordinary None, or explicit corruption error."""
    with session.no_autoflush:
        wake = (
            session.execute(select(Wake.__table__).where(Wake.wake_id == wake_id))
            .mappings()
            .one_or_none()
        )
        metadata = (
            session.execute(
                select(InboundWake.__table__).where(InboundWake.wake_id == wake_id)
            )
            .mappings()
            .one_or_none()
        )
        pointers = (
            session.execute(
                select(
                    ConnectorBinding.connector_binding_id,
                    ConnectorBinding.individual_id,
                )
                .where(ConnectorBinding.pending_wake_id == wake_id)
                .limit(2)
            )
            .mappings()
            .all()
        )
        members = (
            session.execute(
                select(Observation.__table__, Event.event_sequence)
                .join(Event, Event.event_id == Observation.event_id)
                .where(Observation.inbound_wake_id == wake_id)
                .order_by(Event.event_sequence)
                .limit(MAX_INBOUND_REFS + 1)
            )
            .mappings()
            .all()
        )
        markers = (
            session.execute(
                select(Event.__table__)
                .where(
                    Event.event_type == INBOUND_MARKER_TYPE,
                    or_(
                        and_(Event.subject_kind == "wake", Event.subject_id == wake_id),
                        Event.event_id
                        == (None if wake is None else wake.cause_event_id),
                    ),
                )
                .limit(2)
            )
            .mappings()
            .all()
        )
        if metadata is None and not pointers and not members and not markers:
            return None
        if (
            wake is None
            or metadata is None
            or wake.individual_id != individual_id
            or metadata.individual_id != individual_id
            or wake.kind != "external_event"
            or wake.purpose != INBOUND_PURPOSE
            or wake.coalesce_key is not None
            or wake.due_at != metadata.created_at
            or not 1 <= len(members) <= MAX_INBOUND_REFS
            or len(markers) != 1
        ):
            raise ValueError("Inbound wake metadata is missing or inconsistent")
        binding = (
            session.execute(
                select(ConnectorBinding.__table__).where(
                    ConnectorBinding.connector_binding_id
                    == metadata.connector_binding_id
                )
            )
            .mappings()
            .one_or_none()
        )
        if binding is None or binding.individual_id != individual_id:
            raise ValueError("Inbound binding is missing or foreign")
        marker = markers[0]
        if (
            marker.event_id != metadata.creation_event_id
            or marker.event_id != wake.cause_event_id
            or marker.individual_id != individual_id
            or marker.source_kind != "runtime"
            or marker.source_id != PERCEPTION_SOURCE
            or marker.source_binding_id != metadata.connector_binding_id
            or marker.subject_kind != "wake"
            or marker.subject_id != wake_id
            or marker.occurred_at is not None
            or marker.observed_at != metadata.created_at
            or marker.recorded_at != metadata.created_at
            or marker.actor_entity_id is not None
            or marker.causation_event_id is not None
            or marker.correlation_id is not None
        ):
            raise ValueError("Inbound creation marker is inconsistent")
        if metadata.sealed_at is None:
            if (
                wake.status != "pending"
                or len(members) >= MAX_INBOUND_REFS
                or len(pointers) != 1
                or pointers[0].connector_binding_id != metadata.connector_binding_id
                or pointers[0].individual_id != individual_id
            ):
                raise ValueError(
                    "Unsealed inbound wake has inconsistent pointer or lifecycle"
                )
        elif metadata.sealed_at < metadata.created_at or pointers:
            raise ValueError("Sealed inbound wake has inconsistent pointer or timing")
        member_ids = tuple(member.event_id for member in members)
        expected_refs = [
            Ref(kind="event", id=identity).model_dump(mode="json")
            for identity in member_ids
        ]
        if wake.context_refs != expected_refs:
            raise ValueError("Inbound wake refs differ from immutable membership")
        for member in members:
            envelope = (
                session.execute(
                    select(Event.__table__).where(Event.event_id == member.event_id)
                )
                .mappings()
                .one()
            )
            receipt = (
                session.execute(
                    select(
                        IngestionReceipt.individual_id,
                        IngestionReceipt.connector_binding_id,
                    ).where(IngestionReceipt.event_id == member.receipt_event_id)
                )
                .mappings()
                .one_or_none()
            )
            if (
                member.individual_id != individual_id
                or member.connector_binding_id != metadata.connector_binding_id
                or envelope.individual_id != individual_id
                or envelope.event_type != "observation.received"
                or envelope.source_kind != "connector"
                or envelope.source_id != binding.adapter_id
                or envelope.source_binding_id != metadata.connector_binding_id
                or envelope.actor_entity_id is not None
                or envelope.subject_kind is not None
                or envelope.subject_id is not None
                or envelope.causation_event_id is not None
                or envelope.correlation_id is not None
                or envelope.recorded_at < metadata.created_at
                or (
                    metadata.sealed_at is not None
                    and envelope.recorded_at > metadata.sealed_at
                )
                or receipt is None
                or receipt.individual_id != individual_id
                or receipt.connector_binding_id != metadata.connector_binding_id
            ):
                raise ValueError(
                    "Inbound observation linkage is missing or inconsistent"
                )
        cycles = (
            session.execute(
                select(CognitionCycle.__table__)
                .join(CycleWake, CycleWake.cycle_id == CognitionCycle.cycle_id)
                .where(CycleWake.wake_id == wake_id)
                .limit(2)
            )
            .mappings()
            .all()
        )
        if wake.status == "pending":
            if cycles or wake.claimed_at is not None or wake.consumed_at is not None:
                raise ValueError("Pending inbound wake has execution history")
        elif wake.status in {"claimed", "consumed"}:
            if (
                len(cycles) != 1
                or metadata.sealed_at is None
                or wake.claimed_at is None
            ):
                raise ValueError("Claimed inbound wake lacks its owned cycle")
            cycle = cycles[0]
            cycle_members = session.scalars(
                select(CycleWake.wake_id)
                .where(CycleWake.cycle_id == cycle.cycle_id)
                .limit(2)
            ).all()
            if (
                cycle.individual_id != individual_id
                or cycle_members != [wake_id]
                or cycle.started_at != wake.claimed_at
                or wake.claimed_at < metadata.sealed_at
            ):
                raise ValueError(
                    "Inbound cycle ownership or isolated membership is inconsistent"
                )
            if wake.status == "claimed":
                if (
                    cycle.status != "active"
                    or cycle.completed_at is not None
                    or wake.consumed_at is not None
                ):
                    raise ValueError("Claimed inbound wake lacks an active cycle")
            elif (
                cycle.status not in {"completed", "failed"}
                or cycle.completed_at is None
                or wake.consumed_at != cycle.completed_at
                or cycle.completed_at < cycle.started_at
            ):
                raise ValueError("Consumed inbound wake lacks its terminal cycle")
        else:
            raise ValueError("Inbound wake has an unsupported terminal status")
        return InboundWakeRecord(
            wake_id,
            individual_id,
            metadata.connector_binding_id,
            metadata.creation_event_id,
            member_ids,
            metadata.created_at,
            metadata.sealed_at,
            wake.status,
        )


def get_cycle_inbound(session: Session, cycle_id: UUID) -> InboundWakeRecord | None:
    """Inspect every durable member before filtering by owner or mutable kind."""
    with session.no_autoflush:
        cycle = session.execute(
            select(CognitionCycle.individual_id).where(
                CognitionCycle.cycle_id == cycle_id
            )
        ).one_or_none()
        if cycle is None:
            raise ValueError("Inbound cycle is missing")
        managed = session.scalars(
            select(CycleWake.wake_id)
            .join(Wake, Wake.wake_id == CycleWake.wake_id)
            .where(CycleWake.cycle_id == cycle_id, inbound_wake_predicate())
            .limit(2)
        ).all()
        if not managed:
            return None
        ids = session.scalars(
            select(CycleWake.wake_id).where(CycleWake.cycle_id == cycle_id).limit(2)
        ).all()
        if len(managed) != 1 or len(ids) != 1:
            raise ValueError("Inbound cycle must contain exactly its one inbound wake")
        return validate_inbound_wake(session, cycle.individual_id, managed[0])


def validate_inbound_claims(session: Session, individual_id: UUID) -> None:
    """Fail before generic recovery can replace a retained inbound identity."""
    with session.no_autoflush:
        foreign = session.scalar(
            select(Wake.wake_id)
            .where(
                inbound_wake_predicate(),
                _owned_association(individual_id),
                Wake.individual_id != individual_id,
                Wake.status.in_(("pending", "claimed")),
            )
            .limit(1)
        )
        if foreign is not None:
            raise ValueError("Inbound live wake has foreign ownership")
        claimed = session.scalars(
            select(Wake.wake_id)
            .where(
                inbound_wake_predicate(),
                _owned_association(individual_id),
                Wake.status == "claimed",
            )
            .limit(2)
        ).all()
        for identity in claimed:
            validate_inbound_wake(session, individual_id, identity)


def seal_inbound_wake(
    session: Session, individual_id: UUID, wake_id: UUID, now: datetime
) -> InboundWakeRecord:
    """Seal a claim's scope under the canonical lock order, without committing."""
    now = normalize_utc(now)
    if session.new or session.dirty or session.deleted:
        raise ValueError("unflushed_inbound_state")
    with session.no_autoflush:
        session.execute(
            select(Individual.individual_id)
            .where(Individual.individual_id == individual_id)
            .with_for_update()
        ).one()
        session.execute(
            select(GovernanceState.individual_id)
            .where(GovernanceState.individual_id == individual_id)
            .with_for_update()
        ).one()
        record = validate_inbound_wake(session, individual_id, wake_id)
        if record is None:
            raise ValueError("Wake is not managed inbound evidence")
        binding = (
            session.execute(
                select(ConnectorBinding.__table__)
                .where(
                    ConnectorBinding.connector_binding_id == record.connector_binding_id
                )
                .with_for_update()
            )
            .mappings()
            .one()
        )
        session.execute(
            select(Wake.wake_id).where(Wake.wake_id == wake_id).with_for_update()
        ).one()
        record = validate_inbound_wake(session, individual_id, wake_id)
        assert record is not None
        if record.sealed_at is not None:
            if now < record.sealed_at:
                raise ValueError("Inbound claim cannot precede retained seal timing")
            return record
        if now < max(record.created_at, binding.updated_at):
            raise ValueError("Inbound seal cannot precede retained source timing")
        session.execute(
            update(InboundWake)
            .where(InboundWake.wake_id == wake_id)
            .values(sealed_at=now)
        )
        session.execute(
            update(ConnectorBinding)
            .where(ConnectorBinding.connector_binding_id == record.connector_binding_id)
            .values(
                pending_wake_id=None,
                revision=ConnectorBinding.revision + 1,
                updated_at=now,
            )
        )
        sealed = validate_inbound_wake(session, individual_id, wake_id)
        assert sealed is not None
        return sealed
