"""Durable action intent with separate execution and verification dimensions."""

from typing import Literal

from cognition.protocols.capabilities_v1 import EffectClass
from cognition.protocols.common import (
    CognitionId,
    JsonObject,
    NonEmptyString,
    ProtocolModel,
    Ref,
    VersionOne,
)

type ExecutionStatus = Literal[
    "proposed",
    "awaiting_approval",
    "authorized",
    "dispatching",
    "completed",
    "failed",
    "unknown",
    "cancelled",
]
type VerificationStatus = Literal[
    "not_applicable", "unverified", "verified", "contradicted"
]


class ActionV1(ProtocolModel):
    """Action facts, not an executor or a source of self-granted authority.

    Effect class is copied from capability/governance metadata. Policy code must
    check it, references, and transitions before authorization or dispatch.
    Execution completion alone does not establish verification.
    """

    schema_version: VersionOne
    action_id: CognitionId
    individual_id: CognitionId
    parent_action_id: CognitionId | None
    capability_key: NonEmptyString
    capability_version: NonEmptyString
    capability_grant_id: CognitionId
    capability_binding_id: CognitionId
    operation: NonEmptyString
    effect_class: EffectClass
    normalized_input: JsonObject
    parameters_hash: NonEmptyString
    intended_effect: NonEmptyString
    verification_criteria: JsonObject
    impetus_refs: list[Ref]
    execution_status: ExecutionStatus
    verification_status: VerificationStatus
    idempotency_key: NonEmptyString | None
