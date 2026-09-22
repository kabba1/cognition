"""Versioned capability definitions, narrowing grants, and nonsecret bindings."""

from typing import Literal

from pydantic import StrictBool

from cognition.protocols.common import (
    CognitionId,
    JsonObject,
    NonEmptyString,
    ProtocolModel,
    UTCDateTime,
    VersionOne,
)

type EffectClass = Literal["A", "B", "C", "D"]
type RetrySemantics = Literal[
    "native_idempotency", "reconcilable", "safe_repeat", "unsafe_repeat"
]
type VerificationSemantics = Literal[
    "not_applicable", "provider_receipt", "independently_queryable", "custom"
]
type CancellationSemantics = Literal[
    "pre_dispatch_only", "provider_supported", "compensating_action", "unavailable"
]
type ApprovalMode = Literal["autonomous", "constrained", "per_action", "disabled"]
type BindingStatus = Literal["available", "degraded", "unavailable", "revoked"]


class CapabilityOperationV1(ProtocolModel):
    """Adapter-owned operation metadata; models cannot choose its effect class."""

    name: NonEmptyString
    input_schema: JsonObject
    output_schema: JsonObject
    effect_class: EffectClass
    information_scope: JsonObject
    resource_scope: JsonObject
    retry_semantics: RetrySemantics
    verification_semantics: VerificationSemantics
    cancellation_semantics: CancellationSemantics
    credential_requirements: list[NonEmptyString]
    network_required: StrictBool


class CapabilityDefinitionV1(ProtocolModel):
    """Software's technical limits, distinct from an individual's permission."""

    schema_version: VersionOne
    capability_key: NonEmptyString
    version: NonEmptyString
    display_name: NonEmptyString
    description: NonEmptyString
    adapter_key: NonEmptyString
    adapter_version: NonEmptyString
    operations: list[CapabilityOperationV1]


class CapabilityGrantConstraintsV1(ProtocolModel):
    """Explicit narrowing sections, interpreted by the later policy layer."""

    resource_constraints: JsonObject
    destination_constraints: JsonObject
    rate_constraints: JsonObject
    spend_constraints: JsonObject


class CapabilityGrantV1(ProtocolModel):
    """An individual's permission; definition intersection is a policy concern."""

    schema_version: VersionOne
    grant_id: CognitionId
    individual_id: CognitionId
    capability_key: NonEmptyString
    capability_version: NonEmptyString
    allowed_operations: list[NonEmptyString]
    constraints: CapabilityGrantConstraintsV1
    approval_mode: ApprovalMode
    valid_from: UTCDateTime
    valid_until: UTCDateTime | None


class CapabilityBindingDescriptorV1(ProtocolModel):
    """Public deployment metadata; adapters resolve opaque secret refs privately.

    Callers must supply only nonsecret configuration and identity metadata.
    Arbitrary JSON contents cannot be certified as nonsecret by a shape contract.
    """

    schema_version: VersionOne
    binding_id: CognitionId
    capability_key: NonEmptyString
    capability_version: NonEmptyString
    label: NonEmptyString
    adapter_key: NonEmptyString
    adapter_version: NonEmptyString
    status: BindingStatus
    secret_ref: NonEmptyString | None
    nonsecret_config: JsonObject
    external_identity: JsonObject | None
