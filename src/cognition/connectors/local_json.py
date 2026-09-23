"""Stateless, finite local fixture ingestion; no acknowledgement or file writes."""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import cast

from cognition.connectors.base import ConnectorBatch, ConnectorItem
from cognition.domain.perception import (
    MAX_PAGE_BYTES,
    MAX_PAGE_ITEMS,
    InvalidPerception,
    NormalizedItem,
    canonical_json_bytes,
    normalize_item,
    normalized_page_bytes,
    validate_cursor,
    validate_opaque_id,
)
from cognition.protocols.common import JsonObject

MAX_FIXTURE_BYTES = 1024 * 1024


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidPerception("source JSON contains duplicate keys")
        result[key] = value
    return result


def _constant(value: str) -> object:
    raise InvalidPerception("source JSON contains a nonfinite number")


def _parse(raw: str | bytes) -> object:
    try:
        return json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise InvalidPerception("source JSON is invalid") from None


def _cursor(fixture_hash: str, offset: int) -> str:
    return canonical_json_bytes(
        {"fixture_sha256": fixture_hash, "offset": offset}
    ).decode("utf-8")


def _offset(cursor: str | None, fixture_hash: str, count: int) -> int:
    validate_cursor(cursor)
    if cursor is None:
        return 0
    data = _parse(cursor)
    if type(data) is not dict or set(data) != {"fixture_sha256", "offset"}:
        raise InvalidPerception("fixture cursor is invalid")
    offset = data["offset"]
    if (
        data["fixture_sha256"] != fixture_hash
        or type(offset) is not int
        or not 0 <= offset <= count
    ):
        raise InvalidPerception("fixture cursor does not match source")
    return offset


def _source_item(value: object) -> ConnectorItem:
    if type(value) is not dict or not {"payload"} <= set(value) <= {
        "external_id",
        "delivery_key",
        "payload",
        "occurred_at",
    }:
        raise InvalidPerception("fixture item shape is invalid")
    raw_time = value.get("occurred_at")
    occurred = None
    if raw_time is not None:
        try:
            occurred = datetime.fromisoformat(validate_opaque_id(raw_time))
        except (ValueError, OverflowError):
            raise InvalidPerception("fixture timestamp is invalid") from None
    return ConnectorItem(
        external_id=cast(str | None, value.get("external_id")),
        payload=cast(JsonObject, value["payload"]),
        occurred_at=occurred,
        delivery_key=cast(str | None, value.get("delivery_key")),
    )


class LocalJsonConnector:
    """Read a bounded immutable fixture, identified by its exact byte hash."""

    adapter_id = "local_json_v1"

    def __init__(self, path: str | Path, *, source_id: str) -> None:
        self._path = Path(path)
        self._source_id = validate_opaque_id(source_id)

    @property
    def source_id(self) -> str:
        return self._source_id

    def poll(self, cursor: str | None) -> ConnectorBatch:
        try:
            with self._path.open("rb") as source:
                raw = source.read(MAX_FIXTURE_BYTES + 1)
        except OSError:
            raise InvalidPerception("fixture cannot be read") from None
        if len(raw) > MAX_FIXTURE_BYTES:
            raise InvalidPerception("fixture exceeds byte limit")
        try:
            decoded = raw.decode("utf-8")
        except UnicodeError:
            raise InvalidPerception("fixture is not valid UTF-8") from None
        document = _parse(decoded)
        if type(document) is not dict or set(document) != {
            "schema_version",
            "stream_id",
            "items",
        }:
            raise InvalidPerception("fixture document shape is invalid")
        if (
            type(document["schema_version"]) is not int
            or document["schema_version"] != 1
        ):
            raise InvalidPerception("fixture schema version is invalid")
        if validate_opaque_id(document["stream_id"]) != self.source_id:
            raise InvalidPerception("fixture logical source does not match binding")
        values = document["items"]
        if type(values) is not list:
            raise InvalidPerception("fixture items must be an array")
        fixture_hash = hashlib.sha256(raw).hexdigest()
        offset = _offset(cursor, fixture_hash, len(values))
        # Validate the complete bounded fixture so malformed later members are not
        # silently skipped merely because a supplied cursor starts beyond them.
        items = tuple(_source_item(value) for value in values)
        normalized = tuple(normalize_item(item) for item in items)
        selected: list[ConnectorItem] = []
        selected_normalized: list[NormalizedItem] = []
        for index in range(offset, min(len(items), offset + MAX_PAGE_ITEMS)):
            candidate = (*selected_normalized, normalized[index])
            if (
                len(normalized_page_bytes(candidate, _cursor(fixture_hash, index + 1)))
                > MAX_PAGE_BYTES
            ):
                if not selected:
                    raise InvalidPerception("fixture member cannot fit a bounded page")
                break
            selected.append(items[index])
            selected_normalized.append(normalized[index])
        next_cursor = _cursor(fixture_hash, offset + len(selected))
        # Preserve an already-valid opaque cursor exactly at EOF. Initial empty
        # fixtures still establish a hash-bound cursor instead of returning null.
        if not selected and cursor is not None:
            next_cursor = cursor
        return ConnectorBatch(tuple(selected), next_cursor)
