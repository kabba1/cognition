"""Bounded reflection opportunities; scheduling never establishes a claim."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from cognition.db.models.attention import Wake
from cognition.db.models.cognition import CognitionCycle, CycleWake
from cognition.db.models.development import Interest, Preference, SelfState
from cognition.db.models.evidence import Event
from cognition.db.models.reflection import ManagedReflectionBatch, ReflectionState
from cognition.domain.heartbeat import HeartbeatPolicy
from cognition.protocols.common import JsonObject, Ref, new_id, normalize_utc
from cognition.protocols.events_v1 import EventContent, EventEnvelopeV1, EventSource
from cognition.stores.autonomy import _active_cycle, _configuration, _lock
from cognition.stores.evidence import append_event
from cognition.stores.reflection_candidates import (
    earliest_reflection_eligibility,
    select_reflection_candidates,
)
from cognition.stores.reflection_scope import (
    managed_reflection_scope,
    reflection_batch_hash,
)

REFLECTION_PURPOSE = (
    "Review these staged personal interpretations if useful. No change is required. "
    "These references are review targets, not evidence that their claims are true. "
    "Existing maturation and independent grounding requirements still apply."
)
_CADENCE = HeartbeatPolicy(86400, 86400, 1)
_CURSORS = {
    "interest": "interest_cursor",
    "preference": "preference_cursor",
    "self_state": "self_state_cursor",
}


def _reflection_lock(session: Session, identity: UUID) -> bool:
    if any(
        isinstance(
            row,
            (
                ReflectionState,
                ManagedReflectionBatch,
                Interest,
                Preference,
                SelfState,
                Event,
            ),
        )
        for row in session.new | session.dirty | session.deleted
    ):
        raise ValueError("unflushed_scheduler_state")
    return _lock(session, identity)


def validate_reflection_state(
    session: Session, identity: UUID, *, finishing: bool = False
) -> RowMapping | None:
    """Read only committed columns; also find live batches hidden by the pointer."""
    with session.no_autoflush:
        state = (
            session.execute(
                select(ReflectionState.__table__).where(
                    ReflectionState.individual_id == identity
                )
            )
            .mappings()
            .one_or_none()
        )
        marked = exists(
            select(Event.event_id).where(
                Event.event_type == "attention.reflection_scheduled",
                or_(
                    Event.event_id == Wake.cause_event_id,
                    (Event.subject_kind == "wake") & (Event.subject_id == Wake.wake_id),
                ),
            )
        )
        live = set(
            session.scalars(
                select(Wake.wake_id)
                .outerjoin(
                    ManagedReflectionBatch,
                    ManagedReflectionBatch.wake_id == Wake.wake_id,
                )
                .where(
                    or_(
                        Wake.individual_id == identity,
                        ManagedReflectionBatch.individual_id == identity,
                    ),
                    Wake.status.in_(("pending", "claimed")),
                    or_(ManagedReflectionBatch.wake_id.is_not(None), marked),
                )
                .limit(2)
            )
        )
        if len(live) > 1 or (
            live and (state is None or live != {state.managed_wake_id})
        ):
            raise ValueError("Invalid managed reflection live cardinality")
        if state is None:
            return None
        if state.policy_version != 1 or state.revision < 1:
            raise ValueError("Invalid reflection policy")
        if state.last_completed_cycle_id is not None:
            cycle = (
                session.execute(
                    select(CognitionCycle.__table__).where(
                        CognitionCycle.cycle_id == state.last_completed_cycle_id
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                cycle is None
                or cycle.individual_id != identity
                or cycle.status not in {"completed", "failed"}
                or cycle.completed_at is None
                or state.next_review_at
                != _CADENCE.schedule(cycle.completed_at, 86400).due_at
            ):
                raise ValueError("Invalid reflection completed cycle or cadence")
        if state.managed_wake_id is not None:
            scope = managed_reflection_scope(session, identity, state.managed_wake_id)
            if scope is None:
                raise ValueError("Missing managed reflection scope")
            wake = (
                session.execute(
                    select(Wake.__table__).where(Wake.wake_id == state.managed_wake_id)
                )
                .mappings()
                .one()
            )
            if (
                wake.status == "consumed"
                and not state.materialization_pending
                and not finishing
            ):
                raise ValueError(
                    "Consumed managed reflection lacks deferred materialization"
                )
            if wake.status == "claimed":
                active = session.scalar(
                    select(CognitionCycle.cycle_id)
                    .join(CycleWake)
                    .where(
                        CycleWake.wake_id == wake.wake_id,
                        CognitionCycle.individual_id == identity,
                        CognitionCycle.status == "active",
                    )
                )
                if active is None:
                    raise ValueError("Orphan managed reflection claim")
            if wake.status == "consumed":
                terminal = session.execute(
                    select(CognitionCycle.cycle_id, CognitionCycle.completed_at)
                    .join(CycleWake)
                    .where(
                        CycleWake.wake_id == wake.wake_id,
                        CognitionCycle.individual_id == identity,
                        CognitionCycle.status.in_(("completed", "failed")),
                    )
                ).one_or_none()
                if (
                    terminal is None
                    or terminal.completed_at is None
                    or terminal.completed_at != wake.consumed_at
                ):
                    raise ValueError("Invalid managed reflection consumed cycle")
                if (
                    not finishing
                    and session.scalar(
                        select(Event.event_id)
                        .where(
                            Event.individual_id == identity,
                            Event.event_type == "attention.reflection_completed",
                            Event.source_kind == "runtime",
                            Event.source_id == "attention.reflection",
                            Event.subject_kind == "wake",
                            Event.subject_id == wake.wake_id,
                            Event.correlation_id == terminal.cycle_id,
                        )
                        .limit(1)
                    )
                    is None
                ):
                    raise ValueError(
                        "Missing managed reflection consumed cycle accounting"
                    )
        return state


def _event(
    session: Session,
    identity: UUID,
    wake_id: UUID,
    now: datetime,
    kind: str,
    payload: JsonObject,
    cycle_id: UUID | None = None,
) -> UUID:
    event_id = new_id()
    append_event(
        session,
        EventEnvelopeV1(
            schema_version=1,
            event_id=event_id,
            individual_id=identity,
            event_type=f"attention.reflection_{kind}",
            occurred_at=now,
            observed_at=now,
            recorded_at=now,
            source=EventSource(
                kind="runtime", source_id="attention.reflection", binding_id=None
            ),
            actor_entity_id=None,
            causation_event_id=None,
            correlation_id=cycle_id,
            subject=Ref(kind="wake", id=wake_id),
            provenance={"policy_version": 1},
            runtime_version="0.1.0",
            content=EventContent(
                content_type="application/json",
                text=None,
                blob_ref=None,
                content_hash=None,
                sensitivity="internal",
                retention_class="history",
                retain_until=None,
                payload={"policy_version": 1, **payload},
            ),
        ),
    )
    return event_id


def _still_pending(session: Session, identity: UUID, refs: tuple[Ref, ...]) -> bool:
    for ref in refs:
        if ref.kind == "interest":
            query = select(Interest.interest_id).where(
                Interest.individual_id == identity,
                Interest.interest_id == ref.id,
                Interest.status == "candidate",
            )
        elif ref.kind == "preference":
            query = select(Preference.preference_id).where(
                Preference.individual_id == identity,
                Preference.preference_id == ref.id,
                Preference.status == "tentative",
            )
        else:
            query = select(SelfState.self_state_id).where(
                SelfState.individual_id == identity,
                SelfState.self_state_id == ref.id,
                SelfState.layer.in_(("self_belief", "current_value")),
                SelfState.pending_content.is_not(None),
                SelfState.pending_not_before.is_not(None),
            )
        if session.scalar(query) is not None:
            return True
    return False


def _reconcile(session: Session, state: ReflectionState, now: datetime) -> bool:
    """Caller has validated lifecycle, configuration and absence of active cycle."""
    changed = False
    if state.managed_wake_id is not None:
        wake = session.get(Wake, state.managed_wake_id, populate_existing=True)
        assert wake is not None
        if wake.status == "claimed":
            return False
        if wake.status == "pending":
            scope = managed_reflection_scope(session, state.individual_id, wake.wake_id)
            assert scope is not None
            if _still_pending(session, state.individual_id, scope):
                return False
            wake.status, wake.revision = "cancelled", wake.revision + 1
            _event(
                session,
                state.individual_id,
                wake.wake_id,
                now,
                "cancelled",
                {"reason": "no_selected_target_remains_pending"},
            )
        state.managed_wake_id = None
        changed = True
    earliest = earliest_reflection_eligibility(session, state.individual_id)
    if earliest is not None:
        due = max(now, earliest, state.next_review_at)
        candidates = select_reflection_candidates(
            session,
            state.individual_id,
            eligible_by=due,
            cursors={kind: getattr(state, field) for kind, field in _CURSORS.items()},
        )
        if not candidates:
            raise ValueError(
                "Reflection candidates disappeared within the scheduling lock"
            )
        wake_id = new_id()
        metadata: list[JsonObject] = [
            {
                "kind": item.ref.kind,
                "id": str(item.ref.id),
                "revision": item.revision,
                "eligible_at": item.eligible_at.isoformat(),
            }
            for item in candidates
        ]
        wake = Wake(
            wake_id=wake_id,
            individual_id=state.individual_id,
            kind="reflection",
            status="pending",
            due_at=due,
            purpose=REFLECTION_PURPOSE,
            cause_event_id=None,
            context_refs=[item.ref.model_dump(mode="json") for item in candidates],
            coalesce_key=None,
            revision=1,
        )
        session.add(wake)
        session.flush()
        session.add(
            ManagedReflectionBatch(
                wake_id=wake_id,
                individual_id=state.individual_id,
                policy_version=1,
                selected_at=now,
                target_metadata=metadata,
                content_hash=reflection_batch_hash(
                    individual_id=state.individual_id,
                    wake_id=wake_id,
                    policy_version=1,
                    selected_at=now,
                    target_metadata=metadata,
                ),
            )
        )
        session.flush()
        wake.cause_event_id = _event(
            session,
            state.individual_id,
            wake_id,
            now,
            "scheduled",
            {
                "due_at": due.isoformat(),
                "target_metadata": [item for item in metadata],
                "cadence_seconds": 86400,
                "batch_limit": 8,
            },
        )
        state.managed_wake_id = wake_id
        changed = True
    changed = changed or state.materialization_pending
    state.materialization_pending = False
    return changed


def _validate_clock(session: Session, state: ReflectionState, now: datetime) -> None:
    if state.last_completed_cycle_id is None:
        anchor = state.next_review_at
    else:
        anchor = session.scalar(
            select(CognitionCycle.completed_at).where(
                CognitionCycle.cycle_id == state.last_completed_cycle_id
            )
        )
    selected = (
        None
        if state.managed_wake_id is None
        else session.scalar(
            select(ManagedReflectionBatch.selected_at).where(
                ManagedReflectionBatch.wake_id == state.managed_wake_id
            )
        )
    )
    if (anchor is not None and now < anchor) or (
        selected is not None and now < selected
    ):
        raise ValueError("Reflection clock precedes its durable anchor")


def ensure_reflection(
    session: Session, individual_id: UUID, now: datetime
) -> ReflectionState | None:
    now = normalize_utc(now)
    with session.no_autoflush:
        if not _reflection_lock(session, individual_id):
            return None
        retained = validate_reflection_state(session, individual_id)
        state = (
            None
            if retained is None
            else session.get(ReflectionState, individual_id, populate_existing=True)
        )
        if state is not None:
            _validate_clock(session, state, now)
        if _configuration(session, individual_id) is None or _active_cycle(
            session, individual_id
        ):
            return state
        initialized = state is None
        if state is None:
            if earliest_reflection_eligibility(session, individual_id) is None:
                return None
            state = ReflectionState(
                individual_id=individual_id,
                policy_version=1,
                next_review_at=now,
                materialization_pending=True,
                revision=1,
            )
            session.add(state)
            session.flush()
        if _reconcile(session, state, now) and not initialized:
            state.revision += 1
        session.flush()
        return state


def record_reflection_outcome(
    session: Session, cycle_id: UUID, now: datetime
) -> ReflectionState | None:
    now = normalize_utc(now)
    with session.no_autoflush:
        cycle_query = select(CognitionCycle.__table__).where(
            CognitionCycle.cycle_id == cycle_id
        )
        cycle = session.execute(cycle_query).mappings().one_or_none()
        if cycle is None:
            raise ValueError(
                "Persisted terminal cycle required for reflection accounting"
            )
        identity = cycle.individual_id
        allowed = _reflection_lock(session, identity)
        cycle = session.execute(cycle_query).mappings().one()
        if (
            cycle.individual_id != identity
            or cycle.status not in {"completed", "failed"}
            or cycle.completed_at != now
        ):
            raise ValueError(
                "Persisted terminal cycle required for reflection accounting"
            )
        retained = validate_reflection_state(session, identity, finishing=True)
        state = (
            None
            if retained is None
            else session.get(ReflectionState, identity, populate_existing=True)
        )
        accounted = session.scalar(
            select(Event.event_id)
            .where(
                Event.individual_id == identity,
                Event.correlation_id == cycle_id,
                Event.event_type == "attention.reflection_completed",
                Event.source_kind == "runtime",
                Event.source_id == "attention.reflection",
                Event.subject_kind == "wake",
            )
            .limit(1)
        )
        if accounted is not None:
            return state
        managed: list[tuple[UUID, tuple[Ref, ...]]] = []
        for wake_id in session.scalars(
            select(Wake.wake_id)
            .join(CycleWake)
            .where(
                CycleWake.cycle_id == cycle_id,
            )
        ):
            scope = managed_reflection_scope(session, identity, wake_id)
            if scope is not None:
                managed.append((wake_id, scope))
        if not managed:
            return state
        if len(managed) != 1 or state is None or state.managed_wake_id != managed[0][0]:
            raise ValueError("Invalid managed reflection terminal batch")
        wake_id, scope = managed[0]
        wake = (
            session.execute(select(Wake.__table__).where(Wake.wake_id == wake_id))
            .mappings()
            .one()
        )
        if wake.status != "consumed" or wake.consumed_at != now:
            raise ValueError("Managed reflection outcome requires its consumed wake")
        _validate_clock(session, state, now)
        for ref in scope:
            setattr(state, _CURSORS[ref.kind], ref.id)
        state.last_completed_cycle_id = cycle_id
        state.next_review_at = _CADENCE.schedule(now, 86400).due_at
        state.materialization_pending = True
        state.revision += 1
        _event(
            session,
            identity,
            wake_id,
            now,
            "completed",
            {
                "next_review_at": state.next_review_at.isoformat(),
                "cycle_status": cycle.status,
            },
            cycle_id,
        )
        if (
            allowed
            and _configuration(session, identity) is not None
            and not _active_cycle(session, identity)
        ):
            _reconcile(session, state, now)
        session.flush()
        return state
