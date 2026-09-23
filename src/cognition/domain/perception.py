"""Strict, bounded observation normalization without persistence or authority."""

import hashlib
import json
import math
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from cognition.connectors.base import ConnectorBatch, ConnectorItem
from cognition.protocols.common import JsonObject, normalize_utc

MAX_PAGE_ITEMS = 32
MAX_PAGE_BYTES = 256 * 1024
MAX_ITEM_BYTES = 32 * 1024
MAX_ID_BYTES = 512
MAX_CURSOR_BYTES = 4 * 1024
MAX_AUTH_BYTES = 4 * 1024
MAX_JSON_DEPTH = 16


class InvalidPerception(ValueError):
    """Invalid ingress data; messages never interpolate source content."""


@dataclass(frozen=True)
class ConnectorBindingSnapshot:
    connector_binding_id: UUID
    individual_id: UUID
    adapter_id: str
    source_id: str
    enabled: bool
    cursor: str | None
    cursor_revision: int
    binding_revision: int
    individual_revision: int
    governance_revision: int
    operational_status: str


@dataclass(frozen=True)
class NormalizedItem:
    external_id: str | None
    delivery_key: str | None
    dedup_key: str
    fingerprint_version: int
    content_fingerprint: str
    occurred_at: datetime | None
    content_type: str
    payload: JsonObject
    text: str | None
    authentication: JsonObject
    raw_content_hash: str | None = None


@dataclass(frozen=True)
class NormalizedPage:
    individual_id: UUID
    connector_binding_id: UUID
    adapter_id: str
    source_id: str
    observed_at: datetime
    next_cursor: str | None
    items: tuple[NormalizedItem, ...]
    source_page_hash: str


def _string(
    value: object, *, maximum: int | None = None, nonempty: bool = False
) -> str:
    if type(value) is not str:
        raise InvalidPerception("source value must be a string")
    if "\x00" in value or (nonempty and not value):
        raise InvalidPerception("source string is invalid")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise InvalidPerception("source string is not valid UTF-8") from None
    if maximum is not None and len(encoded) > maximum:
        raise InvalidPerception("source string exceeds byte limit")
    return value


def validate_opaque_id(value: object) -> str:
    """Preserve exact identity bytes, including case and Unicode distinctions."""
    return _string(value, maximum=MAX_ID_BYTES, nonempty=True)


def validate_cursor(value: object) -> str | None:
    return None if value is None else _string(value, maximum=MAX_CURSOR_BYTES)


def validate_json(value: object, *, depth: int = 0) -> None:
    """Reject coercion, cycles/deep trees, nonfinite numbers and invalid strings."""
    if value is None or type(value) in (bool, int):
        return
    if type(value) is str:
        _string(value)
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise InvalidPerception("source JSON number must be finite")
        return
    if type(value) not in (dict, list):
        raise InvalidPerception("source value is not JSON")
    if depth >= MAX_JSON_DEPTH:
        raise InvalidPerception("source JSON nesting exceeds limit")
    if type(value) is dict:
        for key, member in value.items():
            _string(key)
            validate_json(member, depth=depth + 1)
    elif type(value) is list:
        for member in value:
            validate_json(member, depth=depth + 1)


def canonical_json_bytes(value: object) -> bytes:
    """Encode previously validated JSON deterministically, without ASCII expansion."""
    try:
        return json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise InvalidPerception("source JSON cannot be encoded") from None


def _utc(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise InvalidPerception("source timestamp is invalid")
    try:
        return normalize_utc(value)
    except (ValueError, OverflowError):
        raise InvalidPerception(
            "source timestamp must be an aware representable instant"
        ) from None


def _item_object(item: NormalizedItem) -> JsonObject:
    return {
        "external_id": item.external_id,
        "delivery_key": item.delivery_key,
        "dedup_key": item.dedup_key,
        "fingerprint_version": item.fingerprint_version,
        "content_fingerprint": item.content_fingerprint,
        "occurred_at": None
        if item.occurred_at is None
        else _utc(item.occurred_at).isoformat(),
        "content_type": item.content_type,
        "payload": item.payload,
        "text": item.text,
        "authentication": item.authentication,
        "raw_content_hash": item.raw_content_hash,
    }


def normalize_item(item: ConnectorItem) -> NormalizedItem:
    """Normalize the initial read-only fixture contract, never trusting envelopes."""
    if type(item) is not ConnectorItem:
        raise InvalidPerception("source page contains a malformed item")
    external = (
        None if item.external_id is None else validate_opaque_id(item.external_id)
    )
    delivery = (
        None if item.delivery_key is None else validate_opaque_id(item.delivery_key)
    )
    if external is None and delivery is None:
        raise InvalidPerception("source item has no stable identity")
    if type(item.payload) is not dict:
        raise InvalidPerception("source payload must be an object")
    validate_json(item.payload)
    payload = deepcopy(item.payload)
    occurred = None if item.occurred_at is None else _utc(item.occurred_at)
    text_value = payload.get("text")
    text_content = text_value if isinstance(text_value, str) else None
    authentication: JsonObject = {
        "mechanism": "local_fixture",
        "authenticated_actor": None,
        "assertions": {},
    }
    identity = {
        "version": 1,
        "kind": "external_id" if external is not None else "delivery_key",
        "value": external if external is not None else delivery,
    }
    content: JsonObject = {
        "occurred_at": None if occurred is None else occurred.isoformat(),
        "content_type": "application/json",
        "payload": payload,
        "text": text_content,
        "authentication": authentication,
    }
    result = NormalizedItem(
        external_id=external,
        delivery_key=None if external is not None else delivery,
        dedup_key=hashlib.sha256(canonical_json_bytes(identity)).hexdigest(),
        fingerprint_version=1,
        content_fingerprint=hashlib.sha256(canonical_json_bytes(content)).hexdigest(),
        occurred_at=occurred,
        content_type="application/json",
        payload=payload,
        text=text_content,
        authentication=authentication,
    )
    if len(canonical_json_bytes(_item_object(result))) > MAX_ITEM_BYTES:
        raise InvalidPerception("normalized source item exceeds byte limit")
    return result


def normalized_page_bytes(
    items: tuple[NormalizedItem, ...], next_cursor: str | None
) -> bytes:
    """Canonical page wrapper shared by source paging and persistence validation."""
    return canonical_json_bytes(
        {"items": [_item_object(item) for item in items], "next_cursor": next_cursor}
    )


def _validate_snapshot(snapshot: ConnectorBindingSnapshot) -> None:
    if type(snapshot) is not ConnectorBindingSnapshot:
        raise InvalidPerception("connector snapshot is invalid")
    if not isinstance(snapshot.individual_id, UUID) or not isinstance(
        snapshot.connector_binding_id, UUID
    ):
        raise InvalidPerception("connector snapshot identity is invalid")
    validate_opaque_id(snapshot.adapter_id)
    validate_opaque_id(snapshot.source_id)
    validate_cursor(snapshot.cursor)
    if (
        type(snapshot.enabled) is not bool
        or type(snapshot.operational_status) is not str
    ):
        raise InvalidPerception("connector snapshot lifecycle is invalid")
    for value, minimum in (
        (snapshot.cursor_revision, 0),
        (snapshot.binding_revision, 1),
        (snapshot.individual_revision, 1),
        (snapshot.governance_revision, 1),
    ):
        if type(value) is not int or value < minimum:
            raise InvalidPerception("connector snapshot revision is invalid")


def normalize_page(
    snapshot: ConnectorBindingSnapshot, batch: ConnectorBatch, observed_at: datetime
) -> NormalizedPage:
    _validate_snapshot(snapshot)
    if type(batch) is not ConnectorBatch or type(batch.items) is not tuple:
        raise InvalidPerception("connector page is invalid")
    if len(batch.items) > MAX_PAGE_ITEMS:
        raise InvalidPerception("source page exceeds item limit")
    cursor = validate_cursor(batch.next_cursor)
    observed = _utc(observed_at)
    items = tuple(normalize_item(cast(ConnectorItem, item)) for item in batch.items)
    encoded = normalized_page_bytes(items, cursor)
    if len(encoded) > MAX_PAGE_BYTES:
        raise InvalidPerception("normalized source page exceeds byte limit")
    return NormalizedPage(
        individual_id=snapshot.individual_id,
        connector_binding_id=snapshot.connector_binding_id,
        adapter_id=snapshot.adapter_id,
        source_id=snapshot.source_id,
        observed_at=observed,
        next_cursor=cursor,
        items=items,
        source_page_hash=hashlib.sha256(encoded).hexdigest(),
    )


def validate_normalized_page(
    snapshot: ConnectorBindingSnapshot, page: NormalizedPage
) -> NormalizedPage:
    """Rebuild every derived field and detach JSON before the trusted write path."""
    if type(page) is not NormalizedPage or type(page.items) is not tuple:
        raise InvalidPerception("normalized page is invalid")
    if (
        page.individual_id,
        page.connector_binding_id,
        page.adapter_id,
        page.source_id,
    ) != (
        snapshot.individual_id,
        snapshot.connector_binding_id,
        snapshot.adapter_id,
        snapshot.source_id,
    ):
        raise InvalidPerception("normalized page belongs to another source")
    source_items: list[ConnectorItem] = []
    for item in page.items:
        if type(item) is not NormalizedItem:
            raise InvalidPerception("normalized item is invalid")
        source_items.append(
            ConnectorItem(
                item.external_id, item.payload, item.occurred_at, item.delivery_key
            )
        )
    rebuilt = normalize_page(
        snapshot,
        ConnectorBatch(tuple(source_items), page.next_cursor),
        page.observed_at,
    )
    if (
        canonical_json_bytes([_item_object(item) for item in page.items])
        != canonical_json_bytes([_item_object(item) for item in rebuilt.items])
        or type(page.source_page_hash) is not str
        or page.source_page_hash != rebuilt.source_page_hash
    ):
        raise InvalidPerception("normalized page integrity mismatch")
    return rebuilt
