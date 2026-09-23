"""Inbound source scripts describe delivery, not observation persistence."""

from datetime import UTC, datetime

import pytest

from cognition.testing.fake_connector import (
    ConnectorBatch,
    ConnectorItem,
    ConnectorPollStep,
    ConnectorScriptExhausted,
    FakeConnector,
    MalformedConnectorItem,
)


def test_duplicates_out_of_order_missing_ids_and_malformed_items_are_preserved():
    older = ConnectorItem(
        "event-1", {"text": "older"}, datetime(2026, 9, 1, tzinfo=UTC)
    )
    newer = ConnectorItem(
        "event-2", {"text": "newer"}, datetime(2026, 9, 2, tzinfo=UTC)
    )
    no_id = ConnectorItem(None, {"text": "no provider ID"})
    malformed = MalformedConnectorItem(["not", "an", "event"], "expected object")
    connector = FakeConnector(
        [
            ConnectorPollStep(None, ConnectorBatch((newer, older), "page-2")),
            ConnectorPollStep(
                "page-2", ConnectorBatch((older, no_id, malformed), None)
            ),
        ]
    )
    first = connector.poll(None)
    second = connector.poll(first.next_cursor)
    assert first.items == (newer, older)
    assert second.items == (older, no_id, malformed)
    assert connector.polls == (None, "page-2")
    assert connector.acknowledgements == ()
    connector.acknowledge("page-2")
    connector.acknowledge(None)
    assert connector.acknowledgements == ("page-2", None)


def test_connector_records_timeout_and_retries_explicitly():
    connector = FakeConnector(
        [
            ConnectorPollStep("cursor", TimeoutError("poll timed out")),
            ConnectorPollStep("cursor", ConnectorBatch((), "next")),
        ]
    )
    with pytest.raises(TimeoutError):
        connector.poll("cursor")
    assert connector.poll("cursor").next_cursor == "next"
    assert connector.polls == ("cursor", "cursor")


def test_connector_wrong_cursor_does_not_consume_expected_step():
    connector = FakeConnector([ConnectorPollStep("expected", ConnectorBatch((), None))])
    with pytest.raises(AssertionError, match="cursor"):
        connector.poll("wrong")
    assert connector.poll("expected").items == ()
    assert connector.polls == ("wrong", "expected")


def test_connector_exhaustion_is_explicit_and_recorded():
    connector = FakeConnector([])
    with pytest.raises(ConnectorScriptExhausted, match="exhausted"):
        connector.poll(None)
    assert connector.polls == (None,)


def test_connector_preserves_custom_provider_exception_metadata():
    class ProviderFailure(RuntimeError):
        def __init__(self, message, *, retryable):
            super().__init__(message)
            self.retryable = retryable

    error = ProviderFailure("service failed", retryable=False)
    connector = FakeConnector([ConnectorPollStep(None, error)])
    with pytest.raises(ProviderFailure) as captured:
        connector.poll(None)
    assert captured.value.retryable is False


def test_connector_scripts_and_delivered_payloads_are_defensive_copies():
    item = ConnectorItem("id", {"nested": {"text": "original"}})
    batch = ConnectorBatch((item,), None)
    connector = FakeConnector(
        [
            ConnectorPollStep(None, batch),
            ConnectorPollStep(None, batch),
        ]
    )
    item.payload["nested"]["text"] = "changed after setup"
    received = connector.poll(None)
    assert received.items[0].payload["nested"]["text"] == "original"
    received.items[0].payload["nested"]["text"] = "changed after delivery"
    assert connector.poll(None).items[0].payload["nested"]["text"] == "original"


def test_shared_records_preserve_old_imports_and_delivery_key_on_scripted_replay():
    from cognition.connectors.base import ConnectorBatch as SharedBatch
    from cognition.connectors.base import ConnectorItem as SharedItem

    assert ConnectorItem is SharedItem
    assert ConnectorBatch is SharedBatch
    old = ConnectorItem("old", {}, datetime(2026, 9, 1, tzinfo=UTC))
    assert old.delivery_key is None
    delivery = SharedItem(None, {"nested": {"text": "original"}}, None, "delivery")
    connector = FakeConnector(
        [ConnectorPollStep(None, SharedBatch((delivery,), "next"))]
    )
    delivery.payload["nested"]["text"] = "changed"
    received = connector.poll(None).items[0]
    assert received.delivery_key == "delivery"
    assert received.payload["nested"]["text"] == "original"
    assert connector.acknowledgements == ()
