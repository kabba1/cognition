"""Durable opt-in internal allowances, with cancellation and terminal cadence."""

from copy import deepcopy
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from cognition.db.models.attention import Wake
from cognition.db.models.cognition import CognitionCycle, CycleWake
from cognition.db.models.evidence import Event
from cognition.db.models.exploration import ExplorationGrant, ExplorationState
from cognition.db.models.governance import GovernanceState
from cognition.domain.exploration import (
    CADENCE_SECONDS,
    MAX_ATTEMPTS,
    MAX_SECONDS,
    MAX_TURNS,
    MAX_WAKES,
)
from cognition.domain.heartbeat import HeartbeatPolicy
from cognition.protocols.common import JsonObject, Ref, new_id, normalize_utc
from cognition.protocols.events_v1 import EventContent, EventEnvelopeV1, EventSource
from cognition.stores.autonomy import _active_cycle, _configuration, _lock
from cognition.stores.evidence import append_event
from cognition.stores.exploration_scope import (
    EXPLORATION_MARKER_SOURCE,
    EXPLORATION_MARKER_TYPE,
    current_exploration_denial,
    exploration_grant_hash,
    get_cycle_exploration,
    get_exploration_grant,
    live_exploration_grants,
)

EXPLORATION_PURPOSE = (
    "An explicit bounded allowance permits internal exploration of a topic you choose. "
    "Sleep or no action is valid. Do not invent observations, traits or obligations. "
    "This allowance authorizes no external action and cannot request additional wakes."
)
_CADENCE = HeartbeatPolicy(CADENCE_SECONDS, CADENCE_SECONDS, 1)


def _exploration_lock(session: Session, individual_id: UUID) -> bool:
    if any(
        isinstance(row, (ExplorationState, ExplorationGrant, Event))
        for row in session.new | session.dirty | session.deleted
    ):
        raise ValueError("unflushed_scheduler_state")
    return _lock(session, individual_id)


def _outcome(
    session: Session,
    individual_id: UUID,
    wake_id: UUID,
    *,
    cycle_id: UUID | None = None,
) -> RowMapping | None:
    query = select(Event.__table__).where(
        Event.individual_id == individual_id,
        Event.event_type
        == (
            "attention.exploration_cancelled"
            if cycle_id is None
            else "attention.exploration_completed"
        ),
        Event.source_kind == "runtime",
        Event.source_id == EXPLORATION_MARKER_SOURCE,
        Event.subject_kind == "wake",
        Event.subject_id == wake_id,
        Event.correlation_id.is_(None)
        if cycle_id is None
        else Event.correlation_id == cycle_id,
    )
    rows = session.execute(query.limit(2)).mappings().all()
    if len(rows) > 1:
        raise ValueError("Duplicate exploration outcome markers")
    return rows[0] if rows else None


def validate_exploration_state(
    session: Session, individual_id: UUID, *, finishing: bool = False
) -> RowMapping | None:
    """Validate retained operational continuity without touching the identity map."""
    with session.no_autoflush:
        live_exploration_grants(session, individual_id)
        state = (
            session.execute(
                select(ExplorationState.__table__).where(
                    ExplorationState.individual_id == individual_id
                )
            )
            .mappings()
            .one_or_none()
        )
        if state is None:
            historical = session.scalar(
                select(ExplorationGrant.wake_id)
                .where(ExplorationGrant.individual_id == individual_id)
                .limit(1)
            )
            historical_marker = session.scalar(
                select(Event.event_id)
                .where(
                    Event.individual_id == individual_id,
                    Event.event_type == EXPLORATION_MARKER_TYPE,
                )
                .limit(1)
            )
            if historical is not None or historical_marker is not None:
                raise ValueError("Missing exploration state for retained grants")
            return None
        if state.policy_version != 1 or state.revision < 1:
            raise ValueError("Invalid exploration policy state")
        # Retained envelope order is authoritative even after payload redaction.
        # Null or older valid pointers must not reset already-spent allowances.
        outcomes = select(Event.event_id, Event.correlation_id).where(
            Event.individual_id == individual_id,
            Event.event_type.in_(
                ("attention.exploration_completed", "attention.exploration_cancelled")
            ),
        )
        latest_outcome = session.execute(
            outcomes.order_by(Event.event_sequence.desc()).limit(1)
        ).one_or_none()
        expected_outcome_id = (
            None if latest_outcome is None else latest_outcome.event_id
        )
        if state.last_outcome_event_id != expected_outcome_id:
            raise ValueError("Exploration outcome anchor differs from retained history")
        latest_completion = session.execute(
            outcomes.where(Event.event_type == "attention.exploration_completed")
            .order_by(Event.event_sequence.desc())
            .limit(1)
        ).one_or_none()
        expected_terminal_id = (
            None if latest_completion is None else latest_completion.correlation_id
        )
        if state.last_terminal_cycle_id != expected_terminal_id or (
            latest_completion is not None and expected_terminal_id is None
        ):
            raise ValueError(
                "Exploration terminal anchor differs from retained history"
            )
        terminal = None
        terminal_exploration = None
        if state.last_terminal_cycle_id is not None:
            terminal = (
                session.execute(
                    select(CognitionCycle.__table__).where(
                        CognitionCycle.cycle_id == state.last_terminal_cycle_id
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                terminal is None
                or terminal.individual_id != individual_id
                or terminal.status not in {"completed", "failed"}
                or terminal.completed_at is None
            ):
                raise ValueError("Invalid exploration terminal cycle")
            terminal_exploration = get_cycle_exploration(session, terminal.cycle_id)
            if terminal_exploration is None:
                raise ValueError("Retained exploration terminal cycle is ordinary")
            terminal_wake = session.execute(
                select(Wake.status, Wake.consumed_at).where(
                    Wake.wake_id == terminal_exploration.grant.wake_id
                )
            ).one()
            terminal_marker = _outcome(
                session,
                individual_id,
                terminal_exploration.grant.wake_id,
                cycle_id=terminal.cycle_id,
            )
            if (
                terminal_wake.status != "consumed"
                or terminal_wake.consumed_at != terminal.completed_at
                or terminal_marker is None
                or latest_completion is None
                or terminal_marker.event_id != latest_completion.event_id
                or terminal_marker.occurred_at != terminal.completed_at
            ):
                raise ValueError("Invalid retained exploration terminal outcome")
        if state.last_outcome_event_id is None:
            if terminal is not None:
                raise ValueError("Missing exploration outcome anchor")
        else:
            marker = (
                session.execute(
                    select(Event.__table__).where(
                        Event.event_id == state.last_outcome_event_id
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                marker is None
                or marker.individual_id != individual_id
                or marker.source_kind != "runtime"
                or marker.source_id != EXPLORATION_MARKER_SOURCE
                or marker.occurred_at is None
                or marker.subject_kind != "wake"
                or marker.subject_id is None
                or marker.event_type
                not in {
                    "attention.exploration_completed",
                    "attention.exploration_cancelled",
                }
                or state.next_eligible_at
                < _CADENCE.schedule(marker.occurred_at, CADENCE_SECONDS).due_at
            ):
                raise ValueError("Invalid exploration outcome anchor or cadence")
            grant = get_exploration_grant(session, individual_id, marker.subject_id)
            if grant is None:
                raise ValueError("Missing exploration outcome grant")
            if marker.event_type == "attention.exploration_completed":
                if (
                    terminal is None
                    or terminal_exploration is None
                    or marker.subject_id != terminal_exploration.grant.wake_id
                    or marker.correlation_id != terminal.cycle_id
                    or marker.occurred_at != terminal.completed_at
                ):
                    raise ValueError("Exploration completed outcome differs from cycle")
            elif marker.correlation_id is not None:
                raise ValueError("Exploration cancellation cannot invent a cycle")
        if state.managed_wake_id is not None:
            grant = get_exploration_grant(session, individual_id, state.managed_wake_id)
            if grant is None:
                raise ValueError("Missing exploration managed grant")
            wake = (
                session.execute(
                    select(Wake.__table__).where(Wake.wake_id == state.managed_wake_id)
                )
                .mappings()
                .one()
            )
            if wake.status in {"claimed", "consumed"}:
                cycle = (
                    session.execute(
                        select(CognitionCycle.__table__)
                        .join(CycleWake)
                        .where(CycleWake.wake_id == wake.wake_id)
                    )
                    .mappings()
                    .one_or_none()
                )
                if (
                    cycle is None
                    or cycle.individual_id != individual_id
                    or get_cycle_exploration(session, cycle.cycle_id) is None
                ):
                    raise ValueError("Managed exploration claim lacks its owned cycle")
                if wake.status == "claimed" and cycle.status != "active":
                    raise ValueError("Orphan managed exploration claim")
                if wake.status == "consumed":
                    if (
                        cycle.status not in {"completed", "failed"}
                        or cycle.completed_at != wake.consumed_at
                        or cycle.completed_at is None
                    ):
                        raise ValueError(
                            "Managed exploration consumption differs from cycle"
                        )
                    if not finishing and (
                        not state.materialization_pending
                        or _outcome(
                            session,
                            individual_id,
                            wake.wake_id,
                            cycle_id=cycle.cycle_id,
                        )
                        is None
                    ):
                        raise ValueError(
                            "Consumed exploration lacks deferred accounting"
                        )
        return state


def _event(
    session: Session,
    individual_id: UUID,
    wake_id: UUID,
    now: datetime,
    kind: str,
    payload: JsonObject,
    cycle_id: UUID | None = None,
) -> UUID:
    identity = new_id()
    append_event(
        session,
        EventEnvelopeV1(
            schema_version=1,
            event_id=identity,
            individual_id=individual_id,
            event_type=f"attention.exploration_{kind}",
            occurred_at=now,
            observed_at=now,
            recorded_at=now,
            source=EventSource(
                kind="runtime", source_id=EXPLORATION_MARKER_SOURCE, binding_id=None
            ),
            actor_entity_id=None,
            causation_event_id=None,
            correlation_id=cycle_id,
            subject=Ref(kind="wake", id=wake_id),
            provenance={"policy_version": 1},
            runtime_version="0.1.0",
            content=EventContent(
                content_type="application/json",
                payload={"policy_version": 1, **payload},
                text=None,
                blob_ref=None,
                content_hash=None,
                sensitivity="internal",
                retention_class="history",
                retain_until=None,
            ),
        ),
    )
    return identity


def _clock(session: Session, state: ExplorationState, now: datetime) -> None:
    anchor = (
        state.next_eligible_at
        if state.last_outcome_event_id is None
        else session.scalar(
            select(Event.occurred_at).where(
                Event.event_id == state.last_outcome_event_id
            )
        )
    )
    created = (
        None
        if state.managed_wake_id is None
        else session.scalar(
            select(ExplorationGrant.created_at).where(
                ExplorationGrant.wake_id == state.managed_wake_id
            )
        )
    )
    if (anchor is not None and now < anchor) or (created is not None and now < created):
        raise ValueError("Exploration clock precedes its retained anchor")


def _cancel(
    session: Session, state: ExplorationState, wake: Wake, now: datetime
) -> None:
    if wake.status == "claimed":
        raise ValueError("Cannot cancel active exploration")
    previous = state.next_eligible_at
    wake.status = "cancelled"
    wake.revision += 1
    state.next_eligible_at = max(
        previous, wake.due_at, _CADENCE.schedule(now, CADENCE_SECONDS).due_at
    )
    state.last_outcome_event_id = _event(
        session,
        state.individual_id,
        wake.wake_id,
        now,
        "cancelled",
        {
            "previous_next_eligible_at": previous.isoformat(),
            "next_eligible_at": state.next_eligible_at.isoformat(),
            "reason": "allowance_cancelled",
            "cadence_seconds": CADENCE_SECONDS,
        },
    )
    state.managed_wake_id = None
    state.materialization_pending = False
    state.revision += 1


def _materialize(
    session: Session, state: ExplorationState, now: datetime, *, enabled: bool
) -> None:
    if state.managed_wake_id is not None:
        wake = session.get(Wake, state.managed_wake_id, populate_existing=True)
        assert wake is not None
        if wake.status == "claimed":
            return
        if wake.status == "pending":
            if enabled:
                return
            _cancel(session, state, wake, now)
        elif wake.status in {"cancelled", "superseded"}:
            if _outcome(session, state.individual_id, wake.wake_id) is None:
                _cancel(session, state, wake, now)
            else:
                state.managed_wake_id = None
        else:  # A consumed pointer was already accounted by the terminal path.
            state.managed_wake_id = None
    if not enabled:
        state.materialization_pending = False
        session.flush()
        return
    governance = session.execute(
        select(GovernanceState.revision, GovernanceState.budget_policy).where(
            GovernanceState.individual_id == state.individual_id
        )
    ).one()
    fields = dict(
        wake_id=new_id(),
        individual_id=state.individual_id,
        policy_version=1,
        authorizing_governance_revision=governance.revision,
        policy_snapshot=deepcopy(governance.budget_policy["internal_exploration"]),
        created_at=now,
        not_before_at=max(now, state.next_eligible_at),
        scope="internal",
        max_turns=MAX_TURNS,
        max_attempts_per_turn=MAX_ATTEMPTS,
        max_seconds=MAX_SECONDS,
        max_wakes=MAX_WAKES,
    )
    wake = Wake(
        wake_id=fields["wake_id"],
        individual_id=state.individual_id,
        kind="routine",
        status="pending",
        due_at=fields["not_before_at"],
        purpose=EXPLORATION_PURPOSE,
        cause_event_id=None,
        context_refs=[],
        coalesce_key=None,
        revision=1,
    )
    session.add(wake)
    session.flush()
    grant = ExplorationGrant(**fields, content_hash=exploration_grant_hash(**fields))
    session.add(grant)
    session.flush()
    wake.cause_event_id = _event(
        session,
        state.individual_id,
        wake.wake_id,
        now,
        "scheduled",
        {
            "not_before_at": grant.not_before_at.isoformat(),
            "content_hash": grant.content_hash,
            "authorizing_governance_revision": grant.authorizing_governance_revision,
            "policy_snapshot": deepcopy(grant.policy_snapshot),
            "scope": "internal",
            "max_turns": MAX_TURNS,
            "max_attempts_per_turn": MAX_ATTEMPTS,
            "max_seconds": MAX_SECONDS,
            "max_wakes": MAX_WAKES,
            "cadence_seconds": CADENCE_SECONDS,
        },
    )
    state.managed_wake_id, state.materialization_pending = wake.wake_id, False
    state.revision += 1
    session.flush()


def ensure_exploration(
    session: Session, individual_id: UUID, now: datetime
) -> ExplorationState | None:
    now = normalize_utc(now)
    with session.no_autoflush:
        allowed = _exploration_lock(session, individual_id)
        retained = validate_exploration_state(session, individual_id)
        state = (
            None
            if retained is None
            else session.get(ExplorationState, individual_id, populate_existing=True)
        )
        if state is not None:
            _clock(session, state, now)
        denial = current_exploration_denial(session, individual_id)
        if (
            not allowed
            or denial == "internal_exploration_policy_invalid"
            or _active_cycle(session, individual_id)
            or _configuration(session, individual_id) is None
        ):
            return state
        if state is None:
            if denial is not None:
                return None
            state = ExplorationState(
                individual_id=individual_id,
                policy_version=1,
                next_eligible_at=now,
                managed_wake_id=None,
                last_terminal_cycle_id=None,
                last_outcome_event_id=None,
                materialization_pending=True,
                revision=1,
            )
            session.add(state)
            session.flush()
        _materialize(session, state, now, enabled=denial is None)
        session.flush()
        return state


def record_exploration_outcome(
    session: Session, cycle_id: UUID, now: datetime
) -> ExplorationState | None:
    now = normalize_utc(now)
    with session.no_autoflush:
        query = select(CognitionCycle.__table__).where(
            CognitionCycle.cycle_id == cycle_id
        )
        cycle = session.execute(query).mappings().one_or_none()
        if cycle is None:
            raise ValueError(
                "Persisted terminal cycle required for exploration accounting"
            )
        individual_id = cycle.individual_id
        allowed = _exploration_lock(session, individual_id)
        cycle = session.execute(query).mappings().one()
        if (
            cycle.individual_id != individual_id
            or cycle.status not in {"completed", "failed"}
            or cycle.completed_at != now
        ):
            raise ValueError(
                "Persisted terminal cycle required for exploration accounting"
            )
        exploration = get_cycle_exploration(session, cycle_id)
        retained = validate_exploration_state(session, individual_id, finishing=True)
        state = (
            None
            if retained is None
            else session.get(ExplorationState, individual_id, populate_existing=True)
        )
        if exploration is None:
            return state
        wake_id = exploration.grant.wake_id
        if _outcome(session, individual_id, wake_id, cycle_id=cycle_id) is not None:
            return state
        if state is None or state.managed_wake_id != wake_id:
            raise ValueError(
                "Exploration terminal allowance differs from current pointer"
            )
        wake = (
            session.execute(select(Wake.__table__).where(Wake.wake_id == wake_id))
            .mappings()
            .one()
        )
        if wake.status != "consumed" or wake.consumed_at != now:
            raise ValueError("Exploration terminal outcome requires its consumed wake")
        _clock(session, state, now)
        state.next_eligible_at = _CADENCE.schedule(now, CADENCE_SECONDS).due_at
        state.last_terminal_cycle_id, state.materialization_pending = cycle_id, True
        state.revision += 1
        state.last_outcome_event_id = _event(
            session,
            individual_id,
            wake_id,
            now,
            "completed",
            {
                "next_eligible_at": state.next_eligible_at.isoformat(),
                "cycle_status": cycle.status,
                "cadence_seconds": CADENCE_SECONDS,
            },
            cycle_id,
        )
        denial = current_exploration_denial(session, individual_id)
        if (
            allowed
            and denial != "internal_exploration_policy_invalid"
            and _configuration(session, individual_id) is not None
            and not _active_cycle(session, individual_id)
        ):
            _materialize(session, state, now, enabled=denial is None)
        session.flush()
        return state
