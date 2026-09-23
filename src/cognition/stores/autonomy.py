"""Durable heartbeat bookkeeping and separately guarded wake materialization."""

import math
from copy import deepcopy
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from cognition.config.revisions import behavior_hash
from cognition.config.schema import BehaviorConfiguration, parse_behavior_config
from cognition.db.models.attention import Wake
from cognition.db.models.autonomy import AutonomyState
from cognition.db.models.cognition import (
    AppliedOperation,
    CognitionCycle,
    CognitionTurn,
    CycleWake,
)
from cognition.db.models.evidence import Event
from cognition.db.models.governance import GovernanceState
from cognition.db.models.identity import Individual
from cognition.db.models.personal import Commitment
from cognition.db.models.runtime import RuntimeConfigRevision
from cognition.domain.heartbeat import HeartbeatPolicy, HeartbeatSchedule
from cognition.protocols.common import JsonObject, Ref, new_id, normalize_utc
from cognition.protocols.events_v1 import EventContent, EventEnvelopeV1, EventSource
from cognition.protocols.executive import IncompatibleExecutiveContract
from cognition.stores.evidence import append_event

HEARTBEAT_PURPOSE = (
    "Consider whether durable intentions or the present situation warrant attention. "
    "Sleep or no action is explicitly permitted; do not invent activity while asleep."
)
_AUTONOMOUS_KINDS = {
    "heartbeat",
    "self_scheduled",
    "reflection",
    "goal_review",
    "routine",
    "maintenance",
}
_PERSONAL_KINDS = {
    f"{kind}_operation"
    for kind in (
        "entity",
        "project",
        "goal",
        "commitment",
        "belief",
        "episode",
        "interest",
        "preference",
        "self_state",
        "relationship",
        "relationship_thread",
    )
}


def _lock(session: Session, individual_id: UUID) -> bool:
    conflicting = (
        Individual,
        GovernanceState,
        AutonomyState,
        Wake,
        RuntimeConfigRevision,
        Commitment,
        CognitionCycle,
        CognitionTurn,
        CycleWake,
        AppliedOperation,
    )
    if any(
        isinstance(row, conflicting)
        for row in session.new | session.dirty | session.deleted
    ):
        raise ValueError("unflushed_scheduler_state")
    person = session.execute(
        select(Individual.operational_status)
        .where(Individual.individual_id == individual_id)
        .with_for_update()
    ).one_or_none()
    governance = session.execute(
        select(GovernanceState.inference_blocked)
        .where(GovernanceState.individual_id == individual_id)
        .with_for_update()
    ).one_or_none()
    if person is None or governance is None:
        raise LookupError("Individual or governance state does not exist")
    return person.operational_status == "active" and not governance.inference_blocked


def _configuration(
    session: Session, individual_id: UUID, identity: UUID | None = None
) -> tuple[UUID, BehaviorConfiguration] | None:
    table = RuntimeConfigRevision.__table__
    query = select(table).where(table.c.individual_id == individual_id)
    if identity is None:
        query = query.where(
            table.c.activated_at.is_not(None), table.c.superseded_at.is_(None)
        )
    else:
        query = query.where(table.c.config_revision_id == identity)
    row = session.execute(query).mappings().one_or_none()
    if row is None:
        if identity is not None:
            raise IncompatibleExecutiveContract(
                "Scheduler configuration is missing or foreign"
            )
        return None
    try:
        behavior = parse_behavior_config(row["sanitized_config"])
    except (ValueError, TypeError) as error:
        raise IncompatibleExecutiveContract(
            "Scheduler configuration is incompatible"
        ) from error
    if (
        row["activated_at"] is None
        or behavior.config_schema_version != row["config_schema_version"]
        or behavior_hash(behavior) != row["content_hash"]
    ):
        raise IncompatibleExecutiveContract(
            "Scheduler configuration integrity mismatch"
        )
    return row["config_revision_id"], behavior


def _policy(config: BehaviorConfiguration) -> HeartbeatPolicy:
    attention = config.attention
    return HeartbeatPolicy(
        attention.heartbeat_min_seconds,
        attention.heartbeat_max_seconds,
        attention.heartbeat_backoff_factor,
    )


def _state(
    session: Session, individual_id: UUID, *, finishing: bool = False
) -> AutonomyState | None:
    state = session.get(AutonomyState, individual_id, populate_existing=True)
    if state is None:
        return None
    if (
        state.policy_version != 1
        or not math.isfinite(state.interval_seconds)
        or state.interval_seconds < 1
        or state.revision < 1
    ):
        raise ValueError("Invalid scheduler state")
    retained = _configuration(session, individual_id, state.config_revision_id)
    assert retained is not None
    if _policy(retained[1]).clamp(state.interval_seconds) != state.interval_seconds:
        raise ValueError("Interval is outside its retained policy")
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
            or cycle.individual_id != individual_id
            or cycle.status not in {"completed", "failed"}
            or cycle.completed_at != state.anchor_at
        ):
            raise ValueError("Invalid scheduler completed cycle")
    if state.managed_wake_id is None:
        if not state.materialization_pending:
            raise ValueError("Missing managed heartbeat")
        return state
    wake = session.get(Wake, state.managed_wake_id, populate_existing=True)
    if (
        wake is None
        or wake.individual_id != individual_id
        or wake.kind != "heartbeat"
        or wake.coalesce_key is not None
    ):
        raise ValueError("Invalid managed heartbeat")
    if (
        wake.status == "consumed"
        and not state.materialization_pending
        and not finishing
    ):
        raise ValueError("Consumed managed heartbeat lacks deferred materialization")
    if wake.status == "claimed":
        owner = session.scalar(
            select(CognitionCycle.cycle_id)
            .join(CycleWake)
            .where(
                CycleWake.wake_id == wake.wake_id,
                CognitionCycle.individual_id == individual_id,
                CognitionCycle.status == "active",
            )
        )
        if owner is None:
            raise ValueError("Orphan managed heartbeat claim")
    return state


def _snapshot(session: Session, state: AutonomyState) -> JsonObject:
    wake = (
        None
        if state.managed_wake_id is None
        else session.get(Wake, state.managed_wake_id)
    )
    return {
        "interval_seconds": state.interval_seconds,
        "anchor_at": normalize_utc(state.anchor_at).isoformat(),
        "managed_wake_id": str(state.managed_wake_id)
        if state.managed_wake_id
        else None,
        "last_completed_cycle_id": str(state.last_completed_cycle_id)
        if state.last_completed_cycle_id
        else None,
        "config_revision_id": str(state.config_revision_id),
        "materialization_pending": state.materialization_pending,
        "wake_status": None if wake is None else wake.status,
        "wake_due_at": None if wake is None else normalize_utc(wake.due_at).isoformat(),
        "wake_context_refs": []
        if wake is None
        else [deepcopy(ref) for ref in wake.context_refs],
    }


def _initialize(
    session: Session,
    individual_id: UUID,
    now: datetime,
    config_id: UUID,
    policy: HeartbeatPolicy,
) -> AutonomyState:
    state = AutonomyState(
        individual_id=individual_id,
        policy_version=1,
        interval_seconds=policy.effective_minimum_seconds,
        anchor_at=now,
        managed_wake_id=None,
        last_completed_cycle_id=None,
        config_revision_id=config_id,
        materialization_pending=True,
        revision=1,
    )
    session.add(state)
    return state


def _active_cycle(session: Session, individual_id: UUID) -> bool:
    return (
        session.scalar(
            select(CognitionCycle.cycle_id)
            .where(
                CognitionCycle.individual_id == individual_id,
                CognitionCycle.status == "active",
            )
            .limit(1)
        )
        is not None
    )


def _desired(
    session: Session, state: AutonomyState, policy: HeartbeatPolicy
) -> tuple[HeartbeatSchedule, list[JsonObject]]:
    commitment = session.execute(
        select(Commitment.commitment_id, Commitment.due_at)
        .where(
            Commitment.individual_id == state.individual_id,
            Commitment.status.in_(("active", "disputed")),
            Commitment.due_at > state.anchor_at,
        )
        .order_by(Commitment.due_at, Commitment.commitment_id)
        .limit(1)
    ).one_or_none()
    schedule = policy.schedule(
        state.anchor_at,
        state.interval_seconds,
        commitment_due=None if commitment is None else commitment.due_at,
    )
    refs: list[JsonObject] = (
        []
        if commitment is None or not schedule.commitment_shortened
        else [
            Ref(kind="commitment", id=commitment.commitment_id).model_dump(mode="json")
        ]
    )
    return schedule, refs


def _materialize(
    session: Session, state: AutonomyState, policy: HeartbeatPolicy
) -> bool:
    schedule, refs = _desired(session, state, policy)
    wake = (
        None
        if state.managed_wake_id is None
        else session.get(Wake, state.managed_wake_id)
    )
    changed = False
    if wake is None or wake.status in {"consumed", "cancelled", "superseded"}:
        wake = Wake(
            wake_id=new_id(),
            individual_id=state.individual_id,
            kind="heartbeat",
            status="pending",
            due_at=schedule.due_at,
            purpose=HEARTBEAT_PURPOSE,
            cause_event_id=None,
            context_refs=refs,
            coalesce_key=None,
            revision=1,
        )
        session.add(wake)
        state.managed_wake_id = wake.wake_id
        changed = True
    elif wake.status != "pending":
        raise ValueError("Cannot materialize a claimed managed heartbeat")
    elif wake.due_at != schedule.due_at or wake.context_refs != refs:
        wake.due_at, wake.context_refs = schedule.due_at, refs
        wake.revision += 1
        changed = True
    state.materialization_pending = False
    # Flush the new wake before snapshot/event construction; caller still owns commit.
    session.flush()
    return changed


def _record(
    session: Session,
    state: AutonomyState,
    before: JsonObject | None,
    now: datetime,
    policy: HeartbeatPolicy,
    *,
    reason: str,
    materialized: bool,
    cycle_id: UUID | None = None,
    autonomous: bool | None = None,
    activity: bool | None = None,
) -> None:
    after = _snapshot(session, state)
    if before == after:
        return
    desired, desired_refs = _desired(session, state, policy)
    if before is not None:
        state.revision += 1
    event_id = new_id()
    append_event(
        session,
        EventEnvelopeV1(
            schema_version=1,
            event_id=event_id,
            individual_id=state.individual_id,
            event_type="attention.heartbeat_updated",
            occurred_at=now,
            observed_at=now,
            recorded_at=now,
            source=EventSource(
                kind="runtime", source_id="attention.heartbeat", binding_id=None
            ),
            actor_entity_id=None,
            causation_event_id=None,
            correlation_id=cycle_id,
            subject=Ref(kind="individual", id=state.individual_id),
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
                payload={
                    "policy_version": 1,
                    "reason": reason,
                    "before": before,
                    "after": after,
                    "wake_materialized": materialized,
                    "autonomous_cycle": autonomous,
                    "recorded_personal_activity": activity,
                    "effective_minimum_seconds": policy.effective_minimum_seconds,
                    "effective_maximum_seconds": policy.effective_maximum_seconds,
                    "backoff_factor": policy.backoff_factor,
                    "desired_due_at": desired.due_at.isoformat(),
                    "desired_context_refs": [ref for ref in desired_refs],
                    "default_due_at": policy.schedule(
                        state.anchor_at, state.interval_seconds
                    ).due_at.isoformat(),
                },
            ),
        ),
    )
    if materialized and state.managed_wake_id is not None:
        wake = session.get(Wake, state.managed_wake_id)
        assert wake is not None
        wake.cause_event_id = event_id
    session.flush()


def ensure_heartbeat(
    session: Session, individual_id: UUID, now: datetime
) -> AutonomyState | None:
    """Ensure one eligible opportunity; unchanged polling writes nothing."""
    now = normalize_utc(now)
    with session.no_autoflush:
        if not _lock(session, individual_id):
            return None
        state = _state(session, individual_id)
        config = _configuration(session, individual_id)
        if config is None:
            return state
        policy = _policy(config[1])
        before = None if state is None else _snapshot(session, state)
        if state is None:
            state = _initialize(session, individual_id, now, config[0], policy)
        if now < state.anchor_at:
            raise ValueError("Scheduler clock precedes its durable anchor")
        if _active_cycle(session, individual_id):
            _record(
                session,
                state,
                before,
                now,
                policy,
                reason="initialize_active_cycle",
                materialized=False,
            )
            return state
        state.config_revision_id = config[0]
        state.interval_seconds = policy.clamp(state.interval_seconds)
        changed = _materialize(session, state, policy)
        _record(
            session,
            state,
            before,
            now,
            policy,
            reason="reconcile" if before else "initialize",
            materialized=changed,
        )
        return state


def record_cycle_outcome(
    session: Session, cycle_id: UUID, now: datetime
) -> AutonomyState | None:
    """Account for one terminal cycle; pause can defer its wake materialization."""
    now = normalize_utc(now)
    with session.no_autoflush:
        cycle = (
            session.execute(
                select(CognitionCycle.__table__).where(
                    CognitionCycle.cycle_id == cycle_id
                )
            )
            .mappings()
            .one_or_none()
        )
        if cycle is None:
            raise ValueError(
                "A persisted terminal cycle is required for scheduler accounting"
            )
        locked_individual = cycle.individual_id
        allowed = _lock(session, locked_individual)
        cycle = (
            session.execute(
                select(CognitionCycle.__table__).where(
                    CognitionCycle.cycle_id == cycle_id
                )
            )
            .mappings()
            .one()
        )
        if (
            cycle.individual_id != locked_individual
            or cycle.status not in {"completed", "failed"}
            or cycle.completed_at != now
        ):
            raise ValueError(
                "A persisted terminal cycle is required for scheduler accounting"
            )
        state = _state(session, cycle.individual_id, finishing=True)
        if state is not None and state.last_completed_cycle_id == cycle_id:
            return state
        accounted = session.scalar(
            select(Event.event_id)
            .where(
                Event.individual_id == locked_individual,
                Event.correlation_id == cycle_id,
                Event.event_type == "attention.heartbeat_updated",
                Event.source_kind == "runtime",
                Event.source_id == "attention.heartbeat",
            )
            .limit(1)
        )
        if accounted is not None:
            return state
        active_config = _configuration(session, cycle.individual_id)
        config = (
            active_config
            if active_config is not None
            else (
                None
                if state is None
                else _configuration(
                    session, cycle.individual_id, state.config_revision_id
                )
            )
        )
        if config is None:
            return None
        policy = _policy(config[1])
        before = None if state is None else _snapshot(session, state)
        if state is None:
            state = _initialize(session, cycle.individual_id, now, config[0], policy)
        if now < state.anchor_at:
            raise ValueError("Scheduler clock precedes its durable anchor")
        kinds = session.scalars(
            select(Wake.kind).join(CycleWake).where(CycleWake.cycle_id == cycle_id)
        ).all()
        autonomous = bool(kinds) and all(kind in _AUTONOMOUS_KINDS for kind in kinds)
        activity = (
            session.scalar(
                select(AppliedOperation.operation_id)
                .join(CognitionTurn)
                .where(
                    CognitionTurn.cycle_id == cycle_id,
                    AppliedOperation.individual_id == cycle.individual_id,
                    AppliedOperation.kind.in_(_PERSONAL_KINDS),
                )
                .limit(1)
            )
            is not None
        )
        state.interval_seconds = policy.next_interval(
            state.interval_seconds, autonomous=autonomous, activity=activity
        )
        state.anchor_at, state.last_completed_cycle_id = now, cycle_id
        state.config_revision_id = config[0]
        state.materialization_pending = True
        changed = False
        if (
            allowed
            and active_config is not None
            and not _active_cycle(session, cycle.individual_id)
        ):
            changed = _materialize(session, state, policy)
        _record(
            session,
            state,
            before,
            now,
            policy,
            reason="cycle_outcome",
            materialized=changed,
            cycle_id=cycle_id,
            autonomous=autonomous,
            activity=activity,
        )
        return state
