"""Connector observations carry authentication metadata, not authority."""

from uuid import UUID

from cognition.protocols.common import (
    JsonObject,
    NonEmptyString,
    ProtocolModel,
    VersionOne,
)
from cognition.protocols.events_v1 import EventEnvelopeV1


class ObservationAuthentication(ProtocolModel):
    mechanism: str | None
    authenticated_actor: str | None
    assertions: JsonObject


class ObservationV1(ProtocolModel):
    schema_version: VersionOne
    event: EventEnvelopeV1
    connector_binding_id: UUID
    external_event_id: str | None
    dedup_key: NonEmptyString
    external_content_type: str | None
    raw_content_hash: str | None
    authentication: ObservationAuthentication
