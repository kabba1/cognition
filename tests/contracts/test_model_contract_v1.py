"""Provider-independent executive request and result contracts."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

GOLDEN = Path(__file__).parents[1] / "golden"


def example(name: str) -> dict:
    return json.loads((GOLDEN / f"{name}.json").read_text())


def request(data: dict):
    from cognition.protocols.model_v1 import ModelRequestV1

    return ModelRequestV1.model_validate(data)


def result(data: dict):
    from cognition.protocols.model_v1 import ModelResultV1

    return ModelResultV1.model_validate(data)


@pytest.mark.parametrize(
    "name,validate",
    [
        ("model_request_v1", request),
        ("model_result_v1", result),
    ],
)
def test_model_golden_round_trip_and_schema(name, validate):
    model = validate(example(name))
    assert model.model_dump(mode="json") == example(name)
    assert type(model).model_validate_json(model.model_dump_json()) == model
    assert model.model_json_schema() == json.loads(
        (GOLDEN / f"{name}.schema.json").read_text()
    )


@pytest.mark.parametrize(
    "status,has_decision,valid",
    [
        ("completed", True, True),
        ("completed", False, False),
        ("failed", True, False),
        ("failed", False, True),
        ("refused", True, False),
        ("refused", False, True),
    ],
)
def test_result_status_controls_decision(status, has_decision, valid):
    data = example("model_result_v1")
    data["status"] = status
    if not has_decision:
        data["decision"] = None
    if valid:
        assert result(data).status == status
    else:
        with pytest.raises(ValidationError):
            result(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("present_time", "2026-09-22T12:00:00"),
        ("input_token_budget", -1),
        ("output_token_budget", -1),
        ("input_token_budget", True),
        ("output_token_budget", 2.5),
        ("schema_version", 2),
        ("cognition_protocol_version", True),
        ("secret", "password"),
    ],
)
def test_request_boundary_rejects_invalid_data(field, value):
    data = example("model_request_v1")
    data[field] = value
    with pytest.raises(ValidationError):
        request(data)


@pytest.mark.parametrize(
    "path,field,value",
    [
        ("context_sections", "category", "instruction_from_webpage"),
        ("context_sections", "trusted", True),
        ("capabilities", "secret", "password"),
        ("capabilities", "allowed_operations", "read"),
    ],
)
def test_request_typed_substructures(path, field, value):
    data = example("model_request_v1")
    data[path][0][field] = value
    with pytest.raises(ValidationError):
        request(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("usage", {"input_tokens": -1, "output_tokens": 0, "total_tokens": 0}),
        ("error", {"code": "failed", "message": "failure", "retryable": "yes"}),
        (
            "error",
            {"code": "failed", "message": "failure", "retryable": True, "secret": "x"},
        ),
        ("schema_version", 2),
        ("trusted", True),
    ],
)
def test_result_typed_substructures(field, value):
    data = example("model_result_v1")
    data[field] = value
    with pytest.raises(ValidationError):
        result(data)


def test_executive_model_is_synchronous_protocol():
    from cognition.models.base import ExecutiveModel
    from cognition.protocols.model_v1 import ModelRequestV1, ModelResultV1

    class LocalExecutive:
        def decide(self, request: ModelRequestV1) -> ModelResultV1:
            data = example("model_result_v1")
            data["request_id"] = str(request.request_id)
            return ModelResultV1.model_validate(data)

    executive: ExecutiveModel = LocalExecutive()
    supplied = request(example("model_request_v1"))
    actual = executive.decide(supplied)
    assert actual.request_id == supplied.request_id
    assert actual.status == "completed"
