"""Model scripts preserve exact requests and expose alternative retry outcomes."""

import json
from pathlib import Path
from uuid import UUID

import pytest

from cognition.models.base import ExecutiveModel
from cognition.protocols.model_v1 import ModelRequestV1, ModelResultV1
from cognition.testing.scripted_model import (
    ModelScriptExhausted,
    ModelScriptStep,
    ScriptedModelAdapter,
    ScriptedProviderError,
)

GOLDEN = Path(__file__).parents[2] / "golden"


def request():
    return ModelRequestV1.model_validate_json(
        (GOLDEN / "model_request_v1.json").read_text("utf-8")
    )


def result():
    return ModelResultV1.model_validate_json(
        (GOLDEN / "model_result_v1.json").read_text("utf-8")
    )


def test_scripted_model_implements_executive_interface_and_d1_d2_are_observable():
    first = result()
    second = result()
    second.decision.decision_id = UUID("99999999-9999-4999-8999-999999999999")
    second.decision.disposition = "continue"
    second.decision.rationale_summary = "A second model call made a different choice."
    model: ExecutiveModel = ScriptedModelAdapter([first, second])
    d1 = model.decide(request()).decision
    d2 = model.decide(request()).decision
    assert d1 == first.decision
    assert d2 == second.decision
    assert d1 != d2
    assert d1.disposition == "sleep"
    assert d1.action_requests == []


def test_records_complete_defensive_request_snapshots_and_independent_results():
    original_request = request()
    original_result = result()
    expected = original_request.model_dump(mode="json")
    adapter = ScriptedModelAdapter([original_result, original_result])
    original_result.decision.rationale_summary = "mutated after setup"
    received = adapter.decide(original_request)
    original_request.context_sections[0].content["summary"] = "mutated by caller"
    received.decision.rationale_summary = "mutated after delivery"
    recorded = adapter.requests
    assert recorded[0].model_dump(mode="json") == expected
    recorded[0].capabilities[0].allowed_operations.append("write")
    assert adapter.requests[0].model_dump(mode="json") == expected
    assert adapter.decide(request()).decision.rationale_summary is None


@pytest.mark.parametrize(
    ("status", "retryable"),
    [
        ("refused", False),
        ("failed", True),
        ("failed", False),
    ],
)
def test_refusal_retryable_and_permanent_result_failures(status, retryable):
    payload = json.loads((GOLDEN / "model_result_v1.json").read_text("utf-8"))
    payload.update(
        {
            "status": status,
            "decision": None,
            "error": {
                "code": status,
                "message": "scripted failure",
                "retryable": retryable,
            },
        }
    )
    adapter = ScriptedModelAdapter([ModelResultV1.model_validate(payload)])
    response = adapter.decide(request())
    assert response.status == status
    assert response.decision is None
    assert response.error.retryable is retryable


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("model timeout"),
        ScriptedProviderError("retryable", retryable=True),
        ScriptedProviderError("permanent", retryable=False),
    ],
)
def test_raised_provider_failures_are_recorded_and_next_attempt_can_succeed(error):
    adapter = ScriptedModelAdapter([error, result()])
    with pytest.raises(type(error)) as captured:
        adapter.decide(request())
    if isinstance(error, ScriptedProviderError):
        assert captured.value.retryable is error.retryable
    assert adapter.decide(request()).status == "completed"
    assert len(adapter.requests) == 2


def test_exhaustion_is_explicit_and_request_still_recorded():
    adapter = ScriptedModelAdapter([])
    with pytest.raises(ModelScriptExhausted, match="exhausted"):
        adapter.decide(request())
    assert adapter.requests == (request(),)


def test_predicate_mismatch_does_not_consume_script_step():
    adapter = ScriptedModelAdapter(
        [
            ModelScriptStep(
                result(), matches=lambda req: req.input_token_budget == 4000
            ),
        ]
    )
    wrong = request()
    wrong.input_token_budget = 1
    with pytest.raises(AssertionError, match="predicate"):
        adapter.decide(wrong)
    assert adapter.decide(request()).status == "completed"
    assert len(adapter.requests) == 2


def test_callable_and_predicate_cannot_mutate_recorded_request_or_each_other():
    def matches(req):
        req.context_sections[0].content["summary"] = "predicate mutation"
        return True

    def respond(req):
        assert req.context_sections[0].content["summary"] == "No pending obligations."
        req.context_sections.clear()
        return result()

    adapter = ScriptedModelAdapter([ModelScriptStep(respond, matches=matches)])
    adapter.decide(request())
    assert adapter.requests == (request(),)


def test_result_for_different_request_is_rejected():
    wrong = result()
    wrong.request_id = UUID("99999999-9999-4999-8999-999999999999")
    adapter = ScriptedModelAdapter([wrong])
    with pytest.raises(ValueError, match="request_id"):
        adapter.decide(request())
