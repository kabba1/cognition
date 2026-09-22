"""PostgreSQL corruption diagnostics for durable cognition recovery records."""

import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select, text
from test_integrity_checks import NOW, create_healthy

from cognition.db.checks import check_database
from cognition.db.models.attention import Wake
from cognition.db.models.cognition import (
    AppliedOperation,
    CognitionCycle,
    CognitionTurn,
    ContextSnapshot,
    CycleWake,
    ModelInvocation,
)
from cognition.protocols.common import new_id
from cognition.protocols.model_v1 import ModelRequestV1, ModelResultV1
from cognition.stores.cognition import (
    CycleLimits,
    apply_decision,
    canonical_json,
    claim_or_resume,
    content_hash,
    latest_turn,
    record_result,
    save_context,
    start_invocation,
)

GOLDEN = Path(__file__).parents[1] / "golden"


@pytest.fixture
def stages(db_session_factory):
    """Build genuine committed states using the runtime-facing stores."""
    person = create_healthy(db_session_factory)

    def advance(stage="decided", decision_changes=None):
        if stage == "pending":
            return person, None, None
        with db_session_factory.begin() as session:
            cycle = claim_or_resume(session, person.individual_id, NOW, CycleLimits())
            turn = latest_turn(session, cycle.cycle_id)
        if stage == "prepared":
            return person, cycle, turn
        request_json = json.loads((GOLDEN / "model_request_v1.json").read_text())
        request_json.update(
            individual_id=str(person.individual_id),
            cycle_id=str(cycle.cycle_id),
            turn_id=str(turn.turn_id),
        )
        request = ModelRequestV1.model_validate(request_json)
        rendered = canonical_json(request_json)
        with db_session_factory.begin() as session:
            save_context(
                session,
                turn_id=turn.turn_id,
                config_revision_id=person.config_revision_id,
                request=request,
                rendered_context=rendered,
                context_hash=hashlib.sha256(rendered.encode()).hexdigest(),
                selected_refs=(),
                retrieval_reasons={},
                estimated_input_tokens=len(rendered),
                adapter="scripted",
                requested_model="scripted",
                now=NOW,
                retain_until=None,
            )
            invocation_id = start_invocation(session, cycle, turn, NOW)
        if stage == "invoking":
            return person, cycle, turn
        result_json = json.loads((GOLDEN / "model_result_v1.json").read_text())
        result_json["decision"].update(
            cycle_id=str(cycle.cycle_id), turn_id=str(turn.turn_id)
        )
        if decision_changes:
            result_json["decision"].update(decision_changes)
        result = ModelResultV1.model_validate(result_json)
        with db_session_factory.begin() as session:
            record_result(session, cycle, turn, invocation_id, request, result, NOW)
        if stage in {"applied", "rejected"}:
            with db_session_factory.begin() as session:
                apply_decision(
                    session, cycle, latest_turn(session, cycle.cycle_id), NOW
                )
        return person, cycle, turn

    return advance


def findings(factory, invariant):
    with factory.begin() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        report = check_database(session)
        return [item for item in report.findings if item.invariant_id == invariant]


@pytest.mark.parametrize(
    "stage", ["pending", "prepared", "invoking", "decided", "applied"]
)
def test_healthy_durable_stages_are_clean(db_session_factory, stages, stage):
    stages(stage)
    with db_session_factory.begin() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        report = check_database(session)
        assert report.findings == ()
        assert report.not_applicable == ()


@pytest.mark.parametrize("stage", ["decided", "rejected"])
@pytest.mark.parametrize("field", ["cycle_id", "turn_id"])
def test_invalid_proposal_targets_are_honest_recorded_evidence(
    db_session_factory, stages, stage, field
):
    _, _, turn = stages(stage, {field: str(new_id())})
    with db_session_factory() as session:
        assert session.get(CognitionTurn, turn.turn_id).status == stage
        assert check_database(session).findings == ()


@pytest.mark.parametrize("corruption", ["unlinked", "terminal", "wrong_individual"])
def test_claimed_wake_requires_active_matching_cycle(
    db_session_factory, stages, corruption
):
    person, cycle, _ = stages("prepared")
    other = (
        create_healthy(db_session_factory) if corruption == "wrong_individual" else None
    )
    with db_session_factory.begin() as session:
        link = session.scalar(
            select(CycleWake).where(CycleWake.cycle_id == cycle.cycle_id)
        )
        if corruption == "unlinked":
            session.delete(link)
        elif corruption == "terminal":
            session.get(CognitionCycle, cycle.cycle_id).status = "completed"
        else:
            session.get(Wake, link.wake_id).individual_id = other.individual_id
    result = findings(db_session_factory, "claimed_wake_cycle")
    assert len(result) == 1
    assert result[0].subject.kind == "wake"
    if corruption == "wrong_individual":
        assert len(findings(db_session_factory, "cycle_wake_individual")) == 1


@pytest.mark.parametrize("corruption", ["none", "two", "not_latest"])
def test_active_cycle_requires_one_latest_unfinished_turn(
    db_session_factory, stages, corruption
):
    _, cycle, turn = stages("prepared")
    with db_session_factory.begin() as session:
        if corruption == "none":
            session.get(CognitionTurn, turn.turn_id).status = "failed"
        else:
            session.add(
                CognitionTurn(
                    turn_id=new_id(),
                    cycle_id=cycle.cycle_id,
                    ordinal=2,
                    status="prepared" if corruption == "two" else "failed",
                    created_at=NOW,
                    completed_at=None,
                    decision_id=None,
                    decision_json=None,
                    decision_hash=None,
                    validation_errors=[],
                    disposition=None,
                )
            )
    assert len(findings(db_session_factory, "cycle_active_turn")) == 1


@pytest.mark.parametrize("stage", ["decided", "applied"])
@pytest.mark.parametrize(
    "corruption", ["missing", "malformed", "hash", "id", "cycle", "turn"]
)
def test_decision_identity_schema_and_hash(
    db_session_factory, stages, stage, corruption
):
    _, _, turn = stages(stage)
    with db_session_factory.begin() as session:
        row = session.get(CognitionTurn, turn.turn_id)
        if corruption == "missing":
            row.decision_json = None
        elif corruption == "hash":
            row.decision_hash = "incorrect"
        elif corruption == "id":
            row.decision_id = new_id()
        else:
            value = dict(row.decision_json)
            if corruption == "malformed":
                value["disposition"] = "private-password-must-not-escape"
            else:
                value[f"{corruption}_id"] = str(new_id())
            row.decision_json = value
            row.decision_hash = content_hash(value)
    result = findings(db_session_factory, "turn_decision")
    if stage == "decided" and corruption in {"cycle", "turn"}:
        assert result == []  # Recorded proposals await semantic validation.
        return
    assert result and result[0].subject.id == turn.turn_id
    assert "private-password" not in repr(result)


@pytest.mark.parametrize(
    "corruption",
    ["hash", "rendered", "mismatch", "schema", "cycle", "turn", "individual", "config"],
)
def test_snapshot_integrity_and_ownership(db_session_factory, stages, corruption):
    _, _, turn = stages("invoking")
    other = create_healthy(db_session_factory) if corruption == "config" else None
    with db_session_factory.begin() as session:
        row = session.scalar(
            select(ContextSnapshot).where(ContextSnapshot.turn_id == turn.turn_id)
        )
        if corruption == "hash":
            row.content_hash = "incorrect"
        elif corruption == "rendered":
            row.rendered_context = "{private-password"
            row.content_hash = hashlib.sha256(row.rendered_context.encode()).hexdigest()
        elif corruption == "config":
            row.config_revision_id = other.config_revision_id
        else:
            value = dict(row.request_json)
            if corruption == "schema":
                value["schema_version"] = "private-password"
            elif corruption == "mismatch":
                value["output_schema"] = "Changed"
            else:
                value[f"{corruption}_id"] = str(new_id())
            row.request_json = value
            if corruption != "mismatch":
                row.rendered_context = canonical_json(value)
                row.content_hash = hashlib.sha256(
                    row.rendered_context.encode()
                ).hexdigest()
    result = findings(db_session_factory, "context_snapshot")
    assert result and result[0].subject.kind == "context_snapshot"
    assert "private-password" not in repr(result)


def test_started_invocation_requires_invoking_turn(db_session_factory, stages):
    _, _, turn = stages("invoking")
    with db_session_factory.begin() as session:
        session.get(CognitionTurn, turn.turn_id).status = "prepared"
    assert len(findings(db_session_factory, "invocation_turn")) == 1


@pytest.mark.parametrize(
    "corruption", ["missing", "schema", "request", "decision", "status"]
)
def test_completed_invocation_matches_request_and_decision(
    db_session_factory, stages, corruption
):
    stages("decided")
    with db_session_factory.begin() as session:
        row = session.scalar(select(ModelInvocation))
        value = dict(row.result_json)
        if corruption == "missing":
            row.result_json = None
        else:
            if corruption == "schema":
                value["schema_version"] = "private-password"
            elif corruption == "request":
                value["request_id"] = str(new_id())
            elif corruption == "status":
                value.update(status="refused", decision=None)
            else:
                value["decision"] = dict(value["decision"], rationale_summary="Altered")
            row.result_json = value
    result = findings(db_session_factory, "invocation_result")
    assert result and "private-password" not in repr(result)


def test_applied_operation_individual_matches_cycle(db_session_factory, stages):
    _, _, turn = stages("applied")
    other = create_healthy(db_session_factory)
    with db_session_factory.begin() as session:
        session.add(
            AppliedOperation(
                operation_id=new_id(),
                turn_id=turn.turn_id,
                individual_id=other.individual_id,
                kind="wake_request",
                applied_at=NOW,
            )
        )
    assert len(findings(db_session_factory, "applied_operation_individual")) == 1


def test_checker_observes_persisted_state_without_flushing_or_repair(
    db_session_factory, stages
):
    _, _, turn = stages("decided")
    with db_session_factory() as session:
        row = session.get(CognitionTurn, turn.turn_id)
        row.decision_hash = "unflushed-corruption"
        assert check_database(session).healthy
        assert row in session.dirty
        assert row.decision_hash == "unflushed-corruption"
    with db_session_factory.begin() as session:
        row = session.get(CognitionTurn, turn.turn_id)
        row.decision_hash = "persisted-corruption"
    assert findings(db_session_factory, "turn_decision")
    with db_session_factory() as session:
        assert (
            session.get(CognitionTurn, turn.turn_id).decision_hash
            == "persisted-corruption"
        )
