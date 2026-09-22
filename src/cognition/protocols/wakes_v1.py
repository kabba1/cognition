"""Attention opportunities with durable purpose and context references."""

from typing import Literal
from uuid import UUID

from cognition.protocols.common import (
    NonEmptyString,
    ProtocolModel,
    Ref,
    UTCDateTime,
    VersionOne,
)

type WakeKind = Literal[
    "bootstrap",
    "external_event",
    "action_result",
    "commitment_due",
    "self_scheduled",
    "goal_review",
    "routine",
    "reflection",
    "maintenance",
    "heartbeat",
    "recovery",
]


class WakeV1(ProtocolModel):
    schema_version: VersionOne
    wake_id: UUID
    individual_id: UUID
    kind: WakeKind
    due_at: UTCDateTime
    purpose: NonEmptyString
    cause_event_id: UUID | None
    context_refs: list[Ref]
    coalesce_key: str | None
