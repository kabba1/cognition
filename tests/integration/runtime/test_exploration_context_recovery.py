"""Rehashed frozen contexts cannot remove or widen an exploration allowance."""

from copy import deepcopy

import pytest
from sqlalchemy import func, select
from test_cognition import NOW, response
from test_cognition import born as born
from test_cognition import owner as owner
from test_frozen_configuration import replace_request

from cognition.db.models.cognition import (
    CognitionTurn,
    ContextSnapshot,
    ModelInvocation,
)
from cognition.policy.governance import AuthenticatedPrincipal
from cognition.protocols.executive import IncompatibleExecutiveContract, parse_request
from cognition.runtime.cognition import CognitionRuntime
from cognition.runtime.exploration_admin import set_internal_exploration
from cognition.stores.cognition import save_context
from cognition.testing.clock import FakeClock
from cognition.testing.scripted_model import ScriptedModelAdapter


def run(owner, born, model=None):
    return CognitionRuntime(owner, born.individual_id, model, FakeClock(NOW)).run_once()


def freeze_exploration(owner, born, factory):
    set_internal_exploration(
        factory,
        born.individual_id,
        AuthenticatedPrincipal("local_os", "test-admin"),
        True,
        "Acceptance allowance",
        FakeClock(NOW),
    )
    assert run(owner, born, ScriptedModelAdapter([response])).status == "completed"
    frozen = run(owner, born)
    assert (frozen.status, frozen.reason) == ("blocked", "model_unavailable")
    return frozen.cycle_id


def snapshot_for(session, cycle_id):
    return session.scalar(
        select(ContextSnapshot)
        .join(CognitionTurn)
        .where(CognitionTurn.cycle_id == cycle_id)
    )


@pytest.mark.parametrize(
    "fault", ["missing", "duplicate", "category", "refs", "boolean", "cap", "scope"]
)
def test_rehashed_control_tampering_blocks_before_any_new_attempt(
    owner, born, db_session_factory, fault
):
    cycle_id = freeze_exploration(owner, born, db_session_factory)
    with db_session_factory.begin() as session:
        snapshot = snapshot_for(session, cycle_id)
        value = deepcopy(snapshot.request_json)
        sections = value["context_sections"]
        control = next(
            item for item in sections if item["name"] == "internal_exploration"
        )
        if fault == "missing":
            sections.remove(control)
        elif fault == "duplicate":
            sections.append(deepcopy(control))
        elif fault == "category":
            control["category"] = "evidence"
        elif fault == "refs":
            control["refs"] = []
        elif fault == "boolean":
            control["content"]["effective_limits"]["max_turns"] = True
        elif fault == "cap":
            control["content"]["effective_limits"]["max_turns"] = 3
        else:
            control["content"]["scope"] = "external"
        replace_request(snapshot, value)
    model = ScriptedModelAdapter([response])
    outcome = run(owner, born, model)
    assert (outcome.status, outcome.reason) == (
        "blocked",
        "incompatible_executive_contract",
    )
    assert model.requests == ()
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ModelInvocation)) == 1
        assert (
            session.scalar(
                select(CognitionTurn.status).where(CognitionTurn.cycle_id == cycle_id)
            )
            == "prepared"
        )


def test_ordinary_frozen_context_rejects_unexpected_exploration_control(
    owner, born, db_session_factory
):
    outcome = run(owner, born)
    with db_session_factory.begin() as session:
        snapshot = snapshot_for(session, outcome.cycle_id)
        value = deepcopy(snapshot.request_json)
        value["context_sections"].append(
            dict(name="internal_exploration", category="control", content={}, refs=[])
        )
        replace_request(snapshot, value)
    result = run(owner, born, ScriptedModelAdapter([response]))
    assert (result.status, result.reason) == (
        "blocked",
        "incompatible_executive_contract",
    )
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ModelInvocation)) == 0


def test_save_boundary_rejects_missing_mandatory_control(
    owner, born, db_session_factory
):
    cycle_id = freeze_exploration(owner, born, db_session_factory)
    with db_session_factory() as session:
        snapshot = snapshot_for(session, cycle_id)
        value = deepcopy(snapshot.request_json)
        value["context_sections"] = [
            s for s in value["context_sections"] if s["name"] != "internal_exploration"
        ]
        with pytest.raises(IncompatibleExecutiveContract):
            save_context(
                session,
                turn_id=snapshot.turn_id,
                config_revision_id=snapshot.config_revision_id,
                request=parse_request(value),
                rendered_context="unused",
                context_hash="0" * 64,
                selected_refs=(),
                retrieval_reasons={},
                estimated_input_tokens=1,
                adapter=snapshot.model_adapter,
                requested_model=snapshot.requested_model,
                now=NOW,
                retain_until=None,
            )
        assert not session.new


def test_decided_recovery_revalidates_control_without_resampling(
    owner, born, db_session_factory, monkeypatch
):
    import cognition.runtime.cognition as runtime_module

    class Interrupted(BaseException):
        pass

    def interrupt_application(*args, **kwargs):
        raise Interrupted()

    cycle_id = freeze_exploration(owner, born, db_session_factory)
    original = runtime_module.apply_decision
    monkeypatch.setattr(runtime_module, "apply_decision", interrupt_application)
    with pytest.raises(Interrupted):
        run(owner, born, ScriptedModelAdapter([response]))
    monkeypatch.setattr(runtime_module, "apply_decision", original)
    with db_session_factory.begin() as session:
        snapshot = snapshot_for(session, cycle_id)
        value = deepcopy(snapshot.request_json)
        value["context_sections"] = [
            section
            for section in value["context_sections"]
            if section["name"] != "internal_exploration"
        ]
        replace_request(snapshot, value)
    outcome = run(owner, born)
    assert (outcome.status, outcome.reason) == (
        "blocked",
        "incompatible_executive_contract",
    )
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ModelInvocation)) == 2
        assert (
            session.scalar(
                select(CognitionTurn.status).where(CognitionTurn.cycle_id == cycle_id)
            )
            == "decided"
        )


def test_selected_v2_executes_the_same_mandatory_exploration_scope(
    owner, born, db_session_factory
):
    from test_cognition import use_v2_config

    from cognition.protocols.executive import parse_result

    def v2_response(request):
        value = response(request).model_dump(mode="json")
        value["schema_version"] = 2
        value["decision"].update(
            schema_version=2,
            entity_operations=[],
            project_operations=[],
            relationship_operations=[],
            relationship_thread_operations=[],
        )
        return parse_result(value)

    use_v2_config(db_session_factory, born.individual_id)
    set_internal_exploration(
        db_session_factory,
        born.individual_id,
        AuthenticatedPrincipal("local_os", "test-admin"),
        True,
        "Version two allowance",
        FakeClock(NOW),
    )
    assert run(owner, born, ScriptedModelAdapter([v2_response])).status == "completed"
    model = ScriptedModelAdapter([v2_response])
    assert run(owner, born, model).status == "completed"
    request = model.requests[0]
    assert request.schema_version == request.cognition_protocol_version == 2
    control = next(
        s for s in request.context_sections if s.name == "internal_exploration"
    )
    assert control.category == "control"
    assert control.content["effective_limits"]["max_turns"] == 1
