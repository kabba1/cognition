"""Opt-in exploration persists one bounded allowance without inventing activity."""

import importlib
from copy import deepcopy
from datetime import timedelta

import pytest
import test_autonomy
from sqlalchemy import func, select

from cognition.db.models.attention import Wake
from cognition.db.models.cognition import CognitionCycle, CycleWake
from cognition.db.models.evidence import Event
from cognition.db.models.governance import GovernanceState
from cognition.db.models.runtime import RuntimeConfigRevision
from cognition.protocols.common import new_id

NOW = test_autonomy.NOW
person = test_autonomy.person
WEEK = timedelta(days=7)


def store():
    return importlib.import_module("cognition.stores.exploration")


def scope():
    return importlib.import_module("cognition.stores.exploration_scope")


def models():
    return importlib.import_module("cognition.db.models.exploration")


def policy(session, person, enabled=True):
    row = session.get(GovernanceState, person)
    row.budget_policy = {
        **row.budget_policy,
        "internal_exploration": {"schema_version": 1, "enabled": enabled},
    }
    row.revision += 1
    session.flush()


def ensure(session, person, now=NOW):
    return store().ensure_exploration(session, person, now)


def enabled(session, person):
    policy(session, person)
    return ensure(session, person)


def claim(session, person, state, now=NOW, **updates):
    values = dict(
        cycle_id=new_id(),
        individual_id=person,
        status="active",
        started_at=now,
        completed_at=None,
        terminal_reason=None,
        max_turns=1,
        max_attempts_per_turn=2,
        max_wakes=1,
        deadline_at=now + timedelta(seconds=120),
        min_wake_delay_seconds=1,
        revision=1,
    )
    cycle = CognitionCycle(**(values | updates))
    session.add(cycle)
    session.flush()
    wake = session.get(Wake, state.managed_wake_id)
    wake.status, wake.claimed_at = "claimed", now
    session.add(CycleWake(cycle_id=cycle.cycle_id, wake_id=wake.wake_id))
    session.flush()
    return cycle.cycle_id


def finish(session, cycle_id, now=NOW):
    cycle = session.get(CognitionCycle, cycle_id)
    cycle.status, cycle.completed_at = "completed", now
    cycle.terminal_reason = "sleep"
    for wake in session.scalars(
        select(Wake).join(CycleWake).where(CycleWake.cycle_id == cycle_id)
    ):
        wake.status, wake.consumed_at = "consumed", now
    session.flush()
    return store().record_exploration_outcome(session, cycle_id, now)


def test_absent_policy_does_not_create_state_or_grants(db_session_factory, person):
    with db_session_factory.begin() as session:
        assert ensure(session, person) is None
        assert session.get(models().ExplorationState, person) is None
        assert not session.scalars(select(models().ExplorationGrant)).all()


def test_enabled_initialization_is_immediate_and_unchanged_polls_write_nothing(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        state = enabled(session, person)
        wake = session.get(Wake, state.managed_wake_id)
        assert wake.kind == "routine" and wake.due_at == NOW
        assert wake.coalesce_key is None and wake.context_refs == []
        grant = scope().get_exploration_grant(session, person, wake.wake_id)
        assert grant.not_before_at == NOW and grant.scope == "internal"
        assert (
            grant.max_turns,
            grant.max_attempts_per_turn,
            grant.max_seconds,
            grant.max_wakes,
        ) == (1, 2, 120, 1)
        original = (state.revision, wake.wake_id, wake.revision)
        event_count = session.scalar(select(func.count()).select_from(Event))
    with db_session_factory.begin() as session:
        state = ensure(session, person, NOW + timedelta(days=30))
        wake = session.get(Wake, state.managed_wake_id)
        assert (state.revision, wake.wake_id, wake.revision) == original
        assert wake.due_at == NOW
        assert session.scalar(select(func.count()).select_from(Event)) == event_count


def test_creation_rolls_back_with_caller(db_session_factory, person):
    with db_session_factory.begin() as session:
        policy(session, person)
    with db_session_factory() as session:
        ensure(session, person)
        session.rollback()
    with db_session_factory() as session:
        assert session.get(models().ExplorationState, person) is None
        assert not session.scalars(select(models().ExplorationGrant)).all()


def test_terminal_accounting_advances_once_and_survives_redacted_outcome_content(
    db_session_factory, person
):
    from cognition.db.models.evidence import EventContent

    with db_session_factory.begin() as session:
        state = enabled(session, person)
        prior = state.managed_wake_id
        cycle = claim(session, person, state)
        state = finish(session, cycle, NOW + timedelta(seconds=30))
        assert state.next_eligible_at == NOW + timedelta(seconds=30) + WEEK
        assert state.last_terminal_cycle_id == cycle and state.managed_wake_id != prior
        assert session.get(Wake, state.managed_wake_id).due_at == state.next_eligible_at
        original = (state.revision, state.managed_wake_id, state.last_outcome_event_id)
        content = session.get(EventContent, state.last_outcome_event_id)
        content.payload = None
        session.flush()
    with db_session_factory.begin() as session:
        state = store().record_exploration_outcome(
            session, cycle, NOW + timedelta(seconds=30)
        )
        assert (
            state.revision,
            state.managed_wake_id,
            state.last_outcome_event_id,
        ) == original


def test_disable_cancels_pending_without_inventing_cycle_and_reenable_retains_cadence(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        state = enabled(session, person)
        prior = state.managed_wake_id
        policy(session, person, False)
        state = ensure(session, person, NOW + timedelta(hours=1))
        assert session.get(Wake, prior).status == "cancelled"
        assert state.last_terminal_cycle_id is None
        assert state.managed_wake_id is None
        expected = NOW + timedelta(hours=1) + WEEK
        assert state.next_eligible_at == expected
        marker = session.get(Event, state.last_outcome_event_id)
        assert (
            marker.event_type == "attention.exploration_cancelled"
            and marker.correlation_id is None
        )
        policy(session, person, True)
        state = ensure(session, person, NOW + timedelta(hours=2))
        assert session.get(Wake, state.managed_wake_id).due_at == expected


def test_disabled_future_allowance_cannot_accelerate_existing_deadline(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        state = enabled(session, person)
        cycle = claim(session, person, state)
        state = finish(session, cycle)
        deadline = state.next_eligible_at
        policy(session, person, False)
        state = ensure(session, person)
        assert state.next_eligible_at == deadline
        assert state.last_terminal_cycle_id == cycle


@pytest.mark.parametrize("deferred", ["pause", "configuration"])
def test_terminal_bookkeeping_defers_materialization_when_not_allowed(
    db_session_factory, person, deferred
):
    with db_session_factory.begin() as session:
        state = enabled(session, person)
        prior = state.managed_wake_id
        cycle = claim(session, person, state)
        if deferred == "pause":
            session.get(GovernanceState, person).inference_blocked = True
        else:
            revision = session.scalar(
                select(RuntimeConfigRevision).where(
                    RuntimeConfigRevision.individual_id == person,
                    RuntimeConfigRevision.superseded_at.is_(None),
                )
            )
            revision.superseded_at = NOW
        session.flush()
        state = finish(session, cycle)
        assert state.materialization_pending and state.managed_wake_id == prior
        assert state.next_eligible_at == NOW + WEEK
        if deferred == "pause":
            session.get(GovernanceState, person).inference_blocked = False
        else:
            revision.superseded_at = None
        session.flush()
        state = ensure(session, person)
        assert state.managed_wake_id != prior and not state.materialization_pending
        assert session.get(Wake, state.managed_wake_id).due_at == NOW + WEEK


def test_disable_never_cancels_claimed_allowance_but_denies_retry(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        state = enabled(session, person)
        prior = state.managed_wake_id
        cycle = claim(session, person, state)
        policy(session, person, False)
        state = ensure(session, person)
        assert state.managed_wake_id == prior
        assert session.get(Wake, prior).status == "claimed"
        assert (
            scope().exploration_start_denial(session, cycle)
            == "internal_exploration_disabled"
        )


def test_invalid_policy_denies_managed_start_without_hiding_ordinary_work(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        state = enabled(session, person)
        pointer = state.managed_wake_id
        row = session.get(GovernanceState, person)
        row.budget_policy = {
            "internal_exploration": {"schema_version": "1", "enabled": True}
        }
        session.flush()
        assert ensure(session, person).managed_wake_id == pointer
        discovery = scope().discover_exploration(session, person, NOW)
        assert discovery.managed_wake_ids == (pointer,) and discovery.eligible is None
        cycle = claim(session, person, state)
        assert (
            scope().exploration_start_denial(session, cycle)
            == "internal_exploration_policy_invalid"
        )


def test_dirty_governance_is_not_implicitly_flushed(db_session_factory, person):
    with db_session_factory() as session:
        row = session.get(GovernanceState, person)
        row.budget_policy = {
            "internal_exploration": {"schema_version": 1, "enabled": True}
        }
        before = deepcopy(row.budget_policy)
        with pytest.raises(ValueError, match="unflushed_scheduler_state"):
            ensure(session, person)
        assert row in session.dirty and row.budget_policy == before


@pytest.mark.parametrize("claimed", [False, True])
def test_current_live_grant_cannot_precede_retained_state_cadence(
    db_session_factory, person, claimed
):
    with db_session_factory.begin() as session:
        state = enabled(session, person)
        if claimed:
            claim(session, person, state)
        state.next_eligible_at = NOW + WEEK
    with db_session_factory() as session:
        with pytest.raises(ValueError, match="cadence"):
            store().validate_exploration_state(session, person)


def test_missing_outcome_instant_is_explicit_integrity_error(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        state = enabled(session, person)
        cycle = claim(session, person, state)
        state = finish(session, cycle)
        session.get(Event, state.last_outcome_event_id).occurred_at = None
    with db_session_factory() as session:
        with pytest.raises(ValueError, match="anchor|outcome"):
            store().validate_exploration_state(session, person)


def test_later_cancellation_cannot_replace_last_real_terminal_with_ordinary_cycle(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        state = enabled(session, person)
        completed = claim(session, person, state)
        state = finish(session, completed)
        policy(session, person, False)
        state = ensure(session, person)
        original = session.get(CognitionCycle, completed)
        ordinary = CognitionCycle(
            **{
                column.name: getattr(original, column.name)
                for column in original.__table__.columns
                if column.name != "cycle_id"
            },
            cycle_id=new_id(),
        )
        session.add(ordinary)
        session.flush()
        wake = session.scalar(select(Wake).where(Wake.kind == "bootstrap"))
        wake.status, wake.consumed_at = "consumed", NOW
        session.add(CycleWake(cycle_id=ordinary.cycle_id, wake_id=wake.wake_id))
        state.last_terminal_cycle_id = ordinary.cycle_id
    with db_session_factory() as session:
        with pytest.raises(ValueError, match="terminal"):
            store().validate_exploration_state(session, person)


def test_completed_marker_cannot_name_another_valid_grant(db_session_factory, person):
    with db_session_factory.begin() as session:
        state = enabled(session, person)
        completed = claim(session, person, state)
        state = finish(session, completed)
        session.get(
            Event, state.last_outcome_event_id
        ).subject_id = state.managed_wake_id
    with db_session_factory() as session:
        with pytest.raises(ValueError, match="terminal|outcome"):
            store().validate_exploration_state(session, person)
