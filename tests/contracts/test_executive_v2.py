"""Explicit executive versions preserve old contracts and reject mixed tuples."""

import importlib
import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

GOLDEN = Path(__file__).parents[1] / "golden"
NEW_FAMILIES = (
    "entity_operations",
    "project_operations",
    "relationship_operations",
    "relationship_thread_operations",
)


def example(name):
    return json.loads((GOLDEN / f"{name}.json").read_text(encoding="utf-8"))


def contracts():
    return importlib.import_module("cognition.protocols.executive")


def decision_data():
    return {
        **example("cognition_decision_v1"),
        "schema_version": 2,
        **{family: [] for family in NEW_FAMILIES},
    }


@pytest.mark.parametrize("value", [None, True, False, 1, 2.0, "2", 3])
def test_version_two_requires_exact_integer_two(value):
    common = importlib.import_module("cognition.protocols.common")
    with pytest.raises(ValidationError):
        TypeAdapter(common.VersionTwo).validate_python(value)


def test_version_two_accepts_integer_two():
    common = importlib.import_module("cognition.protocols.common")
    assert TypeAdapter(common.VersionTwo).validate_python(2) == 2


@pytest.mark.parametrize("family", NEW_FAMILIES)
def test_new_operation_arrays_are_explicit_and_v1_stays_closed(family):
    module = contracts()
    value = decision_data()
    assert module.parse_decision(value).schema_version == 2
    del value[family]
    with pytest.raises(ValidationError):
        module.parse_decision(value)
    value = example("cognition_decision_v1")
    value[family] = []
    with pytest.raises(ValidationError):
        module.parse_decision(value)


@pytest.mark.parametrize("name", ["decision", "request", "result"])
@pytest.mark.parametrize("value", [None, True, False, 1.0, 2.0, "1", "2", 0, 3])
def test_dispatch_rejects_coerced_or_unknown_version(name, value):
    module = contracts()
    fixture = "cognition_decision" if name == "decision" else f"model_{name}"
    payload = example(f"{fixture}_v1")
    payload["schema_version"] = value
    with pytest.raises(module.IncompatibleExecutiveContract):
        getattr(module, f"parse_{name}")(payload)


@pytest.mark.parametrize("contract", ["2.0", "3.0", "3.1"])
def test_supported_frozen_v1_requests_keep_their_contract(contract):
    module = contracts()
    value = example("model_request_v1")
    value["runtime_contract_version"] = contract
    parsed = module.parse_request(value)
    module.validate_request_contract(
        parsed, config_schema_version=1, configured_protocol=1
    )
    assert parsed.model_dump(mode="json") == value


def request_data():
    return {
        **example("model_request_v1"),
        "schema_version": 2,
        "cognition_protocol_version": 2,
        "runtime_contract_version": "3.2",
        "output_schema": "CognitionDecisionV2",
    }


def test_supported_v2_request_tuple():
    module = contracts()
    request = module.parse_request(request_data())
    module.validate_request_contract(
        request, config_schema_version=2, configured_protocol=2
    )
    assert request.model_dump(mode="json") == request_data()


@pytest.mark.parametrize(
    "field,value",
    [
        ("runtime_contract_version", "3.1"),
        ("runtime_contract_version", "unknown"),
        ("output_schema", "CognitionDecisionV1"),
    ],
)
def test_structurally_valid_request_can_still_have_incompatible_tuple(field, value):
    module = contracts()
    payload = request_data()
    payload[field] = value
    request = module.parse_request(payload)
    with pytest.raises(module.IncompatibleExecutiveContract):
        module.validate_request_contract(request)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"config_schema_version": 1},
        {"configured_protocol": 1},
        {"config_schema_version": 2.0},
        {"configured_protocol": "2"},
        {"configured_protocol": True},
    ],
)
def test_configuration_selection_must_match_request_exactly(kwargs):
    module = contracts()
    with pytest.raises(module.IncompatibleExecutiveContract):
        module.validate_request_contract(module.parse_request(request_data()), **kwargs)


def test_v1_request_cannot_claim_v2_runtime_contract():
    module = contracts()
    value = example("model_request_v1")
    value["runtime_contract_version"] = "3.2"
    with pytest.raises(module.IncompatibleExecutiveContract):
        module.validate_request_contract(module.parse_request(value))


def test_mutable_typed_request_is_revalidated():
    module = contracts()
    request = module.parse_request(request_data())
    request.cognition_protocol_version = True
    with pytest.raises(module.IncompatibleExecutiveContract):
        module.validate_request_contract(request)


@pytest.mark.parametrize("outer,nested", [(1, 2), (2, 1)])
def test_result_never_accepts_a_decision_of_the_other_version(outer, nested):
    module = contracts()
    value = example("model_result_v1")
    value["schema_version"] = outer
    value["decision"] = (
        example("cognition_decision_v1") if nested == 1 else decision_data()
    )
    with pytest.raises(ValidationError):
        module.parse_result(value)


@pytest.mark.parametrize("family", NEW_FAMILIES)
@pytest.mark.parametrize("fault", ["missing_identity", "extra_authority", "bad_op"])
def test_new_operation_boundaries_reject_invalid_shape(family, fault):
    value = example("cognition_decision_v2")
    operation = value[family][0]
    if fault == "missing_identity":
        del operation["operation_id"]
    elif fault == "extra_authority":
        operation["grant_admin"] = True
    else:
        operation["op"] = "merge_and_authorize"
    with pytest.raises(ValidationError):
        contracts().parse_decision(value)


@pytest.mark.parametrize(
    "family,field",
    [
        ("entity_operations", "kind"),
        ("entity_operations", "display_name"),
        ("project_operations", "title"),
        ("project_operations", "desired_state"),
        ("relationship_operations", "entity_id"),
        ("relationship_operations", "narrative"),
        ("relationship_thread_operations", "relationship_id"),
        ("relationship_thread_operations", "title"),
        ("relationship_thread_operations", "summary"),
    ],
)
def test_new_creation_requires_content_and_parent_fields(family, field):
    value = example("cognition_decision_v2")
    value[family][0][field] = None
    with pytest.raises(ValidationError):
        contracts().parse_decision(value)


@pytest.mark.parametrize("family", NEW_FAMILIES)
def test_revision_requires_existing_target_identity(family):
    value = example("cognition_decision_v2")
    value[family][0]["op"] = "revise"
    with pytest.raises(ValidationError):
        contracts().parse_decision(value)


@pytest.mark.parametrize(
    "family,target",
    [
        ("project_operations", "project_id"),
        ("relationship_thread_operations", "thread_id"),
    ],
)
@pytest.mark.parametrize("status", [None, "authoritative"])
def test_status_operations_require_a_known_requested_status(family, target, status):
    value = example("cognition_decision_v2")
    operation = value[family][0]
    operation.update(op="set_status", requested_status=status)
    operation[target] = operation["operation_id"]
    with pytest.raises(ValidationError):
        contracts().parse_decision(value)


@pytest.mark.parametrize(
    "status,has_decision,valid",
    [
        ("completed", True, True),
        ("completed", False, False),
        ("refused", True, False),
        ("refused", False, True),
        ("failed", True, False),
        ("failed", False, True),
    ],
)
def test_v2_result_status_controls_decision(status, has_decision, valid):
    value = example("model_result_v2")
    value["status"] = status
    if not has_decision:
        value["decision"] = None
    if valid:
        assert contracts().parse_result(value).status == status
    else:
        with pytest.raises(ValidationError):
            contracts().parse_result(value)


@pytest.mark.parametrize("family", NEW_FAMILIES)
def test_all_new_operation_fields_remain_explicit(family):
    example_data = example("cognition_decision_v2")
    for field in example_data[family][0]:
        value = example("cognition_decision_v2")
        del value[family][0][field]
        with pytest.raises(ValidationError):
            contracts().parse_decision(value)


@pytest.mark.parametrize("name", ["decision", "request", "result"])
@pytest.mark.parametrize("version", [1, 2])
def test_dispatch_does_not_modify_or_retain_mutable_input(name, version):
    module = contracts()
    prefix = "cognition_decision" if name == "decision" else f"model_{name}"
    value = example(f"{prefix}_v{version}")
    serialized = json.dumps(value)
    parser = getattr(module, f"parse_{name}")
    parsed = parser(value)
    assert json.dumps(value) == serialized
    detached = parser(parsed)
    assert detached == parsed and detached is not parsed
