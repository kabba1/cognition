"""Evidence envelopes record provenance independently of interpretation."""

from typing import Literal
from uuid import UUID

from cognition.protocols.common import (
    JsonObject,
    NonEmptyString,
    ProtocolModel,
    Ref,
    UTCDateTime,
    VersionOne,
)

type SourceKind = Literal[
    "runtime", "model", "connector", "capability", "admin", "import"
]
type Sensitivity = Literal["public", "internal", "sensitive"]
type RetentionClass = Literal["history", "standard", "ephemeral"]


class EventSource(ProtocolModel):
    kind: SourceKind
    source_id: str | None
    binding_id: UUID | None


class EventContent(ProtocolModel):
    content_type: NonEmptyString
    payload: JsonObject | None
    text: str | None
    blob_ref: str | None
    content_hash: str | None
    sensitivity: Sensitivity
    retention_class: RetentionClass
    retain_until: UTCDateTime | None


class EventEnvelopeV1(ProtocolModel):
    schema_version: VersionOne
    event_id: UUID
    individual_id: UUID
    event_type: NonEmptyString
    occurred_at: UTCDateTime | None
    observed_at: UTCDateTime
    recorded_at: UTCDateTime
    source: EventSource
    actor_entity_id: UUID | None
    causation_event_id: UUID | None
    correlation_id: UUID | None
    subject: Ref | None
    provenance: JsonObject
    content: EventContent
    runtime_version: NonEmptyString
