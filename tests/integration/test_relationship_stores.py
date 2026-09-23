"""Social interpretations retain owned support and exact transactional history."""

import importlib
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from test_integrity_checks import NOW, create_healthy

from cognition.db.models.evidence import Event, EventContent
from cognition.db.models.personal import Commitment, Entity, PersonalStateRevision
from cognition.protocols.common import Ref, new_id
from cognition.stores.personal import create_entity


def store():
    return importlib.import_module("cognition.stores.relationships")


def models():
    return importlib.import_module("cognition.db.models.relationships")


@pytest.fixture
def state(db_session_factory):
    person = create_healthy(db_session_factory)
    with db_session_factory.begin() as session:
        entity_id = create_entity(
            session, person.individual_id, kind="person", display_name="Morgan", now=NOW
        )
    return person, entity_id


def evidence(state):
    return [Ref(kind="event", id=state[0].genesis_event_id)]


def create(session, state, **kwargs):
    values = dict(
        entity_id=state[1],
        narrative="We are learning to collaborate.",
        rationale="Observed introduction",
        evidence_refs=evidence(state),
        now=NOW,
    )
    values.update(kwargs)
    return store().create_relationship(session, state[0].individual_id, **values)


def thread(session, state, relationship_id, **kwargs):
    values = dict(
        relationship_id=relationship_id,
        title="Discuss the plan",
        summary="The next conversation concerns the plan.",
        rationale="A topic remains open",
        evidence_refs=evidence(state),
        now=NOW,
    )
    values.update(kwargs)
    return store().create_relationship_thread(session, state[0].individual_id, **values)


def test_replacement_has_claim_specific_support_and_exact_history(
    db_session_factory, state
):
    with db_session_factory.begin() as session:
        identity = create(session, state)
        store().revise_relationship(
            session,
            state[0].individual_id,
            identity,
            narrative="We disagree about the plan.",
            rationale="A revised interpretation",
            evidence_refs=[Ref(kind="entity", id=state[1])],
            now=NOW + timedelta(hours=1),
        )
    with db_session_factory() as session:
        row = session.get(models().Relationship, identity)
        revisions = session.scalars(
            select(PersonalStateRevision)
            .where(PersonalStateRevision.object_id == identity)
            .order_by(PersonalStateRevision.revision)
        ).all()
        assert row.revision == 2 and row.entity_id == state[1]
        assert row.evidence_refs == [{"kind": "entity", "id": str(state[1])}]
        assert revisions[0].before_json is None
        assert revisions[1].before_json == revisions[0].after_json
        assert revisions[1].before_json["evidence_refs"] == [
            ref.model_dump(mode="json") for ref in evidence(state)
        ]
        assert revisions[1].after_json["evidence_refs"] == row.evidence_refs
        event = session.get(Event, revisions[1].event_id)
        assert event.event_type == "personal.relationship.revise"
        assert event.source_kind == "runtime"
        payload = session.get(EventContent, event.event_id).payload
        assert payload["changes"][0]["after"] == revisions[1].after_json


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("narrative", " ", "empty_personal_content"),
        ("narrative", None, "empty_personal_content"),
        ("rationale", "\t", "empty_personal_content"),
        ("rationale", None, "empty_personal_content"),
        ("evidence_refs", [], "missing_relationship_evidence"),
        (
            "evidence_refs",
            [{"kind": "event", "id": str(new_id())}],
            "invalid_reference",
        ),
        ("evidence_refs", [Ref(kind="event", id=new_id())], "unknown_ref"),
        ("entity_id", new_id(), "unknown_relationship_entity"),
    ],
)
def test_relationship_creation_rejects_invalid_input(
    db_session_factory, state, field, value, error
):
    with db_session_factory.begin() as session:
        with pytest.raises(ValueError, match=error):
            create(session, state, **{field: value})
        assert (
            session.scalar(select(func.count()).select_from(models().Relationship)) == 0
        )


def test_foreign_links_and_duplicate_entity_are_rejected(db_session_factory, state):
    other = create_healthy(db_session_factory)
    with db_session_factory.begin() as session:
        foreign_entity = create_entity(
            session, other.individual_id, kind="person", display_name="Other", now=NOW
        )
        with pytest.raises(ValueError, match="unknown_relationship_entity"):
            create(session, state, entity_id=foreign_entity)
        with pytest.raises(ValueError, match="unknown_ref"):
            create(
                session,
                state,
                evidence_refs=[Ref(kind="event", id=other.genesis_event_id)],
            )
        create(session, state)
        with pytest.raises(ValueError, match="relationship_already_exists"):
            create(session, state)


@pytest.mark.parametrize("status", ["resolved", "abandoned"])
def test_thread_terminal_transition_preserves_history_and_cannot_reopen(
    db_session_factory, state, status
):
    with db_session_factory.begin() as session:
        relationship_id = create(session, state)
        identity = thread(session, state, relationship_id)
        store().revise_relationship_thread(
            session,
            state[0].individual_id,
            identity,
            status=status,
            rationale="Conversation concluded",
            evidence_refs=evidence(state),
            now=NOW + timedelta(hours=1),
        )
        for requested in (None, "open", status):
            with pytest.raises(
                ValueError, match="invalid_relationship_thread_transition"
            ):
                store().revise_relationship_thread(
                    session,
                    state[0].individual_id,
                    identity,
                    status=requested,
                    rationale="Attempted reopen",
                    evidence_refs=evidence(state),
                    now=NOW + timedelta(hours=2),
                )
        row = session.get(models().RelationshipThread, identity)
        assert row.status == status and row.revision == 2
        assert row.relationship_id == relationship_id
        revisions = session.scalars(
            select(PersonalStateRevision)
            .where(PersonalStateRevision.object_id == identity)
            .order_by(PersonalStateRevision.revision)
        ).all()
        assert revisions[1].before_json == revisions[0].after_json


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("title", " ", "empty_personal_content"),
        ("summary", "", "empty_personal_content"),
        ("rationale", "", "empty_personal_content"),
        ("evidence_refs", [], "missing_relationship_evidence"),
        ("relationship_id", new_id(), "unknown_relationship_target"),
        ("commitment_id", new_id(), "unknown_relationship_commitment"),
    ],
)
def test_thread_creation_rejects_invalid_input(
    db_session_factory, state, field, value, error
):
    with db_session_factory.begin() as session:
        relationship_id = create(session, state)
        if field == "relationship_id":
            relationship_id = value
            kwargs = {}
        else:
            kwargs = {field: value}
        with pytest.raises(ValueError, match=error):
            thread(session, state, relationship_id, **kwargs)
        assert (
            session.scalar(
                select(func.count()).select_from(models().RelationshipThread)
            )
            == 0
        )


def test_noop_revisions_do_not_produce_events(db_session_factory, state):
    with db_session_factory.begin() as session:
        relationship_id = create(session, state)
        thread_id = thread(session, state, relationship_id)
        before = session.scalar(select(func.count()).select_from(Event))
        for method, identity, rationale in (
            (store().revise_relationship, relationship_id, "Observed introduction"),
            (store().revise_relationship_thread, thread_id, "A topic remains open"),
        ):
            with pytest.raises(ValueError, match="no_op_personal_revision"):
                method(
                    session,
                    state[0].individual_id,
                    identity,
                    rationale=rationale,
                    evidence_refs=evidence(state),
                    now=NOW + timedelta(hours=1),
                )
        assert session.scalar(select(func.count()).select_from(Event)) == before


def test_rollback_removes_projection_events_and_history(db_session_factory, state):
    with db_session_factory() as session:
        before = session.scalar(select(func.count()).select_from(Event))
    with pytest.raises(RuntimeError, match="abort"):
        with db_session_factory.begin() as session:
            identity = create(session, state)
            thread(session, state, identity)
            raise RuntimeError("abort")
    with db_session_factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(models().Relationship)) == 0
        )
        assert (
            session.scalar(
                select(func.count()).select_from(models().RelationshipThread)
            )
            == 0
        )
        assert session.scalar(select(func.count()).select_from(Event)) == before
        assert (
            session.scalar(
                select(func.count())
                .select_from(PersonalStateRevision)
                .where(
                    PersonalStateRevision.object_kind.in_(
                        ["relationship", "relationship_thread"]
                    )
                )
            )
            == 0
        )


def test_stale_session_refreshes_before_revision(db_session_factory, state):
    with db_session_factory.begin() as session:
        identity = create(session, state)
    with db_session_factory() as stale:
        cached = stale.get(models().Relationship, identity)
        with db_session_factory.begin() as fresh:
            store().revise_relationship(
                fresh,
                state[0].individual_id,
                identity,
                narrative="New observation",
                rationale="Second",
                evidence_refs=evidence(state),
                now=NOW + timedelta(hours=1),
            )
        store().revise_relationship(
            stale,
            state[0].individual_id,
            identity,
            rationale="Third",
            evidence_refs=evidence(state),
            now=NOW + timedelta(hours=2),
        )
        stale.commit()
        assert cached.narrative == "New observation" and cached.revision == 3


@pytest.mark.parametrize("mutation", ["dirty", "new", "deleted"])
def test_unflushed_social_state_is_rejected_without_losing_edits(
    db_session_factory, state, mutation
):
    with db_session_factory.begin() as session:
        identity = create(session, state)
    with db_session_factory() as session:
        row = session.get(models().Relationship, identity)
        if mutation == "dirty":
            row.narrative = "Unflushed edit"
        elif mutation == "deleted":
            session.delete(row)
        else:
            session.add(models().RelationshipThread(thread_id=new_id()))
        with pytest.raises(ValueError, match="unflushed_personal_state"):
            store().revise_relationship(
                session,
                state[0].individual_id,
                identity,
                narrative="Overwrite",
                rationale="Attempt",
                evidence_refs=evidence(state),
                now=NOW,
            )
        if mutation == "dirty":
            assert row.narrative == "Unflushed edit"
        session.rollback()


def test_context_uses_stored_columns_and_only_rendered_refs(db_session_factory, state):
    with db_session_factory.begin() as session:
        relationship_id = create(session, state)
        thread_id = thread(session, state, relationship_id)
    with db_session_factory() as session:
        with session.no_autoflush:
            session.get(models().Relationship, relationship_id).narrative = "DIRTY"
            session.get(Entity, state[1]).display_name = "DIRTY"
            session.get(models().RelationshipThread, thread_id).title = "DIRTY"
        sections = store().relationship_context_sections(
            session, state[0].individual_id
        )
        assert len(sections) == 2 and all(
            item.category == "relationship" for item in sections
        )
        assert "DIRTY" not in str([item.model_dump(mode="json") for item in sections])
        assert {(ref.kind, ref.id) for ref in sections[0].refs} == {
            ("relationship", relationship_id),
            ("entity", state[1]),
        }
        assert [(ref.kind, ref.id) for ref in sections[1].refs] == [
            ("relationship_thread", thread_id)
        ]
        assert "Morgan" in str(sections[0].content)
        session.rollback()


def test_context_is_bounded_owned_and_excludes_terminal_threads(
    db_session_factory, state
):
    other = create_healthy(db_session_factory)
    with db_session_factory.begin() as session:
        other_entity = create_entity(
            session, other.individual_id, kind="person", display_name="Foreign", now=NOW
        )
        create(session, (other, other_entity))
        ids = []
        for index in range(10):
            entity = create_entity(
                session,
                state[0].individual_id,
                kind="person",
                display_name=f"Person {index}",
                now=NOW,
            )
            relationship = create(
                session, (state[0], entity), now=NOW + timedelta(minutes=index)
            )
            ids.append(relationship)
            identity = thread(
                session, state, relationship, now=NOW + timedelta(minutes=index)
            )
            if index == 9:
                store().revise_relationship_thread(
                    session,
                    state[0].individual_id,
                    identity,
                    status="resolved",
                    rationale="Done",
                    evidence_refs=evidence(state),
                    now=NOW + timedelta(hours=1),
                )
    with db_session_factory() as session:
        sections = store().relationship_context_sections(
            session, state[0].individual_id
        )
        assert len(sections) == 16
        assert [item.refs[0].id for item in sections[:8]] == list(reversed(ids))[:8]
        assert "Foreign" not in str([item.content for item in sections])
        assert all(item.content["item"]["status"] == "open" for item in sections[8:])


def test_commitment_links_require_ownership_preserve_terms_and_null_means_unchanged(
    db_session_factory, state
):
    other = create_healthy(db_session_factory)
    with db_session_factory.begin() as session:
        commitments = []
        for person in (state[0], state[0], other):
            item = Commitment(
                commitment_id=new_id(),
                individual_id=person.individual_id,
                counterparty_entity_id=None,
                title="Promise",
                terms="Review the plan",
                status="active",
                due_at=None,
                rationale="Accepted",
                evidence_refs=[],
                created_at=NOW,
                updated_at=NOW,
                revision=1,
            )
            session.add(item)
            commitments.append(item)
        session.flush()
        relationship_id = create(session, state)
        with pytest.raises(ValueError, match="unknown_relationship_commitment"):
            thread(
                session,
                state,
                relationship_id,
                commitment_id=commitments[2].commitment_id,
            )
        identity = thread(
            session, state, relationship_id, commitment_id=commitments[0].commitment_id
        )
        for proposed, rationale in (
            (commitments[1].commitment_id, "Second promise"),
            (None, "Review again"),
        ):
            store().revise_relationship_thread(
                session,
                state[0].individual_id,
                identity,
                commitment_id=proposed,
                rationale=rationale,
                evidence_refs=evidence(state),
                now=NOW + timedelta(hours=1),
            )
            assert (
                session.get(models().RelationshipThread, identity).commitment_id
                == commitments[1].commitment_id
            )
        with pytest.raises(ValueError, match="unknown_relationship_commitment"):
            store().revise_relationship_thread(
                session,
                state[0].individual_id,
                identity,
                commitment_id=commitments[2].commitment_id,
                rationale="Foreign",
                evidence_refs=evidence(state),
                now=NOW + timedelta(hours=1),
            )
        assert all(
            item.status == "active"
            and item.terms == "Review the plan"
            and item.revision == 1
            for item in commitments
        )


@pytest.mark.parametrize("kind", ["relationship", "thread"])
def test_foreign_targets_and_id_collisions_are_rejected(
    db_session_factory, state, kind
):
    other = create_healthy(db_session_factory)
    with db_session_factory.begin() as session:
        relationship_id = create(session, state)
        thread_id = thread(session, state, relationship_id)
        method, identity = (
            (store().revise_relationship, relationship_id)
            if kind == "relationship"
            else (store().revise_relationship_thread, thread_id)
        )
        with pytest.raises(ValueError, match="unknown_relationship"):
            method(
                session,
                other.individual_id,
                identity,
                rationale="Foreign",
                evidence_refs=[Ref(kind="event", id=other.genesis_event_id)],
                now=NOW,
            )
        with pytest.raises(ValueError, match="personal_id_collision"):
            if kind == "relationship":
                create(session, state, relationship_id=relationship_id)
            else:
                thread(session, state, relationship_id, thread_id=thread_id)
        with pytest.raises(ValueError, match="unknown_relationship_target"):
            thread(session, (other, state[1]), relationship_id)


@pytest.mark.parametrize("kind", ["relationship", "thread"])
@pytest.mark.parametrize("problem", ["content", "evidence", "rationale", "time"])
def test_invalid_revisions_leave_current_projection_and_history_intact(
    db_session_factory, state, kind, problem
):
    with db_session_factory.begin() as session:
        relationship_id = create(session, state)
        thread_id = thread(session, state, relationship_id)
        method, identity, model = (
            (store().revise_relationship, relationship_id, models().Relationship)
            if kind == "relationship"
            else (
                store().revise_relationship_thread,
                thread_id,
                models().RelationshipThread,
            )
        )
        kwargs = dict(
            rationale="New interpretation", evidence_refs=evidence(state), now=NOW
        )
        if problem == "content":
            kwargs["narrative" if kind == "relationship" else "summary"] = " "
        elif problem == "evidence":
            kwargs["evidence_refs"] = []
        elif problem == "rationale":
            kwargs["rationale"] = " "
        else:
            kwargs["now"] = NOW - timedelta(seconds=1)
        with pytest.raises(ValueError):
            method(session, state[0].individual_id, identity, **kwargs)
        assert session.get(model, identity).revision == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(PersonalStateRevision)
                .where(PersonalStateRevision.object_id == identity)
            )
            == 1
        )
