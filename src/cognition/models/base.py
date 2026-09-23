"""The synchronous executive boundary, independent of provider SDKs."""

from typing import Protocol, runtime_checkable

from cognition.protocols.executive import ModelRequest, ModelResult


class ModelRequestIncompatible(ValueError):
    """The local adapter cannot accept this frozen request."""


class ModelUnavailable(RuntimeError):
    """The adapter has no local source available for a new result."""


@runtime_checkable
class ExecutivePreflight(Protocol):
    def validate_request(self, request: ModelRequest) -> None:
        """Check local compatibility without inference or consuming a template."""
        ...


class ExecutiveModel(Protocol):
    def decide(self, request: ModelRequest) -> ModelResult:
        """Return an observed model result; no proposal is applied here."""
        ...
