"""Deliberate and urgent attention is recalled across fresh model boundaries."""

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from cognition.config.loader import load_config
from cognition.config.recording import reconcile_config
from cognition.db.models import Event
from cognition.db.models.cognition import ContextSnapshot, ModelInvocation
from cognition.db.models.personal import (
    Commitment,
    Goal,
    PersonalStateRevision,
    Project,
)
from cognition.protocols.common import Ref, new_id
from cognition.protocols.executive import parse_result
from cognition.protocols.wakes_v1 import WakeV1
from cognition.runtime.birth import BirthInput, birth
from cognition.runtime.cognition import CognitionRuntime
from cognition.runtime.ownership import acquire_runtime_ownership
from cognition.stores.attention import create_or_merge_pending_wake
from cognition.stores.personal import create_project
from cognition.testing.clock import FakeClock
from cognition.testing.scripted_model import ScriptedModelAdapter

NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)
GOLDEN = Path(__file__).parents[2] / "golden"


def example(name):
    return json.loads((GOLDEN / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def clock():
    return FakeClock(NOW)


def make_state(factory, clock):
    config = load_config(Path(__file__).parents[2] / "fixtures/config/valid.toml")
    config.runtime.individual_id = new_id()
    config.attention.context_budget_tokens = 64000
    born = birth(
        factory,
        BirthInput(
            individual_id=config.runtime.individual_id,
            birth_name="Deliberate attention individual",
            founding_orientation="Return to durable matters without a transcript",
            creator_provenance={"source": "acceptance"},
            admin_authn_provider="local_os",
            admin_subject="attention-admin",
            config=config,
            runtime_version="acceptance",
        ),
        clock,
    )
    return born, config


@pytest.fixture
def state(db_session_factory, clock):
    return make_state(db_session_factory, clock)


def response(request, **changes):
    result = example(f"model_result_v{request.schema_version}")
    result["request_id"] = request.request_id
    result["decision"].update(
        decision_id=new_id(), cycle_id=request.cycle_id, turn_id=request.turn_id
    )
    result["decision"].update(changes)
    return parse_result(result)


def operation(family, **changes):
    value = example("cognition_decision_v1")[f"{family}_operations"][0]
    value["operation_id"] = new_id()
    value.update(changes)
    return value


def run(engine, state, clock, model):
    with acquire_runtime_ownership(
        engine,
        state[0].individual_id,
        clock=clock,
        host_id="attention-acceptance",
        process_id=36,
        runtime_version="acceptance",
    ) as ownership:
        return CognitionRuntime(
            ownership, state[0].individual_id, model, clock
        ).run_once()


def add_wake(factory, state, clock, refs=()):
    with factory.begin() as session:
        return create_or_merge_pending_wake(
            session,
            WakeV1(
                schema_version=1,
                wake_id=new_id(),
                individual_id=state[0].individual_id,
                kind="self_scheduled",
                due_at=clock.now(),
                purpose="Reconsider the referenced durable matter",
                cause_event_id=None,
                context_refs=list(refs),
                coalesce_key=None,
            ),
        )


def snapshot(factory, request):
    with factory() as session:
        return session.scalar(
            select(ContextSnapshot).where(ContextSnapshot.turn_id == request.turn_id)
        )


def personal_snapshot(factory, state):
    with factory() as session:
        values = {
            model.__tablename__: [
                deepcopy(dict(row))
                for row in session.execute(
                    select(model.__table__)
                    .where(model.individual_id == state[0].individual_id)
                    .order_by(next(iter(model.__table__.primary_key)))
                ).mappings()
            ]
            for model in (Goal, Commitment, Project, PersonalStateRevision)
        }
        values["personal_events"] = list(
            session.scalars(
                select(Event.event_id)
                .where(
                    Event.individual_id == state[0].individual_id,
                    Event.event_type.like("personal.%"),
                )
                .order_by(Event.event_id)
            )
        )
        return values


def attention_summary(request):
    return next(
        section.content
        for section in request.context_sections
        if section.name == "attention_summary"
    )


def seed_goals(engine, state, clock, *, terminal=False):
    old = operation("goal", title="Old exact research question")
    recent = [
        operation("goal", title=f"New unrelated question {index}")
        for index in range(12)
    ]

    def newer(request):
        clock.advance(timedelta(seconds=1))
        operations = list(recent)
        if terminal:
            operations.append(
                operation(
                    "goal",
                    op="set_status",
                    goal_id=old["operation_id"],
                    title=None,
                    desired_state=None,
                    requested_status="completed",
                    origin=None,
                )
            )
        return response(request, goal_operations=operations)

    model = ScriptedModelAdapter(
        [
            lambda request: response(
                request, disposition="continue", goal_operations=[old]
            ),
            newer,
        ]
    )
    assert run(engine, state, clock, model).status == "completed"
    return old


@pytest.mark.parametrize("terminal", [False, True])
def test_fresh_model_recalls_named_goal_outside_recent_pool_without_mutating_it(
    db_engine, db_session_factory, state, clock, terminal
):
    old = seed_goals(db_engine, state, clock, terminal=terminal)
    reference = Ref(kind="goal", id=old["operation_id"])
    add_wake(db_session_factory, state, clock, [reference])
    before = personal_snapshot(db_session_factory, state)
    fresh = ScriptedModelAdapter([response])
    assert run(db_engine, state, clock, fresh).status == "completed"
    assert len(fresh.requests) == 1
    request = fresh.requests[0]
    section = next(
        section for section in request.context_sections if reference in section.refs
    )
    assert section.content["item"]["title"] == "Old exact research question"
    assert section.content["item"]["status"] == ("completed" if terminal else "active")
    assert (
        snapshot(db_session_factory, request).retrieval_reasons[f"goal:{reference.id}"]
        == "wake_reference"
    )
    assert attention_summary(request)["policy_version"] == 1
    assert personal_snapshot(db_session_factory, state) == before


@pytest.mark.parametrize("reference_ninth", [False, True])
def test_nine_due_commitments_remain_actionable_and_next_turn_advances_urgent_pool(
    db_engine, db_session_factory, state, clock, reference_ninth
):
    commitments = [
        operation(
            "commitment",
            title=f"Urgent obligation {index}",
            requested_status="active",
            due_at=NOW + timedelta(minutes=index),
        )
        for index in range(9)
    ]
    seed = ScriptedModelAdapter(
        [lambda request: response(request, commitment_operations=commitments)]
    )
    assert run(db_engine, state, clock, seed).status == "completed"
    ninth = commitments[8]["operation_id"]
    refs = [Ref(kind="commitment", id=ninth)] if reference_ninth else []
    add_wake(db_session_factory, state, clock, refs)
    fulfilled = operation(
        "commitment",
        op="set_status",
        commitment_id=commitments[0]["operation_id"],
        counterparty_entity_id=None,
        title=None,
        terms=None,
        requested_status="fulfilled",
        due_at=None,
    )
    fresh = ScriptedModelAdapter(
        [
            lambda request: response(
                request, disposition="continue", commitment_operations=[fulfilled]
            ),
            response,
        ]
    )
    assert run(db_engine, state, clock, fresh).status == "completed"
    first, second = fresh.requests
    assert attention_summary(first)["urgent_scan_truncated"] is True
    assert attention_summary(second)["urgent_scan_truncated"] is False
    first_reasons = snapshot(db_session_factory, first).retrieval_reasons
    second_reasons = snapshot(db_session_factory, second).retrieval_reasons
    assert {
        reference
        for reference, reason in first_reasons.items()
        if reason == "urgent_commitment"
    } == {f"commitment:{row['operation_id']}" for row in commitments[:8]}
    assert second_reasons[f"commitment:{ninth}"] == "urgent_commitment"
    if reference_ninth:
        assert first_reasons[f"commitment:{ninth}"] == "wake_reference"
        assert attention_summary(first)["budget_omitted_candidate_count"] == 0
    with db_session_factory() as session:
        assert (
            session.get(Commitment, commitments[0]["operation_id"]).status
            == "fulfilled"
        )
        assert all(
            session.get(Commitment, row["operation_id"]).status == "active"
            for row in commitments[1:]
        )


def test_foreign_reference_remains_a_pointer_without_retrieving_foreign_content(
    db_engine, db_session_factory, state, clock
):
    foreign = make_state(db_session_factory, clock)
    goal = operation("goal", title="Private foreign directory phrase")
    seed = ScriptedModelAdapter(
        [lambda request: response(request, goal_operations=[goal])]
    )
    assert run(db_engine, foreign, clock, seed).status == "completed"
    reference = Ref(kind="goal", id=goal["operation_id"])
    add_wake(db_session_factory, state, clock, [reference])
    fresh = ScriptedModelAdapter([response])
    assert run(db_engine, state, clock, fresh).status == "completed"
    request = fresh.requests[0]
    assert str(reference.id) in request.model_dump_json()
    assert "Private foreign directory phrase" not in request.model_dump_json()
    stored = snapshot(db_session_factory, request)
    assert reference.model_dump(mode="json") not in stored.selected_refs
    assert f"goal:{reference.id}" not in stored.retrieval_reasons
    assert attention_summary(request)["unresolved_direct_refs"] == 1


def test_urgent_byte_overflow_fails_before_any_new_provider_attempt(
    db_engine, db_session_factory, state, clock
):
    huge = operation(
        "commitment", requested_status="active", due_at=NOW, terms="x" * 16000
    )
    seed = ScriptedModelAdapter(
        [lambda request: response(request, commitment_operations=[huge])]
    )
    assert run(db_engine, state, clock, seed).status == "completed"
    changed = state[1].model_copy(deep=True)
    changed.attention.context_budget_tokens = 12000
    reconcile_config(db_session_factory, state[0].individual_id, changed, clock)
    add_wake(db_session_factory, state, clock)
    before = personal_snapshot(db_session_factory, state)
    fresh = ScriptedModelAdapter([response])
    outcome = run(db_engine, state, clock, fresh)
    assert outcome.status == "failed" and outcome.reason == "context_budget"
    assert fresh.requests == ()
    with db_session_factory() as session:
        assert len(session.scalars(select(ModelInvocation)).all()) == 1
    assert personal_snapshot(db_session_factory, state) == before


def test_frozen_attention_survives_new_state_wake_references_and_configuration(
    db_engine, db_session_factory, state, clock
):
    old = seed_goals(db_engine, state, clock)
    add_wake(
        db_session_factory, state, clock, [Ref(kind="goal", id=old["operation_id"])]
    )
    assert run(db_engine, state, clock, None).reason == "model_unavailable"
    with db_session_factory() as session:
        retained = session.scalar(
            select(ContextSnapshot)
            .order_by(ContextSnapshot.created_at.desc(), ContextSnapshot.snapshot_id)
            .limit(1)
        )
        frozen = (
            retained.request_json,
            retained.rendered_context,
            retained.content_hash,
        )
    with db_session_factory.begin() as session:
        project_id = create_project(
            session,
            state[0].individual_id,
            title="Created after the frozen snapshot",
            desired_state="Be visible on the next fresh snapshot",
            rationale="New trusted substrate state",
            evidence_refs=[Ref(kind="event", id=state[0].genesis_event_id)],
            now=clock.now(),
        )
    add_wake(db_session_factory, state, clock, [Ref(kind="project", id=project_id)])
    changed = state[1].model_copy(deep=True)
    changed.model.max_output_tokens += 1
    reconcile_config(db_session_factory, state[0].individual_id, changed, clock)
    fresh = ScriptedModelAdapter([response])
    assert run(db_engine, state, clock, fresh).status == "completed"
    request = fresh.requests[0]
    assert request.model_dump(mode="json") == frozen[0]
    stored = snapshot(db_session_factory, request)
    assert (stored.request_json, stored.rendered_context, stored.content_hash) == frozen
    assert "Created after the frozen snapshot" not in request.model_dump_json()
    next_model = ScriptedModelAdapter([response])
    assert run(db_engine, state, clock, next_model).status == "completed"
    next_request = next_model.requests[0]
    assert "Created after the frozen snapshot" in next_request.model_dump_json()
    assert next_request.output_token_budget == changed.model.max_output_tokens
