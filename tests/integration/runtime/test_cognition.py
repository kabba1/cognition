"""Execution history survives interruption without resampling a durable decision."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, func, select

from cognition.config.loader import load_config
from cognition.protocols.cognition_v1 import CognitionDecisionV1
from cognition.protocols.common import new_id
from cognition.protocols.model_v1 import ModelResultV1
from cognition.runtime.birth import BirthInput, birth
from cognition.runtime.ownership import acquire_runtime_ownership
from cognition.testing.clock import FakeClock
from cognition.testing.scripted_model import ScriptedModelAdapter

NOW = datetime(2026, 9, 22, 18, tzinfo=UTC)


def response(request, *, disposition="sleep", focus="A durable focus", wakes=None):
    decision = CognitionDecisionV1(
        schema_version=1,
        decision_id=new_id(),
        cycle_id=request.cycle_id,
        turn_id=request.turn_id,
        disposition=disposition,
        rationale_summary="Test",
        current_focus={"summary": focus, "refs": []},
        goal_operations=[],
        commitment_operations=[],
        belief_operations=[],
        episode_operations=[],
        interest_operations=[],
        preference_operations=[],
        self_model_operations=[],
        action_requests=[],
        wake_requests=wakes or [],
    )
    return ModelResultV1(
        schema_version=1,
        status="completed",
        request_id=request.request_id,
        decision=decision,
        provider="scripted",
        requested_model="test-model",
        resolved_model="test-model",
        provider_request_id=None,
        usage=None,
        finish_reason="completed",
        error=None,
    )


@pytest.fixture
def born(db_session_factory):
    config = load_config(Path(__file__).parents[2] / "fixtures/config/valid.toml")
    config.runtime.individual_id = new_id()
    return birth(
        db_session_factory,
        BirthInput(
            individual_id=config.runtime.individual_id,
            birth_name="Phase Two",
            founding_orientation="Learn carefully",
            creator_provenance={},
            admin_authn_provider="local_os",
            admin_subject="test-admin",
            config=config,
            runtime_version="test",
        ),
        FakeClock(NOW),
    )


@pytest.fixture
def owner(db_engine, born):
    with acquire_runtime_ownership(
        db_engine,
        born.individual_id,
        clock=FakeClock(NOW),
        host_id="test",
        process_id=1,
        runtime_version="test",
    ) as ownership:
        yield ownership


def test_sleep_applies_focus_and_consumes_wake(owner, born, db_session_factory):
    from cognition.db.models.cognition import AttentionState, CognitionCycle
    from cognition.runtime.cognition import CognitionRuntime
    from cognition.stores.attention import load_wake

    model = ScriptedModelAdapter([response])
    runtime = CognitionRuntime(owner, born.individual_id, model, FakeClock(NOW))
    result = runtime.run_once()
    assert result.status == "completed"
    assert len(model.requests) == 1
    assert runtime.run_once().status == "idle"
    with db_session_factory() as session:
        assert (
            session.get(AttentionState, born.individual_id).current_focus["summary"]
            == "A durable focus"
        )
        assert load_wake(session, born.bootstrap_wake_id).status == "consumed"
        assert session.scalar(select(func.count()).select_from(CognitionCycle)) == 1


def test_model_call_has_no_open_transaction(owner, born):
    from cognition.runtime.cognition import CognitionRuntime

    def inspect_call(request):
        assert not owner.connection.in_transaction()
        return response(request)

    assert (
        CognitionRuntime(
            owner,
            born.individual_id,
            ScriptedModelAdapter(
                [
                    inspect_call,
                ]
            ),
            FakeClock(NOW),
        )
        .run_once()
        .status
        == "completed"
    )


def test_bound_adapter_cannot_infer_using_different_frozen_configuration(
    owner, born, db_session_factory
):
    from cognition.db.models.cognition import ContextSnapshot, ModelInvocation
    from cognition.runtime.cognition import CognitionRuntime

    model = ScriptedModelAdapter([response])
    result = CognitionRuntime(
        owner,
        born.individual_id,
        model,
        FakeClock(NOW),
        bound_model=("another-adapter", "another-model"),
    ).run_once()
    assert result.status == "blocked"
    assert result.reason == "model_configuration_mismatch"
    assert model.requests == ()
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ContextSnapshot)) == 1
        assert session.scalar(select(func.count()).select_from(ModelInvocation)) == 0

    # The frozen context remains usable by its configured adapter.
    assert (
        CognitionRuntime(owner, born.individual_id, model, FakeClock(NOW))
        .run_once()
        .status
        == "completed"
    )


def test_selected_governance_reference_is_usable_in_focus(owner, born):
    from cognition.runtime.cognition import CognitionRuntime

    def echo_governance(request):
        result = response(request)
        result.decision.current_focus.refs = next(
            section.refs
            for section in request.context_sections
            if section.name == "governance"
        )
        return result

    assert (
        CognitionRuntime(
            owner,
            born.individual_id,
            ScriptedModelAdapter([echo_governance]),
            FakeClock(NOW),
        )
        .run_once()
        .status
        == "completed"
    )


def test_legacy_frozen_contract_does_not_gain_personal_mutation_authority(
    owner, born, db_session_factory, monkeypatch
):
    from cognition.db.models.cognition import AttentionState, CognitionTurn
    from cognition.db.models.personal import Goal
    from cognition.protocols.cognition_v1 import GoalOperation
    from cognition.runtime import context
    from cognition.runtime.cognition import CognitionRuntime

    monkeypatch.setattr(context, "RUNTIME_CONTRACT_VERSION", "2.0")

    def goal_response(request):
        result = response(request)
        result.decision.goal_operations = [
            GoalOperation(
                operation_id=new_id(),
                op="create",
                goal_id=None,
                title="Explore",
                desired_state="Learn",
                project_id=None,
                requested_status=None,
                origin=None,
                rationale="Chosen",
                evidence_refs=[],
            )
        ]
        return result

    result = CognitionRuntime(
        owner, born.individual_id, ScriptedModelAdapter([goal_response]), FakeClock(NOW)
    ).run_once()
    assert result.status == "failed"
    with db_session_factory() as session:
        turn = session.scalar(select(CognitionTurn))
        assert "unsupported_operations_for_frozen_contract" in turn.validation_errors
        assert session.scalar(select(func.count()).select_from(Goal)) == 0
        assert session.get(AttentionState, born.individual_id) is None


@pytest.mark.parametrize("reference_source", ["focus", "wake"])
def test_explicit_old_evidence_references_are_retrieved(
    owner, born, db_session_factory, reference_source
):
    from cognition.db.models import Wake
    from cognition.db.models.cognition import AttentionState
    from cognition.runtime.cognition import CognitionRuntime
    from cognition.stores.cognition import record_execution_event

    with db_session_factory.begin() as session:
        old_id = record_execution_event(
            session, born.individual_id, None, "test.old", NOW, {"fact": "remember me"}
        )
        for ordinal in range(40):
            record_execution_event(
                session, born.individual_id, None, "test.recent", NOW, {"n": ordinal}
            )
        refs = [{"kind": "event", "id": str(old_id)}]
        if reference_source == "focus":
            session.add(
                AttentionState(
                    individual_id=born.individual_id,
                    current_focus={"summary": "Recall", "refs": refs},
                    last_cycle_id=None,
                    last_cognition_at=None,
                    revision=1,
                )
            )
        else:
            session.get(Wake, born.bootstrap_wake_id).context_refs = refs

    def inspect_context(request):
        assert any(
            section.name == f"event:{old_id}" for section in request.context_sections
        )
        return response(request)

    assert (
        CognitionRuntime(
            owner,
            born.individual_id,
            ScriptedModelAdapter([inspect_context]),
            FakeClock(NOW),
        )
        .run_once()
        .status
        == "completed"
    )


@pytest.mark.parametrize("invalid_text", ["has\x00nul", "unpaired\ud800surrogate"])
def test_provider_text_incompatible_with_postgres_is_a_bounded_failure(
    owner, born, db_session_factory, invalid_text
):
    from cognition.db.models.cognition import ModelInvocation
    from cognition.runtime.cognition import CognitionRuntime

    model = ScriptedModelAdapter(
        [
            lambda request: response(request, focus=invalid_text),
            lambda request: response(request, focus=invalid_text),
        ]
    )
    result = CognitionRuntime(
        owner, born.individual_id, model, FakeClock(NOW)
    ).run_once()
    assert result.status == "failed"
    assert result.reason == "attempt_limit"
    with db_session_factory() as session:
        attempts = session.scalars(select(ModelInvocation)).all()
        assert len(attempts) == 2
        assert all(attempt.error_code == "invalid_result" for attempt in attempts)
        assert all(attempt.result_json is None for attempt in attempts)


def test_committed_decision_recovery_never_resamples(owner, born, db_session_factory):
    from cognition.db.models.cognition import AttentionState, CognitionTurn
    from cognition.runtime.cognition import CognitionRuntime

    model = ScriptedModelAdapter([lambda request: response(request, focus="D1")])

    def fail_apply(conn, cursor, statement, parameters, context, executemany):
        if "INSERT INTO attention_state" in statement:
            raise RuntimeError("application crash")

    event.listen(owner.connection, "before_cursor_execute", fail_apply)
    try:
        with pytest.raises(RuntimeError, match="application crash"):
            CognitionRuntime(
                owner, born.individual_id, model, FakeClock(NOW)
            ).run_once()
    finally:
        event.remove(owner.connection, "before_cursor_execute", fail_apply)
    with db_session_factory() as session:
        turn = session.scalar(select(CognitionTurn))
        assert turn.status == "decided"
        original = turn.decision_json
    replacement_model = ScriptedModelAdapter([])
    result = CognitionRuntime(
        owner, born.individual_id, replacement_model, FakeClock(NOW)
    ).run_once()
    assert result.status == "completed"
    assert replacement_model.requests == ()
    with db_session_factory() as session:
        turn = session.scalar(select(CognitionTurn))
        assert turn.decision_json == original
        assert (
            session.get(AttentionState, born.individual_id).current_focus["summary"]
            == "D1"
        )


def test_continue_is_bounded(owner, born):
    from cognition.runtime.cognition import CognitionRuntime
    from cognition.stores.cognition import CycleLimits

    model = ScriptedModelAdapter(
        [lambda request: response(request, disposition="continue")] * 3
    )
    outcome = CognitionRuntime(
        owner,
        born.individual_id,
        model,
        FakeClock(NOW),
        limits=CycleLimits(max_turns=2),
    ).run_once()
    assert outcome.status == "completed"
    assert outcome.reason == "turn_limit"
    assert len(model.requests) == 2


def test_pause_in_flight_preserves_decision_then_resumes(
    owner, born, db_session_factory
):
    from cognition.policy.governance import AuthenticatedPrincipal
    from cognition.runtime.cognition import CognitionRuntime
    from cognition.runtime.lifecycle import apply_admin_operation

    def pausing_model(request):
        apply_admin_operation(
            db_session_factory,
            born.individual_id,
            AuthenticatedPrincipal("local_os", "test-admin"),
            "pause",
            "test",
            FakeClock(NOW),
        )
        return response(request)

    assert (
        CognitionRuntime(
            owner,
            born.individual_id,
            ScriptedModelAdapter(
                [
                    pausing_model,
                ]
            ),
            FakeClock(NOW),
        )
        .run_once()
        .status
        == "blocked"
    )
    apply_admin_operation(
        db_session_factory,
        born.individual_id,
        AuthenticatedPrincipal("local_os", "test-admin"),
        "resume",
        "test",
        FakeClock(NOW),
    )
    assert (
        CognitionRuntime(
            owner, born.individual_id, ScriptedModelAdapter([]), FakeClock(NOW)
        )
        .run_once()
        .status
        == "completed"
    )


def test_provider_failures_stop_at_attempt_limit(owner, born, db_session_factory):
    from cognition.db.models.cognition import ModelInvocation
    from cognition.runtime.cognition import CognitionRuntime

    model = ScriptedModelAdapter(
        [RuntimeError("SECRET exception"), RuntimeError("secret")]
    )
    result = CognitionRuntime(
        owner, born.individual_id, model, FakeClock(NOW)
    ).run_once()
    assert result.status == "failed"
    with db_session_factory() as session:
        rows = session.scalars(select(ModelInvocation)).all()
        assert len(rows) == 2
        assert all(row.error_code == "provider_error" for row in rows)
        assert all(row.result_json is None for row in rows)


def test_self_schedule_is_clamped_and_idempotent(owner, born, db_session_factory):
    from cognition.db.models import Wake
    from cognition.runtime.cognition import CognitionRuntime

    operation_id = new_id()
    model = ScriptedModelAdapter(
        [
            lambda request: response(
                request,
                wakes=[
                    {
                        "operation_id": operation_id,
                        "not_before": NOW - timedelta(days=1),
                        "purpose": "Check later",
                        "context_refs": [],
                        "coalesce_key": None,
                    }
                ],
            )
        ]
    )
    runtime = CognitionRuntime(owner, born.individual_id, model, FakeClock(NOW))
    assert runtime.run_once().status == "completed"
    assert runtime.run_once().status == "idle"
    with db_session_factory() as session:
        pending = session.scalars(select(Wake).where(Wake.status == "pending")).all()
        assert len(pending) == 1
        assert pending[0].due_at == NOW + timedelta(seconds=1)


def test_provider_error_message_is_not_persisted(owner, born, db_session_factory):
    from cognition.db.models.cognition import ModelInvocation
    from cognition.protocols.model_v1 import ModelError
    from cognition.runtime.cognition import CognitionRuntime

    def failed(request):
        return ModelResultV1(
            schema_version=1,
            status="failed",
            request_id=request.request_id,
            decision=None,
            provider="scripted",
            requested_model="test-model",
            resolved_model=None,
            provider_request_id=None,
            usage=None,
            finish_reason=None,
            error=ModelError(
                code="transport", message="SECRET-test-credential", retryable=False
            ),
        )

    assert (
        CognitionRuntime(
            owner, born.individual_id, ScriptedModelAdapter([failed]), FakeClock(NOW)
        )
        .run_once()
        .status
        == "failed"
    )
    with db_session_factory() as session:
        row = session.scalar(select(ModelInvocation))
        assert "SECRET-test-credential" not in str(row.result_json)


@pytest.mark.parametrize("has_pending_replacement", [False, True])
def test_orphan_claimed_wake_recovers_without_losing_evidence(
    owner,
    born,
    db_session_factory,
    has_pending_replacement,
):
    from cognition.db.models import Wake
    from cognition.db.models.cognition import CycleWake
    from cognition.protocols.wakes_v1 import WakeV1
    from cognition.runtime.cognition import CognitionRuntime
    from cognition.stores.attention import create_or_merge_pending_wake

    with db_session_factory.begin() as session:
        wake = session.get(Wake, born.bootstrap_wake_id)
        wake.status, wake.claimed_at = "claimed", NOW
        if has_pending_replacement:
            create_or_merge_pending_wake(
                session,
                WakeV1(
                    schema_version=1,
                    wake_id=new_id(),
                    individual_id=born.individual_id,
                    kind="external_event",
                    due_at=NOW,
                    purpose="Another cause",
                    cause_event_id=born.genesis_event_id,
                    context_refs=[],
                    coalesce_key="bootstrap",
                ),
            )
    model = ScriptedModelAdapter([response])
    result = CognitionRuntime(
        owner, born.individual_id, model, FakeClock(NOW)
    ).run_once()
    assert result.status == "completed"
    with db_session_factory() as session:
        assert not session.scalars(select(Wake).where(Wake.status == "claimed")).all()
        assert session.scalar(select(func.count()).select_from(CycleWake)) == 1
        original = session.get(Wake, born.bootstrap_wake_id)
        assert original.cause_event_id == born.genesis_event_id


def test_oversized_goal_does_not_hide_smaller_goals_on_restart(owner, born):
    from cognition.protocols.cognition_v1 import GoalOperation, WakeRequest
    from cognition.runtime.cognition import CognitionRuntime

    goal_ids = [new_id() for _ in range(8)]

    def create_goals(request):
        result = response(request)
        result.decision.goal_operations = [
            GoalOperation(
                operation_id=identity,
                op="create",
                goal_id=None,
                title="x" * 16000 if ordinal == 0 else f"Small goal {ordinal}",
                desired_state="Understand",
                project_id=None,
                requested_status=None,
                origin=None,
                rationale="Chosen",
                evidence_refs=[],
            )
            for ordinal, identity in enumerate(goal_ids)
        ]
        result.decision.wake_requests = [
            WakeRequest(
                operation_id=new_id(),
                not_before=NOW + timedelta(seconds=1),
                purpose="Recall",
                context_refs=[],
                coalesce_key=None,
            )
        ]
        return result

    clock = FakeClock(NOW)
    assert (
        CognitionRuntime(
            owner, born.individual_id, ScriptedModelAdapter([create_goals]), clock
        )
        .run_once()
        .status
        == "completed"
    )
    clock.advance(timedelta(seconds=1))
    fresh = ScriptedModelAdapter([response])
    assert (
        CognitionRuntime(owner, born.individual_id, fresh, clock).run_once().status
        == "completed"
    )
    retrieved = {
        ref.id
        for section in fresh.requests[0].context_sections
        for ref in section.refs
        if ref.kind == "goal"
    }
    assert goal_ids[0] not in retrieved
    assert retrieved & set(goal_ids[1:])
