"""Attention reads bounded owned projections without changing personal state."""

import importlib
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from test_integrity_checks import NOW, create_healthy

from cognition.db.models.development import Interest, SelfState
from cognition.db.models.evidence import Event
from cognition.db.models.personal import (
    Belief,
    Commitment,
    Entity,
    Goal,
    PersonalStateRevision,
    Project,
)
from cognition.db.models.relationships import Relationship, RelationshipThread
from cognition.protocols.cognition_v1 import CurrentFocus
from cognition.protocols.common import Ref, new_id
from cognition.protocols.wakes_v1 import WakeV1
from cognition.stores.personal import personal_context_sections


@pytest.fixture
def person(db_session_factory):
    return create_healthy(db_session_factory)


def seed(session, person, model, **updates):
    common = dict(
        individual_id=person.individual_id, created_at=NOW, updated_at=NOW, revision=1
    )
    defaults = {
        Goal: dict(
            goal_id=new_id(),
            project_id=None,
            title="Goal",
            desired_state="Learn",
            status="active",
            origin="self_generated",
            rationale="Chosen",
            evidence_refs=[],
        ),
        Commitment: dict(
            commitment_id=new_id(),
            counterparty_entity_id=None,
            title="Promise",
            terms="Review",
            status="active",
            due_at=None,
            rationale="Accepted",
            evidence_refs=[],
        ),
        Entity: dict(entity_id=new_id(), kind="person", display_name="Morgan"),
        Project: dict(
            project_id=new_id(),
            title="Project",
            desired_state="Learn",
            status="active",
            rationale="Chosen",
            evidence_refs=[],
        ),
        Relationship: dict(
            relationship_id=new_id(),
            entity_id=None,
            narrative="A connection",
            rationale="Observed",
            evidence_refs=[],
        ),
        RelationshipThread: dict(
            thread_id=new_id(),
            relationship_id=None,
            title="Open topic",
            summary="Discuss",
            status="open",
            commitment_id=None,
            rationale="Observed",
            evidence_refs=[],
        ),
        Belief: dict(
            belief_id=new_id(),
            proposition="A possibility",
            subject_entity_id=None,
            topic=None,
            status="tentative",
            supporting_evidence=[],
            contradicting_evidence=[],
            supersedes_belief_id=None,
            rationale="Observed",
        ),
        Interest: dict(
            interest_id=new_id(),
            topic="Study",
            summary="Explore",
            status="candidate",
            rationale="Observed",
            evidence_refs=[],
            promotion_not_before=NOW + timedelta(days=1),
            retirement_not_before=None,
        ),
        SelfState: dict(
            self_state_id=new_id(),
            layer="self_belief",
            content=None,
            evidence_refs=[],
            pending_content={"value": "A possibility"},
            pending_evidence_refs=[],
            pending_not_before=NOW + timedelta(days=1),
            rationale="Observed",
        ),
    }
    row = model(**{**common, **defaults[model], **updates})
    session.add(row)
    session.flush()
    return row


def wake(person, refs, **updates):
    return WakeV1.model_validate(
        dict(
            schema_version=1,
            wake_id=new_id(),
            individual_id=person.individual_id,
            kind="self_scheduled",
            due_at=NOW,
            purpose="Review named objects",
            cause_event_id=None,
            context_refs=refs,
            coalesce_key=None,
            **updates,
        )
    )


def build(session, person, *, wakes=(), focus=None):
    return importlib.import_module(
        "cognition.stores.attention"
    ).build_personal_attention(
        session, person.individual_id, wakes=wakes, focus=focus, now=NOW
    )


def primary(candidate):
    ref = candidate.section.refs[0]
    return ref.kind, ref.id


@pytest.mark.parametrize("status", ["active", "completed", "abandoned"])
def test_direct_old_goal_is_recalled_outside_recent_pool(
    db_session_factory, person, status
):
    with db_session_factory.begin() as session:
        old = seed(session, person, Goal, title="Old named goal", status=status)
        for index in range(9):
            seed(
                session,
                person,
                Goal,
                title=f"New {index}",
                updated_at=NOW + timedelta(minutes=index + 1),
            )
    with db_session_factory() as session:
        result = build(
            session, person, wakes=[wake(person, [Ref(kind="goal", id=old.goal_id)])]
        )
        match = [
            item for item in result.candidates if primary(item) == ("goal", old.goal_id)
        ]
        assert len(match) == 1 and match[0].reason == "wake_reference"
        assert match[0].section.content["item"]["title"] == "Old named goal"
        assert match[0].section.content["item"]["status"] == status


def test_nine_due_obligations_have_bounded_mandatory_details_and_progress(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        rows = [
            seed(session, person, Commitment, due_at=NOW + timedelta(hours=index - 4))
            for index in range(9)
        ]
        seed(session, person, Commitment, status="proposed", due_at=NOW)
        seed(session, person, Commitment, status="fulfilled", due_at=NOW)
        seed(session, person, Commitment, due_at=NOW + timedelta(hours=25))
    with db_session_factory() as session:
        result = build(session, person)
        urgent = [item for item in result.candidates if item.mandatory]
        assert [primary(item)[1] for item in urgent] == [
            row.commitment_id for row in rows[:8]
        ]
        assert all(item.reason == "urgent_commitment" for item in urgent)
        assert result.urgent_scan_truncated is True
    with db_session_factory.begin() as session:
        for item in rows[:2]:
            session.get(Commitment, item.commitment_id).status = "fulfilled"
    with db_session_factory() as session:
        result = build(session, person)
        urgent = [item for item in result.candidates if item.mandatory]
        assert [primary(item)[1] for item in urgent] == [
            row.commitment_id for row in rows[2:]
        ]
        assert result.urgent_scan_truncated is False


def test_urgent_dedup_precedes_direct_and_ninth_can_be_direct(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        rows = [
            seed(session, person, Commitment, due_at=NOW + timedelta(minutes=index))
            for index in range(9)
        ]
    refs = [Ref(kind="commitment", id=rows[index].commitment_id) for index in (0, 8)]
    with db_session_factory() as session:
        result = build(session, person, wakes=[wake(person, refs)])
        selected = {primary(item): item for item in result.candidates}
        assert len(selected) == len(result.candidates)
        assert (
            selected[("commitment", rows[0].commitment_id)].reason
            == "urgent_commitment"
        )
        assert (
            selected[("commitment", rows[8].commitment_id)].reason == "wake_reference"
        )
        assert result.urgent_scan_truncated is True


def test_foreign_missing_and_nonpersonal_refs_never_claim_retrieval(
    db_session_factory, person
):
    other = create_healthy(db_session_factory)
    with db_session_factory.begin() as session:
        foreign = seed(session, other, Goal, title="SECRET FOREIGN CONTENT")
    refs = [
        Ref(kind="goal", id=foreign.goal_id),
        Ref(kind="goal", id=new_id()),
        Ref(kind="event", id=new_id()),
        Ref(kind="governance", id=person.individual_id),
    ]
    with db_session_factory() as session:
        result = build(session, person, wakes=[wake(person, refs)])
        assert result.candidates == ()
        assert result.unresolved_direct_refs == 2
        assert result.direct_refs_truncated == 0


def test_direct_limit_counts_unique_supported_refs_in_stable_order(
    db_session_factory, person
):
    refs = [Ref(kind="goal", id=new_id()) for _ in range(36)]
    first = wake(person, list(reversed(refs)) + refs[:3])
    with db_session_factory() as session:
        result = build(
            session,
            person,
            wakes=[first],
            focus=CurrentFocus(summary="Repeat", refs=refs),
        )
        assert result.direct_refs_truncated == 4
        assert result.unresolved_direct_refs == 32


def test_link_expansion_stops_after_one_parent_hop(db_session_factory, person):
    with db_session_factory.begin() as session:
        entity = seed(session, person, Entity)
        counterparty = seed(session, person, Entity, display_name="Not one hop")
        relationship = seed(session, person, Relationship, entity_id=entity.entity_id)
        commitment = seed(
            session, person, Commitment, counterparty_entity_id=counterparty.entity_id
        )
        thread = seed(
            session,
            person,
            RelationshipThread,
            relationship_id=relationship.relationship_id,
            commitment_id=commitment.commitment_id,
        )
    with db_session_factory() as session:
        result = build(
            session,
            person,
            wakes=[
                wake(person, [Ref(kind="relationship_thread", id=thread.thread_id)])
            ],
        )
        linked = {
            primary(item): item
            for item in result.candidates
            if item.reason == "linked_reference"
        }
        assert set(linked) == {
            ("relationship", relationship.relationship_id),
            ("commitment", commitment.commitment_id),
        }
        assert linked[("relationship", relationship.relationship_id)].section.refs == [
            Ref(kind="relationship", id=relationship.relationship_id),
            Ref(kind="entity", id=entity.entity_id),
        ]
        assert all(
            primary(item) != ("entity", counterparty.entity_id)
            for item in result.candidates
            if item.reason != "recent_personal"
        )


def test_link_limit_caps_additional_targets_and_deduplicates(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        goals = []
        for _ in range(20):
            project = seed(session, person, Project, status="completed")
            goals.append(seed(session, person, Goal, project_id=project.project_id))
    with db_session_factory() as session:
        result = build(
            session,
            person,
            wakes=[wake(person, [Ref(kind="goal", id=row.goal_id) for row in goals])],
        )
        assert (
            len(
                [
                    item
                    for item in result.candidates
                    if item.reason == "linked_reference"
                ]
            )
            == 16
        )
        assert result.linked_refs_truncated == 4


def test_source_order_and_duplicate_precedence_are_deterministic(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        first = seed(session, person, Goal)
        second = seed(session, person, Goal)
    refs = [Ref(kind="goal", id=row.goal_id) for row in (first, second)]
    a, b = wake(person, refs), wake(person, list(reversed(refs)))
    focus = CurrentFocus(summary="Repeated", refs=refs)
    with db_session_factory() as session:
        left = build(session, person, wakes=[a, b], focus=focus)
        right = build(session, person, wakes=[b, a], focus=focus)
        assert left == right
        assert all(item.reason == "wake_reference" for item in left.candidates)
        focused = build(session, person, focus=focus)
        assert all(item.reason == "focus_reference" for item in focused.candidates)


def test_dirty_identity_map_is_not_rendered_or_flushed_and_reads_have_no_effects(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        goal = seed(session, person, Goal, title="Stored title")
    with db_session_factory() as session:
        events = session.scalar(select(func.count()).select_from(Event))
        revisions = session.scalar(
            select(func.count()).select_from(PersonalStateRevision)
        )
        row = session.get(Goal, goal.goal_id)
        row.title = "DIRTY"
        session.add(Entity(entity_id=new_id()))
        result = build(
            session, person, wakes=[wake(person, [Ref(kind="goal", id=goal.goal_id)])]
        )
        assert result.candidates[0].section.content["item"]["title"] == "Stored title"
        assert row.title == "DIRTY" and row in session.dirty and session.new
        with session.no_autoflush:
            assert session.scalar(select(func.count()).select_from(Event)) == events
            assert (
                session.scalar(select(func.count()).select_from(PersonalStateRevision))
                == revisions
            )
        session.rollback()


def test_direct_development_keeps_guidance_and_recent_projection(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        interest = seed(session, person, Interest)
        self_state = seed(session, person, SelfState)
    refs = [
        Ref(kind="interest", id=interest.interest_id),
        Ref(kind="self_state", id=self_state.self_state_id),
    ]
    with db_session_factory() as session:
        legacy = {
            section.refs[0].kind: section
            for section in personal_context_sections(session, person.individual_id)
        }
        result = build(session, person, wakes=[wake(person, refs)])
        for item in result.candidates:
            assert item.section == legacy[item.section.refs[0].kind]
            assert item.section.content["development_policy_version"] == 1
            assert "grounding_policy" in item.section.content


def test_urgent_horizon_includes_disputed_boundary_but_not_later_or_foreign(
    db_session_factory, person
):
    other = create_healthy(db_session_factory)
    with db_session_factory.begin() as session:
        boundary = seed(
            session,
            person,
            Commitment,
            status="disputed",
            due_at=NOW + timedelta(hours=24),
        )
        seed(
            session,
            person,
            Commitment,
            due_at=NOW + timedelta(hours=24, microseconds=1),
        )
        seed(session, other, Commitment, due_at=NOW - timedelta(days=10))
    with db_session_factory() as session:
        result = build(session, person)
        assert [primary(item) for item in result.candidates if item.mandatory] == [
            ("commitment", boundary.commitment_id)
        ]


def test_terminal_interest_and_thread_recall_are_labeled_without_reopening(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        interest = seed(session, person, Interest, status="retired")
        entity = seed(session, person, Entity)
        relationship = seed(session, person, Relationship, entity_id=entity.entity_id)
        thread = seed(
            session,
            person,
            RelationshipThread,
            relationship_id=relationship.relationship_id,
            status="resolved",
        )
    refs = [
        Ref(kind="interest", id=interest.interest_id),
        Ref(kind="relationship_thread", id=thread.thread_id),
    ]
    with db_session_factory() as session:
        result = build(session, person, wakes=[wake(person, refs)])
        direct = {
            primary(item)[0]: item.section.content
            for item in result.candidates
            if item.reason == "wake_reference"
        }
        assert "cannot reopen" in direct["interest"]["policy"]["guidance"]
        assert "terminal social topic" in direct["relationship_thread"]["source"]
        assert session.get(Interest, interest.interest_id).revision == 1
        assert session.get(RelationshipThread, thread.thread_id).status == "resolved"


def test_belief_entity_parent_does_not_follow_arbitrary_evidence_links(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        entity = seed(session, person, Entity)
        unrelated_project = seed(session, person, Project, status="completed")
        belief = seed(
            session,
            person,
            Belief,
            subject_entity_id=entity.entity_id,
            supporting_evidence=[
                {"kind": "project", "id": str(unrelated_project.project_id)}
            ],
        )
    with db_session_factory() as session:
        result = build(
            session,
            person,
            wakes=[wake(person, [Ref(kind="belief", id=belief.belief_id)])],
        )
        linked = [
            primary(item)
            for item in result.candidates
            if item.reason == "linked_reference"
        ]
        assert linked == [("entity", entity.entity_id)]
        assert ("project", unrelated_project.project_id) not in [
            primary(item) for item in result.candidates
        ]
