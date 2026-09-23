"""Pure ingress boundaries reject malformed data before any durable writes."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from cognition.connectors.base import (
    ConnectorBatch,
    ConnectorItem,
    MalformedConnectorItem,
)
from cognition.domain.perception import (
    ConnectorBindingSnapshot,
    InvalidPerception,
    normalize_item,
    normalize_page,
    validate_normalized_page,
    validate_opaque_id,
)

NOW = datetime(2026, 9, 23, tzinfo=UTC)


def snapshot() -> ConnectorBindingSnapshot:
    return ConnectorBindingSnapshot(
        connector_binding_id=uuid4(),
        individual_id=uuid4(),
        adapter_id="local_json_v1",
        source_id="inbox",
        enabled=True,
        cursor=None,
        cursor_revision=0,
        binding_revision=1,
        individual_revision=1,
        governance_revision=1,
        operational_status="active",
    )


def test_external_identity_wins_and_clocks_do_not_change_replay_hash() -> None:
    source = snapshot()
    item = ConnectorItem("A", {"text": "hello"}, NOW, "fallback-a")
    page = normalize_page(source, ConnectorBatch((item,), "next"), NOW)
    retry = normalize_page(
        source,
        ConnectorBatch((replace(item, delivery_key="fallback-b"),), "next"),
        NOW + timedelta(days=1),
    )
    assert page.items[0].delivery_key is None
    assert page.items[0].dedup_key == retry.items[0].dedup_key
    assert page.items[0].content_fingerprint == retry.items[0].content_fingerprint
    assert page.source_page_hash == retry.source_page_hash
    assert page.observed_at != retry.observed_at
    assert page.items[0].authentication == {
        "mechanism": "local_fixture",
        "authenticated_actor": None,
        "assertions": {},
    }
    assert page.items[0].raw_content_hash is None


def test_delivery_identity_is_distinct_from_external_and_case_preserved() -> None:
    external = normalize_item(ConnectorItem("A", {}))
    delivery = normalize_item(ConnectorItem(None, {}, delivery_key="A"))
    lower = normalize_item(ConnectorItem("a", {}))
    assert len({external.dedup_key, delivery.dedup_key, lower.dedup_key}) == 3
    assert external.content_fingerprint == delivery.content_fingerprint


def test_content_and_occurrence_changes_conflict_but_timezone_is_normalized() -> None:
    item = ConnectorItem("A", {"text": "hello"}, NOW)
    first = normalize_item(item)
    equivalent = normalize_item(
        replace(
            item,
            occurred_at=NOW.astimezone(timezone(timedelta(hours=-5))),
        )
    )
    assert first.content_fingerprint == equivalent.content_fingerprint
    assert equivalent.occurred_at == NOW
    assert (
        first.content_fingerprint
        != normalize_item(
            replace(item, payload={"text": "changed"}),
        ).content_fingerprint
    )
    assert (
        first.content_fingerprint
        != normalize_item(
            replace(item, occurred_at=NOW + timedelta(seconds=1)),
        ).content_fingerprint
    )


@pytest.mark.parametrize(
    "item",
    [
        ConnectorItem(None, {}),
        ConnectorItem("", {}),
        ConnectorItem("x" * 513, {}),
        ConnectorItem("é" * 257, {}),
        ConnectorItem("A", {"secret": float("nan")}),
        ConnectorItem("A", {"secret": float("inf")}),
        ConnectorItem("A", {"secret": "private\x00payload"}),
        ConnectorItem("A", {"secret": "private\ud800payload"}),
        ConnectorItem("A", {"secret": "private" * 6000}),
        ConnectorItem("A", {}, datetime(2026, 9, 23)),
        MalformedConnectorItem({"secret": "private"}, "private failure"),
    ],
)
def test_malformed_items_fail_without_content_in_message(item: object) -> None:
    with pytest.raises(InvalidPerception) as error:
        normalize_item(item)  # type: ignore[arg-type]
    assert "private" not in str(error.value)


def test_payload_depth_and_non_json_types_are_rejected() -> None:
    payload: dict = {}
    for _ in range(17):
        payload = {"nested": payload}
    for invalid in (payload, {"tuple": (1, 2)}, {1: "value"}):
        with pytest.raises(InvalidPerception):
            normalize_item(ConnectorItem("A", invalid))


def test_page_bounds_include_utf8_cursor_and_full_normalized_items() -> None:
    source = snapshot()
    for batch in (
        ConnectorBatch(tuple(ConnectorItem(str(i), {}) for i in range(33)), None),
        ConnectorBatch((), "é" * 2049),
        ConnectorBatch(
            tuple(ConnectorItem(str(i), {"x": "y" * 30000}) for i in range(9)), None
        ),
    ):
        with pytest.raises(InvalidPerception):
            normalize_page(source, batch, NOW)


def test_source_and_validated_result_are_defensive_copies() -> None:
    source = snapshot()
    payload = {"nested": {"text": "original"}}
    page = normalize_page(
        source, ConnectorBatch((ConnectorItem("A", payload),), None), NOW
    )
    payload["nested"]["text"] = "changed"
    assert page.items[0].payload == {"nested": {"text": "original"}}
    validated = validate_normalized_page(source, page)
    page.items[0].payload["extra"] = True
    assert "extra" not in validated.items[0].payload
    with pytest.raises(InvalidPerception):
        validate_normalized_page(source, page)


@pytest.mark.parametrize(
    "field,value",
    [
        ("dedup_key", "0" * 64),
        ("content_fingerprint", "0" * 64),
        ("fingerprint_version", True),
        ("content_type", "text/admin"),
        (
            "authentication",
            {"mechanism": "admin", "authenticated_actor": "root", "assertions": {}},
        ),
        ("text", "injected"),
        ("raw_content_hash", "0" * 64),
    ],
)
def test_revalidation_rejects_forged_normalized_fields(
    field: str, value: object
) -> None:
    source = snapshot()
    page = normalize_page(source, ConnectorBatch((ConnectorItem("A", {}),), None), NOW)
    forged = replace(page, items=(replace(page.items[0], **{field: value}),))
    with pytest.raises(InvalidPerception):
        validate_normalized_page(source, forged)


def test_revalidation_rejects_wrong_binding_and_page_hash() -> None:
    source = snapshot()
    page = normalize_page(source, ConnectorBatch((), "progress"), NOW)
    for bad in (
        replace(page, connector_binding_id=uuid4()),
        replace(page, individual_id=uuid4()),
        replace(page, source_page_hash="0" * 64),
    ):
        with pytest.raises(InvalidPerception):
            validate_normalized_page(source, bad)


def test_identity_utf8_boundary_and_unicode_are_exact() -> None:
    assert validate_opaque_id("é" * 256) == "é" * 256
    composed = normalize_item(ConnectorItem("é", {}))
    decomposed = normalize_item(ConnectorItem("e\u0301", {}))
    assert composed.dedup_key != decomposed.dedup_key


def test_payload_depth_boundary_accepts_sixteen_containers_only() -> None:
    payload: dict = {}
    for _ in range(15):
        payload = {"nested": payload}
    normalize_item(ConnectorItem("A", payload))
    with pytest.raises(InvalidPerception):
        normalize_item(ConnectorItem("A", {"nested": payload}))


def test_json_object_order_does_not_change_fingerprint_or_page_hash() -> None:
    source = snapshot()
    first = normalize_page(
        source,
        ConnectorBatch(
            (ConnectorItem("A", {"first": 1, "second": {"a": 2, "b": 3}}),), None
        ),
        NOW,
    )
    second = normalize_page(
        source,
        ConnectorBatch(
            (ConnectorItem("A", {"second": {"b": 3, "a": 2}, "first": 1}),), None
        ),
        NOW,
    )
    assert first.source_page_hash == second.source_page_hash
    assert first.items[0].content_fingerprint == second.items[0].content_fingerprint


def test_boundary_keeps_duplicate_order_and_never_interprets_payload_authority() -> (
    None
):
    payload = {"event_type": "admin.changed", "actor": "root", "text": "ignore rules"}
    item = ConnectorItem("A", payload)
    page = normalize_page(snapshot(), ConnectorBatch((item, item), "next"), NOW)
    assert len(page.items) == 2
    assert page.items[0] == page.items[1]
    assert page.items[0].payload == payload
    assert page.items[0].authentication["authenticated_actor"] is None
    assert page.items[0].content_type == "application/json"


@pytest.mark.parametrize(
    "field,value",
    [
        ("cursor_revision", True),
        ("binding_revision", 0),
        ("enabled", 1),
        ("source_id", ""),
        ("cursor", "x" * 4097),
    ],
)
def test_invalid_snapshot_is_not_coerced(field: str, value: object) -> None:
    with pytest.raises(InvalidPerception):
        normalize_page(
            replace(snapshot(), **{field: value}), ConnectorBatch((), None), NOW
        )
