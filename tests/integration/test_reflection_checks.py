"""Reflection diagnostics retain historical scope and never mutate dirty sessions."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import event

from cognition.db.checks import check_database
from cognition.db.models.attention import Wake
from cognition.db.models.cognition import CognitionCycle, CycleWake
from cognition.db.models.development import Interest
from cognition.db.models.evidence import Event, EventContent
from cognition.db.models.reflection import ManagedReflectionBatch, ReflectionState
from cognition.protocols.common import new_id
from cognition.stores.identity import create_individual
from cognition.stores.reflection_scope import reflection_batch_hash

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)


def add_person(session):
    return create_individual(
        session,
        individual_id=new_id(),
        birth_at=NOW,
        birth_name="Reflection diagnostics individual",
        founding_orientation="Retain scoped deliberation",
        creator_provenance={},
    ).individual_id


def add_batch(session, person, target):
    wake_id = new_id()
    refs = [{"kind": "interest", "id": str(target)}]
    metadata = [dict(refs[0], revision=1, eligible_at=NOW.isoformat())]
    marker = Event(
        individual_id=person,
        event_type="attention.reflection_scheduled",
        source_kind="runtime",
        source_id="attention.reflection",
        observed_at=NOW,
        recorded_at=NOW,
        subject_kind="wake",
        subject_id=wake_id,
        provenance={},
        runtime_version="test",
    )
    session.add(marker)
    session.flush()
    session.add(
        EventContent(
            event_id=marker.event_id,
            content_type="application/json",
            payload={"private": "secret fixture content"},
            sensitivity="internal",
            retention_class="history",
        )
    )
    session.add(
        Wake(
            wake_id=wake_id,
            individual_id=person,
            kind="reflection",
            status="pending",
            due_at=NOW,
            purpose="Review may lead to no change",
            context_refs=refs,
            cause_event_id=marker.event_id,
        )
    )
    session.flush()
    session.add(
        ManagedReflectionBatch(
            wake_id=wake_id,
            individual_id=person,
            policy_version=1,
            target_metadata=metadata,
            selected_at=NOW,
            content_hash=reflection_batch_hash(
                individual_id=person,
                wake_id=wake_id,
                policy_version=1,
                selected_at=NOW,
                target_metadata=metadata,
            ),
        )
    )
    session.flush()
    return wake_id, marker.event_id


@pytest.fixture
def graph(db_session_factory):
    with db_session_factory.begin() as session:
        person, other = add_person(session), add_person(session)
        target = Interest(
            individual_id=person,
            topic="Patterns",
            summary="Consider patterns",
            status="candidate",
            rationale="Unestablished interpretation",
            evidence_refs=[],
            promotion_not_before=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(target)
        session.flush()
        wake, marker = add_batch(session, person, target.interest_id)
        session.add(
            ReflectionState(
                individual_id=person,
                next_review_at=NOW,
                managed_wake_id=wake,
                materialization_pending=False,
            )
        )
        return person, other, target.interest_id, wake, marker


def findings(session):
    return [
        finding
        for finding in check_database(session).findings
        if finding.invariant_id.startswith("reflection_")
    ]


@pytest.mark.parametrize(
    "corruption",
    [
        "foreign_batch",
        "foreign_wake",
        "foreign_marker",
        "hash",
        "boolean_revision",
        "wrong_refs",
        "source",
    ],
)
def test_scope_corruption_produces_sanitized_bounded_findings(
    db_session_factory, graph, corruption
):
    person, other, _, wake_id, marker_id = graph
    with db_session_factory.begin() as session:
        batch = session.get(ManagedReflectionBatch, wake_id)
        if corruption == "foreign_batch":
            batch.individual_id = other
        elif corruption == "foreign_wake":
            session.get(Wake, wake_id).individual_id = other
        elif corruption == "foreign_marker":
            session.get(Event, marker_id).individual_id = other
        elif corruption == "hash":
            batch.content_hash = "0" * 64
        elif corruption == "boolean_revision":
            # Core bypasses ORM equality: Python considers True equal to integer 1.
            session.execute(
                ManagedReflectionBatch.__table__.update()
                .where(ManagedReflectionBatch.wake_id == wake_id)
                .values(target_metadata=[dict(batch.target_metadata[0], revision=True)])
            )
        elif corruption == "wrong_refs":
            session.get(Wake, wake_id).context_refs = []
        else:
            session.get(Event, marker_id).source_kind = "connector"
    with db_session_factory() as session:
        report = findings(session)
        assert any(finding.invariant_id == "reflection_scope" for finding in report)
        assert "secret fixture content" not in str(report)
        assert all(len(finding.message) < 200 for finding in report)


@pytest.mark.parametrize("remove_wake", [False, True])
def test_historical_creation_marker_finds_deleted_batch_after_pointer_advances(
    db_session_factory, graph, remove_wake
):
    person, _, _, wake_id, marker_id = graph
    with db_session_factory.begin() as session:
        state = session.get(ReflectionState, person)
        state.managed_wake_id = None
        session.get(Wake, wake_id).status = "consumed"
        content = session.get(EventContent, marker_id)
        content.payload, content.redacted_at = None, NOW
        session.delete(session.get(ManagedReflectionBatch, wake_id))
        session.flush()
        if remove_wake:
            session.delete(session.get(Wake, wake_id))
    with db_session_factory() as session:
        assert any(
            finding.invariant_id == "reflection_scope" for finding in findings(session)
        )


@pytest.mark.parametrize("hide", ["clear_pointer", "delete_state", "second_batch"])
def test_live_batches_cannot_hide_outside_current_pointer(
    db_session_factory, graph, hide
):
    person, _, target, _, _ = graph
    with db_session_factory.begin() as session:
        state = session.get(ReflectionState, person)
        if hide == "clear_pointer":
            state.managed_wake_id = None
        elif hide == "delete_state":
            session.delete(state)
        else:
            add_batch(session, person, target)
    with db_session_factory() as session:
        assert any(
            finding.invariant_id == "reflection_state" for finding in findings(session)
        )


def test_historical_valid_scope_survives_redaction_and_no_current_pointer(
    db_session_factory, graph
):
    person, _, _, wake_id, marker_id = graph
    with db_session_factory.begin() as session:
        session.get(ReflectionState, person).managed_wake_id = None
        session.get(Wake, wake_id).status = "cancelled"
        content = session.get(EventContent, marker_id)
        content.payload, content.redacted_at = None, NOW
    with db_session_factory() as session:
        assert findings(session) == []


def complete_historical_batch(session, person, wake_id):
    cycle = CognitionCycle(
        individual_id=person,
        status="completed",
        started_at=NOW,
        completed_at=NOW,
        terminal_reason="sleep",
        max_turns=1,
        max_attempts_per_turn=1,
        max_wakes=1,
        deadline_at=NOW,
        min_wake_delay_seconds=1,
    )
    session.add(cycle)
    session.flush()
    session.add(CycleWake(cycle_id=cycle.cycle_id, wake_id=wake_id))
    wake = session.get(Wake, wake_id)
    wake.status, wake.claimed_at, wake.consumed_at = "consumed", NOW, NOW
    marker = Event(
        individual_id=person,
        event_type="attention.reflection_completed",
        source_kind="runtime",
        source_id="attention.reflection",
        observed_at=NOW,
        recorded_at=NOW,
        subject_kind="wake",
        subject_id=wake_id,
        correlation_id=cycle.cycle_id,
        provenance={},
        runtime_version="test",
    )
    session.add(marker)
    session.flush()
    session.add(
        EventContent(
            event_id=marker.event_id,
            content_type="application/json",
            payload=None,
            sensitivity="internal",
            retention_class="history",
            redacted_at=NOW,
        )
    )
    return cycle.cycle_id, marker.event_id


@pytest.mark.parametrize(
    "corruption",
    [
        "no_cycle",
        "no_marker",
        "foreign_cycle",
        "active_cycle",
        "completion_time",
        "marker_cycle",
        "marker_owner",
        "marker_source",
    ],
)
def test_historical_consumption_requires_owned_terminal_cycle_and_outcome_marker(
    db_session_factory, graph, corruption
):
    person, other, _, wake_id, _ = graph
    with db_session_factory.begin() as session:
        session.get(ReflectionState, person).managed_wake_id = None
        cycle_id, marker_id = complete_historical_batch(session, person, wake_id)
        if corruption == "no_cycle":
            session.delete(session.get(CycleWake, (cycle_id, wake_id)))
        elif corruption == "no_marker":
            session.delete(session.get(EventContent, marker_id))
            session.flush()
            session.delete(session.get(Event, marker_id))
        elif corruption == "foreign_cycle":
            session.get(CognitionCycle, cycle_id).individual_id = other
        elif corruption == "active_cycle":
            session.get(CognitionCycle, cycle_id).status = "active"
        elif corruption == "completion_time":
            session.get(Wake, wake_id).consumed_at = None
        elif corruption == "marker_cycle":
            session.get(Event, marker_id).correlation_id = new_id()
        elif corruption == "marker_owner":
            session.get(Event, marker_id).individual_id = other
        else:
            session.get(Event, marker_id).source_kind = "connector"
    with db_session_factory() as session:
        assert any(
            finding.invariant_id == "reflection_cycle" for finding in findings(session)
        )


def test_historical_consumed_batch_with_redacted_outcome_is_valid(
    db_session_factory, graph
):
    person, _, _, wake_id, _ = graph
    with db_session_factory.begin() as session:
        session.get(ReflectionState, person).managed_wake_id = None
        complete_historical_batch(session, person, wake_id)
    with db_session_factory() as session:
        assert findings(session) == []


def test_historical_claim_outside_current_pointer_still_checks_cycle_ownership(
    db_session_factory, graph
):
    person, _, _, wake_id, _ = graph
    with db_session_factory.begin() as session:
        session.get(ReflectionState, person).managed_wake_id = None
        session.get(Wake, wake_id).status = "claimed"
    with db_session_factory() as session:
        assert any(
            finding.invariant_id == "reflection_cycle" for finding in findings(session)
        )


def test_malformed_orphan_marker_is_not_lost_without_state_or_batch(
    db_session_factory, graph
):
    person, _, _, wake_id, marker_id = graph
    with db_session_factory.begin() as session:
        session.delete(session.get(ReflectionState, person))
        session.delete(session.get(ManagedReflectionBatch, wake_id))
        session.flush()
        session.delete(session.get(Wake, wake_id))
        marker = session.get(Event, marker_id)
        marker.subject_kind, marker.subject_id = None, None
    with db_session_factory() as session:
        assert any(
            finding.invariant_id == "reflection_marker" for finding in findings(session)
        )


def test_diagnostics_do_not_flush_or_refresh_dirty_or_deleted_objects(
    db_session_factory, graph
):
    person, _, _, wake_id, marker_id = graph
    with db_session_factory() as session:
        state = session.get(ReflectionState, person)
        batch = session.get(ManagedReflectionBatch, wake_id)
        wake = session.get(Wake, wake_id)
        marker = session.get(Event, marker_id)
        state.policy_version = 2
        batch.content_hash = "dirty hash must remain"
        wake.context_refs = []
        session.delete(marker)
        original_dirty = set(session.dirty)
        flushed = []
        event.listen(session, "before_flush", lambda *_: flushed.append(True))
        assert findings(session) == []
        assert not flushed
        assert set(session.dirty) == original_dirty
        assert (
            state.policy_version == 2 and batch.content_hash == "dirty hash must remain"
        )
        assert wake.context_refs == [] and marker in session.deleted
