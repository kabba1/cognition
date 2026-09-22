"""An in-process external world independent of Cognition transaction state."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Literal

from cognition.protocols.capabilities_v1 import RetrySemantics
from cognition.protocols.common import JsonObject
from cognition.testing.faults import FaultInjector


@dataclass(frozen=True)
class SendMessage:
    recipient: str
    body: str


@dataclass(frozen=True)
class CreateResource:
    value: JsonObject


@dataclass(frozen=True)
class SetResource:
    resource_id: str
    value: JsonObject


type ActionCommand = SendMessage | CreateResource | SetResource
type FakeOperation = Literal["send_message", "create_resource", "set_resource"]


@dataclass(frozen=True)
class SentMessage:
    message_id: str
    recipient: str
    body: str


@dataclass(frozen=True)
class CreatedResource:
    resource_id: str
    value: JsonObject
    version: int


@dataclass(frozen=True)
class ResourceVersion:
    resource_id: str
    version: int
    value: JsonObject


@dataclass(frozen=True)
class ProviderReceipt:
    receipt_id: str
    operation: FakeOperation
    external_id: str
    version: int | None
    request_key: str | None


@dataclass(frozen=True)
class IdempotencyRecord:
    key: str
    command: ActionCommand
    receipt: ProviderReceipt


class FakeWorld:
    """Authoritative fake provider state; every public snapshot is defensive."""

    def __init__(self) -> None:
        self._messages: list[SentMessage] = []
        self._resources: dict[str, CreatedResource] = {}
        self._versions: list[ResourceVersion] = []
        self._receipts: list[ProviderReceipt] = []
        self._idempotency: dict[str, IdempotencyRecord] = {}
        self._resource_counter = 0

    @property
    def sent_messages(self) -> tuple[SentMessage, ...]:
        return tuple(self._messages)

    @property
    def created_resources(self) -> tuple[CreatedResource, ...]:
        return deepcopy(tuple(self._resources.values()))

    @property
    def resource_versions(self) -> tuple[ResourceVersion, ...]:
        return deepcopy(tuple(self._versions))

    @property
    def receipts(self) -> tuple[ProviderReceipt, ...]:
        return tuple(self._receipts)

    @property
    def idempotency_keys(self) -> tuple[IdempotencyRecord, ...]:
        return deepcopy(tuple(self._idempotency.values()))

    def _existing_receipt(
        self, key: str, command: ActionCommand
    ) -> ProviderReceipt | None:
        record = self._idempotency.get(key)
        if record is None:
            return None
        if record.command != command:
            raise ValueError("idempotency key reused for a different command")
        return record.receipt

    def _apply(
        self, command: ActionCommand, key: str | None, *, native_idempotency: bool
    ) -> ProviderReceipt:
        """Perform and retain the effect and its receipt before returning."""
        command = deepcopy(command)
        version: int | None = None
        operation: FakeOperation
        if isinstance(command, SendMessage):
            external_id = f"message-{len(self._messages) + 1}"
            operation = "send_message"
            self._messages.append(
                SentMessage(external_id, command.recipient, command.body)
            )
        else:
            if isinstance(command, CreateResource):
                operation = "create_resource"
                self._resource_counter += 1
                while f"resource-{self._resource_counter}" in self._resources:
                    self._resource_counter += 1
                external_id = f"resource-{self._resource_counter}"
            else:
                operation = "set_resource"
                external_id = command.resource_id
            existing = self._resources.get(external_id)
            if existing is not None and existing.value == command.value:
                version = existing.version
            else:
                version = 1 if existing is None else existing.version + 1
                self._resources[external_id] = CreatedResource(
                    external_id, deepcopy(command.value), version
                )
                self._versions.append(
                    ResourceVersion(external_id, version, deepcopy(command.value))
                )
        receipt = ProviderReceipt(
            f"receipt-{len(self._receipts) + 1}", operation, external_id, version, key
        )
        self._receipts.append(receipt)
        if native_idempotency and key is not None:
            self._idempotency[key] = IdempotencyRecord(key, command, receipt)
        return receipt

    def _verify(self, receipt: ProviderReceipt) -> bool:
        if receipt not in self._receipts:
            return False
        if receipt.operation == "send_message":
            return any(m.message_id == receipt.external_id for m in self._messages)
        current = self._resources.get(receipt.external_id)
        return current is not None and current.version == receipt.version


class FakeActionProvider:
    """Expose retry semantics, including effects that survive a caller timeout.

    Keys on reconcilable operations are correlation hints, not deduplication.
    Safe repeat only sets a named resource to a value; an identical set neither
    creates a new resource nor appends a resource version. Receipts still record
    each successful provider invocation.
    """

    def __init__(
        self,
        world: FakeWorld,
        profile: RetrySemantics,
        *,
        faults: FaultInjector | None = None,
    ) -> None:
        if profile not in (
            "native_idempotency",
            "reconcilable",
            "safe_repeat",
            "unsafe_repeat",
        ):
            raise ValueError(f"unknown provider profile: {profile}")
        self.world = world
        self.profile = profile
        self.faults = faults if faults is not None else FaultInjector()

    def perform(
        self, command: ActionCommand, *, idempotency_key: str | None = None
    ) -> ProviderReceipt:
        """Perform a command; post-effect faults never roll back external state."""
        if self.profile == "safe_repeat" and not isinstance(command, SetResource):
            raise ValueError("safe_repeat requires SetResource")
        if self.profile == "native_idempotency":
            if not idempotency_key:
                raise ValueError("native_idempotency requires an idempotency key")
            existing = self.world._existing_receipt(idempotency_key, command)
            if existing is not None:
                response = deepcopy(existing)
                self.faults.hit("after_response_constructed")
                return response
        self.faults.hit("before_external_side_effect")
        receipt = self.world._apply(
            command,
            idempotency_key,
            native_idempotency=self.profile == "native_idempotency",
        )
        self.faults.hit("after_external_side_effect")
        self.faults.hit("after_world_persisted_before_response")
        response = deepcopy(receipt)
        self.faults.hit("after_response_constructed")
        return response

    def reconcile(self, request_key: str) -> tuple[ProviderReceipt, ...]:
        """Query receipts by correlation key without re-performing an effect."""
        if self.profile not in ("native_idempotency", "reconcilable"):
            raise ValueError(f"{self.profile} does not support reconciliation")
        self.faults.hit("during_reconciliation")
        return tuple(r for r in self.world.receipts if r.request_key == request_key)

    def verify(self, receipt: ProviderReceipt) -> bool:
        """Check current worldly state independently of a successful response."""
        self.faults.hit("verification_timeout")
        return self.world._verify(receipt)
