"""Provider-neutral, detached inbound delivery records."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pydantic import JsonValue

from cognition.protocols.common import JsonObject


@dataclass(frozen=True)
class ConnectorItem:
    """A stable provider identity and source evidence, without runtime authority."""

    external_id: str | None
    payload: JsonObject
    occurred_at: datetime | None = None
    delivery_key: str | None = None


@dataclass(frozen=True)
class MalformedConnectorItem:
    """Represent an invalid delivery so ingestion can reject its whole page."""

    raw: JsonValue
    reason: str


@dataclass(frozen=True)
class ConnectorBatch:
    """One ordered source page; duplicates remain visible to persistence."""

    items: tuple[ConnectorItem | MalformedConnectorItem, ...]
    next_cursor: str | None


class InboundConnector(Protocol):
    """Read-only polling, with no provider acknowledgement or mutation."""

    @property
    def adapter_id(self) -> str: ...

    @property
    def source_id(self) -> str: ...

    def poll(self, cursor: str | None) -> ConnectorBatch: ...
