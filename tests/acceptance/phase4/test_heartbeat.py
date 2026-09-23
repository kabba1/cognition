"""Durable heartbeat opportunities survive sleep, pauses and exact-D1 recovery."""

import importlib
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, func, select
from test_lexical_recall import example, operation, response, run

from cognition.config.loader import load_config
from cognition.config.recording import reconcile_config
from cognition.db.models.attention import Wake
from cognition.db.models.cognition import (
    AppliedOperation,
    CognitionCycle,
    CognitionTurn,
    ContextSnapshot,
    ModelInvocation,
)
from cognition.db.models.evidence import Event
from cognition.db.models.personal import Goal, PersonalStateRevision
from cognition.db.models.runtime import RuntimeConfigRevision
from cognition.policy.governance import AuthenticatedPrincipal
from cognition.protocols.common import new_id
from cognition.protocols.executive import parse_result
from cognition.runtime.birth import BirthInput, birth
from cognition.runtime.lifecycle import apply_admin_operation
from cognition.stores.cognition import CycleLimits, claim_or_resume, execution_allowed
from cognition.testing.clock import FakeClock
from cognition.testing.scripted_model import ScriptedModelAdapter

NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)


class ProcessInterrupted(BaseException):
    """Simulate process death outside the retryable provider error boundary."""


@pytest.fixture
def clock():
    return FakeClock(NOW)


@pytest.fixture
def state(db_session_factory, clock):
    config = load_config(Path(__file__).parents[2] / "fixtures/config/valid.toml")
    config.runtime.individual_id = new_id()
    config.attention.context_budget_tokens = 64000
    config.attention.heartbeat_min_seconds = 10.0
    config.attention.heartbeat_max_seconds = 80.0
    config.attention.heartbeat_backoff_factor = 2.0
    born = birth(
        db_session_factory,
        BirthInput(
            individual_id=config.runtime.individual_id,
            birth_name="Heartbeat continuity individual",
            founding_orientation="Sleep remains a valid choice",
            creator_provenance={"source": "acceptance"},
            admin_authn_provider="local_os",
            admin_subject="heartbeat-admin",
            config=config,
            runtime_version="acceptance",
        ),
        clock,
    )
    return born, config


def autonomy_model():
    return importlib.import_module("cognition.db.models.autonomy").AutonomyState


def schedule(factory, state):
    with factory() as session:
        row = session.get(autonomy_model(), state[0].individual_id)
        assert row is not None, "An eligible runtime pass must retain scheduler state"
        return deepcopy(
            {field.name: getattr(row, field.name) for field in row.__table__.columns}
        )


def pending_heartbeats(factory, state):
    with factory() as session:
        return session.scalars(
            select(Wake).where(
                Wake.individual_id == state[0].individual_id,
                Wake.kind == "heartbeat",
                Wake.status == "pending",
            )
        ).all()


def future(factory, state, clock, seconds):
    saved = schedule(factory, state)
    pending = pending_heartbeats(factory, state)
    assert len(pending) == 1
    assert saved["managed_wake_id"] == pending[0].wake_id
    assert saved["interval_seconds"] == seconds
    assert saved["anchor_at"] == clock.now()
    assert saved["materialization_pending"] is False
    assert pending[0].due_at == clock.now() + timedelta(seconds=seconds)
    assert pending[0].coalesce_key is None
    return pending[0]


def count(factory, model):
    with factory() as session:
        return session.scalar(select(func.count()).select_from(model))


def admin(factory, state, clock, operation):
    apply_admin_operation(
        factory,
        state[0].individual_id,
        AuthenticatedPrincipal("local_os", "heartbeat-admin"),
        operation,
        "Acceptance lifecycle transition",
        clock,
    )


def bootstrap(engine, state, clock):
    assert (
        run(engine, state, clock, ScriptedModelAdapter([response])).status
        == "completed"
    )


def terminal_result(request, status):
    value = example(f"model_result_v{request.schema_version}")
    value.update(
        request_id=request.request_id,
        status=status,
        decision=None,
        finish_reason=status,
        error={"code": "unavailable", "message": "No result", "retryable": False},
    )
    return parse_result(value)


def test_newborn_sleep_leaves_one_future_opportunity_and_unchanged_polls_write_nothing(
    db_engine, db_session_factory, state, clock
):
    bootstrap(db_engine, state, clock)
    heartbeat = future(db_session_factory, state, clock, 10)
    before = schedule(db_session_factory, state)
    events_before = count(db_session_factory, Event)
    clock.advance(timedelta(seconds=5))
    fresh = ScriptedModelAdapter([])
    assert run(db_engine, state, clock, fresh).status == "idle"
    assert fresh.requests == ()
    assert schedule(db_session_factory, state) == before
    assert pending_heartbeats(db_session_factory, state)[0].due_at == heartbeat.due_at
    assert count(db_session_factory, Event) == events_before
    assert count(db_session_factory, PersonalStateRevision) == 0


def test_noop_heartbeats_back_off_and_cap_across_fresh_models_and_owners(
    db_engine, db_session_factory, state, clock
):
    bootstrap(db_engine, state, clock)
    heartbeat = future(db_session_factory, state, clock, 10)
    for seconds in (20, 40, 80, 80):
        clock.set(heartbeat.due_at)
        fresh = ScriptedModelAdapter([response])
        assert run(db_engine, state, clock, fresh).status == "completed"
        assert len(fresh.requests) == 1
        previous_id = heartbeat.wake_id
        heartbeat = future(db_session_factory, state, clock, seconds)
        assert heartbeat.wake_id != previous_id
    assert count(db_session_factory, CognitionCycle) == 5
    assert count(db_session_factory, PersonalStateRevision) == 0


@pytest.mark.parametrize("later_failure", [False, True])
def test_recorded_personal_activity_resets_backoff_even_before_a_later_failed_turn(
    db_engine, db_session_factory, state, clock, later_failure
):
    bootstrap(db_engine, state, clock)
    clock.set(future(db_session_factory, state, clock, 10).due_at)
    assert (
        run(db_engine, state, clock, ScriptedModelAdapter([response])).status
        == "completed"
    )
    clock.set(future(db_session_factory, state, clock, 20).due_at)
    goal = operation("goal", title="Preserve one deliberate investigation")
    scripts = [
        lambda request: response(
            request,
            disposition="continue" if later_failure else "sleep",
            goal_operations=[goal],
        )
    ]
    if later_failure:
        scripts.append(lambda request: terminal_result(request, "failed"))
    model = ScriptedModelAdapter(scripts)
    outcome = run(db_engine, state, clock, model)
    assert outcome.status == ("failed" if later_failure else "completed")
    future(db_session_factory, state, clock, 10)
    with db_session_factory() as session:
        assert session.get(Goal, goal["operation_id"]).revision == 1
    assert count(db_session_factory, PersonalStateRevision) == 1


def test_long_silence_creates_one_overdue_opportunity_without_catchup_cycles(
    db_engine, db_session_factory, state, clock
):
    bootstrap(db_engine, state, clock)
    clock.advance(timedelta(days=100))
    fresh = ScriptedModelAdapter([response])
    assert run(db_engine, state, clock, fresh).status == "completed"
    assert len(fresh.requests) == 1
    future(db_session_factory, state, clock, 20)
    assert count(db_session_factory, CognitionCycle) == 2
    assert count(db_session_factory, PersonalStateRevision) == 0
    assert run(db_engine, state, clock, None).status == "idle"
    assert count(db_session_factory, CognitionCycle) == 2


def test_pause_preserves_overdue_wake_then_resume_claims_it_once(
    db_engine, db_session_factory, state, clock
):
    bootstrap(db_engine, state, clock)
    before = schedule(db_session_factory, state)
    admin(db_session_factory, state, clock, "pause")
    clock.advance(timedelta(days=1))
    assert run(db_engine, state, clock, None).reason == "lifecycle_or_governance"
    assert schedule(db_session_factory, state) == before
    assert len(pending_heartbeats(db_session_factory, state)) == 1
    admin(db_session_factory, state, clock, "resume")
    fresh = ScriptedModelAdapter([response])
    assert run(db_engine, state, clock, fresh).status == "completed"
    assert len(fresh.requests) == 1
    future(db_session_factory, state, clock, 20)


@pytest.mark.parametrize("status", ["refused", "failed"])
def test_pause_during_terminal_result_accounts_once_and_defers_wake_until_resume(
    db_engine, db_session_factory, state, clock, status
):
    bootstrap(db_engine, state, clock)
    heartbeat = future(db_session_factory, state, clock, 10)
    clock.set(heartbeat.due_at)

    def pausing_provider(request):
        admin(db_session_factory, state, clock, "pause")
        return terminal_result(request, status)

    model = ScriptedModelAdapter([pausing_provider])
    assert run(db_engine, state, clock, model).status == "failed"
    saved = schedule(db_session_factory, state)
    assert saved["interval_seconds"] == 20
    assert saved["anchor_at"] == clock.now()
    assert saved["materialization_pending"] is True
    assert saved["managed_wake_id"] == heartbeat.wake_id
    assert pending_heartbeats(db_session_factory, state) == []
    with db_session_factory() as session:
        assert session.get(Wake, heartbeat.wake_id).status == "consumed"
        assert (
            session.get(CognitionCycle, saved["last_completed_cycle_id"]).status
            == "failed"
        )
    assert run(db_engine, state, clock, None).reason == "lifecycle_or_governance"
    assert schedule(db_session_factory, state) == saved
    admin(db_session_factory, state, clock, "resume")
    fresh = ScriptedModelAdapter([])
    assert run(db_engine, state, clock, fresh).status == "idle"
    assert fresh.requests == ()
    future(db_session_factory, state, clock, 20)
    assert count(db_session_factory, ModelInvocation) == 2


def test_exact_d1_recovery_applies_personal_state_and_heartbeat_outcome_once(
    db_engine, db_session_factory, state, clock
):
    bootstrap(db_engine, state, clock)
    heartbeat = future(db_session_factory, state, clock, 10)
    clock.set(heartbeat.due_at)
    before = schedule(db_session_factory, state)
    goal = operation("goal", title="D1 must survive provider loss")
    model = ScriptedModelAdapter(
        [lambda request: response(request, goal_operations=[goal])]
    )

    def crash_before_personal_write(*args):
        raise ProcessInterrupted()

    event.listen(Goal, "before_insert", crash_before_personal_write)
    try:
        with pytest.raises(ProcessInterrupted):
            run(db_engine, state, clock, model)
    finally:
        event.remove(Goal, "before_insert", crash_before_personal_write)
    request = model.requests[0]
    with db_session_factory() as session:
        turn = session.get(CognitionTurn, request.turn_id)
        assert turn.status == "decided"
        retained = deepcopy((turn.decision_json, turn.decision_hash))
        snapshot = session.scalar(
            select(ContextSnapshot).where(ContextSnapshot.turn_id == request.turn_id)
        )
        frozen = deepcopy((snapshot.request_json, snapshot.content_hash))
        assert session.get(Goal, goal["operation_id"]) is None
    assert schedule(db_session_factory, state) == before
    assert pending_heartbeats(db_session_factory, state) == []
    assert run(db_engine, state, clock, None).status == "completed"
    future(db_session_factory, state, clock, 10)
    with db_session_factory() as session:
        turn = session.get(CognitionTurn, request.turn_id)
        assert turn.status == "applied"
        assert (turn.decision_json, turn.decision_hash) == retained
        snapshot = session.scalar(
            select(ContextSnapshot).where(ContextSnapshot.turn_id == request.turn_id)
        )
        assert (snapshot.request_json, snapshot.content_hash) == frozen
        assert session.get(Goal, goal["operation_id"]).revision == 1
    after = schedule(db_session_factory, state)
    assert after["last_completed_cycle_id"] == request.cycle_id
    assert run(db_engine, state, clock, None).status == "idle"
    assert schedule(db_session_factory, state) == after
    assert count(db_session_factory, AppliedOperation) == 1
    assert count(db_session_factory, ModelInvocation) == 2


def test_active_cycle_predating_scheduler_initializes_without_parallel_wake(
    db_engine, db_session_factory, state, clock
):
    with db_session_factory.begin() as session:
        assert execution_allowed(session, state[0].individual_id)
        cycle = claim_or_resume(
            session, state[0].individual_id, clock.now(), CycleLimits()
        )
        assert cycle is not None
    assert run(db_engine, state, clock, None).reason == "model_unavailable"
    saved = schedule(db_session_factory, state)
    assert saved["managed_wake_id"] is None
    assert saved["materialization_pending"] is True
    assert saved["last_completed_cycle_id"] is None
    assert pending_heartbeats(db_session_factory, state) == []
    fresh = ScriptedModelAdapter([response])
    assert run(db_engine, state, clock, fresh).status == "completed"
    assert fresh.requests[0].cycle_id == cycle.cycle_id
    future(db_session_factory, state, clock, 10)
    assert count(db_session_factory, CognitionCycle) == 1


@pytest.mark.parametrize("prior_scheduler", [False, True])
def test_absent_active_configuration_finishes_without_losing_future_recovery(
    db_engine, db_session_factory, state, clock, prior_scheduler
):
    if prior_scheduler:
        bootstrap(db_engine, state, clock)
        clock.set(future(db_session_factory, state, clock, 10).due_at)
    with db_session_factory.begin() as session:
        session.get(
            RuntimeConfigRevision, state[0].config_revision_id
        ).superseded_at = clock.now()
    outcome = run(db_engine, state, clock, None)
    assert outcome.status == "failed" and outcome.reason == "missing_configuration"
    if prior_scheduler:
        saved = schedule(db_session_factory, state)
        assert saved["interval_seconds"] == 20
        assert saved["materialization_pending"] is True
        assert saved["config_revision_id"] == state[0].config_revision_id
    else:
        with db_session_factory() as session:
            assert session.get(autonomy_model(), state[0].individual_id) is None
    assert pending_heartbeats(db_session_factory, state) == []
    changed = state[1].model_copy(deep=True)
    changed.model.max_output_tokens += 1
    revision = reconcile_config(
        db_session_factory, state[0].individual_id, changed, clock
    ).revision
    assert run(db_engine, state, clock, None).status == "idle"
    future(db_session_factory, state, clock, 20 if prior_scheduler else 10)
    assert (
        schedule(db_session_factory, state)["config_revision_id"]
        == revision.config_revision_id
    )
    assert count(db_session_factory, ModelInvocation) == (1 if prior_scheduler else 0)
