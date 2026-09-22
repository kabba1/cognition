"""Personal interpretations are owned, evidence-linked, and transactionally revised."""

import importlib
import json
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, func, select
from test_integrity_checks import NOW, create_healthy

from cognition.db.models.cognition import (
    AppliedOperation,
    CognitionCycle,
    CognitionTurn,
)
from cognition.db.models.evidence import Event, EventContent
from cognition.protocols.cognition_v1 import CognitionDecisionV1
from cognition.protocols.common import Ref, new_id
from cognition.stores.cognition import (
    CycleLimits,
    claim_or_resume,
    content_hash,
    latest_turn,
)

GOLDEN = Path(__file__).parents[1] / "golden/cognition_decision_v1.json"
FAMILIES = ("goal", "commitment", "belief", "episode")


def store():
    assert importlib.util.find_spec("cognition.stores.personal") is not None
    return importlib.import_module("cognition.stores.personal")


def models():
    return importlib.import_module("cognition.db.models.personal")


@pytest.fixture
def state(db_session_factory):
    person = create_healthy(db_session_factory)
    with db_session_factory.begin() as session:
        cycle = claim_or_resume(session, person.individual_id, NOW, CycleLimits())
        turn = latest_turn(session, cycle.cycle_id)
    return person, cycle, turn


def operation(kind, state, **updates):
    value = json.loads(GOLDEN.read_text())[f"{kind}_operations"][0]
    value["operation_id"] = str(new_id())
    if "evidence_refs" in value:
        value["evidence_refs"] = [
            {"kind": "event", "id": str(state[0].genesis_event_id)}
        ]
    if kind == "belief":
        value["supporting_evidence"] = [
            {"kind": "event", "id": str(state[0].genesis_event_id)}
        ]
    value.update(updates)
    return value


def decision(state, **families):
    value = json.loads(GOLDEN.read_text())
    for name in value:
        if name.endswith("_operations") or name.endswith("_requests"):
            value[name] = []
    value.update(
        cycle_id=str(state[1].cycle_id),
        turn_id=str(state[2].turn_id),
        decision_id=str(new_id()),
        current_focus=None,
        **families,
    )
    return CognitionDecisionV1.model_validate(value)


def persist(factory, state, proposal):
    with factory.begin() as session:
        row = session.get(CognitionTurn, state[2].turn_id)
        row.status = "decided"
        row.decision_id = proposal.decision_id
        row.decision_json = proposal.model_dump(mode="json")
        row.decision_hash = content_hash(row.decision_json)
        row.disposition = proposal.disposition


def apply(factory, state, proposal):
    persist(factory, state, proposal)
    with factory.begin() as session:
        store().apply_personal_operations(
            session, state[0].individual_id, proposal, NOW
        )


def errors(factory, state, proposal):
    with factory.begin() as session:
        return store().validate_personal_operations(
            session, state[0].individual_id, proposal, NOW
        )


def create(factory, state, kind, **updates):
    op = operation(kind, state, **updates)
    proposal = decision(state, **{f"{kind}_operations": [op]})
    apply(factory, state, proposal)
    return getattr(proposal, f"{kind}_operations")[0].operation_id


def test_birth_creates_no_personal_memories(db_session_factory, state):
    with db_session_factory() as session:
        assert store().personal_context_sections(session, state[0].individual_id) == []
        for kind in FAMILIES:
            model = getattr(models(), kind.title())
            assert session.scalar(select(func.count()).select_from(model)) == 0


def test_four_family_batch_preserves_exact_history_and_model_provenance(
    db_session_factory, state
):
    proposal = decision(
        state, **{f"{kind}_operations": [operation(kind, state)] for kind in FAMILIES}
    )
    apply(db_session_factory, state, proposal)
    with db_session_factory() as session:
        revisions = session.scalars(select(models().PersonalStateRevision)).all()
        assert len(revisions) == 4
        assert session.scalar(select(func.count()).select_from(AppliedOperation)) == 4
        for revision in revisions:
            assert revision.before_json is None
            assert revision.after_json["revision"] == 1
            assert revision.turn_id == state[2].turn_id
            assert revision.operation_id is not None
            evidence = session.get(Event, revision.event_id)
            assert evidence.source_kind == "model"
            assert evidence.individual_id == state[0].individual_id
            content = session.get(EventContent, revision.event_id)
            assert content.payload["operation_id"] == str(revision.operation_id)
        for kind in FAMILIES:
            op = getattr(proposal, f"{kind}_operations")[0]
            assert (
                session.get(getattr(models(), kind.title()), op.operation_id)
                is not None
            )


GOAL_NEXT = {
    "active": {"paused", "blocked", "completed", "abandoned"},
    "paused": {"active", "blocked", "completed", "abandoned"},
    "blocked": {"active", "paused", "completed", "abandoned"},
    "completed": set(),
    "abandoned": set(),
}
COMMITMENT_NEXT = {
    "proposed": {"active", "released"},
    "active": {"fulfilled", "released", "broken", "disputed"},
    "disputed": {"active", "released", "broken", "fulfilled"},
    "fulfilled": set(),
    "released": set(),
    "broken": set(),
}
BELIEF_NEXT = {
    "tentative": {"accepted", "disputed", "withdrawn"},
    "accepted": {"tentative", "disputed", "withdrawn"},
    "disputed": {"tentative", "accepted", "withdrawn"},
    "superseded": set(),
    "withdrawn": set(),
}


@pytest.mark.parametrize(
    "kind,previous,requested,allowed",
    [
        (kind, previous, requested, requested in next_states)
        for kind, matrix in (
            ("goal", GOAL_NEXT),
            ("commitment", COMMITMENT_NEXT),
            ("belief", BELIEF_NEXT),
        )
        for previous, next_states in matrix.items()
        for requested in matrix
    ],
)
def test_full_status_transition_matrices(
    db_session_factory, state, kind, previous, requested, allowed
):
    identity = create(db_session_factory, state, kind)
    model = getattr(models(), kind.title())
    with db_session_factory.begin() as session:
        session.get(model, identity).status = previous
    op = operation(
        kind,
        state,
        op="set_status",
        **{f"{kind}_id": str(identity)},
        requested_status=requested,
    )
    for field in (
        "title",
        "desired_state",
        "project_id",
        "origin",
        "terms",
        "counterparty_entity_id",
        "due_at",
        "proposition",
        "subject_entity_id",
        "topic",
        "supersedes_belief_id",
    ):
        if field in op:
            op[field] = None
    if kind == "belief":
        op["contradicting_evidence"] = op["supporting_evidence"]
    proposal = decision(state, **{f"{kind}_operations": [op]})
    actual = errors(db_session_factory, state, proposal)
    assert (actual == ()) is allowed
    if allowed:
        apply(db_session_factory, state, proposal)
        with db_session_factory() as session:
            assert session.get(model, identity).status == requested
            assert session.get(model, identity).revision == 2


@pytest.mark.parametrize(
    "kind,status",
    [
        ("goal", "completed"),
        ("goal", "abandoned"),
        ("commitment", "fulfilled"),
        ("commitment", "disputed"),
        ("belief", "withdrawn"),
        ("belief", "superseded"),
    ],
)
def test_create_cannot_start_in_incompatible_status(
    db_session_factory, state, kind, status
):
    proposal = decision(
        state,
        **{f"{kind}_operations": [operation(kind, state, requested_status=status)]},
    )
    assert errors(db_session_factory, state, proposal)


@pytest.mark.parametrize(
    "status,field",
    [("accepted", "supporting_evidence"), ("disputed", "contradicting_evidence")],
)
def test_belief_status_requires_resolving_evidence(
    db_session_factory, state, status, field
):
    op = operation(
        "belief",
        state,
        requested_status=status,
        supporting_evidence=[],
        contradicting_evidence=[],
    )
    proposal = decision(state, belief_operations=[op])
    assert errors(db_session_factory, state, proposal)
    op[field] = [{"kind": "event", "id": str(state[0].genesis_event_id)}]
    assert (
        errors(db_session_factory, state, decision(state, belief_operations=[op])) == ()
    )


def test_supersede_preserves_old_proposition_and_links_two_histories(
    db_session_factory, state
):
    old_id = create(db_session_factory, state, "belief")
    op = operation(
        "belief",
        state,
        op="supersede",
        supersedes_belief_id=str(old_id),
        proposition="Revised interpretation",
    )
    proposal = decision(state, belief_operations=[op])
    new_id_value = proposal.belief_operations[0].operation_id
    apply(db_session_factory, state, proposal)
    with db_session_factory() as session:
        old = session.get(models().Belief, old_id)
        new = session.get(models().Belief, new_id_value)
        assert (
            old.status == "superseded"
            and old.proposition == "The observed bird may be a robin."
        )
        assert new.proposition == "Revised interpretation"
        assert new.supersedes_belief_id == old_id
        revisions = session.scalars(
            select(models().PersonalStateRevision).where(
                models().PersonalStateRevision.created_at == NOW
            )
        ).all()
        changed = [
            rev
            for rev in revisions
            if rev.object_id == new_id_value or rev.revision == 2
        ]
        assert len(changed) == 2 and len({rev.event_id for rev in changed}) == 1
        assert {rev.operation_id for rev in changed} == {None, new_id_value}
        assert all(rev.turn_id == state[2].turn_id for rev in changed)
        content = session.get(EventContent, changed[0].event_id).payload
        assert str(old_id) in json.dumps(content) and str(new_id_value) in json.dumps(
            content
        )
        old_revision = next(rev for rev in changed if rev.object_id == old_id)
        assert old_revision.before_json["status"] == "tentative"
        assert old_revision.after_json["status"] == "superseded"


@pytest.mark.parametrize("conflict", ["old_target", "new_target"])
def test_supersession_reserves_both_touched_beliefs(
    db_session_factory, state, conflict
):
    old = create(db_session_factory, state, "belief")
    op = operation("belief", state, op="supersede", supersedes_belief_id=str(old))
    second = operation(
        "belief",
        state,
        belief_id=str(old) if conflict == "old_target" else op["operation_id"],
    )
    assert "duplicate_personal_target" in errors(
        db_session_factory, state, decision(state, belief_operations=[op, second])
    )


@pytest.mark.parametrize("field", ["title", "project_id", "origin"])
def test_goal_status_operation_rejects_content_fields(db_session_factory, state, field):
    goal = create(db_session_factory, state, "goal")
    op = operation(
        "goal",
        state,
        op="set_status",
        goal_id=str(goal),
        title=None,
        desired_state=None,
        project_id=None,
        origin=None,
        requested_status="paused",
    )
    op[field] = (
        str(new_id())
        if field == "project_id"
        else "self_generated"
        if field == "origin"
        else "Changed"
    )
    assert errors(db_session_factory, state, decision(state, goal_operations=[op]))


@pytest.mark.parametrize("kind", ["goal", "commitment"])
def test_revise_rejects_status_and_preserves_nullable_fields(
    db_session_factory, state, kind
):
    identity = create(db_session_factory, state, kind)
    op = operation(
        kind, state, op="revise", **{f"{kind}_id": str(identity)}, title="Changed"
    )
    if kind == "goal":
        op.update(origin=None, desired_state=None)
    else:
        op.update(terms=None)
    assert errors(
        db_session_factory, state, decision(state, **{f"{kind}_operations": [op]})
    )
    op["requested_status"] = None
    apply(db_session_factory, state, decision(state, **{f"{kind}_operations": [op]}))
    with db_session_factory() as session:
        row = session.get(getattr(models(), kind.title()), identity)
        assert row.title == "Changed" and row.revision == 2
        assert getattr(row, "desired_state" if kind == "goal" else "terms")
        history = session.scalar(
            select(models().PersonalStateRevision).where(
                models().PersonalStateRevision.object_id == identity,
                models().PersonalStateRevision.revision == 2,
            )
        )
        assert history.before_json["title"] != history.after_json["title"]


@pytest.mark.parametrize("kind", FAMILIES)
def test_foreign_evidence_rejects_whole_batch(db_session_factory, state, kind):
    other = create_healthy(db_session_factory)
    op = operation(kind, state)
    op["supporting_evidence" if kind == "belief" else "evidence_refs"] = [
        {"kind": "event", "id": str(other.genesis_event_id)}
    ]
    proposal = decision(state, **{f"{kind}_operations": [op]})
    assert errors(db_session_factory, state, proposal)
    with pytest.raises(ValueError):
        apply(db_session_factory, state, proposal)
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AppliedOperation)) == 0


def test_sibling_created_object_is_not_valid_evidence(db_session_factory, state):
    goal = operation("goal", state)
    belief = operation(
        "belief",
        state,
        supporting_evidence=[{"kind": "goal", "id": goal["operation_id"]}],
    )
    assert errors(
        db_session_factory,
        state,
        decision(state, goal_operations=[goal], belief_operations=[belief]),
    )


@pytest.mark.parametrize(
    "case",
    [
        "missing_evidence",
        "future_start",
        "future_end",
        "foreign_entity",
        "foreign_project",
    ],
)
def test_episode_requires_owned_grounding_and_nonfuture_times(
    db_session_factory, state, case
):
    op = operation("episode", state)
    if case == "missing_evidence":
        op["evidence_refs"] = []
    elif case == "future_start":
        op.update(starts_at=(NOW + timedelta(days=1)).isoformat(), ends_at=None)
    elif case == "future_end":
        op["ends_at"] = (NOW + timedelta(days=1)).isoformat()
    else:
        op["entity_refs" if case == "foreign_entity" else "project_refs"] = [
            str(new_id())
        ]
    assert errors(db_session_factory, state, decision(state, episode_operations=[op]))


def test_operation_collision_and_duplicate_target_reject(db_session_factory, state):
    identity = create(db_session_factory, state, "goal")
    op = operation("goal", state, operation_id=str(identity))
    assert errors(db_session_factory, state, decision(state, goal_operations=[op]))
    first = operation("goal", state, goal_id=str(new_id()))
    second = operation("goal", state, goal_id=first["goal_id"])
    assert errors(
        db_session_factory, state, decision(state, goal_operations=[first, second])
    )


def test_unsupported_family_and_mutated_schema_reject_without_writes(
    db_session_factory, state
):
    proposal = decision(state, goal_operations=[operation("goal", state)])
    unsupported = json.loads(GOLDEN.read_text())["action_requests"]
    proposal = CognitionDecisionV1.model_validate(
        dict(proposal.model_dump(), action_requests=unsupported)
    )
    assert errors(db_session_factory, state, proposal)
    proposal.action_requests.clear()
    proposal.goal_operations[0].op = "private-invalid"
    assert errors(db_session_factory, state, proposal) == ("invalid_decision",)


def test_total_operation_cap_rejects_before_mutation(db_session_factory, state):
    proposal = decision(
        state, goal_operations=[operation("goal", state) for _ in range(65)]
    )
    assert "too_many_operations" in errors(db_session_factory, state, proposal)


def test_substrate_apis_history_and_owned_references(db_session_factory, state):
    with db_session_factory.begin() as session:
        entity = store().create_entity(
            session,
            state[0].individual_id,
            kind="person",
            display_name="Observed person",
            now=NOW,
        )
        project = store().create_project(
            session,
            state[0].individual_id,
            title="Study",
            desired_state="Understand",
            rationale="Chosen",
            evidence_refs=[],
            now=NOW,
        )
        store().revise_project(
            session,
            state[0].individual_id,
            project,
            title="Revised study",
            rationale="Clarified",
            evidence_refs=[],
            now=NOW,
        )
    proposal = decision(
        state,
        goal_operations=[operation("goal", state, project_id=str(project))],
        commitment_operations=[
            operation("commitment", state, counterparty_entity_id=str(entity))
        ],
    )
    apply(db_session_factory, state, proposal)
    with db_session_factory() as session:
        assert store().personal_reference_exists(
            session, state[0].individual_id, Ref(kind="entity", id=entity)
        )
        assert not store().personal_reference_exists(
            session, new_id(), Ref(kind="entity", id=entity)
        )
        history = session.scalars(
            select(models().PersonalStateRevision).where(
                models().PersonalStateRevision.operation_id.is_(None)
            )
        ).all()
        assert len(history) == 3 and all(row.turn_id is None for row in history)
        assert session.get(models().Project, project).revision == 2


def test_unflushed_conflicts_are_refused_and_stale_rows_are_refreshed(
    db_session_factory, state
):
    identity = create(db_session_factory, state, "goal")
    with db_session_factory() as stale:
        row = stale.get(models().Goal, identity)
        with db_session_factory.begin() as fresh:
            fresh.get(models().Goal, identity).revision = 5
        op = operation(
            "goal",
            state,
            op="revise",
            goal_id=str(identity),
            title="New",
            desired_state=None,
            requested_status=None,
            origin=None,
        )
        proposal = decision(state, goal_operations=[op])
        persist(db_session_factory, state, proposal)
        store().apply_personal_operations(stale, state[0].individual_id, proposal, NOW)
        assert row.revision == 6
        stale.rollback()
        row.title = "Unflushed"
        with pytest.raises(ValueError, match="unflushed"):
            store().apply_personal_operations(
                stale, state[0].individual_id, proposal, NOW
            )
        assert row.title == "Unflushed"


def test_failure_after_projection_insert_rolls_back_batch_and_history(
    db_session_factory, db_engine, state
):
    proposal = decision(
        state,
        goal_operations=[operation("goal", state)],
        belief_operations=[operation("belief", state)],
    )

    def fail_after_insert(
        connection, cursor, statement, parameters, context, executemany
    ):
        if statement.startswith("INSERT INTO goals"):
            raise RuntimeError("injected fault")

    event.listen(db_engine, "after_cursor_execute", fail_after_insert)
    try:
        with pytest.raises(RuntimeError, match="injected fault"):
            apply(db_session_factory, state, proposal)
    finally:
        event.remove(db_engine, "after_cursor_execute", fail_after_insert)
    with db_session_factory() as session:
        for model in (
            models().Goal,
            models().Belief,
            models().PersonalStateRevision,
            AppliedOperation,
        ):
            assert session.scalar(select(func.count()).select_from(model)) == 0


def test_context_is_bounded_labeled_and_does_not_modify_personal_state(
    db_session_factory, state
):
    proposal = decision(
        state,
        goal_operations=[
            operation("goal", state, title=f"Goal {i}") for i in range(20)
        ],
    )
    apply(db_session_factory, state, proposal)
    with db_session_factory() as session:
        sections = store().personal_context_sections(session, state[0].individual_id)
        assert sections and len(sections) <= 32
        assert sum(len(section.refs) for section in sections) <= 32
        assert "model-derived" in json.dumps(
            [section.model_dump(mode="json") for section in sections]
        )
        assert not session.dirty and not session.new
        assert all(
            goal.revision == 1 for goal in session.scalars(select(models().Goal))
        )


@pytest.mark.parametrize("kind", ["goal", "commitment"])
def test_unchanged_revision_is_rejected(db_session_factory, state, kind):
    identity = create(db_session_factory, state, kind)
    op = operation(
        kind, state, op="revise", requested_status=None, **{f"{kind}_id": str(identity)}
    )
    if kind == "goal":
        op["origin"] = None
    assert "no_op_personal_revision" in errors(
        db_session_factory, state, decision(state, **{f"{kind}_operations": [op]})
    )


@pytest.mark.parametrize(
    "kind,field",
    [
        ("commitment", "title"),
        ("commitment", "terms"),
        ("commitment", "counterparty_entity_id"),
        ("commitment", "due_at"),
        ("belief", "proposition"),
        ("belief", "subject_entity_id"),
        ("belief", "topic"),
        ("belief", "supersedes_belief_id"),
    ],
)
def test_status_operation_never_silently_discards_content(
    db_session_factory, state, kind, field
):
    identity = create(db_session_factory, state, kind)
    op = operation(
        kind,
        state,
        op="set_status",
        requested_status="active" if kind == "commitment" else "accepted",
        **{f"{kind}_id": str(identity)},
    )
    for name in (
        "title",
        "terms",
        "counterparty_entity_id",
        "due_at",
        "proposition",
        "subject_entity_id",
        "topic",
        "supersedes_belief_id",
    ):
        if name in op:
            op[name] = None
    op[field] = (
        str(new_id())
        if field.endswith("_id")
        else NOW.isoformat()
        if field == "due_at"
        else "Changed"
    )
    assert "incompatible_operation_fields" in errors(
        db_session_factory, state, decision(state, **{f"{kind}_operations": [op]})
    )


def test_belief_status_preserves_and_unions_evidence(db_session_factory, state):
    identity = create(db_session_factory, state, "belief")
    op = operation(
        "belief",
        state,
        op="set_status",
        belief_id=str(identity),
        proposition=None,
        subject_entity_id=None,
        topic=None,
        requested_status="accepted",
        supporting_evidence=[],
    )
    apply(db_session_factory, state, decision(state, belief_operations=[op]))
    op["operation_id"] = str(new_id())
    op["requested_status"] = "disputed"
    op["supporting_evidence"] = [
        {"kind": "individual", "id": str(state[0].individual_id)}
    ]
    op["contradicting_evidence"] = [
        {"kind": "event", "id": str(state[0].genesis_event_id)}
    ]
    apply(db_session_factory, state, decision(state, belief_operations=[op]))
    with db_session_factory() as session:
        row = session.get(models().Belief, identity)
        assert row.supporting_evidence == [
            {"kind": "event", "id": str(state[0].genesis_event_id)},
            {"kind": "individual", "id": str(state[0].individual_id)},
        ]
        assert row.contradicting_evidence == op["contradicting_evidence"]
        assert row.revision == 3


def test_bad_later_operation_cannot_leave_earlier_projection(db_session_factory, state):
    proposal = decision(
        state,
        goal_operations=[operation("goal", state)],
        episode_operations=[operation("episode", state, evidence_refs=[])],
    )
    with pytest.raises(ValueError, match="episode_evidence_required"):
        apply(db_session_factory, state, proposal)
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(models().Goal)) == 0
        assert session.scalar(select(func.count()).select_from(AppliedOperation)) == 0


def test_supplied_ids_and_foreign_id_collisions(db_session_factory, state):
    supplied = new_id()
    op = operation("goal", state, goal_id=str(supplied))
    apply(db_session_factory, state, decision(state, goal_operations=[op]))
    other = create_healthy(db_session_factory)
    with db_session_factory.begin() as session:
        session.get(models().Goal, supplied).individual_id = other.individual_id
    op["operation_id"] = str(new_id())
    assert "personal_id_collision" in errors(
        db_session_factory, state, decision(state, goal_operations=[op])
    )


@pytest.mark.parametrize(
    "api", ["validate", "apply", "entity", "project", "revise_project"]
)
def test_all_mutation_paths_lock_individual_before_reading_state(
    db_session_factory, db_engine, state, api
):
    project = None
    if api == "revise_project":
        with db_session_factory.begin() as session:
            project = store().create_project(
                session,
                state[0].individual_id,
                title="Initial",
                desired_state="Study",
                rationale="Choice",
                evidence_refs=[],
                now=NOW,
            )
    proposal = decision(state, goal_operations=[operation("goal", state)])
    if api == "apply":
        persist(db_session_factory, state, proposal)
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db_engine, "before_cursor_execute", record)
    try:
        with db_session_factory.begin() as session:
            if api in {"validate", "apply"}:
                method = getattr(store(), f"{api}_personal_operations")
                method(
                    session,
                    state[0].individual_id,
                    proposal,
                    NOW,
                )
            elif api == "entity":
                store().create_entity(
                    session,
                    state[0].individual_id,
                    kind="person",
                    display_name="Observed",
                    now=NOW,
                )
            elif api == "project":
                store().create_project(
                    session,
                    state[0].individual_id,
                    title="Study",
                    desired_state="Understand",
                    rationale="Choice",
                    evidence_refs=[],
                    now=NOW,
                )
            else:
                store().revise_project(
                    session,
                    state[0].individual_id,
                    project,
                    title="Changed",
                    rationale="Choice",
                    evidence_refs=[],
                    now=NOW,
                )
    finally:
        event.remove(db_engine, "before_cursor_execute", record)
    assert "FROM individuals" in statements[0]
    assert "FOR UPDATE" in statements[0]


def test_context_prioritizes_active_goals_over_newer_paused_goals(
    db_session_factory, state
):
    active = decision(
        state,
        goal_operations=[
            operation("goal", state, title=f"Active {i}") for i in range(8)
        ],
    )
    apply(db_session_factory, state, active)
    paused = decision(
        state,
        goal_operations=[
            operation("goal", state, title=f"Paused {i}", requested_status="paused")
            for i in range(8)
        ],
    )
    persist(db_session_factory, state, paused)
    with db_session_factory.begin() as session:
        store().apply_personal_operations(
            session, state[0].individual_id, paused, NOW + timedelta(seconds=1)
        )
    with db_session_factory() as session:
        sections = store().personal_context_sections(session, state[0].individual_id)
        items = [section.content["item"] for section in sections]
        assert len(items) == 8
        assert all(item["status"] == "active" for item in items)


@pytest.mark.parametrize(
    "corruption",
    ["prepared", "applied", "json", "hash", "id", "disposition", "terminal_cycle"],
)
def test_apply_requires_recorded_exact_decision_on_active_cycle(
    db_session_factory, state, corruption
):
    proposal = decision(state, goal_operations=[operation("goal", state)])
    persist(db_session_factory, state, proposal)
    with db_session_factory.begin() as session:
        row = session.get(CognitionTurn, state[2].turn_id)
        if corruption in {"prepared", "applied"}:
            row.status = corruption
        elif corruption == "hash":
            row.decision_hash = "wrong"
        elif corruption == "id":
            row.decision_id = new_id()
        elif corruption == "disposition":
            row.disposition = "wait" if proposal.disposition != "wait" else "sleep"
        elif corruption == "terminal_cycle":
            session.get(CognitionCycle, state[1].cycle_id).status = "completed"
        else:
            row.decision_json = dict(
                row.decision_json, rationale_summary="different persisted proposal"
            )
            row.decision_hash = content_hash(row.decision_json)
    with db_session_factory.begin() as session:
        with pytest.raises(ValueError, match="unrecorded_personal_decision"):
            store().apply_personal_operations(
                session, state[0].individual_id, proposal, NOW
            )
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(models().Goal)) == 0
        assert session.scalar(select(func.count()).select_from(AppliedOperation)) == 0
