import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from cognition.protocols.observations_v1 import ObservationV1

GOLDEN = Path(__file__).parents[1] / "golden"


def example() -> dict:
    return json.loads((GOLDEN / "observation_v1.json").read_text(encoding="utf-8"))


def test_observation_round_trip_and_schema() -> None:
    observation = ObservationV1.model_validate(example())
    assert observation.model_dump(mode="json") == example()
    assert (
        ObservationV1.model_validate_json(observation.model_dump_json()) == observation
    )
    assert observation.model_json_schema() == json.loads(
        (GOLDEN / "observation_v1.schema.json").read_text(encoding="utf-8")
    )


@pytest.mark.parametrize("value", ["", None])
def test_dedup_key_required_nonempty(value: object) -> None:
    data = example()
    data["dedup_key"] = value
    with pytest.raises(ValidationError):
        ObservationV1.model_validate(data)
    del data["dedup_key"]
    with pytest.raises(ValidationError):
        ObservationV1.model_validate(data)


@pytest.mark.parametrize(
    "authentication",
    [
        [],
        {},
        {
            "mechanism": None,
            "authenticated_actor": None,
            "assertions": [],
            "trusted": True,
        },
    ],
)
def test_authentication_shape(authentication: object) -> None:
    data = example()
    data["authentication"] = authentication
    with pytest.raises(ValidationError):
        ObservationV1.model_validate(data)


@pytest.mark.parametrize(
    "field,value",
    [("trusted", True), ("schema_version", 2), ("connector_binding_id", "bad")],
)
def test_observation_rejects_drift(field: str, value: object) -> None:
    data = example()
    data[field] = value
    with pytest.raises(ValidationError):
        ObservationV1.model_validate(data)
