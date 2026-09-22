"""Action execution uncertainty remains independent from verification."""

import json
from itertools import product
from pathlib import Path

import pytest
from pydantic import ValidationError

from cognition.protocols.actions_v1 import ActionV1
from cognition.protocols.common import new_id

GOLDEN = Path(__file__).parents[1] / "golden"
EXECUTION = [
    "proposed",
    "awaiting_approval",
    "authorized",
    "dispatching",
    "completed",
    "failed",
    "unknown",
    "cancelled",
]
VERIFICATION = ["not_applicable", "unverified", "verified", "contradicted"]


def example():
    return json.loads((GOLDEN / "action_v1.json").read_text(encoding="utf-8"))


def test_golden_action_and_schema():
    payload = example()
    parsed = ActionV1.model_validate(payload)
    assert parsed.model_dump(mode="json") == payload
    assert ActionV1.model_validate_json(parsed.model_dump_json()) == parsed
    expected = json.loads((GOLDEN / "action_v1.schema.json").read_text("utf-8"))
    assert ActionV1.model_json_schema() == expected


@pytest.mark.parametrize(
    ("execution", "verification"), list(product(EXECUTION, VERIFICATION))
)
def test_execution_and_verification_are_separate_contract_dimensions(
    execution, verification
):
    parsed = ActionV1.model_validate(
        {
            **example(),
            "execution_status": execution,
            "verification_status": verification,
        }
    )
    assert parsed.execution_status == execution
    assert parsed.verification_status == verification


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("execution_status", "verified"),
        ("verification_status", "completed"),
        ("execution_status", "retry_immediately"),
        ("effect_class", "model_decides"),
        ("normalized_input", []),
        ("verification_criteria", []),
        ("capability_grant_id", None),
        ("capability_binding_id", None),
        ("operation", ""),
        ("parameters_hash", ""),
    ],
)
def test_action_rejects_invalid_values(field, value):
    with pytest.raises(ValidationError):
        ActionV1.model_validate({**example(), field: value})


def test_action_explicit_nulls_and_application_owned_id():
    action_id = new_id()
    parsed = ActionV1.model_validate(
        {
            **example(),
            "action_id": action_id,
            "parent_action_id": None,
            "idempotency_key": None,
        }
    )
    assert parsed.action_id == action_id
    assert parsed.parent_action_id is None
    assert parsed.idempotency_key is None


def test_every_action_field_required_and_no_extras():
    payload = example()
    for field in payload:
        with pytest.raises(ValidationError):
            ActionV1.model_validate({k: v for k, v in payload.items() if k != field})
    with pytest.raises(ValidationError):
        ActionV1.model_validate({**payload, "risk_override": "A"})


@pytest.mark.parametrize("version", [0, 2, True, 1.0, "1"])
def test_action_schema_version_is_integer_one(version):
    with pytest.raises(ValidationError):
        ActionV1.model_validate({**example(), "schema_version": version})


def test_impetus_refs_have_closed_shape():
    payload = example()
    payload["impetus_refs"][0]["authority"] = "unrestricted"
    with pytest.raises(ValidationError):
        ActionV1.model_validate(payload)
