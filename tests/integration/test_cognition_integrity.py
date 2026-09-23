"""PostgreSQL corruption diagnostics for durable cognition recovery records."""

import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select, text
from test_integrity_checks import NOW, create_healthy

from cognition.config.schema import parse_behavior_config
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
from cognition.db.models.runtime import RuntimeConfigRevision
from cognition.protocols.common import new_id
from cognition.protocols.executive import parse_request, parse_result
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
from cognition.stores.configuration import replace_config_revision

GOLDEN = Path(__file__).parents[1] / "golden"


@pytest.fixture
def stages(db_session_factory):
    """Build genuine committed states using the runtime-facing stores."""
    person = create_healthy(db_session_factory)

    def advance(stage="decided", decision_changes=None, protocol_version=1):
        config_revision_id = person.config_revision_id
        if protocol_version == 2:
            with db_session_factory.begin() as session:
                config = session.get(RuntimeConfigRevision, config_revision_id)
                values = dict(config.sanitized_config)
                values.update(
                    config_schema_version=2, execution={"cognition_protocol_version": 2}
                )
                revision, _ = replace_config_revision(
                    session, person.individual_id, parse_behavior_config(values), NOW
                )
                config_revision_id = revision.config_revision_id
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
            runtime_contract_version="3.1" if protocol_version == 1 else "3.2",
            schema_version=protocol_version,
            cognition_protocol_version=protocol_version,
            output_schema=f"CognitionDecisionV{protocol_version}",
        )
        with db_session_factory() as session:
            config = session.get(RuntimeConfigRevision, config_revision_id)
            request_json["context_sections"].insert(
                0,
                {
                    "name": "runtime_control",
                    "category": "control",
                    "content": {
                        "config_revision_id": str(config_revision_id),
                        "config_content_hash": config.content_hash,
                    },
                    "refs": [],
                },
            )
        request = parse_request(request_json)
        rendered = canonical_json(request_json)
        with db_session_factory.begin() as session:
            save_context(
                session,
                turn_id=turn.turn_id,
                config_revision_id=config_revision_id,
                request=request,
                rendered_context=rendered,
                context_hash=hashlib.sha256(rendered.encode()).hexdigest(),
                selected_refs=(),
                retrieval_reasons={},
                estimated_input_tokens=len(rendered),
                adapter="scripted",
                requested_model="test-model",
                now=NOW,
                retain_until=None,
            )
            invocation_id = start_invocation(session, cycle, turn, NOW)
        if stage == "invoking":
            return person, cycle, turn
        result_json = json.loads((GOLDEN / "model_result_v1.json").read_text())
        result_json["schema_version"] = protocol_version
        result_json["decision"]["schema_version"] = protocol_version
        if protocol_version == 2:
            for field in (
                "entity_operations",
                "project_operations",
                "relationship_operations",
                "relationship_thread_operations",
            ):
                result_json["decision"][field] = []
        result_json["decision"].update(
            cycle_id=str(cycle.cycle_id), turn_id=str(turn.turn_id)
        )
        if decision_changes:
            result_json["decision"].update(decision_changes)
        result = parse_result(result_json)
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


@pytest.mark.parametrize("stage", ["prepared", "invoking", "decided", "applied"])
def test_v2_durable_stages_are_clean(db_session_factory, stages, stage):
    stages(stage, protocol_version=2)
    with db_session_factory.begin() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        assert check_database(session).healthy


@pytest.mark.parametrize("corruption", ["json", "hash", "version"])
def test_configuration_corruption_is_bounded_and_not_repaired(
    db_session_factory, stages, corruption
):
    person, _, _ = stages("invoking")
    with db_session_factory.begin() as session:
        row = session.get(RuntimeConfigRevision, person.config_revision_id)
        if corruption == "json":
            row.sanitized_config = {"config_schema_version": "SECRET_CORRUPTION"}
        elif corruption == "hash":
            row.content_hash = "SECRET_CORRUPTION"
        else:
            row.config_schema_version = 2
    result = findings(db_session_factory, "config_revision")
    assert result and "SECRET_CORRUPTION" not in repr(result)
    assert findings(db_session_factory, "context_snapshot")


@pytest.mark.parametrize(
    "corruption",
    [
        "unsupported_tuple",
        "contract_column",
        "adapter",
        "model",
        "configuration_selection",
    ],
)
def test_frozen_snapshot_requires_linked_config_and_contract_tuple(
    db_session_factory, stages, corruption
):
    person, _, turn = stages("invoking")
    with db_session_factory.begin() as session:
        row = session.scalar(
            select(ContextSnapshot).where(ContextSnapshot.turn_id == turn.turn_id)
        )
        if corruption == "unsupported_tuple":
            data = dict(row.request_json, runtime_contract_version="3.2")
            row.request_json = data
            row.rendered_context = canonical_json(data)
            row.content_hash = content_hash(data)
            row.runtime_contract_version = "3.2"
        elif corruption == "contract_column":
            row.runtime_contract_version = "3.2"
        elif corruption == "adapter":
            row.model_adapter = "unselected-adapter"
        elif corruption == "model":
            row.requested_model = "unselected-model"
        else:
            original = session.get(RuntimeConfigRevision, person.config_revision_id)
            values = dict(
                original.sanitized_config,
                config_schema_version=2,
                execution={"cognition_protocol_version": 2},
            )
            changed, _ = replace_config_revision(
                session, person.individual_id, parse_behavior_config(values), NOW
            )
            row.config_revision_id = changed.config_revision_id
    assert findings(db_session_factory, "context_snapshot")


def test_snapshot_validation_uses_historical_config_instead_of_current(
    db_session_factory, stages
):
    person, _, _ = stages("decided")
    with db_session_factory.begin() as session:
        original = session.get(RuntimeConfigRevision, person.config_revision_id)
        values = dict(
            original.sanitized_config,
            config_schema_version=2,
            execution={"cognition_protocol_version": 2},
        )
        replace_config_revision(
            session, person.individual_id, parse_behavior_config(values), NOW
        )
    with db_session_factory.begin() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        assert check_database(session).healthy


@pytest.mark.parametrize("stage", ["decided", "applied"])
def test_matching_v2_result_and_decision_cannot_belong_to_v1_request(
    db_session_factory, stages, stage
):
    _, _, turn = stages(stage)
    with db_session_factory.begin() as session:
        row = session.get(CognitionTurn, turn.turn_id)
        data = dict(row.decision_json, schema_version=2)
        for field in (
            "entity_operations",
            "project_operations",
            "relationship_operations",
            "relationship_thread_operations",
        ):
            data[field] = []
        row.decision_json = data
        row.decision_hash = content_hash(data)
        invocation = session.scalar(
            select(ModelInvocation).where(ModelInvocation.turn_id == turn.turn_id)
        )
        invocation.result_json = dict(
            invocation.result_json, schema_version=2, decision=data
        )
    assert findings(db_session_factory, "invocation_result")
    if stage == "applied":
        assert findings(db_session_factory, "turn_decision")


def test_checker_ignores_dirty_config_and_snapshot_identity_maps(
    db_session_factory, stages
):
    person, _, turn = stages("decided")
    with db_session_factory() as session:
        config = session.get(RuntimeConfigRevision, person.config_revision_id)
        snapshot = session.scalar(
            select(ContextSnapshot).where(ContextSnapshot.turn_id == turn.turn_id)
        )
        config.content_hash = "UNFLUSHED_CONFIG"
        snapshot.model_adapter = "UNFLUSHED_ADAPTER"
        assert check_database(session).healthy
        assert config in session.dirty and snapshot in session.dirty
        assert config.content_hash == "UNFLUSHED_CONFIG"
        assert snapshot.model_adapter == "UNFLUSHED_ADAPTER"


@pytest.mark.parametrize(
    "corruption",
    [
        "relink",
        "embedded_id",
        "embedded_hash",
        "missing",
        "duplicate",
        "category",
        "content",
    ],
)
def test_frozen_control_metadata_must_name_the_exact_linked_configuration(
    db_session_factory, stages, corruption
):
    person, _, turn = stages("invoking")
    with db_session_factory.begin() as session:
        row = session.scalar(
            select(ContextSnapshot).where(ContextSnapshot.turn_id == turn.turn_id)
        )
        if corruption == "relink":
            original = session.get(RuntimeConfigRevision, person.config_revision_id)
            value = parse_behavior_config(original.sanitized_config)
            value.attention.context_budget_tokens += 1024
            replacement, _ = replace_config_revision(
                session, person.individual_id, value, NOW
            )
            row.config_revision_id = replacement.config_revision_id
        else:
            value = json.loads(row.rendered_context)
            control = value["context_sections"][0]
            if corruption == "embedded_id":
                control["content"]["config_revision_id"] = str(new_id())
            elif corruption == "embedded_hash":
                control["content"]["config_content_hash"] = "PRIVATE_BAD_HASH"
            elif corruption == "missing":
                value["context_sections"].pop(0)
            elif corruption == "duplicate":
                value["context_sections"].append(control)
            elif corruption == "category":
                control["category"] = "memory"
            else:
                control["content"] = "PRIVATE_BAD_CONTENT"
            row.request_json = value
            row.rendered_context = canonical_json(value)
            row.content_hash = content_hash(value)
    result = findings(db_session_factory, "context_snapshot")
    assert result and "PRIVATE_BAD" not in repr(result)


@pytest.mark.parametrize(
    "rendered_value,request_value", [(True, 1), (1, True), (1, 1.0)]
)
def test_snapshot_representations_compare_json_types_not_python_equality(
    db_session_factory, stages, rendered_value, request_value
):
    _, _, turn = stages("invoking")
    with db_session_factory.begin() as session:
        row = session.scalar(
            select(ContextSnapshot).where(ContextSnapshot.turn_id == turn.turn_id)
        )
        rendered = json.loads(row.rendered_context)
        rendered["context_sections"][-1]["content"]["observation"] = rendered_value
        request = json.loads(canonical_json(rendered))
        request["context_sections"][-1]["content"]["observation"] = request_value
        assert rendered == request  # Python erases this distinction during equality.
        row.rendered_context = canonical_json(rendered)
        row.content_hash = content_hash(rendered)
        row.request_json = request
    assert findings(db_session_factory, "context_snapshot")
