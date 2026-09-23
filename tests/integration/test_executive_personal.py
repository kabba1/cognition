"""V2 joins every personal family under one detached plan and exact D1 guard."""

import importlib
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select
from test_integrity_checks import NOW, create_healthy

from cognition.db.models.cognition import AppliedOperation, CognitionTurn
from cognition.db.models.evidence import Event
from cognition.db.models.personal import Entity, PersonalStateRevision, Project
from cognition.db.models.relationships import Relationship, RelationshipThread
from cognition.protocols.common import Ref, new_id
from cognition.stores.cognition import (
    CycleLimits,
    claim_or_resume,
    content_hash,
    latest_turn,
)
from cognition.stores.personal import (
    apply_personal_operations,
    create_entity,
    create_project,
    validate_personal_operations,
)
from cognition.stores.relationships import (
    create_relationship,
    create_relationship_thread,
)

FAMILIES = ("entity", "project", "relationship", "relationship_thread")
MODELS = dict(
    entity=Entity,
    project=Project,
    relationship=Relationship,
    relationship_thread=RelationshipThread,
)
GOLDEN = Path(__file__).parents[1] / "golden/cognition_decision_v1.json"


@pytest.fixture
def state(db_session_factory):
    person = create_healthy(db_session_factory)
    with db_session_factory.begin() as session:
        entity = create_entity(
            session, person.individual_id, kind="person", display_name="Known", now=NOW
        )
        other_entity = create_entity(
            session,
            person.individual_id,
            kind="person",
            display_name="Other known",
            now=NOW,
        )
        relationship = create_relationship(
            session,
            person.individual_id,
            entity_id=entity,
            narrative="An acquaintance",
            rationale="Observed",
            evidence_refs=[Ref(kind="event", id=person.genesis_event_id)],
            now=NOW,
        )
        cycle = claim_or_resume(session, person.individual_id, NOW, CycleLimits())
        turn = latest_turn(session, cycle.cycle_id)
    return person, cycle, turn, entity, relationship, other_entity


def operation(state, family, **updates):
    data = dict(
        operation_id=new_id(),
        op="create",
        evidence_refs=[{"kind": "event", "id": state[0].genesis_event_id}],
        rationale="Deliberate choice",
    )
    data.update(
        {
            "entity": dict(entity_id=None, kind="person", display_name="Morgan"),
            "project": dict(
                project_id=None,
                title="Learn",
                desired_state="Understand",
                requested_status=None,
            ),
            "relationship": dict(
                relationship_id=None,
                entity_id=state[5],
                narrative="A working relationship",
            ),
            "relationship_thread": dict(
                thread_id=None,
                relationship_id=state[4],
                title="Follow up",
                summary="Discuss next steps",
                commitment_id=None,
                requested_status=None,
            ),
        }[family]
    )
    data.update(updates)
    return data


def proposal(state, **families):
    data = json.loads(GOLDEN.read_text())
    for key in data:
        if key.endswith("_operations") or key.endswith("_requests"):
            data[key] = []
    data.update(
        schema_version=2,
        current_focus=None,
        cycle_id=state[1].cycle_id,
        turn_id=state[2].turn_id,
        decision_id=new_id(),
    )
    data.update({f"{family}_operations": [] for family in FAMILIES})
    data.update(families)
    return importlib.import_module("cognition.protocols.executive").parse_decision(data)


def persist(factory, state, value):
    with factory.begin() as session:
        turn = session.get(CognitionTurn, state[2].turn_id)
        turn.status = "decided"
        turn.decision_id = value.decision_id
        turn.decision_json = value.model_dump(mode="json")
        turn.decision_hash = content_hash(turn.decision_json)
        turn.disposition = value.disposition


def apply(factory, state, value):
    persist(factory, state, value)
    with factory.begin() as session:
        apply_personal_operations(session, state[0].individual_id, value, NOW)


def errors(factory, state, value):
    with factory.begin() as session:
        return validate_personal_operations(session, state[0].individual_id, value, NOW)


def test_eleven_family_batch_is_one_exact_application(db_session_factory, state):
    old = json.loads(GOLDEN.read_text())
    families = {}
    for name, items in old.items():
        if name.endswith("_operations"):
            for item in items:
                item["operation_id"] = str(new_id())
                if "evidence_refs" in item:
                    item["evidence_refs"] = [
                        {"kind": "event", "id": str(state[0].genesis_event_id)}
                    ]
            families[name] = items
    families.update(
        {f"{family}_operations": [operation(state, family)] for family in FAMILIES}
    )
    value = proposal(state, **families)
    assert errors(db_session_factory, state, value) == ()
    apply(db_session_factory, state, value)
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AppliedOperation)) == 11
        histories = session.scalars(
            select(PersonalStateRevision).where(
                PersonalStateRevision.turn_id == state[2].turn_id
            )
        ).all()
        assert len(histories) == 11
        assert {item.object_kind for item in histories} == {
            "goal",
            "commitment",
            "belief",
            "episode",
            "interest",
            "preference",
            "self_state",
            *FAMILIES,
        }
        for history in histories:
            event = session.get(Event, history.event_id)
            assert event.source_kind == "model"
            assert history.operation_id is not None
        assert session.get(
            CognitionTurn, state[2].turn_id
        ).decision_json == value.model_dump(mode="json")


@pytest.mark.parametrize("family", FAMILIES)
def test_new_families_require_exact_recorded_decision(
    db_session_factory, state, family
):
    value = proposal(state, **{f"{family}_operations": [operation(state, family)]})
    with db_session_factory.begin() as session:
        with pytest.raises(ValueError, match="unrecorded_personal_decision"):
            apply_personal_operations(session, state[0].individual_id, value, NOW)
        assert session.scalar(select(func.count()).select_from(AppliedOperation)) == 0
    persist(db_session_factory, state, value)
    value.rationale_summary = "Changed after retention"
    with db_session_factory.begin() as session:
        with pytest.raises(ValueError, match="unrecorded_personal_decision"):
            apply_personal_operations(session, state[0].individual_id, value, NOW)


def test_invalid_last_family_rejects_earlier_valid_changes(db_session_factory, state):
    value = proposal(
        state,
        entity_operations=[operation(state, "entity")],
        project_operations=[operation(state, "project")],
        relationship_thread_operations=[
            operation(state, "relationship_thread", commitment_id=new_id())
        ],
    )
    assert "unknown_ref" in errors(db_session_factory, state, value)
    persist(db_session_factory, state, value)
    with db_session_factory.begin() as session:
        with pytest.raises(ValueError, match="unknown_ref"):
            apply_personal_operations(session, state[0].individual_id, value, NOW)
        assert session.scalar(select(func.count()).select_from(AppliedOperation)) == 0
        assert session.get(Entity, value.entity_operations[0].operation_id) is None
        assert session.get(Project, value.project_operations[0].operation_id) is None


@pytest.mark.parametrize(
    "link", ["relationship_entity", "thread_relationship", "goal_project", "evidence"]
)
def test_sibling_created_references_are_not_available(db_session_factory, state, link):
    entity = operation(state, "entity")
    relationship = operation(state, "relationship")
    project = operation(state, "project")
    families = dict(
        entity_operations=[entity],
        project_operations=[project],
        relationship_operations=[relationship],
    )
    if link == "relationship_entity":
        relationship["entity_id"] = entity["operation_id"]
    elif link == "thread_relationship":
        families["relationship_thread_operations"] = [
            operation(
                state,
                "relationship_thread",
                relationship_id=relationship["operation_id"],
            )
        ]
    elif link == "goal_project":
        goal = json.loads(GOLDEN.read_text())["goal_operations"][0]
        goal.update(operation_id=str(new_id()), project_id=str(project["operation_id"]))
        families["goal_operations"] = [goal]
    else:
        project["evidence_refs"] = [{"kind": "entity", "id": entity["operation_id"]}]
    assert "unknown_ref" in errors(
        db_session_factory, state, proposal(state, **families)
    )


def test_duplicate_relationship_entity_is_rejected_before_any_write(
    db_session_factory, state
):
    value = proposal(
        state,
        relationship_operations=[
            operation(state, "relationship"),
            operation(state, "relationship"),
        ],
    )
    assert "relationship_already_exists" in errors(db_session_factory, state, value)


@pytest.mark.parametrize("family", FAMILIES)
def test_global_operation_id_replay_and_target_duplicates(
    db_session_factory, state, family
):
    op = operation(state, family)
    value = proposal(
        state,
        **{
            f"{family}_operations": [
                op,
                {
                    **op,
                    "operation_id": new_id(),
                    (
                        "thread_id"
                        if family == "relationship_thread"
                        else f"{family}_id"
                    ): op["operation_id"],
                },
            ]
        },
    )
    assert "duplicate_personal_target" in errors(db_session_factory, state, value)
    original = proposal(state, **{f"{family}_operations": [op]})
    apply(db_session_factory, state, original)
    assert "operation_id_already_applied" in errors(db_session_factory, state, original)


@pytest.mark.parametrize("family", FAMILIES)
def test_blank_rationale_rejected(db_session_factory, state, family):
    value = proposal(
        state, **{f"{family}_operations": [operation(state, family, rationale=" ")]}
    )
    assert "empty_personal_content" in errors(db_session_factory, state, value)


def test_entity_kind_and_social_parent_cannot_be_revised(db_session_factory, state):
    value = proposal(
        state,
        entity_operations=[
            operation(
                state,
                "entity",
                op="revise",
                entity_id=state[3],
                display_name="New name",
            )
        ],
        relationship_operations=[
            operation(state, "relationship", op="revise", relationship_id=state[4])
        ],
    )
    assert "incompatible_operation_fields" in errors(db_session_factory, state, value)


def test_valid_entity_rename_preserves_kind_and_exact_original(
    db_session_factory, state
):
    value = proposal(
        state,
        entity_operations=[
            operation(
                state,
                "entity",
                op="revise",
                entity_id=state[3],
                kind=None,
                display_name="Renamed",
            )
        ],
    )
    apply(db_session_factory, state, value)
    with db_session_factory() as session:
        row = session.get(Entity, state[3])
        assert (
            row.kind == "person" and row.display_name == "Renamed" and row.revision == 2
        )
        history = session.scalar(
            select(PersonalStateRevision).where(
                PersonalStateRevision.object_id == state[3],
                PersonalStateRevision.revision == 2,
            )
        )
        assert history.before_json["display_name"] == "Known"
        assert history.after_json["display_name"] == "Renamed"


PROJECT_NEXT = {
    "active": {"paused", "blocked", "completed", "abandoned"},
    "paused": {"active", "blocked", "completed", "abandoned"},
    "blocked": {"active", "paused", "completed", "abandoned"},
    "completed": set(),
    "abandoned": set(),
}


@pytest.mark.parametrize(
    "previous,requested", [(old, new) for old in PROJECT_NEXT for new in PROJECT_NEXT]
)
def test_project_transition_matrix(db_session_factory, state, previous, requested):
    with db_session_factory.begin() as session:
        identity = create_project(
            session,
            state[0].individual_id,
            title="Plan",
            desired_state="Understand",
            rationale="Chosen",
            evidence_refs=[],
            now=NOW,
        )
        session.get(Project, identity).status = previous
    value = proposal(
        state,
        project_operations=[
            operation(
                state,
                "project",
                op="set_status",
                project_id=identity,
                title=None,
                desired_state=None,
                requested_status=requested,
            )
        ],
    )
    actual = errors(db_session_factory, state, value)
    if requested in PROJECT_NEXT[previous]:
        assert actual == ()
        apply(db_session_factory, state, value)
        with db_session_factory() as session:
            assert session.get(Project, identity).status == requested
    else:
        assert "invalid_project_transition" in actual


@pytest.mark.parametrize(
    "previous,requested",
    [
        (old, new)
        for old in ("open", "resolved", "abandoned")
        for new in ("open", "resolved", "abandoned")
    ],
)
def test_thread_transition_matrix(db_session_factory, state, previous, requested):
    with db_session_factory.begin() as session:
        identity = create_relationship_thread(
            session,
            state[0].individual_id,
            relationship_id=state[4],
            title="Discuss",
            summary="Plan discussion",
            rationale="Topic",
            evidence_refs=[Ref(kind="event", id=state[0].genesis_event_id)],
            now=NOW,
        )
        session.get(RelationshipThread, identity).status = previous
    value = proposal(
        state,
        relationship_thread_operations=[
            operation(
                state,
                "relationship_thread",
                op="set_status",
                thread_id=identity,
                relationship_id=None,
                title=None,
                summary=None,
                requested_status=requested,
            )
        ],
    )
    actual = errors(db_session_factory, state, value)
    if previous == "open" and requested in {"resolved", "abandoned"}:
        assert actual == ()
        apply(db_session_factory, state, value)
        with db_session_factory() as session:
            assert session.get(RelationshipThread, identity).status == requested
    else:
        assert "invalid_relationship_thread_transition" in actual


@pytest.mark.parametrize("family", ["project", "relationship_thread"])
def test_terminal_objects_cannot_be_revised(db_session_factory, state, family):
    with db_session_factory.begin() as session:
        if family == "project":
            identity = create_project(
                session,
                state[0].individual_id,
                title="Plan",
                desired_state="Understand",
                rationale="Chosen",
                evidence_refs=[],
                now=NOW,
            )
            session.get(Project, identity).status = "completed"
            op = operation(
                state,
                family,
                op="revise",
                project_id=identity,
                title="New",
                desired_state=None,
            )
        else:
            identity = create_relationship_thread(
                session,
                state[0].individual_id,
                relationship_id=state[4],
                title="Discuss",
                summary="Plan discussion",
                rationale="Topic",
                evidence_refs=[Ref(kind="event", id=state[0].genesis_event_id)],
                now=NOW,
            )
            session.get(RelationshipThread, identity).status = "resolved"
            op = operation(
                state,
                family,
                op="revise",
                thread_id=identity,
                relationship_id=None,
                title="New",
                summary=None,
            )
    value = proposal(state, **{f"{family}_operations": [op]})
    assert f"invalid_{family}_transition" in errors(db_session_factory, state, value)


def test_relationship_replacement_support_does_not_inherit_previous_claim(
    db_session_factory, state
):
    value = proposal(
        state,
        relationship_operations=[
            operation(
                state,
                "relationship",
                op="revise",
                relationship_id=state[4],
                entity_id=None,
                narrative="A revised assessment",
                evidence_refs=[{"kind": "entity", "id": state[3]}],
            )
        ],
    )
    apply(db_session_factory, state, value)
    with db_session_factory() as session:
        row = session.get(Relationship, state[4])
        assert row.evidence_refs == [{"kind": "entity", "id": str(state[3])}]
        history = session.scalar(
            select(PersonalStateRevision).where(
                PersonalStateRevision.object_id == state[4],
                PersonalStateRevision.revision == 2,
            )
        )
        assert history.before_json["evidence_refs"] == [
            {"kind": "event", "id": str(state[0].genesis_event_id)}
        ]
        assert row.entity_id == state[3]


@pytest.mark.parametrize("family", FAMILIES)
def test_foreign_evidence_rejected_in_every_new_family(
    db_session_factory, state, family
):
    other = create_healthy(db_session_factory)
    value = proposal(
        state,
        **{
            f"{family}_operations": [
                operation(
                    state,
                    family,
                    evidence_refs=[{"kind": "event", "id": other.genesis_event_id}],
                )
            ]
        },
    )
    assert "unknown_ref" in errors(db_session_factory, state, value)


def test_foreign_entities_and_relationship_links_rejected(db_session_factory, state):
    other = create_healthy(db_session_factory)
    with db_session_factory.begin() as session:
        entity_id = create_entity(
            session, other.individual_id, kind="person", display_name="Foreign", now=NOW
        )
        relationship_id = create_relationship(
            session,
            other.individual_id,
            entity_id=entity_id,
            narrative="Foreign relation",
            rationale="Observed",
            evidence_refs=[Ref(kind="event", id=other.genesis_event_id)],
            now=NOW,
        )
    value = proposal(
        state,
        entity_operations=[
            operation(state, "entity", op="revise", entity_id=entity_id, kind=None)
        ],
        relationship_operations=[operation(state, "relationship", entity_id=entity_id)],
        relationship_thread_operations=[
            operation(state, "relationship_thread", relationship_id=relationship_id)
        ],
    )
    actual = errors(db_session_factory, state, value)
    assert "unknown_ref" in actual and "unknown_personal_target" in actual


def test_interrupted_multi_family_application_rolls_back_and_replays_exact_d1(
    db_session_factory, state, monkeypatch
):
    personal = importlib.import_module("cognition.stores.personal")
    value = proposal(
        state,
        entity_operations=[operation(state, "entity")],
        project_operations=[operation(state, "project")],
    )
    persist(db_session_factory, state, value)
    original = personal._write_changes
    calls = []

    def fail_after_write(session, individual_id, changes, now, **kwargs):
        original(session, individual_id, changes, now, **kwargs)
        calls.append(changes[0].kind)
        if changes[0].kind == "project":
            raise RuntimeError("interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(personal, "_write_changes", fail_after_write)
        with pytest.raises(RuntimeError, match="interrupted"):
            with db_session_factory.begin() as session:
                apply_personal_operations(session, state[0].individual_id, value, NOW)
    assert calls == ["entity", "project"]
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AppliedOperation)) == 0
        assert session.get(Entity, value.entity_operations[0].operation_id) is None
        assert session.get(Project, value.project_operations[0].operation_id) is None
        assert session.get(
            CognitionTurn, state[2].turn_id
        ).decision_json == value.model_dump(mode="json")
    with db_session_factory.begin() as session:
        apply_personal_operations(session, state[0].individual_id, value, NOW)
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AppliedOperation)) == 2


def test_v2_store_global_limit_counts_all_new_families(db_session_factory, state):
    value = proposal(
        state,
        entity_operations=[operation(state, "entity") for _ in range(33)],
        project_operations=[operation(state, "project") for _ in range(32)],
    )
    assert errors(db_session_factory, state, value) == ("too_many_operations",)
