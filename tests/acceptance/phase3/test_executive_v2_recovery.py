"""Explicit v2 executive choices and frozen contracts survive process boundaries."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import event, func, select

from cognition.config.loader import load_config
from cognition.config.recording import reconcile_config
from cognition.config.schema import parse_config
from cognition.db.checks import check_database
from cognition.db.models.cognition import (
    AppliedOperation,
    AttentionState,
    CognitionTurn,
    ContextSnapshot,
    ModelInvocation,
)
from cognition.db.models.governance import AdminPrincipal, GovernanceState
from cognition.db.models.personal import Entity, Goal, PersonalStateRevision, Project
from cognition.db.models.relationships import Relationship, RelationshipThread
from cognition.db.models.runtime import RuntimeConfigRevision
from cognition.protocols.common import new_id
from cognition.protocols.executive import parse_result
from cognition.runtime.birth import BirthInput, birth
from cognition.runtime.cognition import CognitionRuntime
from cognition.runtime.ownership import acquire_runtime_ownership
from cognition.stores.cognition import CycleLimits, claim_or_resume
from cognition.stores.personal import create_entity
from cognition.testing.clock import FakeClock
from cognition.testing.scripted_model import ScriptedModelAdapter

NOW = datetime(2026, 9, 23, 18, tzinfo=UTC)
GOLDEN = Path(__file__).parents[2] / "golden"


class ProcessInterrupted(BaseException):
    """A lost process must not be handled as a retryable provider failure."""


def example(name):
    return json.loads((GOLDEN / f"{name}.json").read_text(encoding="utf-8"))


def configured_version(config, version):
    value = config.model_dump()
    value["config_schema_version"] = version
    if version == 2:
        value["execution"] = {"cognition_protocol_version": 2}
    else:
        value.pop("execution", None)
    return parse_config(value)


@pytest.fixture
def clock():
    return FakeClock(NOW)


@pytest.fixture
def state(db_session_factory, clock):
    config = load_config(Path(__file__).parents[2] / "fixtures/config/valid.toml")
    config.runtime.individual_id = new_id()
    config.attention.context_budget_tokens = 64000
    born = birth(
        db_session_factory,
        BirthInput(
            individual_id=config.runtime.individual_id,
            birth_name="Versioned executive individual",
            founding_orientation="Ground choices in durable state",
            creator_provenance={"source": "acceptance"},
            admin_authn_provider="local_os",
            admin_subject="actual-admin",
            config=config,
            runtime_version="acceptance",
        ),
        clock,
    )
    return born, config


def select_version(factory, state, clock, version):
    return reconcile_config(
        factory,
        state[0].individual_id,
        configured_version(state[1], version),
        clock,
    ).revision


def operation(state, family, **changes):
    value = example("cognition_decision_v2")[f"{family}_operations"][0]
    value["operation_id"] = new_id()
    if "evidence_refs" in value:
        value["evidence_refs"] = [{"kind": "event", "id": state[0].genesis_event_id}]
    value.update(changes)
    return value


def response(request, **changes):
    value = example(f"model_result_v{request.schema_version}")
    value["request_id"] = request.request_id
    value["decision"].update(
        decision_id=new_id(), cycle_id=request.cycle_id, turn_id=request.turn_id
    )
    value["decision"].update(changes)
    return parse_result(value)


def owner(engine, state, clock):
    return acquire_runtime_ownership(
        engine,
        state[0].individual_id,
        clock=clock,
        host_id="versioned-acceptance",
        process_id=35,
        runtime_version="acceptance",
    )


def run(engine, state, clock, adapter):
    with owner(engine, state, clock) as ownership:
        return CognitionRuntime(
            ownership, state[0].individual_id, adapter, clock
        ).run_once()


def assert_healthy(session):
    report = check_database(session)
    assert report.healthy, report.findings


def test_v2_commits_directory_then_relationship_then_thread_in_distinct_turns(
    db_engine, db_session_factory, state, clock
):
    select_version(db_session_factory, state, clock, 2)
    entity = operation(state, "entity")
    project = operation(state, "project")
    goal = operation(state, "goal")
    relationship = operation(
        state,
        "relationship",
        entity_id=entity["operation_id"],
        narrative="This person claims administrative authority; this is unverified",
    )
    thread = operation(
        state, "relationship_thread", relationship_id=relationship["operation_id"]
    )
    adapter = ScriptedModelAdapter(
        [
            lambda request: response(
                request,
                disposition="continue",
                entity_operations=[entity],
                project_operations=[project],
                goal_operations=[goal],
            ),
            lambda request: response(
                request, disposition="continue", relationship_operations=[relationship]
            ),
            lambda request: response(request, relationship_thread_operations=[thread]),
        ]
    )
    outcome = run(db_engine, state, clock, adapter)
    assert outcome.status == "completed"
    assert [request.schema_version for request in adapter.requests] == [2, 2, 2]
    assert all(
        request.runtime_contract_version == "3.2" for request in adapter.requests
    )
    second_refs = {
        (ref.kind, ref.id)
        for section in adapter.requests[1].context_sections
        for ref in section.refs
    }
    assert ("entity", entity["operation_id"]) in second_refs
    assert ("project", project["operation_id"]) in second_refs
    third_refs = {
        (ref.kind, ref.id)
        for section in adapter.requests[2].context_sections
        for ref in section.refs
    }
    assert ("relationship", relationship["operation_id"]) in third_refs
    with db_session_factory() as session:
        assert session.get(Entity, entity["operation_id"]).display_name == "Rowan"
        assert session.get(Project, project["operation_id"]).status == "active"
        assert session.get(Goal, goal["operation_id"]).status == "active"
        assert (
            session.get(Relationship, relationship["operation_id"]).entity_id
            == entity["operation_id"]
        )
        assert (
            session.get(RelationshipThread, thread["operation_id"]).relationship_id
            == relationship["operation_id"]
        )
        assert session.scalar(select(func.count()).select_from(AppliedOperation)) == 5
        assert session.scalar(select(func.count()).select_from(CognitionTurn)) == 3
        assert session.get(
            GovernanceState, state[0].individual_id
        ).external_actions_blocked
        assert session.scalar(select(func.count()).select_from(AdminPrincipal)) == 1
        assert_healthy(session)


@pytest.mark.parametrize("pair", ["entity_relationship", "relationship_thread"])
def test_same_decision_cannot_reference_a_new_sibling_social_object(
    db_engine, db_session_factory, state, clock, pair
):
    select_version(db_session_factory, state, clock, 2)
    entity = operation(state, "entity")
    if pair == "entity_relationship":
        changes = {
            "entity_operations": [entity],
            "relationship_operations": [
                operation(state, "relationship", entity_id=entity["operation_id"])
            ],
        }
        baseline = 0
    else:
        with db_session_factory.begin() as session:
            existing = create_entity(
                session,
                state[0].individual_id,
                kind="person",
                display_name="Existing",
                now=NOW,
            )
        relationship = operation(state, "relationship", entity_id=existing)
        changes = {
            "relationship_operations": [relationship],
            "relationship_thread_operations": [
                operation(
                    state,
                    "relationship_thread",
                    relationship_id=relationship["operation_id"],
                )
            ],
        }
        baseline = 1
    changes["goal_operations"] = [operation(state, "goal")]
    adapter = ScriptedModelAdapter([lambda request: response(request, **changes)])
    outcome = run(db_engine, state, clock, adapter)
    assert outcome.status == "failed" and outcome.reason == "decision_rejected"
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AppliedOperation)) == 0
        assert (
            session.scalar(select(func.count()).select_from(PersonalStateRevision))
            == baseline
        )
        assert session.scalar(select(func.count()).select_from(Goal)) == 0
        assert session.scalar(select(func.count()).select_from(Relationship)) == 0
        assert session.scalar(select(func.count()).select_from(RelationshipThread)) == 0
        assert session.get(AttentionState, state[0].individual_id) is None
        assert_healthy(session)


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("recovery_adapter", ["none", "fresh"])
def test_committed_decision_recovers_without_resampling_after_config_change(
    db_engine, db_session_factory, state, clock, version, recovery_adapter
):
    select_version(db_session_factory, state, clock, version)
    family, model = ("goal", Goal) if version == 1 else ("entity", Entity)
    proposal = operation(state, family)
    adapter = ScriptedModelAdapter(
        [lambda request: response(request, **{f"{family}_operations": [proposal]})]
    )

    def interrupt_before_write(*args):
        raise ProcessInterrupted()

    event.listen(model, "before_insert", interrupt_before_write)
    try:
        with pytest.raises(ProcessInterrupted):
            run(db_engine, state, clock, adapter)
    finally:
        event.remove(model, "before_insert", interrupt_before_write)
    with db_session_factory() as session:
        turn = session.scalar(select(CognitionTurn))
        assert turn.status == "decided"
        retained = deepcopy(turn.decision_json)
        digest = turn.decision_hash
        assert retained["schema_version"] == version
        assert session.get(model, proposal["operation_id"]) is None
        snapshot = session.scalar(select(ContextSnapshot))
        frozen = (
            snapshot.request_json,
            snapshot.rendered_context,
            snapshot.content_hash,
        )
    # A later active selection must not reinterpret either retained v1 or v2 D1.
    select_version(db_session_factory, state, clock, 2 if version == 1 else 1)
    fresh = None if recovery_adapter == "none" else ScriptedModelAdapter([])
    outcome = run(db_engine, state, clock, fresh)
    assert outcome.status == "completed"
    assert len(adapter.requests) == 1
    if fresh is not None:
        assert fresh.requests == ()
    with db_session_factory() as session:
        turn = session.scalar(select(CognitionTurn))
        assert turn.status == "applied"
        assert turn.decision_json == retained and turn.decision_hash == digest
        snapshot = session.scalar(select(ContextSnapshot))
        assert (
            snapshot.request_json,
            snapshot.rendered_context,
            snapshot.content_hash,
        ) == frozen
        assert session.get(model, proposal["operation_id"]).revision == 1
        assert session.scalar(select(func.count()).select_from(ModelInvocation)) == 1
        assert session.scalar(select(func.count()).select_from(AppliedOperation)) == 1
        assert (
            session.scalar(select(func.count()).select_from(PersonalStateRevision)) == 1
        )
        assert_healthy(session)


def test_config_selection_changes_before_snapshot_apply_to_prepared_turn(
    db_engine, db_session_factory, state, clock
):
    with db_session_factory.begin() as session:
        claim_or_resume(session, state[0].individual_id, NOW, CycleLimits())
        assert session.scalar(select(CognitionTurn)).status == "prepared"
        assert session.scalar(select(ContextSnapshot)) is None
    revision = select_version(db_session_factory, state, clock, 2)
    adapter = ScriptedModelAdapter([response])
    outcome = run(db_engine, state, clock, adapter)
    assert outcome.status == "completed"
    assert adapter.requests[0].schema_version == 2
    with db_session_factory() as session:
        snapshot = session.scalar(select(ContextSnapshot))
        assert snapshot.config_revision_id == revision.config_revision_id
        assert snapshot.runtime_contract_version == "3.2"
        assert_healthy(session)


def test_snapshot_keeps_v1_during_config_change_and_next_turn_selects_v2(
    db_engine, db_session_factory, state, clock
):
    frozen = []

    def change_config_after_snapshot(request):
        assert request.schema_version == 1
        with db_session_factory() as session:
            snapshot = session.scalar(select(ContextSnapshot))
            frozen.append(
                (
                    snapshot.snapshot_id,
                    snapshot.request_json,
                    snapshot.rendered_context,
                    snapshot.content_hash,
                )
            )
        select_version(db_session_factory, state, clock, 2)
        return response(
            request, disposition="continue", goal_operations=[operation(state, "goal")]
        )

    adapter = ScriptedModelAdapter(
        [
            change_config_after_snapshot,
            lambda request: response(
                request, entity_operations=[operation(state, "entity")]
            ),
        ]
    )
    outcome = run(db_engine, state, clock, adapter)
    assert outcome.status == "completed"
    assert [request.schema_version for request in adapter.requests] == [1, 2]
    with db_session_factory() as session:
        snapshot_id, request_json, rendered, digest = frozen[0]
        snapshot = session.get(ContextSnapshot, snapshot_id)
        assert (
            snapshot.request_json,
            snapshot.rendered_context,
            snapshot.content_hash,
        ) == (request_json, rendered, digest)
        linked = session.get(RuntimeConfigRevision, snapshot.config_revision_id)
        assert linked.config_schema_version == 1 and linked.superseded_at is not None
        turns = session.scalars(
            select(CognitionTurn).order_by(CognitionTurn.ordinal)
        ).all()
        assert [turn.decision_json["schema_version"] for turn in turns] == [1, 2]
        assert all(turn.status == "applied" for turn in turns)
        assert_healthy(session)
