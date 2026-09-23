"""Intentional versioned schema snapshots guard every public contract."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from cognition.protocols.actions_v1 import ActionV1
from cognition.protocols.capabilities_v1 import (
    CapabilityBindingDescriptorV1,
    CapabilityDefinitionV1,
    CapabilityGrantV1,
)
from cognition.protocols.cognition_v1 import CognitionDecisionV1
from cognition.protocols.cognition_v2 import CognitionDecisionV2
from cognition.protocols.events_v1 import EventEnvelopeV1
from cognition.protocols.model_v1 import ModelRequestV1, ModelResultV1
from cognition.protocols.model_v2 import ModelRequestV2, ModelResultV2
from cognition.protocols.observations_v1 import ObservationV1
from cognition.protocols.portability_v1 import PortableManifestV1
from cognition.protocols.wakes_v1 import WakeV1

GOLDEN = Path(__file__).parents[1] / "golden"
CONTRACTS = [
    (EventEnvelopeV1, "event_v1"),
    (ObservationV1, "observation_v1"),
    (WakeV1, "wake_v1"),
    (CognitionDecisionV1, "cognition_decision_v1"),
    (ModelRequestV1, "model_request_v1"),
    (ModelResultV1, "model_result_v1"),
    (CognitionDecisionV2, "cognition_decision_v2"),
    (ModelRequestV2, "model_request_v2"),
    (ModelResultV2, "model_result_v2"),
    (CapabilityDefinitionV1, "capability_definition_v1"),
    (CapabilityGrantV1, "capability_grant_v1"),
    (CapabilityBindingDescriptorV1, "capability_binding_v1"),
    (ActionV1, "action_v1"),
    (PortableManifestV1, "portable_manifest_v1"),
]


@pytest.mark.parametrize("model,name", CONTRACTS)
def test_versioned_golden_contract(model, name):
    data = json.loads((GOLDEN / f"{name}.json").read_text(encoding="utf-8"))
    parsed = model.model_validate(data)
    assert parsed.model_dump(mode="json") == data
    assert model.model_validate_json(parsed.model_dump_json()) == parsed
    schema = model.model_json_schema()
    assert schema == json.loads(
        (GOLDEN / f"{name}.schema.json").read_text(encoding="utf-8")
    )
    version = "format_version" if model is PortableManifestV1 else "schema_version"
    expected_version = 2 if name.endswith("_v2") else 1
    assert version in schema["required"]
    assert schema["properties"][version]["const"] == expected_version
    for invalid in (
        None,
        3 - expected_version,
        True,
        float(expected_version),
        str(expected_version),
    ):
        with pytest.raises(ValidationError):
            model.model_validate({**data, version: invalid})
    with pytest.raises(ValidationError):
        model.model_validate({**data, "unexpected": "contract drift"})


def test_public_versioned_models_are_not_missing_from_registry():
    # Future public contracts must join this explicit compatibility registry.
    import importlib
    import inspect
    import pkgutil

    import cognition.protocols
    from cognition.protocols.common import ProtocolModel

    found = set()
    for module_info in pkgutil.iter_modules(cognition.protocols.__path__):
        module = importlib.import_module(f"cognition.protocols.{module_info.name}")
        for _, model in inspect.getmembers(module, inspect.isclass):
            if issubclass(model, ProtocolModel) and (
                "schema_version" in model.model_fields
                or "format_version" in model.model_fields
            ):
                found.add(model)
    assert found == {model for model, _ in CONTRACTS}
