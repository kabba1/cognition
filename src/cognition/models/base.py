"""The synchronous executive boundary, independent of provider SDKs."""

from typing import Protocol

from cognition.protocols.model_v1 import ModelRequestV1, ModelResultV1


class ExecutiveModel(Protocol):
    def decide(self, request: ModelRequestV1) -> ModelResultV1:
        """Return an observed model result; no proposal is applied here."""
        ...
