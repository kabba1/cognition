"""Capability contracts separate software, authority, and deployment bindings."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from cognition.protocols.capabilities_v1 import (
    CapabilityBindingDescriptorV1,
    CapabilityDefinitionV1,
    CapabilityGrantV1,
)

GOLDEN = Path(__file__).parents[1] / "golden"
MODELS = [
    (CapabilityDefinitionV1, "capability_definition_v1"),
    (CapabilityGrantV1, "capability_grant_v1"),
    (CapabilityBindingDescriptorV1, "capability_binding_v1"),
]


def example(name):
    return json.loads((GOLDEN / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(("model", "name"), MODELS)
def test_golden_example_round_trip_and_schema(model, name):
    payload = example(name)
    parsed = model.model_validate(payload)
    assert parsed.model_dump(mode="json") == payload
    assert model.model_validate_json(parsed.model_dump_json()) == parsed
    expected = json.loads((GOLDEN / f"{name}.schema.json").read_text("utf-8"))
    assert model.model_json_schema() == expected


@pytest.mark.parametrize(("model", "name"), MODELS)
def test_all_top_level_fields_required_and_no_extras(model, name):
    payload = example(name)
    for field in payload:
        incomplete = {key: value for key, value in payload.items() if key != field}
        with pytest.raises(ValidationError):
            model.model_validate(incomplete)
    with pytest.raises(ValidationError):
        model.model_validate({**payload, "extra": "unrecognized"})


@pytest.mark.parametrize(("model", "name"), MODELS)
@pytest.mark.parametrize("version", [0, 2, True, 1.0, "1"])
def test_schema_version_is_integer_one(model, name, version):
    with pytest.raises(ValidationError):
        model.model_validate({**example(name), "schema_version": version})


@pytest.mark.parametrize(
    ("field", "value"),
    [("effect_class", value) for value in ["A", "B", "C", "D"]]
    + [
        ("retry_semantics", value)
        for value in [
            "native_idempotency",
            "reconcilable",
            "safe_repeat",
            "unsafe_repeat",
        ]
    ]
    + [
        ("verification_semantics", value)
        for value in [
            "not_applicable",
            "provider_receipt",
            "independently_queryable",
            "custom",
        ]
    ]
    + [
        ("cancellation_semantics", value)
        for value in [
            "pre_dispatch_only",
            "provider_supported",
            "compensating_action",
            "unavailable",
        ]
    ],
)
def test_supported_operation_classifications(field, value):
    payload = example("capability_definition_v1")
    payload["operations"][0][field] = value
    parsed = CapabilityDefinitionV1.model_validate(payload)
    assert getattr(parsed.operations[0], field) == value


@pytest.mark.parametrize(
    "field",
    [
        "effect_class",
        "retry_semantics",
        "verification_semantics",
        "cancellation_semantics",
    ],
)
def test_operation_classifications_reject_unknown_values(field):
    payload = example("capability_definition_v1")
    payload["operations"][0][field] = "model_decides"
    with pytest.raises(ValidationError):
        CapabilityDefinitionV1.model_validate(payload)


def test_effect_class_belongs_to_operation_and_operation_is_closed():
    payload = example("capability_definition_v1")
    payload["effect_class"] = "A"
    with pytest.raises(ValidationError):
        CapabilityDefinitionV1.model_validate(payload)
    del payload["effect_class"]
    payload["operations"][0]["risk_override"] = "A"
    with pytest.raises(ValidationError):
        CapabilityDefinitionV1.model_validate(payload)


@pytest.mark.parametrize(
    "field", ["input_schema", "output_schema", "information_scope", "resource_scope"]
)
def test_operation_schemas_and_scopes_are_json_objects(field):
    payload = example("capability_definition_v1")
    payload["operations"][0][field] = []
    with pytest.raises(ValidationError):
        CapabilityDefinitionV1.model_validate(payload)
    payload["operations"][0][field] = {"value": object()}
    with pytest.raises(ValidationError):
        CapabilityDefinitionV1.model_validate(payload)


@pytest.mark.parametrize(
    "mode", ["autonomous", "constrained", "per_action", "disabled"]
)
def test_grant_approval_modes(mode):
    parsed = CapabilityGrantV1.model_validate(
        {**example("capability_grant_v1"), "approval_mode": mode}
    )
    assert parsed.approval_mode == mode


def test_grant_only_names_allowed_operations_and_constraints():
    payload = example("capability_grant_v1")
    payload["allowed_operations"] = [{"name": "send", "effect_class": "A"}]
    with pytest.raises(ValidationError):
        CapabilityGrantV1.model_validate(payload)
    payload = example("capability_grant_v1")
    payload["constraints"]["additional_operations"] = ["delete_account"]
    with pytest.raises(ValidationError):
        CapabilityGrantV1.model_validate(payload)
    for section in example("capability_grant_v1")["constraints"]:
        payload = example("capability_grant_v1")
        del payload["constraints"][section]
        with pytest.raises(ValidationError):
            CapabilityGrantV1.model_validate(payload)


def test_unknown_approval_mode_rejected():
    with pytest.raises(ValidationError):
        CapabilityGrantV1.model_validate(
            {**example("capability_grant_v1"), "approval_mode": "unrestricted"}
        )


@pytest.mark.parametrize("field", ["valid_from", "valid_until"])
def test_grant_rejects_naive_dates_and_normalizes_aware_dates(field):
    payload = example("capability_grant_v1")
    payload[field] = "2026-09-22T09:00:00"
    with pytest.raises(ValidationError):
        CapabilityGrantV1.model_validate(payload)
    payload[field] = "2026-09-22T09:00:00-05:00"
    parsed = CapabilityGrantV1.model_validate(payload)
    assert parsed.model_dump(mode="json")[field] == "2026-09-22T14:00:00Z"


def test_grant_may_have_no_expiry():
    parsed = CapabilityGrantV1.model_validate(
        {**example("capability_grant_v1"), "valid_until": None}
    )
    assert parsed.valid_until is None


@pytest.mark.parametrize("status", ["available", "degraded", "unavailable", "revoked"])
def test_binding_statuses(status):
    parsed = CapabilityBindingDescriptorV1.model_validate(
        {**example("capability_binding_v1"), "status": status}
    )
    assert parsed.status == status


@pytest.mark.parametrize("field", ["secret", "secret_value", "api_key", "credentials"])
def test_binding_rejects_raw_credential_fields(field):
    with pytest.raises(ValidationError):
        CapabilityBindingDescriptorV1.model_validate(
            {**example("capability_binding_v1"), field: "must-not-enter-context"}
        )


def test_binding_can_describe_missing_environment():
    parsed = CapabilityBindingDescriptorV1.model_validate(
        {
            **example("capability_binding_v1"),
            "status": "unavailable",
            "secret_ref": None,
            "external_identity": None,
        }
    )
    assert parsed.secret_ref is None
    assert parsed.external_identity is None


def test_binding_status_is_closed():
    with pytest.raises(ValidationError):
        CapabilityBindingDescriptorV1.model_validate(
            {**example("capability_binding_v1"), "status": "authorized"}
        )


def test_operation_owns_network_and_credential_requirements():
    payload = example("capability_definition_v1")
    payload.pop("network_required", None)
    payload.pop("credential_requirements", None)
    payload["operations"][0]["network_required"] = True
    payload["operations"][0]["credential_requirements"] = ["account"]
    operation = CapabilityDefinitionV1.model_validate(payload).operations[0]
    assert operation.network_required is True
    assert operation.credential_requirements == ["account"]
