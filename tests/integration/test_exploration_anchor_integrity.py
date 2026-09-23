"""Retained outcomes cannot be forgotten by clearing operational anchor pointers."""

from datetime import timedelta

import pytest
from test_exploration_checks import NOW, add_cycle, add_grant, findings
from test_exploration_checks import graph as graph

from cognition.db.models.attention import Wake
from cognition.db.models.evidence import Event
from cognition.db.models.exploration import ExplorationGrant, ExplorationState
from cognition.stores.exploration import validate_exploration_state
from cognition.stores.exploration_scope import exploration_grant_hash


def outcome(session, person, wake_id, *, cycle=None, now=NOW):
    marker = Event(
        individual_id=person.individual_id,
        event_type=f"attention.exploration_{'completed' if cycle else 'cancelled'}",
        source_kind="runtime",
        source_id="attention.exploration",
        occurred_at=now,
        observed_at=now,
        recorded_at=now,
        subject_kind="wake",
        subject_id=wake_id,
        correlation_id=None if cycle is None else cycle.cycle_id,
        provenance={},
        runtime_version="test",
    )
    session.add(marker)
    session.flush()
    return marker


@pytest.mark.parametrize("status", ["cancelled", "consumed"])
def test_cleared_outcome_anchors_cannot_make_retained_history_initial(
    db_session_factory, graph, status
):
    person, _, wake_id, _ = graph
    with db_session_factory.begin() as session:
        cycle = (
            add_cycle(session, person, wake_id, status="completed")
            if status == "consumed"
            else None
        )
        session.get(Wake, wake_id).status = status
        outcome = Event(
            individual_id=person.individual_id,
            event_type=f"attention.exploration_{'completed' if cycle else 'cancelled'}",
            source_kind="runtime",
            source_id="attention.exploration",
            occurred_at=NOW,
            observed_at=NOW,
            recorded_at=NOW,
            subject_kind="wake",
            subject_id=wake_id,
            correlation_id=None if cycle is None else cycle.cycle_id,
            provenance={},
            runtime_version="test",
        )
        session.add(outcome)
        session.flush()
        state = session.get(ExplorationState, person.individual_id)
        state.last_outcome_event_id = outcome.event_id
        state.last_terminal_cycle_id = None if cycle is None else cycle.cycle_id
        state.next_eligible_at = NOW + timedelta(days=7)
        state.materialization_pending = cycle is not None
        state.managed_wake_id = wake_id if cycle else None
    with db_session_factory() as session:
        assert validate_exploration_state(session, person.individual_id) is not None
        assert findings(session) == []
    with db_session_factory.begin() as session:
        state = session.get(ExplorationState, person.individual_id)
        state.last_outcome_event_id = None
        state.last_terminal_cycle_id = None
    with db_session_factory() as session:
        with pytest.raises(ValueError):
            validate_exploration_state(session, person.individual_id)
        assert any(item[0] == "exploration_state" for item in findings(session))


@pytest.mark.parametrize("first_status", ["cancelled", "consumed"])
def test_anchors_preserve_latest_cancellation_and_prior_real_terminal_cycle(
    db_session_factory, graph, first_status
):
    person, _, wake_id, _ = graph
    with db_session_factory.begin() as session:
        cycle = (
            add_cycle(session, person, wake_id, status="completed")
            if first_status == "consumed"
            else None
        )
        session.get(Wake, wake_id).status = first_status
        first_outcome = outcome(session, person, wake_id, cycle=cycle)
        later_wake, _ = add_grant(session, person.individual_id)
        later = session.get(Wake, later_wake)
        later.status = "cancelled"
        later.due_at = NOW + timedelta(days=7)
        grant = session.get(ExplorationGrant, later_wake)
        grant.not_before_at = later.due_at
        grant.content_hash = exploration_grant_hash(
            **{
                column.name: getattr(grant, column.name)
                for column in grant.__table__.columns
                if column.name != "content_hash"
            }
        )
        latest_outcome = outcome(
            session, person, later_wake, now=NOW + timedelta(days=7)
        )
        state = session.get(ExplorationState, person.individual_id)
        state.last_outcome_event_id = latest_outcome.event_id
        state.last_terminal_cycle_id = None if cycle is None else cycle.cycle_id
        state.next_eligible_at = NOW + timedelta(days=14)
        state.materialization_pending = False
        state.managed_wake_id = None
        stale_outcome_id = first_outcome.event_id
    with db_session_factory() as session:
        assert validate_exploration_state(session, person.individual_id) is not None
        assert findings(session) == []
    with db_session_factory.begin() as session:
        state = session.get(ExplorationState, person.individual_id)
        if first_status == "cancelled":
            state.last_outcome_event_id = stale_outcome_id
        else:
            state.last_terminal_cycle_id = None
    with db_session_factory() as session:
        with pytest.raises(ValueError):
            validate_exploration_state(session, person.individual_id)
        assert any(item[0] == "exploration_state" for item in findings(session))
