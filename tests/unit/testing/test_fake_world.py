"""External state survives ambiguous responses and deliberately unsafe retries."""

import pytest

from cognition.testing.fake_world import (
    CreateResource,
    FakeActionProvider,
    FakeWorld,
    SendMessage,
    SetResource,
)
from cognition.testing.faults import FaultInjector


@pytest.mark.parametrize(
    "boundary",
    [
        "after_external_side_effect",
        "after_world_persisted_before_response",
        "after_response_constructed",
    ],
)
def test_timeout_after_effect_preserves_world_and_native_retry_deduplicates(boundary):
    world = FakeWorld()
    faults = FaultInjector()
    faults.configure(boundary, error=TimeoutError("ambiguous response"))
    provider = FakeActionProvider(world, "native_idempotency", faults=faults)
    command = SendMessage("operator@example.org", "ready")
    with pytest.raises(TimeoutError, match="ambiguous"):
        provider.perform(command, idempotency_key="send-1")
    assert len(world.sent_messages) == 1
    assert len(world.receipts) == 1
    replacement = FakeActionProvider(world, "native_idempotency")
    receipt = replacement.perform(command, idempotency_key="send-1")
    assert receipt == world.receipts[0]
    assert len(world.sent_messages) == 1
    assert len(world.idempotency_keys) == 1
    assert replacement.verify(receipt) is True


def test_fault_before_effect_leaves_no_worldly_records():
    world = FakeWorld()
    faults = FaultInjector()
    faults.configure("before_external_side_effect", error=TimeoutError())
    provider = FakeActionProvider(world, "native_idempotency", faults=faults)
    with pytest.raises(TimeoutError):
        provider.perform(SendMessage("to", "body"), idempotency_key="key")
    assert world.sent_messages == ()
    assert world.receipts == ()
    assert world.idempotency_keys == ()


def test_unsafe_repeat_can_double_send_after_ambiguous_response():
    world = FakeWorld()
    faults = FaultInjector()
    faults.configure("after_external_side_effect", error=TimeoutError())
    provider = FakeActionProvider(world, "unsafe_repeat", faults=faults)
    command = SendMessage("to", "body")
    with pytest.raises(TimeoutError):
        provider.perform(command, idempotency_key="same-key")
    provider.perform(command, idempotency_key="same-key")
    assert len(world.sent_messages) == 2
    assert world.sent_messages[0].message_id != world.sent_messages[1].message_id
    assert world.idempotency_keys == ()


def test_reconcilable_profile_can_query_effect_without_repeating_it():
    world = FakeWorld()
    faults = FaultInjector()
    faults.configure("after_external_side_effect", error=TimeoutError())
    provider = FakeActionProvider(world, "reconcilable", faults=faults)
    with pytest.raises(TimeoutError):
        provider.perform(CreateResource({"title": "draft"}), idempotency_key="create-1")
    assert len(world.created_resources) == 1
    receipts = provider.reconcile("create-1")
    assert len(receipts) == 1
    assert provider.verify(receipts[0]) is True
    assert provider.reconcile("missing") == ()


def test_safe_repeat_sets_state_without_creating_a_new_resource_version():
    world = FakeWorld()
    faults = FaultInjector()
    faults.configure("after_external_side_effect", error=TimeoutError())
    provider = FakeActionProvider(world, "safe_repeat", faults=faults)
    command = SetResource("draft", {"title": "ready"})
    with pytest.raises(TimeoutError):
        provider.perform(command)
    receipt = provider.perform(command)
    assert len(world.created_resources) == 1
    assert len(world.resource_versions) == 1
    assert provider.verify(receipt) is True
    changed = provider.perform(SetResource("draft", {"title": "revised"}))
    assert len(world.resource_versions) == 2
    assert provider.verify(receipt) is False
    assert provider.verify(changed) is True


@pytest.mark.parametrize("command", [SendMessage("to", "body"), CreateResource({})])
def test_safe_repeat_refuses_non_harmless_operations(command):
    world = FakeWorld()
    with pytest.raises(ValueError, match="SetResource"):
        FakeActionProvider(world, "safe_repeat").perform(command)
    assert world.receipts == ()


def test_native_idempotency_rejects_missing_key_and_changed_request():
    world = FakeWorld()
    provider = FakeActionProvider(world, "native_idempotency")
    with pytest.raises(ValueError, match="key"):
        provider.perform(SendMessage("to", "body"))
    provider.perform(SendMessage("to", "body"), idempotency_key="key")
    with pytest.raises(ValueError, match="different"):
        provider.perform(SendMessage("other", "changed"), idempotency_key="key")
    assert len(world.sent_messages) == 1


@pytest.mark.parametrize(
    ("boundary", "query"),
    [
        ("verification_timeout", "verify"),
        ("during_reconciliation", "reconcile"),
    ],
)
def test_query_faults_do_not_modify_external_world(boundary, query):
    world = FakeWorld()
    faults = FaultInjector()
    provider = FakeActionProvider(world, "reconcilable", faults=faults)
    receipt = provider.perform(
        CreateResource({"state": "ready"}), idempotency_key="key"
    )
    faults.configure(boundary, error=TimeoutError("query timed out"))
    with pytest.raises(TimeoutError):
        getattr(provider, query)(receipt if query == "verify" else "key")
    assert len(world.created_resources) == 1
    assert len(world.resource_versions) == 1
    assert provider.verify(receipt) is True


def test_world_snapshots_and_commands_cannot_be_mutated_from_outside():
    world = FakeWorld()
    provider = FakeActionProvider(world, "native_idempotency")
    value = {"nested": {"value": "original"}}
    provider.perform(CreateResource(value), idempotency_key="key")
    value["nested"]["value"] = "changed"
    resources = world.created_resources
    resources[0].value["nested"]["value"] = "changed again"
    versions = world.resource_versions
    versions[0].value["nested"]["value"] = "changed version"
    keys = world.idempotency_keys
    keys[0].command.value["nested"]["value"] = "changed command"
    assert world.created_resources[0].value == {"nested": {"value": "original"}}
    assert world.resource_versions[0].value == {"nested": {"value": "original"}}
    assert world.idempotency_keys[0].command.value == {"nested": {"value": "original"}}


def test_identical_scripts_produce_identical_ids_and_receipts():
    worlds = [FakeWorld(), FakeWorld()]
    for world in worlds:
        provider = FakeActionProvider(world, "unsafe_repeat")
        provider.perform(SendMessage("to", "one"))
        provider.perform(CreateResource({"title": "draft"}))
    assert worlds[0].receipts == worlds[1].receipts
    assert worlds[0].sent_messages == worlds[1].sent_messages
    assert worlds[0].created_resources == worlds[1].created_resources
