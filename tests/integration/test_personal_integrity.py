"""Personal projections retain scoped links and a complete revision history."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, text

from cognition.config.loader import load_config
from cognition.db.checks import check_database
from cognition.db.models.personal import PersonalStateRevision, Project
from cognition.db.personal_checks import check_personal_state
from cognition.protocols.common import Ref, new_id
from cognition.runtime.birth import BirthInput, birth
from cognition.testing.clock import FakeClock

NOW = datetime(2026, 9, 22, tzinfo=UTC)


def development_row(kind, individual_id):
    from cognition.db.models.development import Interest, Preference, SelfState

    common = dict(
        individual_id=individual_id,
        evidence_refs=[],
        rationale="Candidate",
        created_at=NOW,
        updated_at=NOW,
        revision=1,
    )
    if kind == "self_state":
        return SelfState(
            self_state_id=new_id(),
            layer="self_belief",
            content=None,
            pending_content={"value": "Possible trait"},
            pending_evidence_refs=[],
            pending_not_before=NOW + timedelta(days=1),
            **common,
        )
    timing = dict(
        promotion_not_before=NOW + timedelta(days=1), retirement_not_before=None
    )
    if kind == "interest":
        return Interest(
            interest_id=new_id(),
            topic="Space",
            summary="Candidate",
            status="candidate",
            **common,
            **timing,
        )
    return Preference(
        preference_id=new_id(),
        context="Study",
        statement="Possibly books",
        status="tentative",
        **common,
        **timing,
    )


@pytest.mark.parametrize("kind", ["interest", "preference", "self_state"])
def test_development_projections_require_retained_revision_history(
    db_session_factory, personal, kind
):
    with db_session_factory.begin() as session:
        session.add(development_row(kind, personal[0]))
    assert "personal_revision_chain" in {
        f.invariant_id for f in report(db_session_factory).findings
    }


@pytest.mark.parametrize("kind", ["interest", "preference"])
def test_established_development_state_cannot_precede_eligibility(
    db_session_factory, personal, kind
):
    with db_session_factory.begin() as session:
        row = development_row(kind, personal[0])
        row.status = "established"
        session.add(row)
    assert "development_eligibility" in {
        f.invariant_id for f in report(db_session_factory).findings
    }


@pytest.mark.parametrize(
    "corruption,expected",
    [
        ("foreign_pending_evidence", "personal_evidence"),
        ("pending_presentation", "self_state_layers"),
        ("invalid_wrapper", "self_state_layers"),
    ],
)
def test_pending_self_state_retains_layer_and_evidence_boundaries(
    db_session_factory, personal, corruption, expected
):
    with db_session_factory.begin() as session:
        row = development_row("self_state", personal[0])
        if corruption == "foreign_pending_evidence":
            row.pending_evidence_refs = [{"kind": "event", "id": str(new_id())}]
        elif corruption == "pending_presentation":
            row.layer = "current_identity"
        else:
            row.pending_content = {"unexpected": "shape"}
        session.add(row)
    assert expected in {f.invariant_id for f in report(db_session_factory).findings}


def create_personal(factory):
    from cognition.stores.personal import create_project

    config = load_config(Path(__file__).parents[1] / "fixtures/config/valid.toml")
    config.runtime.individual_id = new_id()
    born = birth(
        factory,
        BirthInput(
            individual_id=config.runtime.individual_id,
            birth_name="Personal checks",
            founding_orientation="Keep honest records",
            creator_provenance={},
            admin_authn_provider="local_os",
            admin_subject="test-admin",
            config=config,
            runtime_version="test",
        ),
        FakeClock(NOW),
    )
    with factory.begin() as session:
        project_id = create_project(
            session,
            born.individual_id,
            title="Explore",
            desired_state="Understand",
            rationale="Chosen",
            evidence_refs=[],
            now=NOW,
        )
    return born.individual_id, project_id


@pytest.fixture
def personal(db_session_factory):
    return create_personal(db_session_factory)


def personal_findings(session):
    findings = []
    check_personal_state(session, lambda *finding: findings.append(finding))
    return findings


def test_standalone_checker_does_not_flush_caller_pending_updates(
    db_session_factory,
    personal,
):
    with db_session_factory.begin() as session:
        project = session.get(Project, personal[1])
        project.title = "caller pending title"
        assert personal_findings(session) == []
        assert project in session.dirty
        assert project.title == "caller pending title"
        session.rollback()
    assert report(db_session_factory).healthy


def test_projection_check_normalizes_database_session_timezone(
    db_session_factory,
    personal,
):
    with db_session_factory.begin() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        session.execute(text("SET LOCAL TIME ZONE 'America/Chicago'"))
        assert personal_findings(session) == []


def report(factory):
    with factory.begin() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        return check_database(session)


def test_personal_history_is_clean_without_mutating_dirty_objects(
    db_session_factory, personal
):
    with db_session_factory.begin() as session:
        project = session.get(Project, personal[1])
        project.title = "uncommitted"
        assert check_database(session).healthy
        assert project.title == "uncommitted"
        assert project in session.dirty
        session.rollback()
    assert report(db_session_factory).healthy


@pytest.mark.parametrize(
    "corruption,expected",
    [
        ("missing_history", "personal_revision_chain"),
        ("changed_projection", "personal_projection"),
        ("missing_evidence", "personal_evidence"),
        ("bad_history_event", "personal_revision_provenance"),
        ("bad_history_snapshot", "personal_revision_chain"),
    ],
)
def test_personal_corruption_is_reported_without_repair(
    db_session_factory, personal, corruption, expected
):
    with db_session_factory.begin() as session:
        project = session.get(Project, personal[1])
        history = session.scalar(select(PersonalStateRevision))
        if corruption == "missing_history":
            session.delete(history)
        elif corruption == "changed_projection":
            project.title = "corrupted secret canary"
        elif corruption == "missing_evidence":
            project.evidence_refs = [{"kind": "event", "id": str(new_id())}]
        elif corruption == "bad_history_event":
            session.execute(text("SET LOCAL session_replication_role = replica"))
            history.event_id = new_id()
        else:
            history.before_json = {"false_previous_state": True}
    result = report(db_session_factory)
    assert expected in {finding.invariant_id for finding in result.findings}
    assert "corrupted secret canary" not in str(result)
    assert result == report(db_session_factory)


def run_personal_decisions(engine, individual_id, changes):
    import json

    from cognition.protocols.model_v1 import ModelResultV1
    from cognition.runtime.cognition import CognitionRuntime
    from cognition.runtime.ownership import acquire_runtime_ownership
    from cognition.testing.scripted_model import ScriptedModelAdapter

    def response(fields):
        def respond(request):
            value = json.loads(
                (Path(__file__).parents[1] / "golden/model_result_v1.json").read_text()
            )
            value["request_id"] = str(request.request_id)
            value["decision"].update(
                decision_id=str(new_id()),
                cycle_id=str(request.cycle_id),
                turn_id=str(request.turn_id),
                **fields,
            )
            return ModelResultV1.model_validate(value)

        return respond

    clock = FakeClock(NOW)
    with acquire_runtime_ownership(
        engine,
        individual_id,
        clock=clock,
        host_id="integrity-test",
        process_id=2,
        runtime_version="test",
    ) as owner:
        result = CognitionRuntime(
            owner,
            individual_id,
            ScriptedModelAdapter([response(c) for c in changes]),
            clock,
        ).run_once()
    assert result.status == "completed"


@pytest.mark.parametrize("link", ["goal_project", "project_evidence", "revision_event"])
def test_foreign_owned_links_are_reported_even_when_their_ids_exist(
    db_engine,
    db_session_factory,
    personal,
    link,
):
    from cognition.db.models.evidence import Event
    from cognition.db.models.personal import Goal

    foreign_id, foreign_project_id = create_personal(db_session_factory)
    operation_id = new_id()
    run_personal_decisions(
        db_engine,
        personal[0],
        [
            {
                "goal_operations": [
                    {
                        "operation_id": str(operation_id),
                        "op": "create",
                        "goal_id": None,
                        "title": "Owned objective",
                        "desired_state": "Grounded understanding",
                        "project_id": str(personal[1]),
                        "requested_status": None,
                        "origin": None,
                        "rationale": "Chosen",
                        "evidence_refs": [],
                    }
                ]
            }
        ],
    )
    assert report(db_session_factory).healthy
    with db_session_factory.begin() as session:
        if link == "goal_project":
            session.get(Goal, operation_id).project_id = foreign_project_id
            expected = "personal_ownership"
        elif link == "project_evidence":
            session.get(Project, personal[1]).evidence_refs = [
                {"kind": "project", "id": str(foreign_project_id)},
            ]
            expected = "personal_evidence"
        else:
            foreign_event_id = session.scalar(
                select(Event.event_id).where(
                    Event.individual_id == foreign_id,
                )
            )
            history = session.scalar(
                select(PersonalStateRevision).where(
                    PersonalStateRevision.object_id == operation_id,
                )
            )
            history.event_id = foreign_event_id
            expected = "personal_revision_provenance"
    result = report(db_session_factory)
    assert expected in {finding.invariant_id for finding in result.findings}
    assert result == report(db_session_factory)


@pytest.mark.parametrize("corruption", [None, "predecessor_status", "self_reference"])
def test_belief_supersession_preserves_two_honest_histories(
    db_engine,
    db_session_factory,
    personal,
    corruption,
):
    from cognition.db.models.evidence import Event
    from cognition.db.models.personal import Belief

    original_id, replacement_id = new_id(), new_id()
    with db_session_factory() as session:
        evidence_id = session.scalar(
            select(Event.event_id).where(
                Event.individual_id == personal[0],
                Event.event_type == "individual.born",
            )
        )
    original = {
        "operation_id": str(original_id),
        "op": "create",
        "belief_id": None,
        "proposition": "Initial interpretation",
        "subject_entity_id": None,
        "topic": "evidence",
        "requested_status": "accepted",
        "supporting_evidence": [{"kind": "event", "id": str(evidence_id)}],
        "contradicting_evidence": [],
        "supersedes_belief_id": None,
        "rationale": "Grounded initial interpretation",
    }
    replacement = dict(
        original,
        operation_id=str(replacement_id),
        op="supersede",
        proposition="Revised interpretation",
        supersedes_belief_id=str(original_id),
        rationale="Reinterpret the evidence",
    )
    run_personal_decisions(
        db_engine,
        personal[0],
        [
            {"disposition": "continue", "belief_operations": [original]},
            {"belief_operations": [replacement]},
        ],
    )
    clean = report(db_session_factory)
    assert clean.healthy, clean.findings
    with db_session_factory.begin() as session:
        old = session.get(Belief, original_id)
        new = session.get(Belief, replacement_id)
        assert (
            old.status == "superseded" and old.proposition == "Initial interpretation"
        )
        assert new.supersedes_belief_id == original_id
        revisions = session.scalars(
            select(PersonalStateRevision).where(
                PersonalStateRevision.object_kind == "belief",
            )
        ).all()
        assert len(revisions) == 3
        old_change = next(
            r for r in revisions if r.object_id == original_id and r.revision == 2
        )
        new_change = next(r for r in revisions if r.object_id == replacement_id)
        assert old_change.operation_id is None
        assert new_change.operation_id == replacement_id
        assert old_change.turn_id == new_change.turn_id
        assert old_change.event_id == new_change.event_id
        if corruption == "predecessor_status":
            old.status = "accepted"
        elif corruption == "self_reference":
            new.supersedes_belief_id = replacement_id
    result = report(db_session_factory)
    if corruption is None:
        assert result.healthy
    else:
        assert "belief_supersession" in {
            finding.invariant_id for finding in result.findings
        }
    assert result == report(db_session_factory)


def social_records(factory, owner, project_id):
    from cognition.stores.personal import create_entity
    from cognition.stores.relationships import (
        create_relationship,
        create_relationship_thread,
    )

    with factory.begin() as session:
        entity_id = create_entity(
            session, owner, kind="person", display_name="Known person", now=NOW
        )
        relationship_id = create_relationship(
            session,
            owner,
            entity_id=entity_id,
            narrative="A subjective account",
            rationale="Retain the interpretation",
            evidence_refs=[Ref(kind="project", id=project_id)],
            now=NOW,
        )
        thread_id = create_relationship_thread(
            session,
            owner,
            relationship_id=relationship_id,
            title="Open question",
            summary="An unresolved conversation",
            rationale="Retain the question",
            evidence_refs=[Ref(kind="project", id=project_id)],
            now=NOW,
        )
    return entity_id, relationship_id, thread_id


@pytest.mark.parametrize(
    "corruption,expected",
    [
        (None, None),
        ("foreign_entity", "personal_ownership"),
        ("foreign_relationship", "personal_ownership"),
        ("missing_evidence", "personal_evidence"),
        ("empty_evidence", "personal_evidence"),
        ("changed_projection", "personal_projection"),
        ("missing_history", "personal_revision_chain"),
    ],
)
def test_relationship_integrity_covers_social_ownership_and_history(
    db_session_factory, personal, corruption, expected
):
    from cognition.db.models.relationships import Relationship, RelationshipThread

    _, relationship_id, thread_id = social_records(db_session_factory, *personal)
    foreign = create_personal(db_session_factory)
    foreign_entity, foreign_relationship, _ = social_records(
        db_session_factory, *foreign
    )
    assert report(db_session_factory).healthy
    with db_session_factory.begin() as session:
        relationship = session.get(Relationship, relationship_id)
        thread = session.get(RelationshipThread, thread_id)
        if corruption == "foreign_entity":
            relationship.entity_id = foreign_entity
        elif corruption == "foreign_relationship":
            thread.relationship_id = foreign_relationship
        elif corruption == "missing_evidence":
            thread.evidence_refs = [{"kind": "event", "id": str(new_id())}]
        elif corruption == "empty_evidence":
            relationship.evidence_refs = []
        elif corruption == "changed_projection":
            relationship.narrative = "Corruption canary"
        elif corruption == "missing_history":
            history = session.scalar(
                select(PersonalStateRevision).where(
                    PersonalStateRevision.object_id == thread_id
                )
            )
            session.delete(history)
    result = report(db_session_factory)
    if expected is None:
        assert result.healthy, result.findings
    else:
        assert expected in {f.invariant_id for f in result.findings}
    assert "Corruption canary" not in str(result)
    assert report(db_session_factory) == result


@pytest.mark.parametrize("kind", ["relationship", "relationship_thread"])
def test_social_history_cannot_silently_reparent_an_existing_object(
    db_session_factory, personal, kind
):
    from cognition.db.models.relationships import Relationship, RelationshipThread
    from cognition.stores.relationships import (
        revise_relationship,
        revise_relationship_thread,
    )

    _, relationship_id, thread_id = social_records(db_session_factory, *personal)
    other_entity, other_relationship, _ = social_records(db_session_factory, *personal)
    with db_session_factory.begin() as session:
        args = dict(
            rationale="Reconsidered",
            evidence_refs=[Ref(kind="project", id=personal[1])],
            now=NOW + timedelta(seconds=1),
        )
        if kind == "relationship":
            revise_relationship(
                session, personal[0], relationship_id, narrative="New account", **args
            )
        else:
            revise_relationship_thread(
                session, personal[0], thread_id, summary="New question", **args
            )
    assert report(db_session_factory).healthy
    with db_session_factory.begin() as session:
        if kind == "relationship":
            # Avoid the entity uniqueness constraint while changing an owned parent.
            from cognition.stores.personal import create_entity

            other_entity = create_entity(
                session, personal[0], kind="person", display_name="Third", now=NOW
            )
            identity, field, replacement = relationship_id, "entity_id", other_entity
            row = session.get(Relationship, identity)
        else:
            identity, field, replacement = (
                thread_id,
                "relationship_id",
                other_relationship,
            )
            row = session.get(RelationshipThread, identity)
        setattr(row, field, replacement)
        history = session.scalar(
            select(PersonalStateRevision).where(
                PersonalStateRevision.object_id == identity,
                PersonalStateRevision.revision == 2,
            )
        )
        history.after_json = {**history.after_json, field: str(replacement)}
    assert "personal_parent_identity" in {
        f.invariant_id for f in report(db_session_factory).findings
    }
