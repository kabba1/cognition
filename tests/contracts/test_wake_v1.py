import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from cognition.protocols.wakes_v1 import WakeV1

GOLDEN = Path(__file__).parents[1] / "golden"


def example() -> dict:
    return json.loads((GOLDEN / "wake_v1.json").read_text(encoding="utf-8"))


def test_bootstrap_round_trip_and_schema() -> None:
    wake = WakeV1.model_validate(example())
    assert wake.kind == "bootstrap"
    assert wake.model_dump(mode="json") == example()
    assert WakeV1.model_validate_json(wake.model_dump_json()) == wake
    assert wake.model_json_schema() == json.loads(
        (GOLDEN / "wake_v1.schema.json").read_text(encoding="utf-8")
    )


@pytest.mark.parametrize(
    "kind",
    [
        "bootstrap",
        "external_event",
        "action_result",
        "commitment_due",
        "self_scheduled",
        "goal_review",
        "routine",
        "reflection",
        "maintenance",
        "heartbeat",
        "recovery",
    ],
)
def test_wake_kinds(kind: str) -> None:
    data = example()
    data["kind"] = kind
    assert WakeV1.model_validate(data).kind == kind


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "cognition"),
        ("purpose", ""),
        ("schema_version", 2),
        ("trusted", True),
        ("due_at", "2026-09-22T12:00:00"),
        ("wake_id", "bad"),
    ],
)
def test_invalid_wake(field: str, value: object) -> None:
    data = example()
    data[field] = value
    with pytest.raises(ValidationError):
        WakeV1.model_validate(data)
