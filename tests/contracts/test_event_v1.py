"""Evidence envelopes preserve provenance without granting trust."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from cognition.protocols.events_v1 import EventEnvelopeV1

GOLDEN = Path(__file__).parents[1] / "golden"


def example() -> dict:
    return json.loads((GOLDEN / "event_v1.json").read_text(encoding="utf-8"))


def test_event_round_trip_and_schema() -> None:
    event = EventEnvelopeV1.model_validate(example())
    assert event.model_dump(mode="json") == example()
    assert EventEnvelopeV1.model_validate_json(event.model_dump_json()) == event
    assert event.model_json_schema() == json.loads(
        (GOLDEN / "event_v1.schema.json").read_text(encoding="utf-8")
    )


@pytest.mark.parametrize(
    "kind", ["runtime", "model", "connector", "capability", "admin", "import"]
)
def test_source_kinds(kind: str) -> None:
    data = example()
    data["source"]["kind"] = kind
    assert EventEnvelopeV1.model_validate(data).source.kind == kind


@pytest.mark.parametrize("field", ["occurred_at", "observed_at", "recorded_at"])
def test_event_timestamp_boundaries(field: str) -> None:
    data = example()
    data[field] = "2026-09-22T12:00:00+02:00"
    event = EventEnvelopeV1.model_validate(data)
    assert getattr(event, field) == datetime(2026, 9, 22, 10, tzinfo=UTC)
    data[field] = "2026-09-22T12:00:00"
    with pytest.raises(ValidationError):
        EventEnvelopeV1.model_validate(data)


@pytest.mark.parametrize("value", [2, 0, True, 1.0, "1"])
def test_version_rejected(value: object) -> None:
    data = example()
    data["schema_version"] = value
    with pytest.raises(ValidationError):
        EventEnvelopeV1.model_validate(data)


def test_invalid_shapes_and_secret_class_rejected() -> None:
    mutations = [
        ((), "trusted", True),
        (("source",), "kind", "webpage"),
        (("source",), "trusted", True),
        (("content",), "sensitivity", "secret"),
        (("content",), "secret", "credential"),
        (("content",), "payload", [1, 2]),
        (("content",), "retain_until", "2026-09-22T00:00:00"),
        ((), "event_id", "bad-uuid"),
        ((), "event_type", ""),
        ((), "provenance", []),
        ((), "subject", {"kind": "", "id": "12345678-1234-4234-9234-123456789abc"}),
    ]
    for path, field, value in mutations:
        data = deepcopy(example())
        target = data
        for part in path:
            target = target[part]
        target[field] = value
        with pytest.raises(ValidationError):
            EventEnvelopeV1.model_validate(data)


@pytest.mark.parametrize("field", list(example()))
def test_all_envelope_fields_are_explicit(field: str) -> None:
    data = example()
    del data[field]
    with pytest.raises(ValidationError):
        EventEnvelopeV1.model_validate(data)
