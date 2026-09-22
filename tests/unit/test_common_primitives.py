"""Boundary tests for shared identity, reference, and UTC primitives."""

from datetime import UTC, datetime, timedelta, timezone, tzinfo
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

from cognition.protocols.common import (
    Clock,
    CognitionId,
    Ref,
    SystemClock,
    UTCDateTime,
    new_id,
    normalize_utc,
    parse_id,
)


class Timestamp(BaseModel):
    at: UTCDateTime


class NoOffset(tzinfo):
    def utcoffset(self, dt: datetime | None) -> None:
        return None


def test_new_id_is_uuid4() -> None:
    generated: CognitionId = new_id()
    assert isinstance(generated, UUID)
    assert generated.version == 4


@pytest.mark.parametrize(
    "text",
    [
        "12345678-1234-4234-9234-123456789abc",
        "12345678-1234-1234-9234-123456789abc",
    ],
)
def test_supplied_id_is_preserved(text: str) -> None:
    supplied = UUID(text)
    assert parse_id(supplied) is supplied
    assert parse_id(text) == supplied
    ref = Ref(kind="future-kind", id=text)
    assert ref.id == supplied
    assert Ref.model_validate_json(ref.model_dump_json()).id == supplied


@pytest.mark.parametrize("value", ["", "not-a-uuid", "12345678-1234"])
def test_malformed_ids_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        parse_id(value)
    with pytest.raises(ValidationError):
        Ref(kind="example", id=value)


def test_ref_json_round_trip() -> None:
    data = {"kind": "arbitrary-kind", "id": "12345678-1234-4234-9234-123456789abc"}
    ref = Ref.model_validate(data)
    assert ref.model_dump(mode="json") == data
    assert Ref.model_validate_json(ref.model_dump_json()) == ref


def test_ref_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Ref.model_validate({"kind": "example", "id": new_id(), "trusted": True})


@pytest.mark.parametrize("missing", ["kind", "id"])
def test_ref_requires_both_fields(missing: str) -> None:
    data: dict[str, object] = {"kind": "example", "id": new_id()}
    del data[missing]
    with pytest.raises(ValidationError, match="missing"):
        Ref.model_validate(data)


@pytest.mark.parametrize("offset", [0, -6, 5.5])
def test_aware_datetime_normalizes_preserving_instant(offset: float) -> None:
    supplied = datetime(2026, 9, 22, 12, 30, tzinfo=timezone(timedelta(hours=offset)))
    expected = datetime(2026, 9, 22, 12, 30, tzinfo=UTC) - timedelta(hours=offset)
    assert normalize_utc(supplied) == expected
    assert normalize_utc(supplied).tzinfo is UTC
    stamp = Timestamp(at=supplied)
    assert stamp.at == expected
    assert stamp.at.tzinfo is UTC
    reparsed = Timestamp.model_validate_json(stamp.model_dump_json())
    assert reparsed.at == expected
    assert reparsed.at.tzinfo is UTC


@pytest.mark.parametrize(
    "value",
    [datetime(2026, 9, 22), datetime(2026, 9, 22, tzinfo=NoOffset())],
)
def test_naive_datetime_is_rejected(value: datetime) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        normalize_utc(value)
    with pytest.raises(ValidationError):
        Timestamp(at=value)


@pytest.mark.parametrize("value", ["2026-09-22T12:30:00", "not-a-date"])
def test_invalid_datetime_json_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        Timestamp.model_validate({"at": value})


def test_datetime_json_offset_normalizes_to_utc() -> None:
    stamp = Timestamp.model_validate_json('{"at":"2026-09-22T12:30:00+05:30"}')
    assert stamp.at == datetime(2026, 9, 22, 7, tzinfo=UTC)
    assert stamp.at.tzinfo is UTC


def test_system_clock_returns_current_utc() -> None:
    clock: Clock = SystemClock()
    before = datetime.now(UTC)
    actual = clock.now()
    after = datetime.now(UTC)
    assert actual.tzinfo is UTC
    assert before <= actual <= after
