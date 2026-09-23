"""Provider-independent transport for explicitly selected executive protocol two."""

from typing import Literal, Self
from uuid import UUID

from pydantic import model_validator

from cognition.protocols.cognition_v2 import CognitionDecisionV2
from cognition.protocols.common import (
    JsonObject,
    NonEmptyString,
    ProtocolModel,
    UTCDateTime,
    VersionTwo,
)
from cognition.protocols.model_v1 import (
    ContextSection,
    EffectiveCapabilityView,
    ModelError,
    NonNegativeInt,
    TokenUsage,
)


class ModelRequestV2(ProtocolModel):
    schema_version: VersionTwo
    request_id: UUID
    individual_id: UUID
    cycle_id: UUID
    turn_id: UUID
    cognition_protocol_version: VersionTwo
    runtime_contract_version: NonEmptyString
    present_time: UTCDateTime
    context_sections: list[ContextSection]
    capabilities: list[EffectiveCapabilityView]
    output_schema: NonEmptyString
    input_token_budget: NonNegativeInt
    output_token_budget: NonNegativeInt
    inference_preferences: JsonObject | None = None


class ModelResultV2(ProtocolModel):
    schema_version: VersionTwo
    status: Literal["completed", "refused", "failed"]
    request_id: UUID
    decision: CognitionDecisionV2 | None
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
