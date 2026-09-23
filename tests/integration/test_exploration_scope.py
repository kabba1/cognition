"""Immutable exploration identity cannot become an ordinary larger allowance."""

from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import delete, select
from test_exploration_stores import NOW, claim, enabled, models, scope
from test_exploration_stores import person as person

from cognition.db.models.attention import Wake
from cognition.db.models.cognition import CycleWake
from cognition.db.models.evidence import Event, EventContent
from cognition.db.models.governance import GovernanceState


def fields():
    return dict(
        wake_id=UUID(int=1),
        individual_id=UUID(int=2),
        policy_version=1,
        authorizing_governance_revision=2,
        policy_snapshot={"schema_version": 1, "enabled": True},
        created_at=NOW,
        not_before_at=NOW,
        scope="internal",
        max_turns=1,
        max_attempts_per_turn=2,
        max_seconds=120,
        max_wakes=1,
    )


def test_hash_binds_each_immutable_field():
    original = fields()
    digest = scope().exploration_grant_hash(**original)
    scope().validate_exploration_grant_snapshot(**original, content_hash=digest)
    for key, value in (
        ("wake_id", UUID(int=9)),
        ("individual_id", UUID(int=9)),
        ("authorizing_governance_revision", 3),
        ("created_at", NOW - timedelta(seconds=1)),
        ("not_before_at", NOW + timedelta(seconds=1)),
    ):
        with pytest.raises(ValueError):
            scope().validate_exploration_grant_snapshot(
                **(original | {key: value}), content_hash=digest
            )


@pytest.mark.parametrize(
    "update",
    [
        {"policy_version": True},
        {"max_turns": True},
        {"max_attempts_per_turn": 2.0},
        {"max_seconds": 121},
        {"max_wakes": 2},
        {"scope": "external"},
        {"authorizing_governance_revision": 0},
        {"policy_snapshot": {"schema_version": 1, "enabled": False}},
        {"not_before_at": NOW - timedelta(seconds=1)},
        {"created_at": NOW.replace(tzinfo=None)},
    ],
)
def test_immutable_caps_and_policy_are_strict(update):
    with pytest.raises(ValueError):
        scope().exploration_grant_hash(**(fields() | update))


def test_creation_content_redaction_does_not_erase_grant_identity(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        state = enabled(session, person)
        wake = session.get(Wake, state.managed_wake_id)
        session.get(EventContent, wake.cause_event_id).payload = None
        session.flush()
        grant = scope().get_exploration_grant(session, person, wake.wake_id)
        assert grant.wake_id == wake.wake_id
        found = scope().discover_exploration(session, person, NOW)
        assert found.managed_wake_ids == (wake.wake_id,) and found.eligible == grant


@pytest.mark.parametrize(
    "corruption", ["kind", "timing", "key", "marker", "missing_grant", "hidden_pointer"]
)
def test_managed_corruption_cannot_fall_through_to_ordinary_claims(
    db_session_factory, person, corruption
):
    with db_session_factory.begin() as session:
        state = enabled(session, person)
        wake = session.get(Wake, state.managed_wake_id)
        if corruption == "kind":
            wake.kind = "external_event"
        elif corruption == "timing":
            wake.due_at -= timedelta(seconds=1)
        elif corruption == "key":
            wake.coalesce_key = "ordinary"
        elif corruption == "marker":
            session.get(Event, wake.cause_event_id).source_kind = "connector"
        elif corruption == "missing_grant":
            session.execute(
                delete(models().ExplorationGrant).where(
                    models().ExplorationGrant.wake_id == wake.wake_id
                )
            )
        else:
            state.managed_wake_id = None
        session.flush()
    with db_session_factory() as session:
        with pytest.raises(ValueError):
            scope().discover_exploration(session, person, NOW)


def test_unrelated_governance_revision_does_not_revoke_valid_grant(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        state = enabled(session, person)
        original = scope().get_exploration_grant(session, person, state.managed_wake_id)
        governance = session.get(GovernanceState, person)
        governance.revision += 10
        governance.hard_boundaries = {"unrelated": True}
        session.flush()
        assert scope().discover_exploration(session, person, NOW).eligible == original


@pytest.mark.parametrize(
    "update",
    [
        {"max_turns": 2},
        {"max_attempts_per_turn": 3},
        {"max_wakes": 2},
        {"deadline_at": NOW + timedelta(seconds=121)},
        {"started_at": NOW - timedelta(seconds=1)},
    ],
)
def test_cycle_must_obey_immutable_grant_limits(db_session_factory, person, update):
    with db_session_factory.begin() as session:
        state = enabled(session, person)
        cycle = claim(session, person, state, **update)
        with pytest.raises(ValueError):
            scope().get_cycle_exploration(session, cycle)


def test_cycle_must_contain_only_its_exploration_wake(db_session_factory, person):
    with db_session_factory.begin() as session:
        state = enabled(session, person)
        cycle = claim(session, person, state)
        ordinary = session.scalar(select(Wake).where(Wake.kind == "bootstrap"))
        session.add(CycleWake(cycle_id=cycle, wake_id=ordinary.wake_id))
        session.flush()
        with pytest.raises(ValueError):
            scope().get_cycle_exploration(session, cycle)


def test_control_is_exact_typed_scoped_and_required_only_for_managed_cycles(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        state = enabled(session, person)
        cycle_id = claim(session, person, state)
        cycle = scope().get_cycle_exploration(session, cycle_id)
        control = scope().exploration_control(cycle)
        assert control.name == "internal_exploration" and control.category == "control"
        assert [ref.id for ref in control.refs] == [state.managed_wake_id]
        scope().validate_exploration_control(cycle, [control])
        scope().validate_exploration_control(None, [])
        for sections in ([], [control, control]):
            with pytest.raises(ValueError):
                scope().validate_exploration_control(cycle, sections)
        with pytest.raises(ValueError):
            scope().validate_exploration_control(None, [control])
        for update in (
            {"category": "evidence"},
            {"refs": []},
            {"content": {"scope": "internal"}},
        ):
            changed = control.model_copy(deep=True, update=update)
            with pytest.raises(ValueError):
                scope().validate_exploration_control(cycle, [changed])
        changed = control.model_copy(deep=True)
        changed.content["effective_limits"]["max_turns"] = True
        with pytest.raises(ValueError):
            scope().validate_exploration_control(cycle, [changed])


def test_read_only_discovery_ignores_dirty_cached_governance(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        state = enabled(session, person)
        pointer = state.managed_wake_id
    with db_session_factory() as session:
        cached = session.get(GovernanceState, person)
        cached.budget_policy = {}
        assert (
            scope().discover_exploration(session, person, NOW).eligible.wake_id
            == pointer
        )
        assert cached in session.dirty and cached.budget_policy == {}


def test_foreign_pointer_alone_cannot_hide_managed_identity_from_discovery(
    db_session_factory, person
):
    from test_integrity_checks import create_healthy

    other = create_healthy(db_session_factory)
    with db_session_factory.begin() as session:
        ordinary = session.scalar(
            select(Wake).where(Wake.kind == "bootstrap", Wake.individual_id == person)
        )
        session.add(
            models().ExplorationState(
                individual_id=other.individual_id,
                next_eligible_at=NOW,
                managed_wake_id=ordinary.wake_id,
                materialization_pending=False,
            )
        )
    with db_session_factory() as session:
        with pytest.raises(ValueError):
            scope().discover_exploration(session, person, NOW)
