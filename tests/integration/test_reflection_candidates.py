"""Reflection opportunities read bounded owned staged state without mutations."""

import importlib
from collections import Counter
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import event

from cognition.db.models.development import Interest, Preference, SelfState
from cognition.db.models.identity import Individual
from cognition.protocols.common import new_id

NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)


def api():
    return importlib.import_module("cognition.stores.reflection_candidates")


def owner(session):
    identity = new_id()
    session.add(
        Individual(
            individual_id=identity,
            birth_at=NOW - timedelta(days=10),
            birth_name="Reflection candidate fixture",
            founding_orientation="Review grounded staged claims",
            creator_provenance={},
            operational_status="active",
            revision=1,
        )
    )
    session.flush()
    return identity


@pytest.fixture
def person(db_session_factory):
    with db_session_factory.begin() as session:
        return owner(session)


def seed(session, person, kind, identity, *, eligible=NOW, **updates):
    common = dict(
        individual_id=person,
        rationale="Fixture observation",
        evidence_refs=[],
        created_at=NOW - timedelta(days=10),
        updated_at=NOW,
        revision=1,
    )
    if kind == "interest":
        model = Interest
        values = dict(
            interest_id=UUID(int=identity),
            topic="Study",
            summary="A candidate interest",
            status="candidate",
            promotion_not_before=eligible,
            retirement_not_before=None,
        )
    elif kind == "preference":
        model = Preference
        values = dict(
            preference_id=UUID(int=identity),
            context="Planning",
            statement="A tentative preference",
            status="tentative",
            promotion_not_before=eligible,
            retirement_not_before=None,
        )
    else:
        model = SelfState
        values = dict(
            self_state_id=UUID(int=identity),
            layer="self_belief",
            content=None,
            pending_content={},
            pending_evidence_refs=[],
            pending_not_before=eligible,
        )
    row = model(**(common | values | updates))
    session.add(row)
    return row


def select(session, person, *, eligible_by=NOW, cursors=None):
    return api().select_reflection_candidates(
        session, person, eligible_by=eligible_by, cursors=cursors or {}
    )


def identities(candidates):
    return [(candidate.ref.kind, candidate.ref.id.int) for candidate in candidates]


def test_empty_and_foreign_only_state_has_no_eligibility(db_session_factory, person):
    with db_session_factory.begin() as session:
        assert api().earliest_reflection_eligibility(session, person) is None
        assert select(session, person) == ()
        other = owner(session)
        seed(session, other, "interest", 1)
    with db_session_factory() as session:
        assert api().earliest_reflection_eligibility(session, person) is None
        assert select(session, person) == ()


@pytest.mark.parametrize("earliest_kind", ["interest", "preference", "self_state"])
def test_earliest_aggregate_includes_future_pending_states(
    db_session_factory, person, earliest_kind
):
    with db_session_factory.begin() as session:
        for index, kind in enumerate(("interest", "preference", "self_state"), 1):
            seed(
                session,
                person,
                kind,
                index,
                eligible=NOW + timedelta(days=1 if kind == earliest_kind else 2),
            )
    with db_session_factory() as session:
        assert api().earliest_reflection_eligibility(session, person) == (
            NOW + timedelta(days=1)
        )
        assert select(session, person) == ()


def test_terminal_and_unstaged_layers_are_not_candidates(db_session_factory, person):
    with db_session_factory.begin() as session:
        for number, status in enumerate(("established", "dormant", "retired"), 1):
            seed(session, person, "interest", number, status=status)
        for number, status in enumerate(("established", "retired"), 10):
            seed(session, person, "preference", number, status=status)
        for number, layer in enumerate(("current_identity", "narrative"), 20):
            seed(session, person, "self_state", number, layer=layer)
        for number, layer in enumerate(("self_belief", "current_value"), 30):
            seed(
                session,
                person,
                "self_state",
                number,
                layer=layer,
                content={},
                pending_content=None,
                pending_not_before=None,
            )
    with db_session_factory() as session:
        assert api().earliest_reflection_eligibility(session, person) is None
        assert select(session, person) == ()


def test_cutoff_is_inclusive_and_normalizes_timezone(db_session_factory, person):
    with db_session_factory.begin() as session:
        seed(session, person, "interest", 1, eligible=NOW)
        seed(session, person, "preference", 2, eligible=NOW + timedelta(microseconds=1))
        seed(session, person, "self_state", 3, eligible=NOW, pending_content={})
    with db_session_factory() as session:
        result = select(
            session, person, eligible_by=NOW.astimezone(timezone(timedelta(hours=-5)))
        )
        assert identities(result) == [("interest", 1), ("self_state", 3)]
        assert all(candidate.eligible_at == NOW for candidate in result)
        assert all(candidate.eligible_at.tzinfo is UTC for candidate in result)
        with pytest.raises(ValueError, match="timezone-aware"):
            select(session, person, eligible_by=NOW.replace(tzinfo=None))


def test_round_robin_allocates_across_families_before_filling_eight(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        for kind in ("interest", "preference"):
            for number in range(1, 13):
                seed(session, person, kind, number)
        seed(session, person, "self_state", 1)
        seed(session, person, "self_state", 2, layer="current_value")
    with db_session_factory() as session:
        assert identities(select(session, person)) == [
            ("interest", 1),
            ("preference", 1),
            ("self_state", 1),
            ("interest", 2),
            ("preference", 2),
            ("self_state", 2),
            ("interest", 3),
            ("preference", 3),
        ]


@pytest.mark.parametrize("kind", ["interest", "preference"])
def test_single_family_fills_limit_in_uuid_order_not_time_order(
    db_session_factory, person, kind
):
    with db_session_factory.begin() as session:
        for number in reversed(range(1, 26)):
            seed(
                session, person, kind, number, eligible=NOW - timedelta(seconds=number)
            )
    with db_session_factory() as session:
        assert identities(select(session, person)) == [(kind, i) for i in range(1, 9)]


@pytest.mark.parametrize(
    "cursor,expected",
    [(None, [2, 4, 6, 8]), (5, [6, 8, 2, 4]), (8, [2, 4, 6, 8]), (99, [2, 4, 6, 8])],
)
def test_wrap_uses_uuid_positions_even_when_cursor_target_is_missing(
    db_session_factory, person, cursor, expected
):
    with db_session_factory.begin() as session:
        for number in (2, 4, 6, 8):
            seed(session, person, "interest", number)
    with db_session_factory() as session:
        result = select(
            session,
            person,
            cursors={"interest": None if cursor is None else UUID(int=cursor)},
        )
        assert identities(result) == [("interest", number) for number in expected]


def test_consumed_batch_cursor_simulation_reaches_the_whole_backlog(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        for kind in ("interest", "preference"):
            for number in range(1, 21):
                seed(session, person, kind, number)
    cursors = {}
    seen = Counter()
    with db_session_factory() as session:
        for _ in range(5):
            batch = select(session, person, cursors=cursors)
            assert len(batch) == len(set(identities(batch))) == 8
            for candidate in batch:
                seen[(candidate.ref.kind, candidate.ref.id.int)] += 1
                cursors[candidate.ref.kind] = candidate.ref.id
    assert seen == Counter(
        (kind, i) for kind in ("interest", "preference") for i in range(1, 21)
    )


def test_ownership_and_maturity_filter_before_uuid_limits(db_session_factory, person):
    with db_session_factory.begin() as session:
        other = owner(session)
        for number in range(1, 30):
            seed(session, other, "interest", number)
        for number in range(30, 60):
            seed(session, person, "interest", number, eligible=NOW + timedelta(days=1))
        for number in range(60, 68):
            seed(session, person, "interest", number)
    with db_session_factory() as session:
        assert identities(select(session, person)) == [
            ("interest", i) for i in range(60, 68)
        ]


def test_core_reads_leave_dirty_deleted_and_transient_orm_state_untouched(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        seed(session, person, "interest", 1, revision=2)
        seed(session, person, "preference", 2)
    with db_session_factory() as session:
        dirty = session.get(Interest, UUID(int=1))
        deleted = session.get(Preference, UUID(int=2))
        dirty.status, dirty.promotion_not_before, dirty.revision = (
            "retired",
            NOW + timedelta(days=10),
            3,
        )
        session.delete(deleted)
        transient = seed(session, person, "interest", 3)
        assert api().earliest_reflection_eligibility(session, person) == NOW
        batch = select(session, person)
        assert identities(batch) == [("interest", 1), ("preference", 2)]
        assert batch[0].revision == 2 and batch[0].eligible_at == NOW
        assert dirty in session.dirty and dirty.status == "retired"
        assert deleted in session.deleted and transient in session.new


def test_stale_identity_map_cannot_override_persisted_revision(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        seed(session, person, "interest", 1)
    with db_session_factory() as stale:
        cached = stale.get(Interest, UUID(int=1))
        with db_session_factory.begin() as concurrent:
            row = concurrent.get(Interest, UUID(int=1))
            row.revision = 7
        batch = select(stale, person)
        assert batch[0].revision == 7
        assert cached.revision == 1 and cached not in stale.dirty
    assert batch[0].ref.id == UUID(int=1) and batch[0].eligible_at == NOW
    with pytest.raises(FrozenInstanceError):
        batch[0].revision = 9


def test_queries_have_bounded_results_and_do_not_write(
    db_engine, db_session_factory, person
):
    with db_session_factory.begin() as session:
        for kind in ("interest", "preference"):
            for number in range(1, 41):
                seed(session, person, kind, number)
        seed(session, person, "self_state", 1)
        seed(session, person, "self_state", 2, layer="current_value")
    observed = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        observed.append((statement.lower(), cursor.rowcount))

    event.listen(db_engine, "after_cursor_execute", capture)
    try:
        with db_session_factory() as session:
            assert api().earliest_reflection_eligibility(session, person) == NOW
            assert len(observed) == 3
            assert all("min(" in sql and count == 1 for sql, count in observed)
            observed.clear()
            batch = select(
                session,
                person,
                cursors={
                    kind: UUID(int=38)
                    for kind in ("interest", "preference", "self_state")
                },
            )
            assert len(batch) == 8
            assert 3 <= len(observed) <= 6
            assert sum(count for _, count in observed) <= 48
            assert all(
                sql.lstrip().startswith("select") and "limit" in sql and 0 <= count <= 8
                for sql, count in observed
            )
    finally:
        event.remove(db_engine, "after_cursor_execute", capture)
