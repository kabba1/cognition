"""Provider-independent requests and observations of executive model output."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, StrictBool, model_validator

from cognition.protocols.cognition_v1 import CognitionDecisionV1
from cognition.protocols.common import (
    JsonObject,
    NonEmptyString,
    ProtocolModel,
    Ref,
    UTCDateTime,
    VersionOne,
)

NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
type ContextCategory = Literal[
    "control",
    "present",
    "self",
    "commitments",
    "evidence",
    "memory",
    "relationship",
    "capability",
]


class ContextSection(ProtocolModel):
    name: NonEmptyString
    category: ContextCategory
    content: str | JsonObject
    refs: list[Ref]


class EffectiveCapabilityView(ProtocolModel):
    """Nonsecret operation names visible to an executive, not an authority grant."""

    key: NonEmptyString
    version: NonEmptyString
    allowed_operations: list[NonEmptyString]


class ModelRequestV1(ProtocolModel):
    schema_version: VersionOne
    request_id: UUID
    individual_id: UUID
    cycle_id: UUID
    turn_id: UUID
    cognition_protocol_version: VersionOne
    runtime_contract_version: NonEmptyString
    present_time: UTCDateTime
    context_sections: list[ContextSection]
    capabilities: list[EffectiveCapabilityView]
    output_schema: NonEmptyString
    input_token_budget: NonNegativeInt
    output_token_budget: NonNegativeInt
    inference_preferences: JsonObject | None = None


class TokenUsage(ProtocolModel):
    """Unknown usage stays null rather than being reported as zero."""

    input_tokens: NonNegativeInt | None
    output_tokens: NonNegativeInt | None
    total_tokens: NonNegativeInt | None


class ModelError(ProtocolModel):
    code: NonEmptyString
    message: str
    retryable: StrictBool


class ModelResultV1(ProtocolModel):
    schema_version: VersionOne
    status: Literal["completed", "refused", "failed"]
    request_id: UUID
    decision: CognitionDecisionV1 | None
    provider: NonEmptyString
    requested_model: NonEmptyString
    resolved_model: str | None
    provider_request_id: str | None
    usage: TokenUsage | None
    finish_reason: str | None
    error: ModelError | None

    @model_validator(mode="after")
    def decision_matches_status(self) -> Self:
        if self.status == "completed" and self.decision is None:
            raise ValueError("completed results require a decision")
        if self.status != "completed" and self.decision is not None:
            raise ValueError("failed or refused results cannot carry a decision")
        return self
