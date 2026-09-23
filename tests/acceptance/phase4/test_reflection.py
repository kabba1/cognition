"""Managed review is bounded attention, never automatic personal establishment."""

from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import event, func, select
from test_heartbeat import ProcessInterrupted, admin, bootstrap, count, terminal_result
from test_heartbeat import clock as clock
from test_heartbeat import state as state
from test_lexical_recall import event as observation_event
from test_lexical_recall import operation, redact, response, run

from cognition.config.recording import reconcile_config
from cognition.db.models.attention import Wake
from cognition.db.models.cognition import (
    AppliedOperation,
    CognitionTurn,
    ContextSnapshot,
    ModelInvocation,
)
from cognition.db.models.development import Interest, Preference, SelfState
from cognition.db.models.evidence import Event
from cognition.db.models.personal import PersonalStateRevision
from cognition.db.models.reflection import ManagedReflectionBatch, ReflectionState
from cognition.db.models.runtime import RuntimeConfigRevision
from cognition.protocols.common import Ref
from cognition.testing.scripted_model import ScriptedModelAdapter

DAY = timedelta(hours=24)


def observation(factory, state, clock, *, source="connector"):
    with factory.begin() as session:
        identity = observation_event(
            session, state, clock, "A separate grounded observation", source=source
        )
    return Ref(kind="event", id=identity)


def reflection(factory, state):
    with factory() as session:
        row = session.get(ReflectionState, state[0].individual_id)
        assert row is not None
        return deepcopy(
            {column.name: getattr(row, column.name) for column in row.__table__.columns}
        )


def managed_wake(factory, state):
    saved = reflection(factory, state)
    with factory() as session:
        return session.get(Wake, saved["managed_wake_id"])


def completed_batches(factory, state):
    with factory() as session:
        return session.scalar(
            select(func.count())
            .select_from(Event)
            .where(
                Event.individual_id == state[0].individual_id,
                Event.event_type == "attention.reflection_completed",
            )
        )


def stage(engine, factory, state, clock, **changes):
    model = ScriptedModelAdapter([lambda request: response(request, **changes)])
    assert run(engine, state, clock, model).status == "completed"
    fresh = ScriptedModelAdapter([])
    assert run(engine, state, clock, fresh).status == "idle"
    assert fresh.requests == ()
    return managed_wake(factory, state)


def establish_interest(identity, *, refs=()):
    return operation(
        "interest",
        op="establish",
        interest_id=identity,
        topic=None,
        summary=None,
        evidence_refs=list(refs),
    )


def validation_errors(factory, request):
    with factory() as session:
        return session.get(CognitionTurn, request.turn_id).validation_errors


def test_birth_and_empty_sleep_do_not_fabricate_reflection(
    db_engine, db_session_factory, state, clock
):
    bootstrap(db_engine, state, clock)
    with db_session_factory() as session:
        assert session.get(ReflectionState, state[0].individual_id) is None
        assert not session.scalars(select(Wake).where(Wake.kind == "reflection")).all()
    assert count(db_session_factory, ManagedReflectionBatch) == 0
    assert count(db_session_factory, PersonalStateRevision) == 0
    assert run(db_engine, state, clock, None).status == "idle"
    assert count(db_session_factory, ManagedReflectionBatch) == 0


def test_mature_managed_target_still_cannot_establish_without_grounding(
    db_engine, db_session_factory, state, clock
):
    candidate = operation("interest")
    wake = stage(
        db_engine, db_session_factory, state, clock, interest_operations=[candidate]
    )
    assert wake.due_at == clock.now() + DAY
    assert wake.context_refs == [
        {"kind": "interest", "id": str(candidate["operation_id"])}
    ]
    clock.set(wake.due_at)
    model = ScriptedModelAdapter(
        [
            lambda request: response(
                request,
                interest_operations=[establish_interest(candidate["operation_id"])],
            )
        ]
    )
    result = run(db_engine, state, clock, model)
    assert result.status == "failed" and result.reason == "decision_rejected"
    assert "development_grounding_required" in validation_errors(
        db_session_factory, model.requests[0]
    )
    with db_session_factory() as session:
        row = session.get(Interest, candidate["operation_id"])
        assert row.status == "candidate" and row.revision == 1
        assert session.get(Wake, wake.wake_id).status == "consumed"
    assert count(db_session_factory, PersonalStateRevision) == 1
    assert reflection(db_session_factory, state)["next_review_at"] == clock.now() + DAY


def test_managed_review_can_establish_three_staged_families_with_separated_anchors(
    db_engine, db_session_factory, state, clock
):
    first = observation(db_session_factory, state, clock)
    candidate = operation("interest", evidence_refs=[first])
    tentative = operation("preference", evidence_refs=[first])
    pending = operation(
        "self_model",
        layer="self_belief",
        proposed_content="I examine original observations",
        evidence_refs=[first],
    )
    wake = stage(
        db_engine,
        db_session_factory,
        state,
        clock,
        interest_operations=[candidate],
        preference_operations=[tentative],
        self_model_operations=[pending],
    )
    assert len(wake.context_refs) == 3
    clock.set(wake.due_at)
    second = observation(db_session_factory, state, clock, source="capability")
    promoted = operation(
        "preference",
        op="establish",
        preference_id=tentative["operation_id"],
        context=None,
        statement=None,
        evidence_refs=[second],
    )
    inferred = operation(
        "self_model",
        layer="self_belief",
        proposed_content=pending["proposed_content"],
        evidence_refs=[second],
    )
    model = ScriptedModelAdapter(
        [
            lambda request: response(
                request,
                interest_operations=[
                    establish_interest(candidate["operation_id"], refs=[second])
                ],
                preference_operations=[promoted],
                self_model_operations=[inferred],
            )
        ]
    )
    assert run(db_engine, state, clock, model).status == "completed"
    assert len(model.requests) == 1
    with db_session_factory() as session:
        for table, identity in (
            (Interest, candidate["operation_id"]),
            (Preference, tentative["operation_id"]),
        ):
            row = session.get(table, identity)
            assert row.status == "established" and row.revision == 2
            assert {Ref.model_validate(ref).id for ref in row.evidence_refs} == {
                first.id,
                second.id,
            }
        row = session.get(SelfState, pending["operation_id"])
        assert row.content == {"value": pending["proposed_content"]}
        assert row.pending_content is None and row.pending_not_before is None
        assert {Ref.model_validate(ref).id for ref in row.evidence_refs} == {
            first.id,
            second.id,
        }
    saved = reflection(db_session_factory, state)
    assert saved["managed_wake_id"] is None and not saved["materialization_pending"]
    assert saved["last_completed_cycle_id"] == model.requests[0].cycle_id
    assert completed_batches(db_session_factory, state) == 1


def test_grounded_ninth_target_cannot_piggyback_on_the_selected_eight(
    db_engine, db_session_factory, state, clock
):
    first = observation(db_session_factory, state, clock)
    candidates = [operation("interest", evidence_refs=[first]) for _ in range(9)]
    wake = stage(
        db_engine, db_session_factory, state, clock, interest_operations=candidates
    )
    selected = {Ref.model_validate(ref).id for ref in wake.context_refs}
    assert len(selected) == 8
    omitted = next(
        item["operation_id"]
        for item in candidates
        if item["operation_id"] not in selected
    )
    clock.set(wake.due_at)
    second = observation(db_session_factory, state, clock, source="capability")
    model = ScriptedModelAdapter(
        [
            lambda request: response(
                request,
                interest_operations=[establish_interest(omitted, refs=[second])],
            )
        ]
    )
    result = run(db_engine, state, clock, model)
    assert result.status == "failed" and result.reason == "decision_rejected"
    errors = validation_errors(db_session_factory, model.requests[0])
    assert "development_reflection_required" in errors
    assert "development_grounding_required" not in errors
    with db_session_factory() as session:
        assert all(
            row.status == "candidate" and row.revision == 1
            for row in session.scalars(select(Interest))
        )
    assert count(db_session_factory, PersonalStateRevision) == 9


def test_decided_promotion_recovers_without_model_after_creation_content_redaction(
    db_engine, db_session_factory, state, clock
):
    first = observation(db_session_factory, state, clock)
    candidate = operation("interest", evidence_refs=[first])
    wake = stage(
        db_engine, db_session_factory, state, clock, interest_operations=[candidate]
    )
    clock.set(wake.due_at)
    second = observation(db_session_factory, state, clock, source="capability")
    model = ScriptedModelAdapter(
        [
            lambda request: response(
                request,
                interest_operations=[
                    establish_interest(candidate["operation_id"], refs=[second])
                ],
            )
        ]
    )

    def crash_after_update(*args):
        raise ProcessInterrupted()

    event.listen(Interest, "after_update", crash_after_update)
    try:
        with pytest.raises(ProcessInterrupted):
            run(db_engine, state, clock, model)
    finally:
        event.remove(Interest, "after_update", crash_after_update)
    request = model.requests[0]
    with db_session_factory.begin() as session:
        turn = session.get(CognitionTurn, request.turn_id)
        assert turn.status == "decided"
        retained = deepcopy((turn.decision_json, turn.decision_hash))
        snapshot = session.scalar(
            select(ContextSnapshot).where(ContextSnapshot.turn_id == request.turn_id)
        )
        frozen = deepcopy((snapshot.request_json, snapshot.content_hash))
        batch = session.get(ManagedReflectionBatch, wake.wake_id)
        original_batch = deepcopy((batch.target_metadata, batch.content_hash))
        assert session.get(Interest, candidate["operation_id"]).status == "candidate"
        assert session.get(Wake, wake.wake_id).status == "claimed"
        redact(session, state, clock, wake.cause_event_id)
    assert completed_batches(db_session_factory, state) == 0
    assert run(db_engine, state, clock, None).status == "completed"
    with db_session_factory() as session:
        turn = session.get(CognitionTurn, request.turn_id)
        assert turn.status == "applied"
        assert (turn.decision_json, turn.decision_hash) == retained
        snapshot = session.scalar(
            select(ContextSnapshot).where(ContextSnapshot.turn_id == request.turn_id)
        )
        assert (snapshot.request_json, snapshot.content_hash) == frozen
        batch = session.get(ManagedReflectionBatch, wake.wake_id)
        assert (batch.target_metadata, batch.content_hash) == original_batch
        row = session.get(Interest, candidate["operation_id"])
        assert row.status == "established" and row.revision == 2
    saved = reflection(db_session_factory, state)
    assert saved["last_completed_cycle_id"] == request.cycle_id
    assert run(db_engine, state, clock, None).status == "idle"
    assert reflection(db_session_factory, state) == saved
    assert completed_batches(db_session_factory, state) == 1
    assert count(db_session_factory, ModelInvocation) == 2
    assert count(db_session_factory, AppliedOperation) == 2


@pytest.mark.parametrize("status", ["refused", "failed"])
def test_inflight_pause_accounts_batch_once_and_resume_defers_review_without_model(
    db_engine, db_session_factory, state, clock, status
):
    candidate = operation("interest")
    wake = stage(
        db_engine, db_session_factory, state, clock, interest_operations=[candidate]
    )
    clock.set(wake.due_at)

    def pausing_provider(request):
        clock.advance(timedelta(hours=2))
        admin(db_session_factory, state, clock, "pause")
        return terminal_result(request, status)

    model = ScriptedModelAdapter([pausing_provider])
    assert run(db_engine, state, clock, model).status == "failed"
    saved = reflection(db_session_factory, state)
    assert saved["next_review_at"] == clock.now() + DAY
    assert saved["materialization_pending"] and saved["managed_wake_id"] == wake.wake_id
    assert saved["interest_cursor"] == candidate["operation_id"]
    with db_session_factory() as session:
        assert session.get(Wake, wake.wake_id).status == "consumed"
        assert not session.scalars(
            select(Wake).where(Wake.kind == "reflection", Wake.status == "pending")
        ).all()
    assert run(db_engine, state, clock, None).reason == "lifecycle_or_governance"
    admin(db_session_factory, state, clock, "resume")
    fresh = ScriptedModelAdapter([])
    assert run(db_engine, state, clock, fresh).status == "idle"
    assert fresh.requests == ()
    resumed = reflection(db_session_factory, state)
    assert resumed["next_review_at"] == saved["next_review_at"]
    assert resumed["last_completed_cycle_id"] == saved["last_completed_cycle_id"]
    assert not resumed["materialization_pending"]
    assert managed_wake(db_session_factory, state).due_at == saved["next_review_at"]
    assert completed_batches(db_session_factory, state) == 1
    assert count(db_session_factory, ModelInvocation) == 2


def test_unresolved_candidate_gets_one_later_batch_after_long_silence(
    db_engine, db_session_factory, state, clock
):
    candidate = operation("interest")
    wake = stage(
        db_engine, db_session_factory, state, clock, interest_operations=[candidate]
    )
    clock.set(wake.due_at)
    for lapse in (timedelta(0), DAY * 10):
        clock.advance(lapse)
        fresh = ScriptedModelAdapter([response])
        assert run(db_engine, state, clock, fresh).status == "completed"
        assert len(fresh.requests) == 1
        following = managed_wake(db_session_factory, state)
        assert following.due_at == clock.now() + DAY
        assert following.context_refs == wake.context_refs
        with db_session_factory() as session:
            assert (
                len(
                    session.scalars(
                        select(Wake).where(
                            Wake.kind == "reflection", Wake.status == "pending"
                        )
                    ).all()
                )
                == 1
            )
        assert run(db_engine, state, clock, None).status == "idle"
    assert completed_batches(db_session_factory, state) == 2
    assert count(db_session_factory, ManagedReflectionBatch) == 3
    assert count(db_session_factory, PersonalStateRevision) == 1


def test_missing_configuration_finishes_managed_batch_and_later_materializes(
    db_engine, db_session_factory, state, clock
):
    candidate = operation("interest")
    wake = stage(
        db_engine, db_session_factory, state, clock, interest_operations=[candidate]
    )
    clock.set(wake.due_at)
    with db_session_factory.begin() as session:
        session.get(
            RuntimeConfigRevision, state[0].config_revision_id
        ).superseded_at = clock.now()
    outcome = run(db_engine, state, clock, None)
    assert outcome.status == "failed" and outcome.reason == "missing_configuration"
    saved = reflection(db_session_factory, state)
    assert saved["materialization_pending"] and saved["managed_wake_id"] == wake.wake_id
    assert saved["next_review_at"] == clock.now() + DAY
    assert count(db_session_factory, ModelInvocation) == 1
    changed = state[1].model_copy(deep=True)
    changed.model.max_output_tokens += 1
    reconcile_config(db_session_factory, state[0].individual_id, changed, clock)
    assert run(db_engine, state, clock, None).status == "idle"
    assert managed_wake(db_session_factory, state).due_at == saved["next_review_at"]
    assert completed_batches(db_session_factory, state) == 1
    assert count(db_session_factory, ModelInvocation) == 1
