"""Scheduler transitions are durable, owned and independent of model inference."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from cognition.config.loader import load_config
from cognition.db.models.attention import Wake
from cognition.db.models.evidence import Event
from cognition.db.models.governance import GovernanceState
from cognition.protocols.common import new_id
from cognition.runtime.birth import BirthInput, birth
from cognition.testing.clock import FakeClock

NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)


@pytest.fixture
def person(db_session_factory):
    config = load_config(Path(__file__).parents[1] / "fixtures/config/valid.toml")
    config.runtime.individual_id = new_id()
    return birth(
        db_session_factory,
        BirthInput(
            individual_id=config.runtime.individual_id,
            birth_name="Heartbeat store individual",
            founding_orientation="Retain grounded continuity",
            creator_provenance={},
            admin_authn_provider="local_os",
            admin_subject="scheduler-admin",
            config=config,
            runtime_version="test",
        ),
        FakeClock(NOW),
    ).individual_id


def ensure(session, identity, now=NOW):
    from cognition.stores.autonomy import ensure_heartbeat

    return ensure_heartbeat(session, identity, now)


def test_initialize_once_and_unchanged_poll_has_no_writes(db_session_factory, person):
    from cognition.db.models.autonomy import AutonomyState

    with db_session_factory.begin() as session:
        state = ensure(session, person)
        assert state.interval_seconds == 60
        assert state.anchor_at == NOW
        wake = session.get(Wake, state.managed_wake_id)
        assert wake.kind == "heartbeat" and wake.status == "pending"
        assert wake.due_at == NOW + timedelta(seconds=60)
        assert wake.coalesce_key is None
        assert not state.materialization_pending
        marker = (state.revision, wake.revision, state.managed_wake_id)
        count = session.scalar(select(func.count()).select_from(Event))
    with db_session_factory.begin() as session:
        state = ensure(session, person, NOW + timedelta(seconds=20))
        wake = session.get(Wake, state.managed_wake_id)
        assert (state.revision, wake.revision, state.managed_wake_id) == marker
        assert session.scalar(select(func.count()).select_from(Event)) == count
        assert session.scalar(select(func.count()).select_from(AutonomyState)) == 1


def test_initialization_rolls_back_with_caller(db_session_factory, person):
    from cognition.db.models.autonomy import AutonomyState

    with db_session_factory() as session:
        ensure(session, person)
        session.rollback()
    with db_session_factory() as session:
        assert session.get(AutonomyState, person) is None
        assert not session.scalars(select(Wake).where(Wake.kind == "heartbeat")).all()


def test_inference_block_prevents_initialization(db_session_factory, person):
    from cognition.db.models.autonomy import AutonomyState

    with db_session_factory.begin() as session:
        session.get(GovernanceState, person).inference_blocked = True
    with db_session_factory.begin() as session:
        assert ensure(session, person) is None
        assert session.get(AutonomyState, person) is None


def test_cancelled_managed_wake_is_replaced_without_reusing_its_identity(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        state = ensure(session, person)
        previous = state.managed_wake_id
        session.get(Wake, previous).status = "cancelled"
    with db_session_factory.begin() as session:
        state = ensure(session, person, NOW + timedelta(seconds=10))
        assert state.managed_wake_id != previous
        assert session.get(Wake, previous).status == "cancelled"
        assert session.get(Wake, state.managed_wake_id).due_at == NOW + timedelta(
            seconds=60
        )


def test_managed_orphan_claim_rejects_before_generic_recovery(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        state = ensure(session, person)
        session.get(Wake, state.managed_wake_id).status = "claimed"
    with db_session_factory.begin() as session:
        with pytest.raises(ValueError, match="managed heartbeat"):
            ensure(session, person)


def test_diagnostics_report_foreign_managed_wake_without_flushing_dirty_state(
    db_session_factory, person
):
    from cognition.db.checks import check_database
    from cognition.db.models.autonomy import AutonomyState
    from cognition.db.models.identity import Individual

    with db_session_factory.begin() as session:
        state = ensure(session, person)
        pointer = state.managed_wake_id
        other = Individual(
            individual_id=new_id(),
            birth_at=NOW,
            birth_name="Other",
            founding_orientation="Foreign",
            creator_provenance={},
            operational_status="active",
            revision=1,
        )
        session.add(other)
        session.flush()
        session.get(Wake, pointer).individual_id = other.individual_id
    with db_session_factory() as session:
        state = session.get(AutonomyState, person)
        state.interval_seconds = 99
        report = check_database(session)
        assert any(f.invariant_id == "autonomy_managed_wake" for f in report.findings)
        assert state.interval_seconds == 99 and state in session.dirty


def test_diagnostics_allow_deferred_consumed_pointer(db_session_factory, person):
    from cognition.db.checks import check_database

    with db_session_factory.begin() as session:
        state = ensure(session, person)
        state.materialization_pending = True
        session.get(Wake, state.managed_wake_id).status = "consumed"
    with db_session_factory() as session:
        assert not [
            f
            for f in check_database(session).findings
            if f.invariant_id.startswith("autonomy_")
        ]


def test_consumed_pointer_without_deferred_marker_is_not_silently_repaired(
    db_session_factory, person
):
    from cognition.db.checks import check_database

    with db_session_factory.begin() as session:
        state = ensure(session, person)
        session.get(Wake, state.managed_wake_id).status = "consumed"
    with db_session_factory.begin() as session:
        with pytest.raises(ValueError, match="managed heartbeat"):
            ensure(session, person)
        assert any(
            f.invariant_id == "autonomy_managed_wake"
            for f in check_database(session).findings
        )


def add_commitment(session, person, due):
    from cognition.db.models.personal import Commitment

    row = Commitment(
        commitment_id=new_id(),
        individual_id=person,
        title="A dated obligation",
        terms="Consider the evidence",
        status="active",
        due_at=due,
        rationale="Fixture",
        evidence_refs=[],
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(row)
    session.flush()
    return row.commitment_id


def test_deadline_shortens_from_fixed_anchor_and_overdue_cannot_defeat_backoff(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        state = ensure(session, person)
        state.interval_seconds = 600
        session.flush()
        deadline = add_commitment(session, person, NOW + timedelta(seconds=300))
    for elapsed in (0, 100, 200, 350):
        with db_session_factory.begin() as session:
            state = ensure(session, person, NOW + timedelta(seconds=elapsed))
            wake = session.get(Wake, state.managed_wake_id)
            assert wake.due_at == NOW + timedelta(seconds=300)
            assert wake.context_refs == [{"kind": "commitment", "id": str(deadline)}]
    with db_session_factory.begin() as session:
        state = ensure(session, person, NOW + timedelta(seconds=400))
        state.anchor_at = NOW + timedelta(seconds=400)
    with db_session_factory.begin() as session:
        state = ensure(session, person, NOW + timedelta(seconds=400))
        wake = session.get(Wake, state.managed_wake_id)
        assert wake.due_at == NOW + timedelta(seconds=1000)
        assert wake.context_refs == []


def test_configuration_changes_clamp_valid_retained_interval_without_poll_drift(
    db_session_factory, person
):
    from cognition.stores.configuration import (
        get_active_config,
        replace_config_revision,
    )

    with db_session_factory.begin() as session:
        state = ensure(session, person)
        state.interval_seconds = 600
    with db_session_factory.begin() as session:
        behavior = get_active_config(session, person).sanitized_config
        behavior.attention.heartbeat_min_seconds = 90.0
        behavior.attention.heartbeat_max_seconds = 180.0
        revision, _ = replace_config_revision(session, person, behavior, NOW)
        config_id = revision.config_revision_id
    with db_session_factory.begin() as session:
        state = ensure(session, person, NOW + timedelta(seconds=20))
        assert state.interval_seconds == 180 and state.config_revision_id == config_id
        assert session.get(Wake, state.managed_wake_id).due_at == NOW + timedelta(
            seconds=180
        )


def test_dirty_commitment_is_rejected_before_its_stale_deadline_can_be_used(
    db_session_factory, person
):
    from cognition.db.models.personal import Commitment

    with db_session_factory.begin() as session:
        ensure(session, person)
        deadline = add_commitment(session, person, NOW + timedelta(seconds=300))
    with db_session_factory() as session:
        row = session.get(Commitment, deadline)
        row.due_at = NOW + timedelta(seconds=10)
        with pytest.raises(ValueError, match="unflushed_scheduler_state"):
            ensure(session, person)
        assert row in session.dirty


def test_retained_interval_outside_its_own_policy_is_not_repaired(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        state = ensure(session, person)
        state.interval_seconds = 1
    with db_session_factory.begin() as session:
        with pytest.raises(ValueError, match="retained policy"):
            ensure(session, person)


def claim_managed(session, person):
    from cognition.db.models.cognition import CognitionCycle, CycleWake

    state = ensure(session, person)
    cycle = CognitionCycle(
        cycle_id=new_id(),
        individual_id=person,
        status="active",
        started_at=NOW,
        max_turns=3,
        max_attempts_per_turn=2,
        max_wakes=16,
        deadline_at=NOW + timedelta(seconds=1200),
        min_wake_delay_seconds=1,
    )
    session.add(cycle)
    session.flush()
    session.get(Wake, state.managed_wake_id).status = "claimed"
    session.add(CycleWake(cycle_id=cycle.cycle_id, wake_id=state.managed_wake_id))
    session.flush()
    return cycle.cycle_id


def test_paused_accounting_reports_desired_deadline_separately_from_actual_wake(
    db_session_factory, person
):
    from cognition.db.models.evidence import EventContent
    from cognition.stores.cognition import finish_cycle

    with db_session_factory.begin() as session:
        state = ensure(session, person)
        state.interval_seconds = 600
    with db_session_factory.begin() as session:
        deadline = add_commitment(session, person, NOW + timedelta(seconds=100))
        cycle_id = claim_managed(session, person)
        session.get(GovernanceState, person).inference_blocked = True
    with db_session_factory.begin() as session:
        finish_cycle(
            session, cycle_id, NOW + timedelta(seconds=10), "refused", failed=True
        )
        count = session.scalar(select(func.count()).select_from(Event))
        finish_cycle(
            session, cycle_id, NOW + timedelta(seconds=10), "refused", failed=True
        )
        assert session.scalar(select(func.count()).select_from(Event)) == count
        payload = session.scalar(
            select(EventContent.payload)
            .join(Event)
            .where(
                Event.event_type == "attention.heartbeat_updated",
                Event.correlation_id == cycle_id,
            )
        )
        assert payload["wake_materialized"] is False
        assert payload["desired_due_at"] == (NOW + timedelta(seconds=100)).isoformat()
        assert payload["desired_context_refs"] == [
            {"kind": "commitment", "id": str(deadline)}
        ]
        assert payload["after"]["wake_status"] == "consumed"


def test_outcome_reads_persisted_cycle_instead_of_stale_cached_completion(
    db_session_factory, person
):
    from cognition.db.models.autonomy import AutonomyState
    from cognition.db.models.cognition import CognitionCycle
    from cognition.stores.autonomy import record_cycle_outcome

    with db_session_factory.begin() as session:
        cycle_id = claim_managed(session, person)
        cycle = session.get(CognitionCycle, cycle_id)
        cycle.status, cycle.completed_at = "completed", NOW
        session.get(
            Wake, session.get(AutonomyState, person).managed_wake_id
        ).status = "consumed"
    with db_session_factory() as stale:
        cached = stale.get(CognitionCycle, cycle_id)
        with db_session_factory.begin() as concurrent:
            concurrent.get(CognitionCycle, cycle_id).status = "active"
        assert cached.status == "completed"
        with pytest.raises(ValueError, match="persisted terminal cycle"):
            record_cycle_outcome(stale, cycle_id, NOW)


@pytest.mark.parametrize("corruption", ["hash", "unactivated", "anchor"])
def test_retained_policy_and_completed_anchor_corruption_block_and_diagnose(
    db_session_factory, person, corruption
):
    from cognition.db.checks import check_database
    from cognition.db.models.autonomy import AutonomyState
    from cognition.db.models.runtime import RuntimeConfigRevision
    from cognition.stores.cognition import finish_cycle

    with db_session_factory.begin() as session:
        cycle_id = claim_managed(session, person)
        finish_cycle(session, cycle_id, NOW, "sleep")
    with db_session_factory.begin() as session:
        state = session.get(AutonomyState, person)
        config = session.get(RuntimeConfigRevision, state.config_revision_id)
        if corruption == "hash":
            config.content_hash = "0" * 64
        elif corruption == "unactivated":
            config.activated_at = None
        else:
            state.anchor_at = NOW + timedelta(seconds=1)
    with db_session_factory.begin() as session:
        with pytest.raises(
            ValueError, match="Scheduler configuration|scheduler completed cycle"
        ):
            ensure(session, person, NOW + timedelta(seconds=1))
        expected = (
            "autonomy_completed_cycle"
            if corruption == "anchor"
            else "autonomy_configuration"
        )
        assert any(f.invariant_id == expected for f in check_database(session).findings)


def test_unflushed_cycle_cannot_be_accounted_or_implicitly_flushed(
    db_session_factory, person
):
    from cognition.db.models.cognition import CognitionCycle
    from cognition.stores.autonomy import record_cycle_outcome

    with db_session_factory.begin() as session:
        cycle_id = claim_managed(session, person)
    with db_session_factory() as session:
        cycle = session.get(CognitionCycle, cycle_id)
        cycle.status, cycle.completed_at = "completed", NOW
        with pytest.raises(ValueError, match="unflushed_scheduler_state"):
            record_cycle_outcome(session, cycle_id, NOW)
        assert cycle in session.dirty


def test_old_outcome_cannot_be_recounted_after_another_cycle_at_the_same_instant(
    db_session_factory, person
):
    from cognition.db.models.autonomy import AutonomyState
    from cognition.stores.autonomy import record_cycle_outcome
    from cognition.stores.cognition import finish_cycle

    with db_session_factory.begin() as session:
        first = claim_managed(session, person)
        finish_cycle(session, first, NOW, "sleep")
    with db_session_factory.begin() as session:
        second = claim_managed(session, person)
        finish_cycle(session, second, NOW, "sleep")
        state = session.get(AutonomyState, person)
        expected = (
            state.last_completed_cycle_id,
            state.interval_seconds,
            state.revision,
        )
    with db_session_factory.begin() as session:
        state = record_cycle_outcome(session, first, NOW)
        assert (
            state.last_completed_cycle_id,
            state.interval_seconds,
            state.revision,
        ) == expected
