"""Real PostgreSQL recovery at committed and interrupted cognition boundaries."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError

from cognition.config.loader import load_config
from cognition.db.checks import check_database
from cognition.db.locks import OwnershipLostError
from cognition.db.models import Event, Individual, Wake
from cognition.db.models.cognition import (
    AppliedOperation,
    AttentionState,
    CognitionCycle,
    CognitionTurn,
    ContextSnapshot,
    CycleWake,
    ModelInvocation,
)
from cognition.policy.governance import AuthenticatedPrincipal
from cognition.protocols.cognition_v1 import CognitionDecisionV1
from cognition.protocols.common import new_id
from cognition.protocols.model_v1 import ModelResultV1
from cognition.protocols.wakes_v1 import WakeV1
from cognition.runtime.birth import BirthInput, birth
from cognition.runtime.cognition import CognitionRuntime
from cognition.runtime.lifecycle import apply_admin_operation
from cognition.runtime.ownership import acquire_runtime_ownership
from cognition.stores.attention import create_or_merge_pending_wake
from cognition.stores.cognition import CycleLimits
from cognition.stores.configuration import get_active_config, replace_config_revision
from cognition.stores.governance import update_governance
from cognition.testing.clock import FakeClock
from cognition.testing.scripted_model import ScriptedModelAdapter

NOW = datetime(2026, 9, 22, 20, tzinfo=UTC)


class ProcessInterrupted(BaseException):
    """A process boundary must escape the provider-error retry handler."""


def result_for(request, *, focus="D1", disposition="sleep", wakes=(), **changes):
    values = dict(
        schema_version=1,
        decision_id=new_id(),
        cycle_id=request.cycle_id,
        turn_id=request.turn_id,
        disposition=disposition,
        rationale_summary="Acceptance decision",
        current_focus={"summary": focus, "refs": []},
        goal_operations=[],
        commitment_operations=[],
        belief_operations=[],
        episode_operations=[],
        interest_operations=[],
        preference_operations=[],
        self_model_operations=[],
        action_requests=[],
        wake_requests=list(wakes),
    )
    values.update(changes)
    return ModelResultV1(
        schema_version=1,
        status="completed",
        request_id=request.request_id,
        decision=CognitionDecisionV1(**values),
        provider="scripted",
        requested_model="test-model",
        resolved_model="test-model",
        provider_request_id=None,
        usage=None,
        finish_reason="completed",
        error=None,
    )


def wake_request(*, operation_id=None, coalesce_key=None):
    return {
        "operation_id": operation_id or new_id(),
        "not_before": NOW + timedelta(minutes=10),
        "purpose": "Revisit the evidence",
        "context_refs": [],
        "coalesce_key": coalesce_key,
    }


@pytest.fixture
def clock():
    return FakeClock(NOW)


@pytest.fixture
def born(db_session_factory, clock):
    config = load_config(Path(__file__).parents[2] / "fixtures/config/valid.toml")
    config.runtime.individual_id = new_id()
    return birth(
        db_session_factory,
        BirthInput(
            individual_id=config.runtime.individual_id,
            birth_name="Recovery individual",
            founding_orientation="Explore carefully",
            creator_provenance={"source": "acceptance"},
            admin_authn_provider="local_os",
            admin_subject="recovery-admin",
            config=config,
            runtime_version="acceptance",
        ),
        clock,
    )


def acquire_owner(db_engine, individual_id, clock):
    return acquire_runtime_ownership(
        db_engine,
        individual_id,
        clock=clock,
        host_id="acceptance",
        process_id=22,
        runtime_version="acceptance",
    )


@pytest.fixture
def owner(db_engine, born, clock):
    with acquire_owner(db_engine, born.individual_id, clock) as ownership:
        yield ownership


@contextmanager
def interrupt_sql(connection, fragment, *, after=False):
    """Inject a process interruption only after reaching a genuine SQL boundary."""
    reached = []

    def interrupt(conn, cursor, statement, parameters, context, executemany):
        if fragment in statement:
            reached.append(statement)
            raise ProcessInterrupted(fragment)

    hook = "after_cursor_execute" if after else "before_cursor_execute"
    event.listen(connection, hook, interrupt)
    try:
        with pytest.raises(ProcessInterrupted, match=fragment):
            yield
        assert reached, "The intended transaction boundary was never exercised"
    finally:
        event.remove(connection, hook, interrupt)


def assert_healthy(factory):
    with factory() as session:
        report = check_database(session)
        assert report.healthy, report.findings


def assert_no_effects(session, individual_id):
    assert session.get(AttentionState, individual_id) is None
    assert session.scalar(select(func.count()).select_from(AppliedOperation)) == 0
    assert (
        session.scalar(
            select(func.count()).select_from(Wake).where(Wake.kind == "self_scheduled")
        )
        == 0
    )


def test_unrecorded_result_may_be_resampled_with_identical_persisted_request(
    owner, born, clock, db_session_factory
):
    unrecorded = []

    def interrupted_model(request):
        unrecorded.append(result_for(request, focus="D1 lost in memory"))
        raise ProcessInterrupted("after inference, before result commit")

    first = ScriptedModelAdapter([interrupted_model])
    with pytest.raises(ProcessInterrupted):
        CognitionRuntime(owner, born.individual_id, first, clock).run_once()
    with db_session_factory() as session:
        invocation = session.scalar(select(ModelInvocation))
        assert invocation.status == "started"
        assert invocation.result_json is None
        turn = session.scalar(select(CognitionTurn))
        assert turn.status == "invoking" and turn.decision_json is None
        snapshot = session.scalar(select(ContextSnapshot))
        original_request = snapshot.request_json
        assert_no_effects(session, born.individual_id)
    assert_healthy(db_session_factory)

    second = ScriptedModelAdapter([lambda request: result_for(request, focus="D2")])
    outcome = CognitionRuntime(owner, born.individual_id, second, clock).run_once()
    assert outcome.status == "completed"
    assert second.requests[0].model_dump(mode="json") == original_request
    assert first.requests == second.requests
    with db_session_factory() as session:
        attempts = session.scalars(
            select(ModelInvocation).order_by(ModelInvocation.attempt_number)
        ).all()
        assert [row.status for row in attempts] == ["abandoned", "completed"]
        assert attempts[0].error_code == "interrupted"
        turn = session.scalar(select(CognitionTurn))
        assert turn.decision_id != unrecorded[0].decision.decision_id
        assert (
            session.get(AttentionState, born.individual_id).current_focus["summary"]
            == "D2"
        )
    assert_healthy(db_session_factory)


def test_snapshot_commit_freezes_request_and_config_before_first_invocation(
    owner, born, clock, db_session_factory
):
    unused = ScriptedModelAdapter([])
    with interrupt_sql(owner.connection, "INSERT INTO model_invocations"):
        CognitionRuntime(owner, born.individual_id, unused, clock).run_once()
    assert unused.requests == ()
    with db_session_factory.begin() as session:
        snapshot = session.scalar(select(ContextSnapshot))
        original_request = snapshot.request_json
        original_config_id = snapshot.config_revision_id
        assert session.scalar(select(func.count()).select_from(ModelInvocation)) == 0
        config = get_active_config(session, born.individual_id).sanitized_config
        config.model.requested_model = "replacement-model"
        config.model.max_output_tokens = 256
        replacement, changed = replace_config_revision(
            session, born.individual_id, config, NOW + timedelta(seconds=1)
        )
        assert changed and replacement.config_revision_id != original_config_id
    clock.advance(timedelta(seconds=2))
    model = ScriptedModelAdapter([result_for])
    assert (
        CognitionRuntime(owner, born.individual_id, model, clock).run_once().status
        == "completed"
    )
    assert model.requests[0].model_dump(mode="json") == original_request
    assert model.requests[0].output_token_budget == 1024
    with db_session_factory() as session:
        snapshot = session.scalar(select(ContextSnapshot))
        assert snapshot.config_revision_id == original_config_id
        assert snapshot.requested_model == "test-model"
    assert_healthy(db_session_factory)


def test_claim_commit_recovers_original_wake_batch_after_context_interruption(
    owner, born, clock, db_session_factory
):
    model = ScriptedModelAdapter([result_for])
    with interrupt_sql(owner.connection, "INSERT INTO context_snapshots"):
        CognitionRuntime(owner, born.individual_id, model, clock).run_once()
    with db_session_factory() as session:
        cycle_id = session.scalar(select(CognitionCycle.cycle_id))
        assert session.get(Wake, born.bootstrap_wake_id).status == "claimed"
        assert session.scalar(select(CycleWake.cycle_id)) == cycle_id
        assert session.scalar(select(CognitionTurn.status)) == "prepared"
        assert session.scalar(select(func.count()).select_from(ContextSnapshot)) == 0
    assert model.requests == ()
    outcome = CognitionRuntime(owner, born.individual_id, model, clock).run_once()
    assert outcome.cycle_id == cycle_id and outcome.status == "completed"
    assert len(model.requests) == 1
    assert_healthy(db_session_factory)


def test_application_rollback_after_wake_insert_recovers_d1_without_resampling(
    owner, born, clock, db_session_factory, db_engine
):
    wake = wake_request()
    model = ScriptedModelAdapter([lambda request: result_for(request, wakes=[wake])])
    with interrupt_sql(owner.connection, "INSERT INTO wakes", after=True):
        CognitionRuntime(owner, born.individual_id, model, clock).run_once()
    with db_session_factory() as session:
        turn = session.scalar(select(CognitionTurn))
        assert turn.status == "decided"
        stored = turn.decision_json
        assert session.get(Wake, born.bootstrap_wake_id).status == "claimed"
        assert_no_effects(session, born.individual_id)
    assert_healthy(db_session_factory)
    unused = ScriptedModelAdapter([])
    # A BaseException after a cursor write invalidates the physical connection;
    # reacquisition models the process restart and must retain the stored decision.
    owner.close()
    with acquire_owner(db_engine, born.individual_id, clock) as successor:
        runtime = CognitionRuntime(successor, born.individual_id, unused, clock)
        assert runtime.run_once().status == "completed"
        assert runtime.run_once().status == "idle"
    assert unused.requests == ()
    with db_session_factory() as session:
        assert session.scalar(select(CognitionTurn.decision_json)) == stored
        assert (
            session.get(AttentionState, born.individual_id).current_focus["summary"]
            == "D1"
        )
        assert session.get(Wake, wake["operation_id"]).status == "pending"
        assert session.scalar(select(func.count()).select_from(AppliedOperation)) == 1
        assert session.get(Wake, born.bootstrap_wake_id).status == "consumed"
    assert_healthy(db_session_factory)


def test_dead_owner_cannot_record_its_result_and_successor_reuses_context(
    db_engine, born, clock, db_session_factory
):
    old_owner = acquire_owner(db_engine, born.individual_id, clock)

    def kill_owner(request):
        with db_engine.begin() as connection:
            assert connection.scalar(
                text("SELECT pg_terminate_backend(:pid)"),
                {"pid": old_owner.backend_pid},
            )
        return result_for(request, focus="D1 fenced")

    first = ScriptedModelAdapter([kill_owner])
    try:
        with pytest.raises((DBAPIError, OwnershipLostError)):
            CognitionRuntime(old_owner, born.individual_id, first, clock).run_once()
        with pytest.raises(OwnershipLostError):
            _ = old_owner.connection
    finally:
        old_owner.close()
    with db_session_factory() as session:
        assert session.scalar(select(CognitionTurn.decision_json)) is None
        assert session.scalar(select(ModelInvocation.status)) == "started"
        assert_no_effects(session, born.individual_id)
    successor_model = ScriptedModelAdapter(
        [lambda request: result_for(request, focus="D2")]
    )
    with acquire_owner(db_engine, born.individual_id, clock) as successor:
        result = CognitionRuntime(
            successor, born.individual_id, successor_model, clock
        ).run_once()
    assert result.status == "completed"
    assert first.requests == successor_model.requests
    with db_session_factory() as session:
        assert (
            session.get(AttentionState, born.individual_id).current_focus["summary"]
            == "D2"
        )
    assert_healthy(db_session_factory)


@pytest.mark.parametrize("block", ["pause", "inference"])
def test_in_flight_governance_change_records_result_but_defers_all_effects(
    owner, born, clock, db_session_factory, block
):
    def set_block(enabled):
        if block == "pause":
            apply_admin_operation(
                db_session_factory,
                born.individual_id,
                AuthenticatedPrincipal("local_os", "recovery-admin"),
                "pause" if enabled else "resume",
                "Acceptance boundary",
                clock,
            )
        else:
            with db_session_factory.begin() as session:
                update_governance(
                    session, born.individual_id, inference_blocked=enabled
                )

    def blocked_model(request):
        set_block(True)
        return result_for(request, wakes=[wake_request()])

    first = ScriptedModelAdapter([blocked_model])
    assert (
        CognitionRuntime(owner, born.individual_id, first, clock).run_once().status
        == "blocked"
    )
    with db_session_factory() as session:
        assert session.scalar(select(CognitionTurn.status)) == "decided"
        assert session.scalar(select(ModelInvocation.status)) == "completed"
        assert_no_effects(session, born.individual_id)
        assert session.get(Wake, born.bootstrap_wake_id).status == "claimed"
    set_block(False)
    unused = ScriptedModelAdapter([])
    assert (
        CognitionRuntime(owner, born.individual_id, unused, clock).run_once().status
        == "completed"
    )
    assert unused.requests == ()
    assert_healthy(db_session_factory)


@pytest.mark.parametrize(
    "proposal,error",
    [
        ("unsupported", "unsupported_operations"),
        ("cycle", "cycle_id_mismatch"),
        ("turn", "turn_id_mismatch"),
        ("duplicate", "duplicate_operation_id"),
        ("unknown_ref", "unknown_ref"),
    ],
)
def test_invalid_semantics_preserve_exact_decision_and_reject_atomically(
    owner, born, clock, db_session_factory, proposal, error
):
    recorded = []
    scheduled = wake_request()

    def invalid_decision(request):
        changes = {}
        wakes = [scheduled]
        if proposal == "unsupported":
            changes["self_model_operations"] = [
                {
                    "operation_id": new_id(),
                    "layer": "current_identity",
                    "op": "propose_revision",
                    "proposed_content": "Changed identity",
                    "evidence_refs": [],
                    "rationale": "Proposal without authority",
                }
            ]
        elif proposal in ("cycle", "turn"):
            changes[f"{proposal}_id"] = new_id()
        elif proposal == "duplicate":
            wakes.append(dict(scheduled))
        else:
            changes["current_focus"] = {
                "summary": "Unknown",
                "refs": [{"kind": "event", "id": new_id()}],
            }
        result = result_for(request, wakes=wakes, **changes)
        recorded.append(result.decision.model_dump(mode="json"))
        return result

    with db_session_factory() as session:
        genesis = session.get(Individual, born.individual_id).founding_orientation
        genesis_ids = session.scalars(
            select(Event.event_id).where(Event.event_type == "individual.born")
        ).all()
    model = ScriptedModelAdapter([invalid_decision])
    outcome = CognitionRuntime(owner, born.individual_id, model, clock).run_once()
    assert outcome.status == "failed" and outcome.reason == "decision_rejected"
    assert len(model.requests) == 1
    with db_session_factory() as session:
        turn = session.scalar(select(CognitionTurn))
        assert turn.status == "rejected" and error in turn.validation_errors
        assert turn.decision_json == recorded[0]
        assert (
            session.scalar(select(ModelInvocation.result_json))["decision"]
            == recorded[0]
        )
        assert_no_effects(session, born.individual_id)
        assert (
            session.get(Individual, born.individual_id).founding_orientation == genesis
        )
        assert (
            session.scalars(
                select(Event.event_id).where(Event.event_type == "individual.born")
            ).all()
            == genesis_ids
        )
    assert_healthy(db_session_factory)


def test_deadline_ends_continuation_without_another_inference(
    owner, born, clock, db_session_factory
):
    def slow_model(request):
        clock.advance(timedelta(seconds=6))
        return result_for(request, disposition="continue")

    model = ScriptedModelAdapter([slow_model])
    result = CognitionRuntime(
        owner,
        born.individual_id,
        model,
        clock,
        limits=CycleLimits(max_seconds=5),
    ).run_once()
    assert result.status == "completed" and result.reason == "deadline"
    assert len(model.requests) == 1
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(CognitionTurn)) == 1
        assert session.scalar(select(CognitionCycle.deadline_at)) == NOW + timedelta(
            seconds=5
        )
    assert_healthy(db_session_factory)


def test_recovery_cannot_increase_persisted_attempt_limit(
    owner, born, clock, db_session_factory
):
    def interrupt(request):
        raise ProcessInterrupted("invoking")

    with pytest.raises(ProcessInterrupted):
        CognitionRuntime(
            owner,
            born.individual_id,
            ScriptedModelAdapter([interrupt]),
            clock,
            limits=CycleLimits(max_attempts_per_turn=1),
        ).run_once()
    unused = ScriptedModelAdapter([])
    result = CognitionRuntime(
        owner,
        born.individual_id,
        unused,
        clock,
        limits=CycleLimits(max_attempts_per_turn=10),
    ).run_once()
    assert result.status == "failed" and result.reason == "attempt_limit"
    assert unused.requests == ()
    with db_session_factory() as session:
        assert session.scalar(select(ModelInvocation.status)) == "abandoned"
        assert session.scalar(select(CognitionCycle.max_attempts_per_turn)) == 1
    assert_healthy(db_session_factory)


def test_refusal_is_terminal_and_is_not_retried(owner, born, clock, db_session_factory):
    def refuse(request):
        return ModelResultV1(
            schema_version=1,
            status="refused",
            request_id=request.request_id,
            decision=None,
            provider="scripted",
            requested_model="test-model",
            resolved_model=None,
            provider_request_id=None,
            usage=None,
            finish_reason="refusal",
            error=None,
        )

    model = ScriptedModelAdapter([refuse, result_for])
    outcome = CognitionRuntime(owner, born.individual_id, model, clock).run_once()
    assert outcome.status == "failed" and outcome.reason == "refused"
    assert len(model.requests) == 1
    with db_session_factory() as session:
        invocation = session.scalar(select(ModelInvocation))
        assert invocation.status == "failed" and invocation.error_code == "refused"
        assert invocation.result_json["status"] == "refused"
        assert_no_effects(session, born.individual_id)
    assert_healthy(db_session_factory)


def test_bounded_wake_claim_leaves_concurrent_arrival_pending(
    owner, born, clock, db_session_factory
):
    def create_pending(identity, due):
        with db_session_factory.begin() as session:
            create_or_merge_pending_wake(
                session,
                WakeV1(
                    schema_version=1,
                    wake_id=identity,
                    individual_id=born.individual_id,
                    kind="external_event",
                    due_at=due,
                    purpose="New evidence",
                    cause_event_id=None,
                    context_refs=[],
                    coalesce_key=None,
                ),
            )

    first, second, third, concurrent = [new_id() for _ in range(4)]
    for offset, identity in enumerate((first, second, third), start=1):
        create_pending(identity, NOW - timedelta(minutes=4 - offset))

    def arrival(request):
        create_pending(concurrent, NOW - timedelta(minutes=10))
        return result_for(request)

    model = ScriptedModelAdapter([arrival])
    result = CognitionRuntime(
        owner,
        born.individual_id,
        model,
        clock,
        limits=CycleLimits(max_wakes=2),
    ).run_once()
    assert result.status == "completed"
    with db_session_factory() as session:
        consumed = set(
            session.scalars(select(Wake.wake_id).where(Wake.status == "consumed"))
        )
        pending = set(
            session.scalars(select(Wake.wake_id).where(Wake.status == "pending"))
        )
        assert consumed == {first, second}
        assert pending == {third, concurrent, born.bootstrap_wake_id}
        assert set(session.scalars(select(CycleWake.wake_id))) == {first, second}
    assert_healthy(db_session_factory)


def test_mandatory_context_overflow_fails_before_inference(
    owner, born, clock, db_session_factory
):
    with db_session_factory.begin() as session:
        config = get_active_config(session, born.individual_id).sanitized_config
        config.attention.context_budget_tokens = 1
        replace_config_revision(session, born.individual_id, config, NOW)
    unused = ScriptedModelAdapter([])
    outcome = CognitionRuntime(owner, born.individual_id, unused, clock).run_once()
    assert outcome.status == "failed" and outcome.reason == "context_budget"
    assert unused.requests == ()
    with db_session_factory() as session:
        assert session.scalar(select(CognitionTurn.status)) == "failed"
        assert session.scalar(select(func.count()).select_from(ContextSnapshot)) == 0
        assert session.scalar(select(func.count()).select_from(ModelInvocation)) == 0
        assert_no_effects(session, born.individual_id)
        assert session.get(Wake, born.bootstrap_wake_id).status == "consumed"
    assert_healthy(db_session_factory)


def test_crash_after_application_commit_cannot_reapply_or_resample(
    owner, born, clock, db_session_factory, db_engine
):
    wake = wake_request()
    terminal_written = False
    terminal_committed = False
    connection = owner.connection

    def observe_terminal(conn, cursor, statement, parameters, context, executemany):
        nonlocal terminal_written
        if "UPDATE cognition_cycles SET status" in statement:
            terminal_written = True

    def observe_commit(conn):
        nonlocal terminal_committed
        if terminal_written:
            terminal_committed = True

    def crash_next_transaction(
        conn, cursor, statement, parameters, context, executemany
    ):
        if terminal_committed:
            raise ProcessInterrupted("after application commit")

    event.listen(connection, "after_cursor_execute", observe_terminal)
    event.listen(connection, "commit", observe_commit)
    event.listen(connection, "before_cursor_execute", crash_next_transaction)
    try:
        model = ScriptedModelAdapter(
            [lambda request: result_for(request, wakes=[wake])]
        )
        with pytest.raises(ProcessInterrupted, match="after application commit"):
            CognitionRuntime(owner, born.individual_id, model, clock).run_once()
    finally:
        event.remove(connection, "after_cursor_execute", observe_terminal)
        event.remove(connection, "commit", observe_commit)
        event.remove(connection, "before_cursor_execute", crash_next_transaction)
    assert terminal_committed
    with db_session_factory() as session:
        assert session.scalar(select(CognitionCycle.status)) == "completed"
        assert session.scalar(select(CognitionTurn.status)) == "applied"
        assert session.get(Wake, born.bootstrap_wake_id).status == "consumed"
        assert session.get(Wake, wake["operation_id"]).status == "pending"
    owner.close()
    unused = ScriptedModelAdapter([])
    with acquire_owner(db_engine, born.individual_id, clock) as successor:
        assert (
            CognitionRuntime(successor, born.individual_id, unused, clock)
            .run_once()
            .status
            == "idle"
        )
    assert unused.requests == ()
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AppliedOperation)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(Event)
                .where(Event.event_type == "cognition.cycle_finished")
            )
            == 1
        )
    assert_healthy(db_session_factory)


def test_operation_identity_is_global_across_cycles(
    owner, born, clock, db_session_factory
):
    scheduled = wake_request()
    first = ScriptedModelAdapter(
        [lambda request: result_for(request, wakes=[scheduled])]
    )
    assert (
        CognitionRuntime(owner, born.individual_id, first, clock).run_once().status
        == "completed"
    )
    with db_session_factory.begin() as session:
        create_or_merge_pending_wake(
            session,
            WakeV1(
                schema_version=1,
                wake_id=new_id(),
                individual_id=born.individual_id,
                kind="external_event",
                due_at=NOW,
                purpose="Another cycle",
                cause_event_id=None,
                context_refs=[],
                coalesce_key=None,
            ),
        )
    second = ScriptedModelAdapter(
        [
            lambda request: result_for(
                request, focus="D2 must not apply", wakes=[scheduled]
            )
        ]
    )
    outcome = CognitionRuntime(owner, born.individual_id, second, clock).run_once()
    assert outcome.status == "failed" and outcome.reason == "decision_rejected"
    with db_session_factory() as session:
        rejected = session.scalar(
            select(CognitionTurn).where(CognitionTurn.status == "rejected")
        )
        assert "operation_id_already_applied" in rejected.validation_errors
        assert (
            session.get(AttentionState, born.individual_id).current_focus["summary"]
            == "D1"
        )
        assert session.scalar(select(func.count()).select_from(AppliedOperation)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(Wake)
                .where(Wake.kind == "self_scheduled")
            )
            == 1
        )
    assert_healthy(db_session_factory)


def test_coalesced_wake_effects_keep_both_operation_identities(
    owner, born, clock, db_session_factory
):
    first = wake_request(coalesce_key="review")
    second = wake_request(coalesce_key="review")
    second["not_before"] = NOW + timedelta(minutes=5)
    model = ScriptedModelAdapter(
        [lambda request: result_for(request, wakes=[first, second])]
    )
    assert (
        CognitionRuntime(owner, born.individual_id, model, clock).run_once().status
        == "completed"
    )
    with db_session_factory() as session:
        pending = session.scalars(select(Wake).where(Wake.status == "pending")).all()
        assert len(pending) == 1
        assert pending[0].due_at == NOW + timedelta(minutes=5)
        assert set(session.scalars(select(AppliedOperation.operation_id))) == {
            first["operation_id"],
            second["operation_id"],
        }
    assert_healthy(db_session_factory)


@pytest.mark.parametrize("malformed", ["request_id", "result_shape"])
def test_invalid_model_results_are_bounded_without_proposal_effects(
    owner, born, clock, db_session_factory, malformed
):
    class InvalidAdapter:
        calls = 0

        def decide(self, request):
            self.calls += 1
            if malformed == "result_shape":
                return object()
            result = result_for(request, wakes=[wake_request()])
            result.request_id = new_id()
            return result

    model = InvalidAdapter()
    outcome = CognitionRuntime(owner, born.individual_id, model, clock).run_once()
    assert outcome.status == "failed" and outcome.reason == "attempt_limit"
    assert model.calls == 2
    with db_session_factory() as session:
        invocations = session.scalars(select(ModelInvocation)).all()
        assert len(invocations) == 2
        expected = (
            "request_id_mismatch" if malformed == "request_id" else "invalid_result"
        )
        assert all(
            row.status == "failed" and row.error_code == expected for row in invocations
        )
        assert all(row.result_json is None for row in invocations)
        assert session.scalar(select(CognitionTurn.decision_json)) is None
        assert_no_effects(session, born.individual_id)
    assert_healthy(db_session_factory)


def test_wake_identity_collision_is_a_durable_rejection_not_a_recovery_loop(
    owner, born, clock, db_session_factory
):
    # Operation IDs are used as new wake IDs. A valid UUID may already identify
    # a bootstrap/external wake without appearing in applied_operations.
    legitimate = wake_request()
    collision = wake_request(operation_id=born.bootstrap_wake_id)
    model = ScriptedModelAdapter(
        [lambda request: result_for(request, wakes=[legitimate, collision])]
    )
    outcome = CognitionRuntime(owner, born.individual_id, model, clock).run_once()
    assert outcome.status == "failed" and outcome.reason == "decision_rejected"
    with db_session_factory() as session:
        turn = session.scalar(select(CognitionTurn))
        assert turn.status == "rejected" and turn.validation_errors
        assert turn.decision_json["wake_requests"][1]["operation_id"] == str(
            born.bootstrap_wake_id
        )
        assert_no_effects(session, born.individual_id)
        assert session.get(Wake, born.bootstrap_wake_id).kind == "bootstrap"
    unused = ScriptedModelAdapter([])
    assert (
        CognitionRuntime(owner, born.individual_id, unused, clock).run_once().status
        == "idle"
    )
    assert unused.requests == ()
    assert_healthy(db_session_factory)


def test_uncommitted_claim_rolls_back_wake_links_and_initial_turn(
    owner, born, clock, db_session_factory, db_engine
):
    unused = ScriptedModelAdapter([])
    with interrupt_sql(owner.connection, "INSERT INTO cognition_turns", after=True):
        CognitionRuntime(owner, born.individual_id, unused, clock).run_once()
    with db_session_factory() as session:
        assert session.get(Wake, born.bootstrap_wake_id).status == "pending"
        for model in (CognitionCycle, CognitionTurn, CycleWake):
            assert session.scalar(select(func.count()).select_from(model)) == 0
    assert unused.requests == ()
    owner.close()
    with acquire_owner(db_engine, born.individual_id, clock) as successor:
        model = ScriptedModelAdapter([result_for])
        assert (
            CognitionRuntime(successor, born.individual_id, model, clock)
            .run_once()
            .status
            == "completed"
        )
    assert_healthy(db_session_factory)
