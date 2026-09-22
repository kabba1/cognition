"""Personal interpretations survive restart with atomic, evidence-linked effects."""

import importlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, func, select

from cognition.config.loader import load_config
from cognition.db.checks import check_database
from cognition.db.models import Event, Individual, Wake
from cognition.db.models.cognition import (
    AppliedOperation,
    AttentionState,
    CognitionTurn,
    ModelInvocation,
)
from cognition.protocols.cognition_v1 import CognitionDecisionV1
from cognition.protocols.common import Ref, new_id
from cognition.protocols.model_v1 import ModelResultV1
from cognition.runtime.birth import BirthInput, birth
from cognition.runtime.cognition import CognitionRuntime
from cognition.runtime.ownership import acquire_runtime_ownership
from cognition.testing.clock import FakeClock
from cognition.testing.scripted_model import ScriptedModelAdapter

NOW = datetime(2026, 9, 22, 21, tzinfo=UTC)


class PersonalWriteInterrupted(BaseException):
    """Test-side process termination after PostgreSQL accepted a personal write."""


def personal_models():
    assert importlib.util.find_spec("cognition.db.models.personal") is not None
    return importlib.import_module("cognition.db.models.personal")


def make_birth(factory, clock):
    config = load_config(Path(__file__).parents[2] / "fixtures/config/valid.toml")
    config.runtime.individual_id = new_id()
    config.attention.context_budget_tokens = 64000
    return birth(
        factory,
        BirthInput(
            individual_id=config.runtime.individual_id,
            birth_name="Personal recovery individual",
            founding_orientation="Make revisable interpretations from evidence",
            creator_provenance={"source": "acceptance"},
            admin_authn_provider="local_os",
            admin_subject="acceptance-admin",
            config=config,
            runtime_version="acceptance",
        ),
        clock,
    )


@pytest.fixture
def clock():
    return FakeClock(NOW)


@pytest.fixture
def born(db_session_factory, clock):
    return make_birth(db_session_factory, clock)


def acquire_owner(engine, individual_id, clock):
    return acquire_runtime_ownership(
        engine,
        individual_id,
        clock=clock,
        host_id="personal-acceptance",
        process_id=33,
        runtime_version="acceptance",
    )


def result_for(request, **changes):
    values = dict(
        schema_version=1,
        decision_id=new_id(),
        cycle_id=request.cycle_id,
        turn_id=request.turn_id,
        disposition="sleep",
        rationale_summary="D1 personal state",
        current_focus={"summary": "Follow evidence carefully", "refs": []},
        goal_operations=[],
        commitment_operations=[],
        belief_operations=[],
        episode_operations=[],
        interest_operations=[],
        preference_operations=[],
        self_model_operations=[],
        action_requests=[],
        wake_requests=[],
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


def goal(*, operation_id=None, evidence_refs=()):
    return dict(
        operation_id=operation_id or new_id(),
        op="create",
        goal_id=None,
        title="Investigate the durable record",
        desired_state="Understand the evidence",
        project_id=None,
        requested_status=None,
        origin=None,
        rationale="A self-generated inquiry",
        evidence_refs=list(evidence_refs),
    )


def personal_operations(born):
    evidence = Ref(kind="event", id=born.genesis_event_id)
    return dict(
        goal_operations=[goal()],
        commitment_operations=[
            dict(
                operation_id=new_id(),
                op="create",
                commitment_id=None,
                counterparty_entity_id=None,
                title="Review the record",
                terms="Review the supplied evidence",
                requested_status="active",
                due_at=NOW + timedelta(days=1),
                rationale="Explicitly adopted review",
                evidence_refs=[evidence],
            )
        ],
        belief_operations=[
            dict(
                operation_id=new_id(),
                op="create",
                belief_id=None,
                proposition="The durable record includes a birth event",
                subject_entity_id=None,
                topic="continuity",
                requested_status="accepted",
                supporting_evidence=[evidence],
                contradicting_evidence=[],
                supersedes_belief_id=None,
                rationale="Interpretation grounded in this record",
            )
        ],
        episode_operations=[
            dict(
                operation_id=new_id(),
                op="create",
                summary="Reviewed the birth record",
                starts_at=NOW,
                ends_at=NOW,
                evidence_refs=[evidence],
                entity_refs=[],
                project_refs=[],
                salience_factors=["self_change"],
            )
        ],
    )


def wake(*, operation_id=None):
    return dict(
        operation_id=operation_id or new_id(),
        not_before=NOW + timedelta(seconds=5),
        purpose="Revisit persisted interpretations",
        context_refs=[],
        coalesce_key=None,
    )


def count_owned(session, model, individual_id):
    return session.scalar(
        select(func.count())
        .select_from(model)
        .where(model.individual_id == individual_id)
    )


def assert_no_personal_effects(session, individual_id):
    models = personal_models()
    for model in (
        models.Goal,
        models.Commitment,
        models.Belief,
        models.Episode,
        models.PersonalStateRevision,
        AppliedOperation,
    ):
        assert count_owned(session, model, individual_id) == 0
    assert session.get(AttentionState, individual_id) is None
    assert (
        session.scalar(
            select(func.count())
            .select_from(Wake)
            .where(
                Wake.individual_id == individual_id,
                Wake.kind == "self_scheduled",
            )
        )
        == 0
    )


def assert_four_personal_effects(session, born, operations):
    models = personal_models()
    for model, family, id_name in (
        (models.Goal, "goal_operations", "goal_id"),
        (models.Commitment, "commitment_operations", "commitment_id"),
        (models.Belief, "belief_operations", "belief_id"),
        (models.Episode, "episode_operations", "episode_id"),
    ):
        operation_id = operations[family][0]["operation_id"]
        row = session.get(model, operation_id)
        assert row is not None and row.individual_id == born.individual_id
        assert getattr(row, id_name) == operation_id
        assert row.revision == 1
        assert count_owned(session, model, born.individual_id) == 1
    stored_goal = session.get(
        models.Goal, operations["goal_operations"][0]["operation_id"]
    )
    assert stored_goal.status == "active" and stored_goal.origin == "self_generated"
    assert stored_goal.title == "Investigate the durable record"
    belief = session.get(
        models.Belief, operations["belief_operations"][0]["operation_id"]
    )
    assert belief.status == "accepted"
    assert belief.proposition == "The durable record includes a birth event"
    expected_evidence = [{"kind": "event", "id": str(born.genesis_event_id)}]
    assert belief.supporting_evidence == expected_evidence
    assert belief.contradicting_evidence == []
    episode = session.get(
        models.Episode, operations["episode_operations"][0]["operation_id"]
    )
    assert episode.summary == "Reviewed the birth record"
    assert episode.starts_at == episode.ends_at == NOW
    assert episode.evidence_refs == expected_evidence
    assert episode.salience_factors == ["self_change"]
    commitment = session.get(
        models.Commitment, operations["commitment_operations"][0]["operation_id"]
    )
    assert commitment.status == "active"
    assert commitment.terms == "Review the supplied evidence"
    assert commitment.due_at == NOW + timedelta(days=1)
    histories = session.scalars(
        select(models.PersonalStateRevision).where(
            models.PersonalStateRevision.individual_id == born.individual_id,
        )
    ).all()
    assert len(histories) == 4
    for history in histories:
        assert history.before_json is None
        assert history.after_json and history.revision == 1
        assert (
            session.get(AppliedOperation, history.operation_id).turn_id
            == history.turn_id
        )
        assert session.get(Event, history.event_id).source_kind == "model"
    assert {(history.object_kind, history.object_id) for history in histories} == {
        ("goal", operations["goal_operations"][0]["operation_id"]),
        ("commitment", operations["commitment_operations"][0]["operation_id"]),
        ("belief", operations["belief_operations"][0]["operation_id"]),
        ("episode", operations["episode_operations"][0]["operation_id"]),
    }


def test_birth_does_not_invent_personal_state(born, db_session_factory):
    models = personal_models()
    with db_session_factory() as session:
        for model in (
            models.Entity,
            models.Project,
            models.Goal,
            models.Commitment,
            models.Belief,
            models.Episode,
            models.PersonalStateRevision,
        ):
            assert count_owned(session, model, born.individual_id) == 0


def test_four_personal_families_apply_with_provenance_and_immutable_genesis(
    db_engine,
    db_session_factory,
    born,
    clock,
):
    operations = personal_operations(born)
    with db_session_factory() as session:
        identity = session.get(Individual, born.individual_id)
        genesis = (
            identity.birth_name,
            identity.founding_orientation,
            identity.creator_provenance,
        )
    model = ScriptedModelAdapter([lambda request: result_for(request, **operations)])
    with acquire_owner(db_engine, born.individual_id, clock) as owner:
        outcome = CognitionRuntime(owner, born.individual_id, model, clock).run_once()
    assert outcome.status == "completed"
    assert len(model.requests) == 1
    with db_session_factory() as session:
        assert_four_personal_effects(session, born, operations)
        assert count_owned(session, AppliedOperation, born.individual_id) == 4
        assert session.get(Wake, born.bootstrap_wake_id).status == "consumed"
        identity = session.get(Individual, born.individual_id)
        assert (
            identity.birth_name,
            identity.founding_orientation,
            identity.creator_provenance,
        ) == genesis
        report = check_database(session)
        assert report.healthy, report.findings


@pytest.mark.parametrize("table", ["goals", "personal_state_revisions"])
def test_partial_personal_write_rolls_back_and_committed_d1_is_never_resampled(
    db_engine,
    db_session_factory,
    born,
    clock,
    table,
):
    operations = personal_operations(born)
    d1 = []

    def respond(request):
        result = result_for(request, **operations, wake_requests=[wake()])
        d1.append(result.decision)
        return result

    first = ScriptedModelAdapter([respond])
    reached = []

    def interrupt(conn, cursor, statement, parameters, context, executemany):
        if f"INSERT INTO {table} " in statement:
            reached.append(statement)
            raise PersonalWriteInterrupted("personal write accepted before commit")

    with acquire_owner(db_engine, born.individual_id, clock) as owner:
        connection = owner.connection
        event.listen(connection, "after_cursor_execute", interrupt)
        try:
            with pytest.raises(PersonalWriteInterrupted):
                CognitionRuntime(owner, born.individual_id, first, clock).run_once()
        finally:
            event.remove(connection, "after_cursor_execute", interrupt)
    assert len(reached) == 1
    with db_session_factory() as session:
        assert_no_personal_effects(session, born.individual_id)
        turn = session.scalar(select(CognitionTurn))
        assert turn.status == "decided"
        assert turn.decision_json == d1[0].model_dump(mode="json")
        assert session.scalar(select(ModelInvocation)).status == "completed"
        assert session.get(Wake, born.bootstrap_wake_id).status == "claimed"
    unused_d2 = ScriptedModelAdapter([lambda request: result_for(request)])
    with acquire_owner(db_engine, born.individual_id, clock) as owner:
        outcome = CognitionRuntime(
            owner, born.individual_id, unused_d2, clock
        ).run_once()
    assert outcome.status == "completed"
    assert unused_d2.requests == ()
    with db_session_factory() as session:
        assert_four_personal_effects(session, born, operations)
        assert count_owned(session, AppliedOperation, born.individual_id) == 5
        turn = session.scalar(select(CognitionTurn))
        assert turn.decision_json == d1[0].model_dump(mode="json")
        assert turn.status == "applied"
        assert session.scalar(select(func.count()).select_from(ModelInvocation)) == 1
        report = check_database(session)
        assert report.healthy, report.findings


def test_new_runtime_reads_personal_state_from_storage_in_its_context(
    db_engine,
    db_session_factory,
    born,
    clock,
):
    operations = personal_operations(born)
    first = ScriptedModelAdapter(
        [
            lambda request: result_for(request, **operations, wake_requests=[wake()]),
        ]
    )
    with acquire_owner(db_engine, born.individual_id, clock) as owner:
        assert (
            CognitionRuntime(owner, born.individual_id, first, clock).run_once().status
            == "completed"
        )
    clock.advance(timedelta(seconds=6))
    second = ScriptedModelAdapter([result_for])
    with acquire_owner(db_engine, born.individual_id, clock) as owner:
        assert (
            CognitionRuntime(owner, born.individual_id, second, clock).run_once().status
            == "completed"
        )
    assert len(second.requests) == 1
    sections = second.requests[0].context_sections
    refs = {(ref.kind, ref.id) for section in sections for ref in section.refs}
    for kind, family in (
        ("goal", "goal_operations"),
        ("commitment", "commitment_operations"),
        ("belief", "belief_operations"),
        ("episode", "episode_operations"),
    ):
        assert (kind, operations[family][0]["operation_id"]) in refs
    personal_sections = [
        s
        for s in sections
        if any(
            ref.kind in {"goal", "commitment", "belief", "episode"} for ref in s.refs
        )
    ]
    personal_text = json.dumps([s.model_dump(mode="json") for s in personal_sections])
    assert "The durable record includes a birth event" in personal_text
    assert "Reviewed the birth record" in personal_text
    assert "Review the supplied evidence" in personal_text
    assert "Investigate the durable record" in personal_text
    assert all(section.category != "control" for section in personal_sections)
    with db_session_factory() as session:
        assert_four_personal_effects(session, born, operations)


@pytest.mark.parametrize("unsupported", ["interest", "action"])
def test_unsupported_family_rejects_otherwise_valid_personal_focus_and_wake_effects(
    db_engine,
    db_session_factory,
    born,
    clock,
    unsupported,
):
    operations = personal_operations(born)
    if unsupported == "interest":
        operations["interest_operations"] = [
            dict(
                operation_id=new_id(),
                op="create_candidate",
                interest_id=None,
                topic="continuity",
                summary="Explore continuity",
                evidence_refs=[],
                rationale="This family is not implemented yet",
            )
        ]
    else:
        operations["action_requests"] = [
            dict(
                operation_id=new_id(),
                capability_key="mail",
                operation="send",
                arguments={},
                intended_effect="Send a message",
                verification_expectation={},
                impetus_refs=[],
                rationale="Not authorized in this increment",
            )
        ]
    model = ScriptedModelAdapter(
        [
            lambda request: result_for(request, **operations, wake_requests=[wake()]),
        ]
    )
    with acquire_owner(db_engine, born.individual_id, clock) as owner:
        outcome = CognitionRuntime(owner, born.individual_id, model, clock).run_once()
    assert outcome.status == "failed" and outcome.reason == "decision_rejected"
    with db_session_factory() as session:
        assert_no_personal_effects(session, born.individual_id)
        turn = session.scalar(select(CognitionTurn))
        assert turn.status == "rejected" and turn.decision_json is not None
        assert turn.validation_errors


@pytest.mark.parametrize(
    "foreign_kind",
    ["goal_evidence", "belief_subject", "commitment_counterparty", "episode_entity"],
)
def test_foreign_evidence_and_entity_links_reject_the_whole_decision(
    db_engine,
    db_session_factory,
    born,
    clock,
    foreign_kind,
):
    foreign = make_birth(db_session_factory, clock)
    assert importlib.util.find_spec("cognition.stores.personal") is not None
    personal = importlib.import_module("cognition.stores.personal")
    with db_session_factory.begin() as session:
        entity_id = personal.create_entity(
            session,
            foreign.individual_id,
            kind="person",
            display_name="Other individual",
            now=NOW,
        )
    operations = personal_operations(born)
    if foreign_kind == "goal_evidence":
        operations["goal_operations"][0]["evidence_refs"] = [
            Ref(kind="event", id=foreign.genesis_event_id)
        ]
    elif foreign_kind == "belief_subject":
        operations["belief_operations"][0]["subject_entity_id"] = entity_id
    elif foreign_kind == "commitment_counterparty":
        operations["commitment_operations"][0]["counterparty_entity_id"] = entity_id
    else:
        operations["episode_operations"][0]["entity_refs"] = [entity_id]
    model = ScriptedModelAdapter([lambda request: result_for(request, **operations)])
    with acquire_owner(db_engine, born.individual_id, clock) as owner:
        outcome = CognitionRuntime(owner, born.individual_id, model, clock).run_once()
    assert outcome.status == "failed" and outcome.reason == "decision_rejected"
    with db_session_factory() as session:
        assert_no_personal_effects(session, born.individual_id)
        assert (
            count_owned(session, personal_models().Entity, foreign.individual_id) == 1
        )


@pytest.mark.parametrize("other_family", ["commitment", "wake"])
def test_duplicate_operation_id_across_families_rejects_all_effects(
    db_engine,
    db_session_factory,
    born,
    clock,
    other_family,
):
    operations = personal_operations(born)
    duplicate_id = operations["goal_operations"][0]["operation_id"]
    wakes = [wake()]
    if other_family == "commitment":
        operations["commitment_operations"][0]["operation_id"] = duplicate_id
    else:
        wakes[0]["operation_id"] = duplicate_id
    model = ScriptedModelAdapter(
        [
            lambda request: result_for(request, **operations, wake_requests=wakes),
        ]
    )
    with acquire_owner(db_engine, born.individual_id, clock) as owner:
        outcome = CognitionRuntime(owner, born.individual_id, model, clock).run_once()
    assert outcome.status == "failed" and outcome.reason == "decision_rejected"
    with db_session_factory() as session:
        assert_no_personal_effects(session, born.individual_id)


def test_previously_applied_wake_operation_id_cannot_be_reused_for_a_goal(
    db_engine,
    db_session_factory,
    born,
    clock,
):
    operation_id = new_id()
    first = ScriptedModelAdapter(
        [
            lambda request: result_for(
                request, wake_requests=[wake(operation_id=operation_id)]
            ),
        ]
    )
    with acquire_owner(db_engine, born.individual_id, clock) as owner:
        assert (
            CognitionRuntime(owner, born.individual_id, first, clock).run_once().status
            == "completed"
        )
    clock.advance(timedelta(seconds=6))
    second = ScriptedModelAdapter(
        [
            lambda request: result_for(
                request, goal_operations=[goal(operation_id=operation_id)]
            ),
        ]
    )
    with acquire_owner(db_engine, born.individual_id, clock) as owner:
        outcome = CognitionRuntime(owner, born.individual_id, second, clock).run_once()
    assert outcome.status == "failed" and outcome.reason == "decision_rejected"
    with db_session_factory() as session:
        assert count_owned(session, personal_models().Goal, born.individual_id) == 0
        assert (
            count_owned(
                session, personal_models().PersonalStateRevision, born.individual_id
            )
            == 0
        )
        assert count_owned(session, AppliedOperation, born.individual_id) == 1
        assert session.get(AppliedOperation, operation_id).kind == "wake_request"
