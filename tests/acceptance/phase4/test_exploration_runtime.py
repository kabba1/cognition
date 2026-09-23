"""Opt-in exploration spends a bounded grant without restricting ordinary work."""

from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import event, func, select
from test_execution_budget_recovery import FiniteFailingProvider
from test_heartbeat import ProcessInterrupted, terminal_result
from test_lexical_recall import clock as clock
from test_lexical_recall import operation, response, run
from test_lexical_recall import state as state
from test_lexical_recall import wake as ordinary_wake

from cognition.config.recording import reconcile_config
from cognition.db.models.attention import Wake
from cognition.db.models.cognition import (
    AppliedOperation,
    CognitionCycle,
    CognitionTurn,
    ContextSnapshot,
    CycleWake,
    ModelInvocation,
)
from cognition.db.models.development import Interest
from cognition.db.models.evidence import Event
from cognition.db.models.exploration import ExplorationGrant, ExplorationState
from cognition.db.models.governance import GovernanceState
from cognition.db.models.personal import Goal, PersonalStateRevision
from cognition.db.models.runtime import RuntimeConfigRevision
from cognition.policy.governance import AuthenticatedPrincipal
from cognition.protocols.common import Ref, new_id
from cognition.runtime.cognition import CognitionRuntime
from cognition.runtime.exploration_admin import set_internal_exploration
from cognition.runtime.lifecycle import apply_admin_operation
from cognition.runtime.ownership import acquire_runtime_ownership
from cognition.stores.cognition import CycleLimits
from cognition.testing.scripted_model import ScriptedModelAdapter

WEEK = timedelta(days=7)
PRINCIPAL = AuthenticatedPrincipal("local_os", "lexical-admin")


def toggle(factory, state, clock, enabled):
    return set_internal_exploration(
        factory,
        state[0].individual_id,
        PRINCIPAL,
        enabled,
        "Acceptance controls a bounded internal allowance",
        clock,
    )


def lifecycle(factory, state, clock, operation):
    return apply_admin_operation(
        factory,
        state[0].individual_id,
        PRINCIPAL,
        operation,
        "Acceptance lifecycle transition",
        clock,
    )


def saved(factory, state):
    with factory() as session:
        row = session.get(ExplorationState, state[0].individual_id)
        assert row is not None, "An enabled eligible runtime pass must retain a grant"
        return deepcopy(
            {column.name: getattr(row, column.name) for column in row.__table__.columns}
        )


def count(factory, model):
    with factory() as session:
        return session.scalar(select(func.count()).select_from(model))


def controls(request):
    return [
        section
        for section in request.context_sections
        if section.name == "internal_exploration"
    ]


def ready(engine, factory, state, clock):
    toggle(factory, state, clock, True)
    bootstrap = ScriptedModelAdapter([response])
    assert run(engine, state, clock, bootstrap).status == "completed"
    assert not controls(bootstrap.requests[0])
    snapshot = saved(factory, state)
    with factory() as session:
        wake = session.get(Wake, snapshot["managed_wake_id"])
        assert wake.status == "pending" and wake.due_at <= clock.now()
        assert wake.kind == "routine" and wake.coalesce_key is None
    return snapshot["managed_wake_id"]


def cycle_attempts(factory, cycle_id):
    with factory() as session:
        return session.scalars(
            select(ModelInvocation)
            .join(CognitionTurn)
            .where(CognitionTurn.cycle_id == cycle_id)
            .order_by(ModelInvocation.attempt_number)
        ).all()


def assert_spent(factory, state, clock, cycle_id):
    snapshot = saved(factory, state)
    assert snapshot["last_terminal_cycle_id"] == cycle_id
    assert snapshot["next_eligible_at"] >= clock.now() + WEEK
    assert snapshot["last_outcome_event_id"] is not None
    with factory() as session:
        marker = session.get(Event, snapshot["last_outcome_event_id"])
        assert marker.event_type == "attention.exploration_completed"
        assert marker.correlation_id == cycle_id
    return snapshot


def test_bootstrap_and_due_ordinary_work_keep_full_limits_before_exploration(
    db_engine, db_session_factory, state, clock
):
    grant_id = ready(db_engine, db_session_factory, state, clock)
    ordinary = ordinary_wake(db_session_factory, state, clock)
    model = ScriptedModelAdapter(
        [
            lambda request: response(request, disposition="continue"),
            lambda request: response(request, disposition="continue"),
            response,
        ]
    )
    result = run(db_engine, state, clock, model)
    assert result.status == "completed" and len(model.requests) == 3
    assert all(not controls(request) for request in model.requests)
    with db_session_factory() as session:
        cycle = session.get(CognitionCycle, result.cycle_id)
        assert cycle.max_turns == 3 and cycle.max_wakes == 16
        assert (
            session.scalar(
                select(CycleWake.wake_id).where(CycleWake.cycle_id == cycle.cycle_id)
            )
            == ordinary
        )
        assert session.get(Wake, grant_id).status == "pending"
    exploration = ScriptedModelAdapter([response])
    result = run(db_engine, state, clock, exploration)
    assert result.status == "completed" and len(exploration.requests) == 1
    control = controls(exploration.requests[0])
    assert len(control) == 1 and control[0].category == "control"
    with db_session_factory() as session:
        cycle = session.get(CognitionCycle, result.cycle_id)
        assert (cycle.max_turns, cycle.max_attempts_per_turn, cycle.max_wakes) == (
            1,
            2,
            1,
        )
        assert cycle.deadline_at - cycle.started_at <= timedelta(seconds=120)
        assert session.scalars(
            select(CycleWake.wake_id).where(CycleWake.cycle_id == cycle.cycle_id)
        ).all() == [grant_id]
        grant = session.get(ExplorationGrant, grant_id)
        assert control[0].refs == [Ref(kind="wake", id=grant_id)]
        assert control[0].content == {
            "policy_version": 1,
            "grant_id": str(grant_id),
            "individual_id": str(state[0].individual_id),
            "cycle_id": str(cycle.cycle_id),
            "scope": "internal",
            "grant_content_hash": grant.content_hash,
            "authorizing_governance_revision": grant.authorizing_governance_revision,
            "not_before_at": grant.not_before_at.isoformat(),
            "effective_limits": {
                "max_turns": 1,
                "max_attempts_per_turn": 2,
                "max_wakes": 1,
                "max_seconds": (cycle.deadline_at - cycle.started_at).total_seconds(),
                "started_at": cycle.started_at.isoformat(),
                "deadline_at": cycle.deadline_at.isoformat(),
            },
            "wake_requests_allowed": False,
        }
    assert_spent(db_session_factory, state, clock, result.cycle_id)


@pytest.mark.parametrize("personal", [False, True])
def test_continue_still_finishes_after_one_turn_with_optional_personal_choice(
    db_engine, db_session_factory, state, clock, personal
):
    ready(db_engine, db_session_factory, state, clock)
    goal = operation("goal", title="A freely chosen internal question")
    model = ScriptedModelAdapter(
        [
            lambda request: response(
                request,
                disposition="continue",
                goal_operations=[goal] if personal else [],
            )
        ]
    )
    result = run(db_engine, state, clock, model)
    assert result.status == "completed" and result.reason == "turn_limit"
    assert len(model.requests) == 1
    assert_spent(db_session_factory, state, clock, result.cycle_id)
    assert count(db_session_factory, Goal) == int(personal)
    assert count(db_session_factory, Interest) == 0
    assert count(db_session_factory, PersonalStateRevision) == int(personal)
    assert run(db_engine, state, clock, None).status == "idle"


def test_exploration_rejects_self_scheduled_escape_and_all_effects_atomically(
    db_engine, db_session_factory, state, clock
):
    ready(db_engine, db_session_factory, state, clock)
    goal = operation("goal")
    wake_id = new_id()
    model = ScriptedModelAdapter(
        [
            lambda request: response(
                request,
                goal_operations=[goal],
                wake_requests=[
                    dict(
                        operation_id=wake_id,
                        not_before=clock.now(),
                        purpose="Escape the allowance",
                        context_refs=[],
                        coalesce_key=None,
                    )
                ],
            )
        ]
    )
    result = run(db_engine, state, clock, model)
    assert result.status == "failed" and result.reason == "decision_rejected"
    with db_session_factory() as session:
        assert session.get(Goal, goal["operation_id"]) is None
        assert session.get(Wake, wake_id) is None
        turn = session.get(CognitionTurn, model.requests[0].turn_id)
        assert turn.status == "rejected" and turn.validation_errors
    assert count(db_session_factory, AppliedOperation) == 0
    assert_spent(db_session_factory, state, clock, result.cycle_id)


def test_failed_attempts_spend_grant_before_exhausted_provider_preflight(
    db_engine, db_session_factory, state, clock
):
    ready(db_engine, db_session_factory, state, clock)
    provider = FiniteFailingProvider(2)
    result = run(db_engine, state, clock, provider)
    assert (result.status, result.reason) == ("failed", "attempt_limit")
    assert provider.calls == 2
    assert len(cycle_attempts(db_session_factory, result.cycle_id)) == 2
    assert_spent(db_session_factory, state, clock, result.cycle_id)


@pytest.mark.parametrize("attempts, seconds", [(9, 3600), (1, 30)])
def test_exploration_intersects_caller_and_grant_limits(
    db_engine, db_session_factory, state, clock, attempts, seconds
):
    ready(db_engine, db_session_factory, state, clock)
    model = ScriptedModelAdapter([response])
    with acquire_runtime_ownership(
        db_engine,
        state[0].individual_id,
        clock=clock,
        host_id="exploration-acceptance",
        process_id=38,
        runtime_version="acceptance",
    ) as owner:
        result = CognitionRuntime(
            owner,
            state[0].individual_id,
            model,
            clock,
            limits=CycleLimits(
                max_turns=9,
                max_attempts_per_turn=attempts,
                max_seconds=seconds,
                max_wakes=50,
            ),
        ).run_once()
    assert result.status == "completed"
    limits = controls(model.requests[0])[0].content["effective_limits"]
    assert limits["max_attempts_per_turn"] == min(attempts, 2)
    assert limits["max_seconds"] == min(seconds, 120)
    assert limits["max_turns"] == limits["max_wakes"] == 1
    with db_session_factory() as session:
        cycle = session.get(CognitionCycle, result.cycle_id)
        assert cycle.max_attempts_per_turn == min(attempts, 2)
        assert cycle.deadline_at - cycle.started_at == timedelta(
            seconds=min(seconds, 120)
        )


def test_locked_due_ordinary_wake_does_not_make_exploration_eligible(
    db_engine, db_session_factory, state, clock
):
    grant_id = ready(db_engine, db_session_factory, state, clock)
    ordinary = ordinary_wake(db_session_factory, state, clock)
    model = ScriptedModelAdapter([response])
    with db_session_factory.begin() as competing:
        competing.scalar(select(Wake).where(Wake.wake_id == ordinary).with_for_update())
        result = run(db_engine, state, clock, model)
        assert result.status == "idle"
        assert model.requests == ()
    with db_session_factory() as session:
        assert session.get(Wake, grant_id).status == "pending"
        assert session.get(Wake, ordinary).status == "pending"


def test_expired_unstarted_exploration_spends_grant_without_provider(
    db_engine, db_session_factory, state, clock
):
    ready(db_engine, db_session_factory, state, clock)
    blocked = run(db_engine, state, clock, None)
    assert blocked.status == "blocked" and blocked.cycle_id is not None
    assert cycle_attempts(db_session_factory, blocked.cycle_id) == []
    clock.advance(timedelta(seconds=121))
    result = run(db_engine, state, clock, None)
    assert result.cycle_id == blocked.cycle_id
    assert (result.status, result.reason) == ("failed", "deadline")
    assert cycle_attempts(db_session_factory, result.cycle_id) == []
    assert_spent(db_session_factory, state, clock, result.cycle_id)


def interrupt(request):
    raise ProcessInterrupted()


def test_abandoned_starts_recover_without_model_or_third_charge(
    db_engine, db_session_factory, state, clock
):
    ready(db_engine, db_session_factory, state, clock)
    models = []
    for _ in range(2):
        model = ScriptedModelAdapter([interrupt])
        models.append(model)
        with pytest.raises(ProcessInterrupted):
            run(db_engine, state, clock, model)
    result = run(db_engine, state, clock, None)
    assert (result.status, result.reason) == ("failed", "attempt_limit")
    attempts = cycle_attempts(db_session_factory, result.cycle_id)
    assert len(attempts) == 2 and all(row.status == "abandoned" for row in attempts)
    assert models[0].requests[0] == models[1].requests[0]
    assert_spent(db_session_factory, state, clock, result.cycle_id)


def test_disable_during_authorized_call_preserves_returned_personal_decision(
    db_engine, db_session_factory, state, clock
):
    ready(db_engine, db_session_factory, state, clock)
    goal = operation("goal", title="Keep the already authorized choice")

    def decide(request):
        toggle(db_session_factory, state, clock, False)
        return response(request, goal_operations=[goal])

    model = ScriptedModelAdapter([decide])
    result = run(db_engine, state, clock, model)
    assert result.status == "completed" and len(model.requests) == 1
    assert count(db_session_factory, Goal) == 1
    assert_spent(db_session_factory, state, clock, result.cycle_id)
    assert run(db_engine, state, clock, None).status == "idle"


def test_disable_after_abandoned_start_terminalizes_without_retry_or_provider(
    db_engine, db_session_factory, state, clock
):
    ready(db_engine, db_session_factory, state, clock)
    model = ScriptedModelAdapter([interrupt])
    with pytest.raises(ProcessInterrupted):
        run(db_engine, state, clock, model)
    toggle(db_session_factory, state, clock, False)
    result = run(db_engine, state, clock, None)
    assert result.status == "failed"
    assert result.cycle_id == model.requests[0].cycle_id
    attempts = cycle_attempts(db_session_factory, result.cycle_id)
    assert len(attempts) == 1 and attempts[0].status == "abandoned"
    assert_spent(db_session_factory, state, clock, result.cycle_id)


def test_exact_d1_survives_disable_and_provider_loss_without_recompilation(
    db_engine, db_session_factory, state, clock
):
    ready(db_engine, db_session_factory, state, clock)
    goal = operation("goal", title="Recover exact exploration D1")
    model = ScriptedModelAdapter(
        [lambda request: response(request, goal_operations=[goal])]
    )

    def crash(*args):
        raise ProcessInterrupted()

    event.listen(Goal, "before_insert", crash)
    try:
        with pytest.raises(ProcessInterrupted):
            run(db_engine, state, clock, model)
    finally:
        event.remove(Goal, "before_insert", crash)
    request = model.requests[0]
    with db_session_factory() as session:
        turn = session.get(CognitionTurn, request.turn_id)
        assert turn.status == "decided"
        retained = deepcopy((turn.decision_json, turn.decision_hash))
        context = session.scalar(
            select(ContextSnapshot).where(ContextSnapshot.turn_id == request.turn_id)
        )
        frozen = deepcopy((context.request_json, context.content_hash))
    toggle(db_session_factory, state, clock, False)
    result = run(db_engine, state, clock, None)
    assert result.status == "completed" and result.cycle_id == request.cycle_id
    with db_session_factory() as session:
        turn = session.get(CognitionTurn, request.turn_id)
        assert turn.status == "applied"
        assert (turn.decision_json, turn.decision_hash) == retained
        context = session.scalar(
            select(ContextSnapshot).where(ContextSnapshot.turn_id == request.turn_id)
        )
        assert (context.request_json, context.content_hash) == frozen
        assert session.get(Goal, goal["operation_id"]).revision == 1
    assert len(cycle_attempts(db_session_factory, result.cycle_id)) == 1
    assert_spent(db_session_factory, state, clock, result.cycle_id)
    assert run(db_engine, state, clock, None).status == "idle"
    assert count(db_session_factory, AppliedOperation) == 1


@pytest.mark.parametrize("invalid", [False, True])
def test_absent_or_invalid_policy_does_not_block_ordinary_cognition(
    db_engine, db_session_factory, state, clock, invalid
):
    if invalid:
        with db_session_factory.begin() as session:
            session.get(GovernanceState, state[0].individual_id).budget_policy = {
                "internal_exploration": {"schema_version": 1, "enabled": "yes"}
            }
    model = ScriptedModelAdapter([response])
    assert run(db_engine, state, clock, model).status == "completed"
    assert len(model.requests) == 1 and not controls(model.requests[0])
    assert count(db_session_factory, ExplorationGrant) == 0
    assert count(db_session_factory, ExplorationState) == 0


def test_disabling_pending_allowance_preserves_cancellation_cadence_across_reenable(
    db_engine, db_session_factory, state, clock
):
    grant_id = ready(db_engine, db_session_factory, state, clock)
    toggle(db_session_factory, state, clock, False)
    assert run(db_engine, state, clock, None).status == "idle"
    cancelled = saved(db_session_factory, state)
    assert cancelled["last_terminal_cycle_id"] is None
    assert cancelled["next_eligible_at"] >= clock.now() + WEEK
    with db_session_factory() as session:
        assert session.get(Wake, grant_id).status == "cancelled"
        assert (
            session.get(Event, cancelled["last_outcome_event_id"]).event_type
            == "attention.exploration_cancelled"
        )
    toggle(db_session_factory, state, clock, True)
    assert run(db_engine, state, clock, None).status == "idle"
    after = saved(db_session_factory, state)
    assert after["next_eligible_at"] == cancelled["next_eligible_at"]
    assert count(db_session_factory, ModelInvocation) == 1


@pytest.mark.parametrize("deferral", ["pause", "configuration"])
def test_terminal_outcome_accounts_once_while_successor_is_deferred(
    db_engine, db_session_factory, state, clock, deferral
):
    grant_id = ready(db_engine, db_session_factory, state, clock)

    def decide(request):
        if deferral == "pause":
            lifecycle(db_session_factory, state, clock, "pause")
        else:
            with db_session_factory.begin() as session:
                session.get(
                    RuntimeConfigRevision, state[0].config_revision_id
                ).superseded_at = clock.now()
        return terminal_result(request, "refused")

    model = ScriptedModelAdapter([decide])
    result = run(db_engine, state, clock, model)
    assert result.status == "failed"
    deferred = assert_spent(db_session_factory, state, clock, result.cycle_id)
    assert deferred["materialization_pending"] is True
    with db_session_factory() as session:
        assert session.get(Wake, grant_id).status == "consumed"
    if deferral == "pause":
        assert run(db_engine, state, clock, None).reason == "lifecycle_or_governance"
        lifecycle(db_session_factory, state, clock, "resume")
    else:
        assert run(db_engine, state, clock, None).status == "idle"
        changed = state[1].model_copy(deep=True)
        changed.model.max_output_tokens += 1
        reconcile_config(db_session_factory, state[0].individual_id, changed, clock)
    assert run(db_engine, state, clock, None).status == "idle"
    assert (
        saved(db_session_factory, state)["last_outcome_event_id"]
        == deferred["last_outcome_event_id"]
    )
    assert len(cycle_attempts(db_session_factory, result.cycle_id)) == 1


def test_long_silence_yields_one_grant_after_ordinary_overdue_work(
    db_engine, db_session_factory, state, clock
):
    ready(db_engine, db_session_factory, state, clock)
    first = run(db_engine, state, clock, ScriptedModelAdapter([response]))
    assert first.status == "completed"
    assert_spent(db_session_factory, state, clock, first.cycle_id)
    clock.advance(timedelta(days=90))
    ordinary = ScriptedModelAdapter([response])
    assert run(db_engine, state, clock, ordinary).status == "completed"
    assert not controls(ordinary.requests[0])
    model = ScriptedModelAdapter([response])
    result = run(db_engine, state, clock, model)
    assert result.status == "completed" and len(model.requests) == 1
    assert controls(model.requests[0])
    assert_spent(db_session_factory, state, clock, result.cycle_id)
    assert run(db_engine, state, clock, None).status == "idle"
    with db_session_factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Event)
                .where(Event.event_type == "attention.exploration_completed")
            )
            == 2
        )
