"""Configured PostgreSQL lexical recall remains scoped, bounded and read-only."""

import importlib
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from test_integrity_checks import NOW, create_healthy

from cognition.db.models.evidence import Event, EventContent
from cognition.db.models.personal import Belief, Episode, PersonalStateRevision
from cognition.protocols.cognition_v1 import CurrentFocus
from cognition.protocols.common import new_id
from cognition.protocols.events_v1 import EventEnvelopeV1
from cognition.protocols.wakes_v1 import WakeV1
from cognition.stores.evidence import StoredEvent, append_event


@pytest.fixture
def person(db_session_factory):
    return create_healthy(db_session_factory)


def belief(session, person, text="Robins nest nearby", **updates):
    values = dict(
        belief_id=new_id(),
        individual_id=person.individual_id,
        proposition=text,
        subject_entity_id=None,
        topic=None,
        status="tentative",
        supporting_evidence=[],
        contradicting_evidence=[],
        supersedes_belief_id=None,
        rationale="Observed",
        created_at=NOW,
        updated_at=NOW,
        revision=1,
    )
    values.update(updates)
    row = Belief(**values)
    session.add(row)
    session.flush()
    return row


def episode(session, person, text="Observed robins nearby", **updates):
    values = dict(
        episode_id=new_id(),
        individual_id=person.individual_id,
        summary=text,
        starts_at=None,
        ends_at=None,
        evidence_refs=[],
        entity_refs=[],
        project_refs=[],
        salience_factors=[],
        created_at=NOW,
        revision=1,
    )
    values.update(updates)
    row = Episode(**values)
    session.add(row)
    session.flush()
    return row


def event(
    session,
    person,
    text="Robins visited today",
    *,
    source="connector",
    sensitivity="internal",
    **updates,
):
    values = dict(
        schema_version=1,
        event_id=new_id(),
        individual_id=person.individual_id,
        event_type="observation",
        occurred_at=NOW,
        observed_at=NOW,
        recorded_at=NOW,
        source=dict(kind=source, source_id=None, binding_id=None),
        actor_entity_id=None,
        causation_event_id=None,
        correlation_id=None,
        subject=None,
        provenance={},
        runtime_version="test",
        content=dict(
            content_type="text/plain",
            payload=None,
            text=text,
            blob_ref=None,
            content_hash=None,
            sensitivity=sensitivity,
            retention_class="history",
            retain_until=None,
        ),
    )
    values.update(updates)
    return append_event(session, EventEnvelopeV1.model_validate(values))


def wake(person, purpose, **updates):
    values = dict(
        schema_version=1,
        wake_id=new_id(),
        individual_id=person.individual_id,
        kind="self_scheduled",
        due_at=NOW,
        purpose=purpose,
        cause_event_id=None,
        context_refs=[],
        coalesce_key=None,
    )
    values.update(updates)
    return WakeV1.model_validate(values)


def retrieve(session, person, *, purpose=None, wakes=(), focus=None):
    if purpose is not None:
        wakes = [wake(person, purpose)]
    return importlib.import_module(
        "cognition.stores.lexical"
    ).retrieve_lexical_attention(
        session, person.individual_id, wakes=wakes, focus=focus
    )


def identity(match):
    if isinstance(match.content, StoredEvent):
        return "event", match.content.envelope.event_id
    ref = match.content.refs[0]
    return ref.kind, ref.id


def test_english_normalization_finds_each_corpus_and_preserves_first_query_origin(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        first = belief(session, person, "Running is enjoyable")
        second = episode(session, person, "Ran and then ran again while running")
        third = event(session, person, "Runs completed")
    source = wake(person, "runs running run")
    with db_session_factory() as session:
        result = retrieve(session, person, wakes=[source])
        assert {identity(match) for match in result.matches} == {
            ("belief", first.belief_id),
            ("episode", second.episode_id),
            ("event", third.envelope.event_id),
        }
        assert result.query_terms == ("runs",)
        assert result.query_sources == (f"wake:{source.wake_id}",)
        assert result.effective_query == "'run'"
        assert all(match.rank > 0 for match in result.matches)


@pytest.mark.parametrize("text", ["the and or to a", "!!! && || <-> :*", " "])
def test_empty_normalized_input_has_no_lexical_candidates(
    db_session_factory, person, text
):
    with db_session_factory() as session:
        result = retrieve(session, person, focus=CurrentFocus(summary=text, refs=[]))
        assert result.matches == () and result.query_terms == ()
        assert result.query_sources == () and result.effective_query == ""


def test_focus_first_and_round_robin_source_allocation_are_bounded(
    db_session_factory, person
):
    wakes = [
        wake(
            person,
            f"wake{index} " + " ".join(f"verbose{word}" for word in range(31)),
            due_at=NOW + timedelta(seconds=index),
        )
        for index in range(17)
    ]
    with db_session_factory() as session:
        result = retrieve(
            session,
            person,
            wakes=list(reversed(wakes)),
            focus=CurrentFocus(summary="focusneedle focussecond", refs=[]),
        )
        assert len(result.query_terms) == 16
        assert (
            result.query_terms[0] == "focusneedle"
            and result.query_sources[0] == "focus"
        )
        assert result.query_terms[1:] == tuple(f"wake{index}" for index in range(15))
        assert f"wake:{wakes[16].wake_id}" not in result.query_sources
        assert "verbose0" not in result.query_terms


def test_stopword_prefix_does_not_let_sixteen_wakes_displace_focus(
    db_session_factory, person
):
    wakes = [
        wake(person, f"wake{index}", due_at=NOW + timedelta(seconds=index))
        for index in range(16)
    ]
    with db_session_factory() as session:
        result = retrieve(
            session,
            person,
            wakes=wakes,
            focus=CurrentFocus(summary="The and obsidian", refs=[]),
        )
        assert result.query_terms == (
            "obsidian",
            *(f"wake{index}" for index in range(15)),
        )
        assert result.query_sources[0] == "focus"


@pytest.mark.parametrize(
    "text,expected",
    [
        (" " * 507 + "chocolate", ()),
        (" " * 503 + "pineapple" + " tail", ("pineapple",)),
        ("x" * 65 + " robin", ("robin",)),
        ("the " * 32 + "robin", ()),
        ("café & robin:*", ("café", "robin")),
    ],
)
def test_source_word_and_boundary_caps(db_session_factory, person, text, expected):
    with db_session_factory() as session:
        result = retrieve(session, person, focus=CurrentFocus(summary=text, refs=[]))
        assert result.query_terms == expected


def test_query_is_plain_data_and_uses_or_semantics(db_session_factory, person):
    with db_session_factory.begin() as session:
        robin = belief(session, person, "robin")
        sparrow = episode(session, person, "sparrow")
    with db_session_factory() as session:
        result = retrieve(session, person, purpose="robin | !sparrow:* ; ' --")
        assert {identity(match) for match in result.matches} == {
            ("belief", robin.belief_id),
            ("episode", sparrow.episode_id),
        }
        assert result.query_terms == ("robin", "sparrow")
        assert result.effective_query == "'robin' | 'sparrow'"


def test_owned_current_eligibility_and_protected_events_filter_before_limit(
    db_session_factory, person
):
    other = create_healthy(db_session_factory)
    with db_session_factory.begin() as session:
        good_belief = belief(session, person, "robin", status="disputed")
        good_episode = episode(session, person, "robin")
        good_event = event(session, person, "robin", sensitivity="public")
        for _ in range(10):
            belief(session, person, "robin " * 10, status="withdrawn")
            belief(session, other, "robin " * 10)
            episode(session, other, "robin " * 10)
            event(session, person, "robin " * 10, sensitivity="sensitive")
            event(session, person, "robin " * 10, source="admin")
            event(session, other, "robin " * 10)
        redacted = event(session, person, "robin " * 10)
        session.get(EventContent, redacted.envelope.event_id).redacted_at = NOW
        event(session, person, "robin " * 10, event_type="individual.born")
    with db_session_factory() as session:
        result = retrieve(session, person, purpose="robin")
        assert {identity(match) for match in result.matches} == {
            ("belief", good_belief.belief_id),
            ("episode", good_episode.episode_id),
            ("event", good_event.envelope.event_id),
        }


def test_each_corpus_has_eight_ranked_hits_with_stable_ties(db_session_factory, person):
    with db_session_factory.begin() as session:
        beliefs = [belief(session, person, "robin") for _ in range(10)]
        episodes = [episode(session, person, "robin") for _ in range(10)]
        events = [event(session, person, "robin") for _ in range(10)]
    with db_session_factory() as session:
        result = retrieve(session, person, purpose="robin")
        groups = {
            kind: [
                identity(match)[1]
                for match in result.matches
                if identity(match)[0] == kind
            ]
            for kind in ("belief", "episode", "event")
        }
        assert groups["belief"] == sorted(row.belief_id for row in beliefs)[:8]
        assert groups["episode"] == sorted(row.episode_id for row in episodes)[:8]
        assert (
            groups["event"] == [row.envelope.event_id for row in reversed(events)][:8]
        )
        assert len(result.matches) == 24


def test_lexical_rank_precedes_recency(db_session_factory, person):
    with db_session_factory.begin() as session:
        strong = belief(session, person, "robin " * 6)
        recent = belief(session, person, "robin", updated_at=NOW + timedelta(days=1))
    with db_session_factory() as session:
        result = retrieve(session, person, purpose="robin")
        assert [identity(match)[1] for match in result.matches] == [
            strong.belief_id,
            recent.belief_id,
        ]
        assert result.matches[0].rank > result.matches[1].rank


def test_only_indexed_prefixes_and_text_are_searchable(db_session_factory, person):
    with db_session_factory.begin() as session:
        belief(session, person, "pad " * 2048 + "robin")
        belief(session, person, "plain", topic="pad " * 256 + "robin")
        episode(session, person, "pad " * 2048 + "robin")
        event(session, person, "pad " * 2048 + "robin")
        payload_only = event(session, person, None)
        session.get(EventContent, payload_only.envelope.event_id).payload = {
            "text": "robin"
        }
        metadata_only = event(session, person, "plain")
        session.get(Event, metadata_only.envelope.event_id).source_id = "robin"
    with db_session_factory() as session:
        assert retrieve(session, person, purpose="robin").matches == ()


def test_index_tracks_revision_and_content_redaction(db_session_factory, person):
    with db_session_factory.begin() as session:
        first = belief(session, person, "robin")
        second = event(session, person, "robin")
    with db_session_factory() as session:
        assert len(retrieve(session, person, purpose="robin").matches) == 2
    with db_session_factory.begin() as session:
        session.get(Belief, first.belief_id).proposition = "sparrow"
        content = session.get(EventContent, second.envelope.event_id)
        content.text = None
        content.redacted_at = NOW
    with db_session_factory() as session:
        assert retrieve(session, person, purpose="robin").matches == ()
        assert [
            identity(match)
            for match in retrieve(session, person, purpose="sparrow").matches
        ] == [("belief", first.belief_id)]


def test_no_dirty_orm_leakage_refresh_autoflush_or_retrieval_mutation(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        first = belief(session, person, "robin")
        second = event(session, person, "robin")
    with db_session_factory() as session:
        with session.no_autoflush:
            row = session.get(Belief, first.belief_id)
            content = session.get(EventContent, second.envelope.event_id)
            row.proposition = "DIRTY"
            content.text = "DIRTY"
            session.add(Belief(belief_id=new_id()))
            revisions = session.scalar(
                select(func.count()).select_from(PersonalStateRevision)
            )
            events = session.scalar(select(func.count()).select_from(Event))
        result = retrieve(session, person, purpose="robin")
        assert len(result.matches) == 2
        assert "DIRTY" not in str(result.matches)
        assert row.proposition == "DIRTY" and content.text == "DIRTY"
        assert row in session.dirty and content in session.dirty and session.new
        with session.no_autoflush:
            assert (
                session.scalar(select(func.count()).select_from(PersonalStateRevision))
                == revisions
            )
            assert session.scalar(select(func.count()).select_from(Event)) == events
        session.rollback()


def test_expired_but_unredacted_event_retains_existing_visibility(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        stored = event(session, person, "robin")
        session.get(EventContent, stored.envelope.event_id).retain_until = (
            NOW - timedelta(days=1)
        )
    with db_session_factory() as session:
        assert [
            identity(match)
            for match in retrieve(session, person, purpose="robin").matches
        ] == [("event", stored.envelope.event_id)]


def test_raw_term_budget_is_distinct_bounded_and_round_robin_before_normalization(
    db_session_factory, person
):
    lexical = importlib.import_module("cognition.stores.lexical")
    focus = CurrentFocus(
        summary=" ".join(f"focus{index}" for index in range(32)), refs=[]
    )
    sources = [
        wake(
            person,
            " ".join(f"source{source}word{index}" for index in range(32)),
            due_at=NOW + timedelta(seconds=source),
        )
        for source in range(3)
    ]
    raw = lexical._raw_query_terms(person.individual_id, list(reversed(sources)), focus)
    assert len(raw) == 64 and len({term for term, _ in raw}) == 64
    assert [term for term, _ in raw[:8]] == [
        "focus0",
        "source0word0",
        "source1word0",
        "source2word0",
        "focus1",
        "source0word1",
        "source1word1",
        "source2word1",
    ]
    with db_session_factory() as session:
        result = retrieve(session, person, wakes=sources, focus=focus)
        assert result.query_terms == tuple(term for term, _ in raw[:16])


def test_foreign_wakes_do_not_supply_terms_or_displace_owned_source_limit(
    db_session_factory, person
):
    other = create_healthy(db_session_factory)
    foreign = [
        wake(other, "FOREIGNQUERY", due_at=NOW - timedelta(seconds=1))
        for _ in range(16)
    ]
    own = wake(person, "robin")
    with db_session_factory() as session:
        result = retrieve(session, person, wakes=[*foreign, own])
        assert result.query_terms == ("robin",)
        assert result.query_sources == (f"wake:{own.wake_id}",)


def test_query_equivalence_keeps_first_origin_and_source_order_is_stable(
    db_session_factory, person
):
    early = wake(person, "running birds", due_at=NOW)
    late = wake(person, "runs bird", due_at=NOW + timedelta(seconds=1))
    focus = CurrentFocus(summary="run", refs=[])
    with db_session_factory() as session:
        first = retrieve(session, person, wakes=[late, early], focus=focus)
        second = retrieve(session, person, wakes=[early, late], focus=focus)
        assert first == second
        assert first.query_terms == ("run", "birds")
        assert first.query_sources == ("focus", f"wake:{early.wake_id}")


def test_belief_topic_is_searchable_and_events_without_content_are_excluded(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        target = belief(session, person, "A general observation", topic="robin")
        missing = event(session, person, "robin")
        session.delete(session.get(EventContent, missing.envelope.event_id))
    with db_session_factory() as session:
        result = retrieve(session, person, purpose="robin")
        assert [identity(match) for match in result.matches] == [
            ("belief", target.belief_id)
        ]
