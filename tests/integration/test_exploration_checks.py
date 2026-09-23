"""Exploration diagnostics observe durable policy and grants without changing them."""

import hashlib
import json
from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import event
from test_integrity_checks import NOW, create_healthy

from cognition.db.exploration_checks import check_exploration_state
from cognition.db.models.attention import Wake
from cognition.db.models.cognition import (
    CognitionCycle,
    CognitionTurn,
    ContextSnapshot,
    CycleWake,
    ModelInvocation,
)
from cognition.db.models.evidence import Event, EventContent
from cognition.db.models.exploration import ExplorationGrant, ExplorationState
from cognition.db.models.governance import GovernanceState
from cognition.protocols.common import new_id
from cognition.protocols.executive import parse_request
from cognition.stores.exploration_scope import (
    exploration_control,
    exploration_grant_hash,
    get_cycle_exploration,
)


def findings(session):
    result = []
    check_exploration_state(session, lambda *finding: result.append(finding))
    return result


@pytest.mark.parametrize(
    "policy",
    [
        None,
        {"schema_version": True, "enabled": True},
        {"schema_version": 1, "enabled": "private fixture secret"},
    ],
)
def test_invalid_reserved_policy_is_diagnosed_without_any_scheduler_state(
    db_session_factory, policy
):
    born = create_healthy(db_session_factory)
    with db_session_factory.begin() as session:
        session.get(GovernanceState, born.individual_id).budget_policy = {
            "internal_exploration": policy
        }
    with db_session_factory() as session:
        result = findings(session)
        assert any(item[0] == "exploration_policy" for item in result)
        assert "private fixture secret" not in str(result)


def test_policy_diagnostics_ignore_dirty_cache_and_never_flush(
    db_engine, db_session_factory
):
    born = create_healthy(db_session_factory)
    writes = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().split(maxsplit=1)[0] in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)

    with db_session_factory() as session:
        governance = session.get(GovernanceState, born.individual_id)
        governance.budget_policy = {"internal_exploration": {"enabled": "dirty"}}
        event.listen(db_engine, "before_cursor_execute", observe)
        try:
            assert findings(session) == []
        finally:
            event.remove(db_engine, "before_cursor_execute", observe)
        assert governance in session.dirty
        assert governance.budget_policy == {
            "internal_exploration": {"enabled": "dirty"}
        }
        assert not writes


def add_grant(session, person):
    wake_id = new_id()
    marker = Event(
        individual_id=person,
        event_type="attention.exploration_scheduled",
        source_kind="runtime",
        source_id="attention.exploration",
        occurred_at=NOW,
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
            payload={"private": "private fixture secret"},
            sensitivity="internal",
            retention_class="history",
        )
    )
    session.add(
        Wake(
            wake_id=wake_id,
            individual_id=person,
            kind="routine",
            status="pending",
            due_at=NOW,
            purpose="Internal exploration may end in sleep",
            context_refs=[],
            cause_event_id=marker.event_id,
        )
    )
    session.flush()
    fields = dict(
        individual_id=person,
        wake_id=wake_id,
        policy_version=1,
        authorizing_governance_revision=1,
        policy_snapshot={"schema_version": 1, "enabled": True},
        created_at=NOW,
        not_before_at=NOW,
        scope="internal",
        max_turns=1,
        max_attempts_per_turn=2,
        max_seconds=120,
        max_wakes=1,
    )
    session.add(
        ExplorationGrant(**fields, content_hash=exploration_grant_hash(**fields))
    )
    session.flush()
    return wake_id, marker.event_id


@pytest.fixture
def graph(db_session_factory):
    person = create_healthy(db_session_factory)
    other = create_healthy(db_session_factory)
    with db_session_factory.begin() as session:
        wake_id, marker_id = add_grant(session, person.individual_id)
        session.add(
            ExplorationState(
                individual_id=person.individual_id,
                next_eligible_at=NOW,
                managed_wake_id=wake_id,
                materialization_pending=False,
            )
        )
    return person, other, wake_id, marker_id


def test_valid_pending_grant_is_clean(db_session_factory, graph):
    with db_session_factory() as session:
        assert findings(session) == []


@pytest.mark.parametrize(
    "corruption",
    [
        "hash",
        "foreign_grant",
        "foreign_wake",
        "foreign_marker",
        "kind",
        "timing",
        "marker_source",
        "marker_subject",
    ],
)
def test_retained_grant_corruption_is_diagnosed_without_disclosing_content(
    db_session_factory, graph, corruption
):
    person, other, wake_id, marker_id = graph
    with db_session_factory.begin() as session:
        grant = session.get(ExplorationGrant, wake_id)
        wake = session.get(Wake, wake_id)
        marker = session.get(Event, marker_id)
        if corruption == "hash":
            grant.content_hash = "0" * 64
        elif corruption == "foreign_grant":
            grant.individual_id = other.individual_id
        elif corruption == "foreign_wake":
            wake.individual_id = other.individual_id
        elif corruption == "foreign_marker":
            marker.individual_id = other.individual_id
        elif corruption == "kind":
            wake.kind = "self_scheduled"
        elif corruption == "timing":
            wake.due_at = NOW - timedelta(microseconds=1)
        elif corruption == "marker_source":
            marker.source_kind = "connector"
        else:
            marker.subject_kind, marker.subject_id = "individual", person.individual_id
    with db_session_factory() as session:
        result = findings(session)
        assert any(item[0] == "exploration_grant" for item in result)
        assert "private fixture secret" not in str(result)


@pytest.mark.parametrize("hide", ["pointer", "state", "second_grant"])
def test_live_grants_cannot_hide_outside_the_current_pointer(
    db_session_factory, graph, hide
):
    person, _, _, _ = graph
    with db_session_factory.begin() as session:
        state = session.get(ExplorationState, person.individual_id)
        if hide == "pointer":
            state.managed_wake_id = None
        elif hide == "state":
            session.delete(state)
        else:
            add_grant(session, person.individual_id)
    with db_session_factory() as session:
        assert any(item[0] == "exploration_state" for item in findings(session))


def test_historical_marker_finds_missing_grant_after_redaction_and_pointer_advance(
    db_session_factory, graph
):
    person, _, wake_id, marker_id = graph
    with db_session_factory.begin() as session:
        session.get(ExplorationState, person.individual_id).managed_wake_id = None
        session.get(Wake, wake_id).status = "cancelled"
        content = session.get(EventContent, marker_id)
        content.payload, content.redacted_at = None, NOW
        session.delete(session.get(ExplorationGrant, wake_id))
    with db_session_factory() as session:
        assert any(item[0] == "exploration_grant" for item in findings(session))


def add_cycle(session, person, wake_id, *, status="active"):
    cycle = CognitionCycle(
        individual_id=person.individual_id,
        status=status,
        started_at=NOW,
        completed_at=None if status == "active" else NOW,
        terminal_reason=None if status == "active" else "sleep",
        max_turns=1,
        max_attempts_per_turn=2,
        max_wakes=1,
        deadline_at=NOW + timedelta(seconds=120),
        min_wake_delay_seconds=1,
    )
    session.add(cycle)
    session.flush()
    session.add(CycleWake(cycle_id=cycle.cycle_id, wake_id=wake_id))
    wake = session.get(Wake, wake_id)
    wake.status = "claimed" if status == "active" else "consumed"
    wake.claimed_at = NOW
    wake.consumed_at = None if status == "active" else NOW
    session.flush()
    return cycle


@pytest.mark.parametrize(
    "fault",
    [
        "orphan_claim",
        "consumed_without_accounting",
        "cancelled_without_accounting",
        "cycle_limits",
        "mixed_cycle",
    ],
)
def test_grant_lifecycle_and_physical_cycle_limits_are_diagnosed(
    db_session_factory, graph, fault
):
    person, _, wake_id, _ = graph
    with db_session_factory.begin() as session:
        if fault == "orphan_claim":
            session.get(Wake, wake_id).status = "claimed"
        elif fault == "cancelled_without_accounting":
            session.get(Wake, wake_id).status = "cancelled"
        else:
            cycle = add_cycle(
                session,
                person,
                wake_id,
                status="completed"
                if fault == "consumed_without_accounting"
                else "active",
            )
            if fault == "cycle_limits":
                cycle.max_turns = 2
            elif fault == "mixed_cycle":
                session.add(
                    CycleWake(cycle_id=cycle.cycle_id, wake_id=person.bootstrap_wake_id)
                )
    with db_session_factory() as session:
        assert any(
            item[0] in {"exploration_cycle", "exploration_outcome"}
            for item in findings(session)
        )


def add_snapshot(session, person, cycle, version):
    turn = CognitionTurn(
        cycle_id=cycle.cycle_id, ordinal=1, status="prepared", created_at=NOW
    )
    session.add(turn)
    session.flush()
    exploration = get_cycle_exploration(session, cycle.cycle_id)
    request = parse_request(
        dict(
            schema_version=version,
            request_id=new_id(),
            individual_id=person.individual_id,
            cycle_id=cycle.cycle_id,
            turn_id=turn.turn_id,
            cognition_protocol_version=version,
            runtime_contract_version="3.1" if version == 1 else "3.2",
            present_time=NOW,
            context_sections=[exploration_control(exploration)] if exploration else [],
            capabilities=[],
            output_schema=f"CognitionDecisionV{version}",
            input_token_budget=12000,
            output_token_budget=1024,
            inference_preferences=None,
        )
    )
    value = request.model_dump(mode="json")
    rendered = json.dumps(value, sort_keys=True, separators=(",", ":"))
    snapshot = ContextSnapshot(
        turn_id=turn.turn_id,
        config_revision_id=person.config_revision_id,
        runtime_contract_version=request.runtime_contract_version,
        model_adapter="fake",
        requested_model="test",
        request_json=value,
        rendered_context=rendered,
        content_hash=hashlib.sha256(rendered.encode()).hexdigest(),
        selected_refs=[],
        retrieval_reasons={},
        estimated_input_tokens=100,
        created_at=NOW,
    )
    session.add(snapshot)
    session.flush()
    return snapshot


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize(
    "fault",
    [None, "missing", "duplicate", "category", "boolean_limit", "refs", "scope"],
)
def test_frozen_exploration_control_matches_exact_durable_grant_and_cycle(
    db_session_factory, graph, version, fault
):
    person, _, wake_id, _ = graph
    with db_session_factory.begin() as session:
        cycle = add_cycle(session, person, wake_id)
        snapshot = add_snapshot(session, person, cycle, version)
        value = deepcopy(snapshot.request_json)
        sections = value["context_sections"]
        control = sections[0]
        if fault == "missing":
            sections.clear()
        elif fault == "duplicate":
            sections.append(deepcopy(control))
        elif fault == "category":
            control["category"] = "present"
        elif fault == "boolean_limit":
            control["content"]["effective_limits"]["max_turns"] = True
        elif fault == "refs":
            control["refs"] = []
        elif fault == "scope":
            control["content"]["scope"] = "external"
        # Core ensures bool/int inequality is not lost to Python ORM equality.
        session.execute(
            ContextSnapshot.__table__.update()
            .where(ContextSnapshot.snapshot_id == snapshot.snapshot_id)
            .values(request_json=value)
        )
    with db_session_factory() as session:
        controls = [
            item for item in findings(session) if item[0] == "exploration_control"
        ]
        assert bool(controls) is (fault is not None)


def test_dirty_grant_and_wake_values_are_not_refreshed_or_used(
    db_session_factory, graph
):
    _, _, wake_id, _ = graph
    with db_session_factory() as session:
        wake, grant = session.get(Wake, wake_id), session.get(ExplorationGrant, wake_id)
        wake.kind, grant.content_hash = "self_scheduled", "0" * 64
        assert findings(session) == []
        assert wake.kind == "self_scheduled" and grant.content_hash == "0" * 64
        assert wake in session.dirty and grant in session.dirty


@pytest.mark.parametrize("status", ["cancelled", "consumed"])
@pytest.mark.parametrize("missing_time", [False, True])
def test_historical_valid_outcome_survives_payload_redaction(
    db_session_factory, graph, status, missing_time
):
    person, _, wake_id, _ = graph
    with db_session_factory.begin() as session:
        cycle = (
            add_cycle(session, person, wake_id, status="completed")
            if status == "consumed"
            else None
        )
        session.get(Wake, wake_id).status = status
        outcome = Event(
            individual_id=person.individual_id,
            event_type=f"attention.exploration_{'completed' if cycle else 'cancelled'}",
            source_kind="runtime",
            source_id="attention.exploration",
            occurred_at=NOW,
            observed_at=NOW,
            recorded_at=NOW,
            subject_kind="wake",
            subject_id=wake_id,
            correlation_id=None if cycle is None else cycle.cycle_id,
            provenance={},
            runtime_version="test",
        )
        session.add(outcome)
        session.flush()
        session.add(
            EventContent(
                event_id=outcome.event_id,
                content_type="application/json",
                sensitivity="internal",
                retention_class="history",
                payload=None,
                redacted_at=NOW,
            )
        )
        state = session.get(ExplorationState, person.individual_id)
        state.last_outcome_event_id = outcome.event_id
        state.last_terminal_cycle_id = None if cycle is None else cycle.cycle_id
        state.next_eligible_at = NOW + timedelta(days=7)
        state.materialization_pending = True
        state.managed_wake_id = wake_id if cycle else None
        if missing_time:
            outcome.occurred_at = None
    with db_session_factory() as session:
        result = findings(session)
        if missing_time:
            assert any(item[0] == "exploration_state" for item in result)
        else:
            assert result == []


def test_ordinary_frozen_request_rejects_unexpected_exploration_control(
    db_session_factory, graph
):
    person, _, wake_id, _ = graph
    with db_session_factory.begin() as session:
        cycle = add_cycle(session, person, wake_id)
        snapshot = add_snapshot(session, person, cycle, 1)
        # Move only the durable cycle membership to its ordinary bootstrap wake;
        # the forged control must not turn that cycle into an exploration grant.
        claim = session.get(CycleWake, (cycle.cycle_id, wake_id))
        session.delete(claim)
        session.flush()
        session.add(
            CycleWake(cycle_id=cycle.cycle_id, wake_id=person.bootstrap_wake_id)
        )
        snapshot_id = snapshot.snapshot_id
    with db_session_factory() as session:
        assert any(
            item[0] == "exploration_control" and item[2] == snapshot_id
            for item in findings(session)
        )


def test_frozen_control_diagnostics_ignore_dirty_snapshot_cache(
    db_session_factory, graph
):
    person, _, wake_id, _ = graph
    with db_session_factory.begin() as session:
        cycle = add_cycle(session, person, wake_id)
        snapshot_id = add_snapshot(session, person, cycle, 2).snapshot_id
    with db_session_factory() as session:
        snapshot = session.get(ContextSnapshot, snapshot_id)
        value = deepcopy(snapshot.request_json)
        value["context_sections"] = []
        snapshot.request_json = value
        assert findings(session) == []
        assert snapshot.request_json == value and snapshot in session.dirty


def test_general_database_report_includes_reserved_policy_diagnostic(
    db_session_factory,
):
    from cognition.db.checks import check_database

    born = create_healthy(db_session_factory)
    with db_session_factory.begin() as session:
        session.get(GovernanceState, born.individual_id).budget_policy = {
            "internal_exploration": None
        }
    with db_session_factory() as session:
        assert any(
            item.invariant_id == "exploration_policy"
            for item in check_database(session).findings
        )


@pytest.mark.parametrize(
    "fault", ["three_starts", "extra_turn", "deadline_start", "before_cycle_start"]
)
def test_physical_invocation_and_turn_history_cannot_exceed_grant(
    db_session_factory, graph, fault
):
    person, _, wake_id, _ = graph
    with db_session_factory.begin() as session:
        cycle = add_cycle(session, person, wake_id)
        turn = CognitionTurn(
            cycle_id=cycle.cycle_id, ordinal=1, status="invoking", created_at=NOW
        )
        session.add(turn)
        session.flush()
        if fault == "extra_turn":
            session.add(
                CognitionTurn(
                    cycle_id=cycle.cycle_id,
                    ordinal=2,
                    status="prepared",
                    created_at=NOW,
                )
            )
        else:
            for number in range(1, 4 if fault == "three_starts" else 2):
                started_at = (
                    cycle.deadline_at
                    if fault == "deadline_start"
                    else NOW - timedelta(microseconds=1)
                    if fault == "before_cycle_start"
                    else NOW
                )
                session.add(
                    ModelInvocation(
                        turn_id=turn.turn_id,
                        attempt_number=number,
                        status="failed",
                        started_at=started_at,
                        completed_at=NOW,
                        error_code="provider_error",
                    )
                )
    with db_session_factory() as session:
        assert any(item[0] == "exploration_attempts" for item in findings(session))
