"""Development stays tentative until grounded reflection and survives exact replay."""

import importlib
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, func, select, text

from cognition.config.loader import load_config
from cognition.db.checks import check_database
from cognition.db.models import Individual, Wake
from cognition.db.models.cognition import (
    AppliedOperation,
    AttentionState,
    CognitionTurn,
    ModelInvocation,
)
from cognition.db.models.personal import Goal, PersonalStateRevision
from cognition.protocols.cognition_v1 import CognitionDecisionV1
from cognition.protocols.common import Ref, new_id
from cognition.protocols.events_v1 import EventContent, EventEnvelopeV1, EventSource
from cognition.protocols.model_v1 import ModelResultV1
from cognition.protocols.wakes_v1 import WakeV1
from cognition.runtime.birth import BirthInput, birth
from cognition.runtime.cognition import CognitionRuntime
from cognition.runtime.ownership import acquire_runtime_ownership
from cognition.stores.attention import create_or_merge_pending_wake
from cognition.stores.evidence import append_event
from cognition.testing.clock import FakeClock
from cognition.testing.scripted_model import ScriptedModelAdapter

NOW = datetime(2026, 9, 22, 12, tzinfo=UTC)
DAY = timedelta(hours=24)


class DevelopmentWriteInterrupted(BaseException):
    """Process termination after a genuine SQL write, outside provider retry."""


def models():
    assert importlib.util.find_spec("cognition.db.models.development") is not None
    return importlib.import_module("cognition.db.models.development")


@pytest.fixture
def clock():
    return FakeClock(NOW)


@pytest.fixture
def born(db_session_factory, clock):
    config = load_config(Path(__file__).parents[2] / "fixtures/config/valid.toml")
    config.runtime.individual_id = new_id()
    config.attention.context_budget_tokens = 64000
    return birth(
        db_session_factory,
        BirthInput(
            individual_id=config.runtime.individual_id,
            birth_name="Immutable birth name",
            founding_orientation="Reflect on evidence without inventing traits",
            creator_provenance={"source": "acceptance"},
            admin_authn_provider="local_os",
            admin_subject="development-admin",
            config=config,
            runtime_version="acceptance",
        ),
        clock,
    )


def owner(engine, individual_id, clock):
    return acquire_runtime_ownership(
        engine,
        individual_id,
        clock=clock,
        host_id="development-acceptance",
        process_id=31,
        runtime_version="acceptance",
    )


def response(request, **changes):
    fields = dict(
        schema_version=1,
        decision_id=new_id(),
        cycle_id=request.cycle_id,
        turn_id=request.turn_id,
        disposition="sleep",
        rationale_summary="Development D1",
        current_focus=None,
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
    fields.update(changes)
    return ModelResultV1(
        schema_version=1,
        status="completed",
        request_id=request.request_id,
        decision=CognitionDecisionV1(**fields),
        provider="scripted",
        requested_model="test-model",
        resolved_model="test-model",
        provider_request_id=None,
        usage=None,
        finish_reason="completed",
        error=None,
    )


def run(engine, born, clock, **changes):
    adapter = ScriptedModelAdapter([lambda request: response(request, **changes)])
    with owner(engine, born.individual_id, clock) as ownership:
        outcome = CognitionRuntime(
            ownership, born.individual_id, adapter, clock
        ).run_once()
    return outcome, adapter


def attention(factory, born, clock, *, kind="reflection", refs=()):
    with factory.begin() as session:
        return create_or_merge_pending_wake(
            session,
            WakeV1(
                schema_version=1,
                wake_id=new_id(),
                individual_id=born.individual_id,
                kind=kind,
                due_at=clock.now(),
                purpose="Explicit test attention opportunity",
                cause_event_id=None,
                context_refs=list(refs),
                coalesce_key=None,
            ),
        )


def observation(factory, born, instant, *, source="connector"):
    event_id = new_id()
    with factory.begin() as session:
        append_event(
            session,
            EventEnvelopeV1(
                schema_version=1,
                event_id=event_id,
                individual_id=born.individual_id,
                event_type="observation.recorded",
                occurred_at=instant,
                observed_at=instant,
                recorded_at=instant,
                source=EventSource(
                    kind=source, source_id="acceptance-source", binding_id=None
                ),
                actor_entity_id=None,
                causation_event_id=None,
                correlation_id=None,
                subject=None,
                provenance={"source": "controlled observation"},
                runtime_version="acceptance",
                content=EventContent(
                    content_type="application/json",
                    payload={"observation": "A grounded event"},
                    text=None,
                    blob_ref=None,
                    content_hash=None,
                    sensitivity="public",
                    retention_class="history",
                    retain_until=None,
                ),
            ),
        )
    return Ref(kind="event", id=event_id)


def interest(*, op="create_candidate", identity=None, refs=()):
    return dict(
        operation_id=new_id(),
        op=op,
        interest_id=identity,
        topic="field observations" if op == "create_candidate" else None,
        summary="Candidate inquiry into field observations"
        if op == "create_candidate"
        else None,
        evidence_refs=list(refs),
        rationale="A revisable interpretation of experience",
    )


def preference(*, op="create_tentative", identity=None, refs=()):
    return dict(
        operation_id=new_id(),
        op=op,
        preference_id=identity,
        context="Research" if op == "create_tentative" else None,
        statement="Prefer primary observations" if op == "create_tentative" else None,
        evidence_refs=list(refs),
        rationale="Tentative until sustained grounding",
    )


def self_proposal(
    *, content="I value careful observation", layer="self_belief", refs=()
):
    return dict(
        operation_id=new_id(),
        layer=layer,
        op="propose_revision",
        proposed_content=content,
        evidence_refs=list(refs),
        rationale="Inferred self-description requires reflective grounding",
    )


def initial_operations(*, refs=()):
    return dict(
        interest_operations=[interest(refs=refs)],
        preference_operations=[preference(refs=refs)],
        self_model_operations=[self_proposal(refs=refs)],
    )


def goal():
    return dict(
        operation_id=new_id(),
        op="create",
        goal_id=None,
        title="A chosen investigation",
        desired_state="Review grounded observations",
        project_id=None,
        requested_status=None,
        origin=None,
        rationale="A deliberate choice",
        evidence_refs=[],
    )


def promotion(operations, refs):
    return dict(
        interest_operations=[
            interest(
                op="establish",
                identity=operations["interest_operations"][0]["operation_id"],
                refs=refs,
            )
        ],
        preference_operations=[
            preference(
                op="establish",
                identity=operations["preference_operations"][0]["operation_id"],
                refs=refs,
            )
        ],
        self_model_operations=[self_proposal(refs=refs)],
    )


def development_rows(session, individual_id):
    return {
        model.__tablename__: [
            deepcopy(dict(row))
            for row in session.execute(
                select(model.__table__).where(model.individual_id == individual_id),
            ).mappings()
        ]
        for model in (models().Interest, models().Preference, models().SelfState)
    }


def assert_tentative(session, born, operations):
    interest_row = session.get(
        models().Interest, operations["interest_operations"][0]["operation_id"]
    )
    preference_row = session.get(
        models().Preference, operations["preference_operations"][0]["operation_id"]
    )
    self_row = session.scalar(
        select(models().SelfState).where(
            models().SelfState.individual_id == born.individual_id,
            models().SelfState.layer == "self_belief",
        )
    )
    assert (
        interest_row.status == "candidate"
        and interest_row.promotion_not_before == NOW + DAY
    )
    assert (
        preference_row.status == "tentative"
        and preference_row.promotion_not_before == NOW + DAY
    )
    assert (
        interest_row.retirement_not_before
        is preference_row.retirement_not_before
        is None
    )
    assert self_row.content is None
    assert self_row.pending_content == {"value": "I value careful observation"}
    assert self_row.pending_not_before == NOW + DAY


def test_first_utterance_only_creates_candidates_and_pending_inferred_content(
    db_engine,
    db_session_factory,
    born,
    clock,
):
    with db_session_factory() as session:
        assert all(
            rows == []
            for rows in development_rows(session, born.individual_id).values()
        )
    operations = initial_operations()
    outcome, adapter = run(db_engine, born, clock, **operations)
    assert outcome.status == "completed" and len(adapter.requests) == 1
    with db_session_factory() as session:
        assert_tentative(session, born, operations)
        assert (
            session.scalar(select(func.count()).select_from(PersonalStateRevision)) == 3
        )
        report = check_database(session)
        assert report.healthy, report.findings


@pytest.mark.parametrize("wake_kind", ["reflection", "self_scheduled"])
def test_reflection_after_a_day_promotes_using_union_of_separated_anchors(
    db_engine,
    db_session_factory,
    born,
    clock,
    wake_kind,
):
    first = observation(db_session_factory, born, NOW)
    operations = initial_operations(refs=[first])
    assert run(db_engine, born, clock, **operations)[0].status == "completed"
    clock.advance(DAY)
    second = observation(db_session_factory, born, clock.now(), source="capability")
    attention(
        db_session_factory,
        born,
        clock,
        kind=wake_kind,
        refs=[
            Ref(
                kind="interest", id=operations["interest_operations"][0]["operation_id"]
            ),
            Ref(
                kind="preference",
                id=operations["preference_operations"][0]["operation_id"],
            ),
            Ref(
                kind="self_state",
                id=operations["self_model_operations"][0]["operation_id"],
            ),
        ],
    )
    outcome, _ = run(db_engine, born, clock, **promotion(operations, [second]))
    assert outcome.status == "completed"
    expected_ids = {first.id, second.id}
    with db_session_factory() as session:
        for model in (models().Interest, models().Preference):
            row = session.scalar(select(model))
            assert row.status == "established" and row.revision == 2
            assert {
                Ref.model_validate(ref).id for ref in row.evidence_refs
            } == expected_ids
        self_row = session.scalar(select(models().SelfState))
        assert self_row.content == {"value": "I value careful observation"}
        assert self_row.pending_content is None and self_row.pending_not_before is None
        assert {
            Ref.model_validate(ref).id for ref in self_row.evidence_refs
        } == expected_ids
        report = check_database(session)
        assert report.healthy, report.findings


def test_passive_context_reads_and_routine_wakes_do_not_strengthen_development(
    db_engine,
    db_session_factory,
    born,
    clock,
):
    from cognition.stores.personal import personal_context_sections

    operations = initial_operations()
    assert run(db_engine, born, clock, **operations)[0].status == "completed"
    with db_session_factory() as session:
        before = development_rows(session, born.individual_id)
        history_count = session.scalar(
            select(func.count()).select_from(PersonalStateRevision)
        )
    clock.advance(DAY * 2)
    for _ in range(3):
        with db_session_factory.begin() as session:
            session.execute(text("SET TRANSACTION READ ONLY"))
            assert personal_context_sections(session, born.individual_id)
        attention(db_session_factory, born, clock, kind="routine")
        outcome, adapter = run(db_engine, born, clock)
        assert outcome.status == "completed"
        assert any(
            ref.kind == "interest"
            for section in adapter.requests[0].context_sections
            for ref in section.refs
        )
    with db_session_factory() as session:
        assert development_rows(session, born.individual_id) == before
        assert (
            session.scalar(select(func.count()).select_from(PersonalStateRevision))
            == history_count
        )


@pytest.mark.parametrize(
    "bad_grounding", ["duplicate", "own_change", "too_close", "future", "wrong_wake"]
)
def test_ineligible_grounding_cannot_establish_an_interest(
    db_engine,
    db_session_factory,
    born,
    clock,
    bad_grounding,
):
    first = observation(db_session_factory, born, NOW)
    candidate = interest(refs=[first] if bad_grounding != "own_change" else [])
    assert (
        run(db_engine, born, clock, interest_operations=[candidate])[0].status
        == "completed"
    )
    with db_session_factory() as session:
        before = development_rows(session, born.individual_id)
        own_event = session.scalar(
            select(PersonalStateRevision.event_id).where(
                PersonalStateRevision.object_id == candidate["operation_id"],
            )
        )
    clock.advance(DAY)
    if bad_grounding == "duplicate":
        refs = [first, first]
    elif bad_grounding == "own_change":
        refs = [
            Ref(kind="event", id=own_event),
            observation(db_session_factory, born, clock.now()),
        ]
    else:
        timestamp = (
            NOW + timedelta(hours=23) if bad_grounding == "too_close" else clock.now()
        )
        if bad_grounding == "future":
            timestamp += timedelta(seconds=1)
        refs = [first, observation(db_session_factory, born, timestamp)]
    attention(
        db_session_factory,
        born,
        clock,
        kind="external_event" if bad_grounding == "wrong_wake" else "reflection",
    )
    outcome, _ = run(
        db_engine,
        born,
        clock,
        interest_operations=[
            interest(
                op="establish",
                identity=candidate["operation_id"],
                refs=refs,
            )
        ],
    )
    assert outcome.status == "failed" and outcome.reason == "decision_rejected"
    with db_session_factory() as session:
        assert development_rows(session, born.individual_id) == before
        assert (
            session.scalar(select(func.count()).select_from(PersonalStateRevision)) == 1
        )


@pytest.mark.parametrize("layer", ["self_belief", "current_value"])
def test_restarts_preserve_pending_deadline_and_new_proposal_keeps_current(
    db_engine,
    db_session_factory,
    born,
    clock,
    layer,
):
    first = observation(db_session_factory, born, NOW)
    initial = self_proposal(layer=layer, refs=[first])
    assert (
        run(db_engine, born, clock, self_model_operations=[initial])[0].status
        == "completed"
    )
    clock.advance(timedelta(hours=12))
    early = observation(db_session_factory, born, clock.now())
    attention(db_session_factory, born, clock)
    assert (
        run(
            db_engine,
            born,
            clock,
            self_model_operations=[
                self_proposal(layer=layer, refs=[early]),
            ],
        )[0].status
        == "completed"
    )
    with db_session_factory() as session:
        state = session.scalar(select(models().SelfState))
        assert state.pending_not_before == NOW + DAY and state.content is None
        assert {Ref.model_validate(ref).id for ref in state.pending_evidence_refs} == {
            first.id,
            early.id,
        }
    clock.advance(timedelta(hours=12))
    last = observation(db_session_factory, born, clock.now())
    attention(db_session_factory, born, clock)
    assert (
        run(
            db_engine,
            born,
            clock,
            self_model_operations=[
                self_proposal(layer=layer, refs=[last]),
            ],
        )[0].status
        == "completed"
    )
    clock.advance(timedelta(minutes=1))
    attention(db_session_factory, born, clock)
    assert (
        run(
            db_engine,
            born,
            clock,
            self_model_operations=[
                self_proposal(
                    layer=layer,
                    content="A different inferred self-description",
                    refs=[last],
                ),
            ],
        )[0].status
        == "completed"
    )
    with db_session_factory() as session:
        state = session.scalar(select(models().SelfState))
        assert state.content == {"value": "I value careful observation"}
        assert state.pending_content == {
            "value": "A different inferred self-description"
        }
        assert state.pending_not_before == clock.now() + DAY
        assert state.revision == 4


def test_current_identity_presentation_never_rewrites_genesis(
    db_engine,
    db_session_factory,
    born,
    clock,
):
    with db_session_factory() as session:
        before = dict(session.execute(select(Individual.__table__)).mappings().one())
    proposed = {
        "display_name": "Chosen presentation",
        "description": "My present introduction",
    }
    outcome, _ = run(
        db_engine,
        born,
        clock,
        self_model_operations=[
            self_proposal(layer="current_identity", content=proposed),
        ],
    )
    assert outcome.status == "completed"
    with db_session_factory() as session:
        assert (
            dict(session.execute(select(Individual.__table__)).mappings().one())
            == before
        )
        self_row = session.scalar(select(models().SelfState))
        assert self_row.layer == "current_identity" and self_row.content == {
            "value": proposed
        }
        assert self_row.pending_content is None


@pytest.mark.parametrize("family", ["interest", "preference"])
def test_retirement_deadline_survives_restarts_and_rejected_early_reflection(
    db_engine,
    db_session_factory,
    born,
    clock,
    family,
):
    build = interest if family == "interest" else preference
    model = models().Interest if family == "interest" else models().Preference
    field = family + "_operations"
    first = observation(db_session_factory, born, NOW)
    initial = build(refs=[first])
    identity = initial["operation_id"]
    assert run(db_engine, born, clock, **{field: [initial]})[0].status == "completed"
    clock.advance(DAY)
    second = observation(db_session_factory, born, clock.now())
    attention(db_session_factory, born, clock)
    assert (
        run(
            db_engine,
            born,
            clock,
            **{
                field: [
                    build(op="establish", identity=identity, refs=[second]),
                ]
            },
        )[0].status
        == "completed"
    )
    if family == "interest":
        attention(db_session_factory, born, clock, kind="routine")
        assert (
            run(
                db_engine,
                born,
                clock,
                **{
                    field: [
                        build(op="set_dormant", identity=identity, refs=[second]),
                    ]
                },
            )[0].status
            == "completed"
        )
    with db_session_factory() as session:
        before = deepcopy(
            dict(
                session.execute(
                    select(model.__table__).where(
                        model.individual_id == born.individual_id
                    ),
                )
                .mappings()
                .one()
            )
        )
        assert before["retirement_not_before"] == NOW + DAY * 8
    clock.advance(DAY * 6)
    new_anchor = observation(db_session_factory, born, clock.now())
    attention(db_session_factory, born, clock)
    rejected, _ = run(
        db_engine,
        born,
        clock,
        **{
            field: [
                build(op="retire", identity=identity, refs=[new_anchor]),
            ]
        },
    )
    assert rejected.status == "failed" and rejected.reason == "decision_rejected"
    with db_session_factory() as session:
        assert (
            dict(
                session.execute(
                    select(model.__table__).where(
                        model.individual_id == born.individual_id
                    ),
                )
                .mappings()
                .one()
            )
            == before
        )
    clock.advance(DAY)
    attention(db_session_factory, born, clock)
    assert (
        run(
            db_engine,
            born,
            clock,
            **{
                field: [
                    build(op="retire", identity=identity, refs=[new_anchor]),
                ]
            },
        )[0].status
        == "completed"
    )
    with db_session_factory() as session:
        row = session.get(model, identity)
        assert row.status == "retired"
        assert {Ref.model_validate(ref).id for ref in row.evidence_refs} == {
            first.id,
            second.id,
            new_anchor.id,
        }
        report = check_database(session)
        assert report.healthy, report.findings


@pytest.mark.parametrize("invalid", ["missing_preference", "unsupported_action"])
def test_mixed_development_and_existing_families_reject_atomically(
    db_engine,
    db_session_factory,
    born,
    clock,
    invalid,
):
    operations = initial_operations()
    operations["goal_operations"] = [goal()]
    if invalid == "missing_preference":
        operations["preference_operations"] = [
            preference(op="establish", identity=new_id())
        ]
    else:
        operations["action_requests"] = [
            dict(
                operation_id=new_id(),
                capability_key="mail",
                operation="send",
                arguments={},
                intended_effect="An unsupported external effect",
                verification_expectation={},
                impetus_refs=[],
                rationale="Must reject the complete decision",
            )
        ]
    outcome, _ = run(
        db_engine,
        born,
        clock,
        **operations,
        current_focus={"summary": "Must not be applied", "refs": []},
    )
    assert outcome.status == "failed" and outcome.reason == "decision_rejected"
    with db_session_factory() as session:
        assert all(
            rows == []
            for rows in development_rows(session, born.individual_id).values()
        )
        for model in (Goal, PersonalStateRevision, AppliedOperation):
            assert session.scalar(select(func.count()).select_from(model)) == 0
        assert session.get(AttentionState, born.individual_id) is None


@pytest.mark.parametrize("table", ["interests", "self_states"])
def test_partial_development_write_recovers_committed_d1_without_resampling(
    db_engine,
    db_session_factory,
    born,
    clock,
    table,
):
    operations = initial_operations()
    operations["goal_operations"] = [goal()]
    committed = []

    def respond(request):
        result = response(request, **operations)
        committed.append(result.decision)
        return result

    adapter = ScriptedModelAdapter([respond])
    reached = []

    def interrupt(conn, cursor, statement, parameters, context, executemany):
        if f"INSERT INTO {table} " in statement:
            reached.append(statement)
            raise DevelopmentWriteInterrupted("personal write before commit")

    with owner(db_engine, born.individual_id, clock) as ownership:
        connection = ownership.connection
        event.listen(connection, "after_cursor_execute", interrupt)
        try:
            with pytest.raises(DevelopmentWriteInterrupted):
                CognitionRuntime(
                    ownership, born.individual_id, adapter, clock
                ).run_once()
        finally:
            event.remove(connection, "after_cursor_execute", interrupt)
    assert len(reached) == 1
    with db_session_factory() as session:
        assert all(
            rows == []
            for rows in development_rows(session, born.individual_id).values()
        )
        for model in (Goal, PersonalStateRevision, AppliedOperation):
            assert session.scalar(select(func.count()).select_from(model)) == 0
        turn = session.scalar(select(CognitionTurn))
        assert turn.status == "decided" and turn.decision_json == committed[
            0
        ].model_dump(mode="json")
        assert session.scalar(select(ModelInvocation)).status == "completed"
        assert session.get(Wake, born.bootstrap_wake_id).status == "claimed"
    unused = ScriptedModelAdapter([response])
    with owner(db_engine, born.individual_id, clock) as ownership:
        outcome = CognitionRuntime(
            ownership, born.individual_id, unused, clock
        ).run_once()
    assert outcome.status == "completed" and unused.requests == ()
    with db_session_factory() as session:
        assert_tentative(session, born, operations)
        assert session.scalar(select(func.count()).select_from(Goal)) == 1
        assert (
            session.scalar(select(func.count()).select_from(PersonalStateRevision)) == 4
        )
        assert session.scalar(select(func.count()).select_from(AppliedOperation)) == 4
        assert session.scalar(select(func.count()).select_from(ModelInvocation)) == 1
        assert session.scalar(select(CognitionTurn)).decision_json == committed[
            0
        ].model_dump(mode="json")
        report = check_database(session)
        assert report.healthy, report.findings
