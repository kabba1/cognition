"""Finite fixture pages are restart-stable and honor actual normalized budgets."""

import json
from pathlib import Path

import pytest

from cognition.connectors.local_json import LocalJsonConnector
from cognition.domain.perception import InvalidPerception


def fixture(path: Path, items: list[dict]) -> None:
    path.write_text(
        json.dumps({"schema_version": 1, "stream_id": "inbox", "items": items}),
        encoding="utf-8",
    )


def test_constructor_does_not_read_file_and_errors_are_sanitized(
    tmp_path: Path,
) -> None:
    adapter = LocalJsonConnector(tmp_path / "private-missing.json", source_id="inbox")
    with pytest.raises(InvalidPerception) as error:
        adapter.poll(None)
    assert "private" not in str(error.value)


def test_pages_reopen_with_cursor_and_eof_is_stable(tmp_path: Path) -> None:
    path = tmp_path / "source.json"
    fixture(path, [{"external_id": str(i), "payload": {}} for i in range(35)])
    first = LocalJsonConnector(path, source_id="inbox").poll(None)
    assert [item.external_id for item in first.items] == [str(i) for i in range(32)]
    second = LocalJsonConnector(path, source_id="inbox").poll(first.next_cursor)
    assert [item.external_id for item in second.items] == ["32", "33", "34"]
    eof = LocalJsonConnector(path, source_id="inbox").poll(second.next_cursor)
    assert eof.items == ()
    assert eof.next_cursor == second.next_cursor


def test_byte_paging_preserves_all_ordered_items(tmp_path: Path) -> None:
    path = tmp_path / "source.json"
    fixture(
        path,
        [{"external_id": str(i), "payload": {"x": "y" * 15000}} for i in range(32)],
    )
    first = LocalJsonConnector(path, source_id="inbox").poll(None)
    assert 1 < len(first.items) < 32
    second = LocalJsonConnector(path, source_id="inbox").poll(first.next_cursor)
    assert [item.external_id for item in first.items + second.items] == [
        str(i) for i in range(32)
    ]


def test_empty_fixture_initializes_hash_and_changed_bytes_conflict(
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.json"
    fixture(path, [])
    first = LocalJsonConnector(path, source_id="inbox").poll(None)
    assert first.items == ()
    assert first.next_cursor is not None
    assert LocalJsonConnector(path, source_id="inbox").poll(first.next_cursor) == first
    path.write_text(path.read_text() + " ")
    with pytest.raises(InvalidPerception):
        LocalJsonConnector(path, source_id="inbox").poll(first.next_cursor)


@pytest.mark.parametrize("offset", [True, -1, 1, 0.0, "0", None])
def test_cursor_requires_in_range_exact_integer(tmp_path: Path, offset: object) -> None:
    path = tmp_path / "source.json"
    fixture(path, [])
    adapter = LocalJsonConnector(path, source_id="inbox")
    cursor = json.loads(adapter.poll(None).next_cursor)
    cursor["offset"] = offset
    with pytest.raises(InvalidPerception):
        adapter.poll(json.dumps(cursor))


@pytest.mark.parametrize(
    "raw",
    [
        '{"schema_version":1,"stream_id":"inbox","items":[],"items":[]}',
        '{"schema_version":true,"stream_id":"inbox","items":[]}',
        '{"schema_version":1,"stream_id":"other","items":[]}',
        '{"schema_version":1,"stream_id":"inbox","items":[],"admin":true}',
        '{"schema_version":1,"stream_id":"inbox","items":[{"external_id":"a","payload":{"secret":NaN}}]}',
        '{"schema_version":1,"stream_id":"inbox","items":[{"payload":{}}]}',
        '{"schema_version":1,"stream_id":"inbox","items":[{"external_id":"a","payload":{},"authority":"admin"}]}',
        '{"schema_version":1,"stream_id":"inbox","items":[{"external_id":"a","payload":{},"occurred_at":"2026-09-23"}]}',
    ],
)
def test_malformed_fixture_rejected_without_source_content(
    tmp_path: Path, raw: str
) -> None:
    path = tmp_path / "source.json"
    path.write_text(raw)
    with pytest.raises(InvalidPerception) as error:
        LocalJsonConnector(path, source_id="inbox").poll(None)
    assert "secret" not in str(error.value)


def test_fixture_over_byte_cap_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "source.json"
    path.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(InvalidPerception):
        LocalJsonConnector(path, source_id="inbox").poll(None)


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff",
        b'{"schema_version":1,"stream_id":"inbox","items":[{"external_id":"A","payload":{"x":1,"x":2}}]}',
        b'{"schema_version":1,"stream_id":"inbox","items":[{"external_id":"A","payload":{"x":"\\u0000"}}]}',
        b'{"schema_version":1,"stream_id":"inbox","items":[{"external_id":"A","payload":{"x":"\\ud800"}}]}',
        b'{"schema_version":1,"stream_id":"inbox","items":[{"external_id":"A","payload":{"x":1e999}}]}',
    ],
)
def test_invalid_encodings_and_nested_json_are_rejected(
    tmp_path: Path, raw: bytes
) -> None:
    path = tmp_path / "source.json"
    path.write_bytes(raw)
    with pytest.raises(InvalidPerception):
        LocalJsonConnector(path, source_id="inbox").poll(None)


def test_cursor_rejects_unknown_fields_duplicate_keys_and_wrong_hash(
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.json"
    fixture(path, [])
    adapter = LocalJsonConnector(path, source_id="inbox")
    valid = json.loads(adapter.poll(None).next_cursor)
    wrong_hash = dict(valid, fixture_sha256="0" * 64)
    extra = dict(valid, authority="admin")
    duplicate = json.dumps(valid)[:-1] + ',"offset":0}'
    for invalid in (json.dumps(wrong_hash), json.dumps(extra), duplicate):
        with pytest.raises(InvalidPerception):
            adapter.poll(invalid)


def test_fixture_delivery_only_identity_and_aware_occurrence_are_supported(
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.json"
    fixture(
        path,
        [
            {
                "delivery_key": "delivery-1",
                "occurred_at": "2026-09-23T10:00:00-05:00",
                "payload": {"text": "hello"},
            }
        ],
    )
    item = LocalJsonConnector(path, source_id="inbox").poll(None).items[0]
    assert item.external_id is None
    assert item.delivery_key == "delivery-1"
    assert item.occurred_at.isoformat() == "2026-09-23T10:00:00-05:00"
