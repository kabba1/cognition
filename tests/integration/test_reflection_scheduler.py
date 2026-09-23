"""Managed review opportunities do not establish personal interpretations."""

from datetime import timedelta

import pytest
import test_autonomy
from sqlalchemy import func, select

from cognition.db.models.attention import Wake
from cognition.db.models.development import Interest
from cognition.db.models.evidence import Event
from cognition.db.models.governance import GovernanceState
from cognition.protocols.common import new_id

NOW = test_autonomy.NOW
person = test_autonomy.person


def candidate(session, identity, *, eligible=NOW):
    row = Interest(
        interest_id=new_id(),
        individual_id=identity,
        topic="A possible interest",
        summary="Consider this without assuming it is established",
        status="candidate",
        rationale="Fixture",
        evidence_refs=[],
        promotion_not_before=eligible,
        created_at=NOW - timedelta(days=2),
        updated_at=NOW,
        revision=1,
    )
    session.add(row)
    session.flush()
    return row.interest_id


def ensure(session, identity, now=NOW):
    from cognition.stores.reflection import ensure_reflection

    return ensure_reflection(session, identity, now)


def claim(session, identity, now=NOW):
    from cognition.stores.cognition import CycleLimits, claim_or_resume

    for wake in session.scalars(select(Wake).where(Wake.kind != "reflection")):
        if wake.status == "pending":
            wake.status = "cancelled"
    session.flush()
    cycle = claim_or_resume(session, identity, now, CycleLimits())
    assert cycle is not None
    return cycle.cycle_id


def test_no_candidates_does_not_fabricate_reflection_state(db_session_factory, person):
    with db_session_factory.begin() as session:
        assert ensure(session, person) is None
        assert not session.scalars(select(Wake).where(Wake.kind == "reflection")).all()


def test_future_maturity_and_unchanged_poll_keep_exact_batch(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        target = candidate(session, person, eligible=NOW + timedelta(hours=8))
        state = ensure(session, person)
        wake = session.get(Wake, state.managed_wake_id)
        assert wake.due_at == NOW + timedelta(hours=8)
        assert wake.context_refs == [{"kind": "interest", "id": str(target)}]
        expected = (state.revision, wake.wake_id, wake.revision)
        count = session.scalar(select(func.count()).select_from(Event))
    with db_session_factory.begin() as session:
        state = ensure(session, person, NOW + timedelta(hours=1))
        wake = session.get(Wake, state.managed_wake_id)
        assert (state.revision, wake.wake_id, wake.revision) == expected
        assert session.scalar(select(func.count()).select_from(Event)) == count
        assert session.get(Interest, target).status == "candidate"


@pytest.mark.parametrize("failed", [False, True])
def test_completed_batch_advances_once_and_schedules_after_actual_completion(
    db_session_factory, person, failed
):
    from cognition.stores.cognition import finish_cycle
    from cognition.stores.reflection import record_reflection_outcome

    with db_session_factory.begin() as session:
        target = candidate(session, person)
        state = ensure(session, person)
        first_wake = state.managed_wake_id
        cycle = claim(session, person)
        finish_cycle(session, cycle, NOW + timedelta(hours=2), "sleep", failed=failed)
        state = record_reflection_outcome(session, cycle, NOW + timedelta(hours=2))
        assert state.interest_cursor == target
        assert state.next_review_at == NOW + timedelta(hours=26)
        assert state.managed_wake_id != first_wake
        wake = session.get(Wake, state.managed_wake_id)
        assert wake.due_at == state.next_review_at
        expected = (state.revision, state.managed_wake_id)
    with db_session_factory.begin() as session:
        state = record_reflection_outcome(session, cycle, NOW + timedelta(hours=2))
        assert (state.revision, state.managed_wake_id) == expected


def test_pause_accounts_outcome_but_defers_successor(db_session_factory, person):
    from cognition.stores.cognition import finish_cycle
    from cognition.stores.reflection import record_reflection_outcome

    with db_session_factory.begin() as session:
        candidate(session, person)
        state = ensure(session, person)
        prior = state.managed_wake_id
        cycle = claim(session, person)
        session.get(GovernanceState, person).inference_blocked = True
        session.flush()
        finish_cycle(session, cycle, NOW, "provider_failure", failed=True)
        state = record_reflection_outcome(session, cycle, NOW)
        assert state.managed_wake_id == prior and state.materialization_pending
        assert state.next_review_at == NOW + timedelta(days=1)
    with db_session_factory.begin() as session:
        session.get(GovernanceState, person).inference_blocked = False
    with db_session_factory.begin() as session:
        state = ensure(session, person, NOW + timedelta(days=10))
        assert state.managed_wake_id != prior
        assert session.get(Wake, state.managed_wake_id).due_at == NOW + timedelta(
            days=10
        )
        assert state.last_completed_cycle_id == cycle


def test_established_targets_cancel_without_advancing_cursor_or_cadence(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        target = candidate(session, person)
        state = ensure(session, person)
        prior, due = state.managed_wake_id, state.next_review_at
        session.get(Interest, target).status = "established"
    with db_session_factory.begin() as session:
        state = ensure(session, person, NOW + timedelta(hours=1))
        assert session.get(Wake, prior).status == "cancelled"
        assert state.managed_wake_id is None
        assert state.interest_cursor is None and state.next_review_at == due
        assert not state.materialization_pending


def test_scheduler_creation_rolls_back_atomically(db_session_factory, person):
    from cognition.db.models.reflection import ManagedReflectionBatch, ReflectionState

    with db_session_factory.begin() as session:
        candidate(session, person)
    with db_session_factory() as session:
        ensure(session, person)
        session.rollback()
    with db_session_factory() as session:
        assert session.get(ReflectionState, person) is None
        assert (
            session.scalar(select(func.count()).select_from(ManagedReflectionBatch))
            == 0
        )


def test_orphan_managed_claim_fails_before_generic_recovery(db_session_factory, person):
    with db_session_factory.begin() as session:
        candidate(session, person)
        state = ensure(session, person)
        session.get(Wake, state.managed_wake_id).status = "claimed"
    with db_session_factory.begin() as session:
        with pytest.raises(ValueError, match="Orphan managed reflection"):
            ensure(session, person)


def test_dirty_candidate_does_not_implicitly_flush(db_session_factory, person):
    with db_session_factory.begin() as session:
        target = candidate(session, person)
    with db_session_factory() as session:
        row = session.get(Interest, target)
        row.status = "established"
        with pytest.raises(ValueError, match="unflushed_scheduler_state"):
            ensure(session, person)
        assert row in session.dirty


def test_live_batch_cannot_be_hidden_by_dropping_current_pointer(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        candidate(session, person)
        state = ensure(session, person)
        state.managed_wake_id = None
    with db_session_factory.begin() as session:
        with pytest.raises(ValueError, match="live cardinality"):
            ensure(session, person)


def test_consumed_pointer_without_terminal_membership_is_not_replaced(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        candidate(session, person)
        state = ensure(session, person)
        state.materialization_pending = True
        wake = session.get(Wake, state.managed_wake_id)
        wake.status, wake.consumed_at = "consumed", NOW
    with db_session_factory.begin() as session:
        with pytest.raises(ValueError, match="consumed cycle"):
            ensure(session, person)


def test_clock_before_selected_batch_cannot_change_scheduler(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        candidate(session, person)
        ensure(session, person)
    with db_session_factory.begin() as session:
        with pytest.raises(ValueError, match="clock"):
            ensure(session, person, NOW - timedelta(seconds=1))


@pytest.mark.parametrize("claimed", [False, True])
def test_successor_deadline_cannot_bypass_retained_cadence(
    db_session_factory, person, claimed
):
    from cognition.stores.cognition import finish_cycle

    with db_session_factory.begin() as session:
        candidate(session, person)
        ensure(session, person)
        cycle_id = claim(session, person)
        finish_cycle(session, cycle_id, NOW, "sleep")
    with db_session_factory.begin() as session:
        from cognition.db.models.reflection import ReflectionState

        state = session.get(ReflectionState, person)
        wake = session.get(Wake, state.managed_wake_id)
        assert wake.due_at == NOW + timedelta(days=1)
        wake.due_at = NOW
        session.flush()
        if claimed:
            claim(session, person)
    with db_session_factory.begin() as session:
        with pytest.raises(ValueError, match="cadence"):
            ensure(session, person)


def test_pending_wake_linked_to_terminal_cycle_cannot_advance_cadence(
    db_session_factory, person
):
    from cognition.db.models.cognition import CognitionCycle
    from cognition.stores.reflection import record_reflection_outcome

    with db_session_factory.begin() as session:
        candidate(session, person)
        state = ensure(session, person)
        cycle_id = claim(session, person)
        cycle = session.get(CognitionCycle, cycle_id)
        cycle.status, cycle.completed_at = "completed", NOW
        session.get(Wake, state.managed_wake_id).status = "pending"
    with db_session_factory.begin() as session:
        with pytest.raises(ValueError, match="consumed"):
            record_reflection_outcome(session, cycle_id, NOW)


def test_terminal_accounting_cannot_hide_managed_batch_by_mutating_wake_kind(
    db_session_factory, person
):
    from cognition.stores.cognition import finish_cycle

    with db_session_factory.begin() as session:
        candidate(session, person)
        state = ensure(session, person)
        cycle_id = claim(session, person)
        session.get(Wake, state.managed_wake_id).kind = "self_scheduled"
        state.managed_wake_id = None
    with db_session_factory.begin() as session:
        with pytest.raises(ValueError, match="reflection"):
            finish_cycle(session, cycle_id, NOW, "provider_failure", failed=True)


def test_no_active_configuration_defers_materialization_not_terminal_accounting(
    db_session_factory, person
):
    from cognition.db.models.runtime import RuntimeConfigRevision
    from cognition.stores.cognition import finish_cycle
    from cognition.stores.reflection import record_reflection_outcome

    with db_session_factory.begin() as session:
        candidate(session, person)
        state = ensure(session, person)
        previous = state.managed_wake_id
        cycle_id = claim(session, person)
        config = session.scalar(
            select(RuntimeConfigRevision).where(
                RuntimeConfigRevision.superseded_at.is_(None)
            )
        )
        config.superseded_at = NOW
        session.flush()
        finish_cycle(session, cycle_id, NOW, "missing_configuration", failed=True)
        state = record_reflection_outcome(session, cycle_id, NOW)
        assert state.last_completed_cycle_id == cycle_id
        assert state.materialization_pending and state.managed_wake_id == previous
        assert (
            ensure(session, person, NOW + timedelta(hours=1)).managed_wake_id
            == previous
        )


def test_unrelated_cycle_does_not_advance_reflection_cadence(
    db_session_factory, person
):
    from cognition.stores.cognition import CycleLimits, claim_or_resume, finish_cycle
    from cognition.stores.reflection import record_reflection_outcome

    with db_session_factory.begin() as session:
        candidate(session, person, eligible=NOW + timedelta(hours=4))
        state = ensure(session, person)
        expected = (state.revision, state.next_review_at, state.managed_wake_id)
        bootstrap = claim_or_resume(session, person, NOW, CycleLimits())
        finish_cycle(session, bootstrap.cycle_id, NOW, "sleep")
        state = record_reflection_outcome(session, bootstrap.cycle_id, NOW)
        assert (state.revision, state.next_review_at, state.managed_wake_id) == expected
        assert state.interest_cursor is None


def test_active_legacy_cycle_does_not_initialize_parallel_batch(
    db_session_factory, person
):
    from cognition.stores.cognition import CycleLimits, claim_or_resume

    with db_session_factory.begin() as session:
        candidate(session, person)
        claim_or_resume(session, person, NOW, CycleLimits())
        assert ensure(session, person) is None


def test_partial_establishment_preserves_scope_and_due_time(db_session_factory, person):
    with db_session_factory.begin() as session:
        first = candidate(session, person)
        candidate(session, person)
        state = ensure(session, person)
        prior = state.managed_wake_id
        session.get(Interest, first).status = "established"
    with db_session_factory.begin() as session:
        state = ensure(session, person, NOW + timedelta(days=3))
        wake = session.get(Wake, state.managed_wake_id)
        assert state.managed_wake_id == prior and wake.due_at == NOW
        assert len(wake.context_refs) == 2


def test_older_outcome_replay_survives_pointer_advance_and_content_redaction(
    db_session_factory, person
):
    from cognition.db.models.evidence import EventContent
    from cognition.stores.cognition import finish_cycle
    from cognition.stores.reflection import record_reflection_outcome

    with db_session_factory.begin() as session:
        candidate(session, person)
        ensure(session, person)
        first = claim(session, person)
        finish_cycle(session, first, NOW, "sleep")
    with db_session_factory.begin() as session:
        later = NOW + timedelta(days=1)
        second = claim(session, person, later)
        finish_cycle(session, second, later, "sleep")
        state = record_reflection_outcome(session, second, later)
        expected = (state.revision, state.managed_wake_id, state.next_review_at)
        for event_id in session.scalars(
            select(Event.event_id).where(
                Event.event_type == "attention.reflection_completed"
            )
        ):
            content = session.get(EventContent, event_id)
            content.payload, content.text = None, None
    with db_session_factory.begin() as session:
        state = record_reflection_outcome(session, first, NOW)
        assert (state.revision, state.managed_wake_id, state.next_review_at) == expected
