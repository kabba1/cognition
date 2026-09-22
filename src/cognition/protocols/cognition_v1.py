"""Versioned executive proposals, without state application or authority."""

from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from cognition.protocols.common import (
    JsonObject,
    NonEmptyString,
    ProtocolModel,
    Ref,
    UTCDateTime,
    VersionOne,
)

type GoalStatus = Literal["active", "paused", "blocked", "completed", "abandoned"]
type GoalOrigin = Literal[
    "self_generated",
    "founding_orientation",
    "interest_derived",
    "external_request_adopted",
    "relationship_commitment",
    "project_dependency",
]
type CommitmentStatus = Literal[
    "proposed", "active", "fulfilled", "released", "broken", "disputed"
]
type BeliefStatus = Literal[
    "tentative", "accepted", "disputed", "superseded", "withdrawn"
]
type SalienceFactor = Literal[
    "consequence",
    "novelty",
    "active_commitment",
    "relationship",
    "self_change",
    "unresolved",
]


def _require_fields(**fields: object) -> None:
    for name, value in fields.items():
        if value is None or value == "":
            raise ValueError(f"{name} is required for this operation")


class CurrentFocus(ProtocolModel):
    summary: str
    refs: list[Ref]


class GoalOperation(ProtocolModel):
    operation_id: UUID
    op: Literal["create", "revise", "set_status"]
    goal_id: UUID | None
    title: str | None
    desired_state: str | None
    project_id: UUID | None
    requested_status: GoalStatus | None
    origin: GoalOrigin | None
    rationale: str
    evidence_refs: list[Ref]

    @model_validator(mode="after")
    def coherent_operation(self) -> Self:
        if self.op == "create":
            _require_fields(title=self.title, desired_state=self.desired_state)
        else:
            _require_fields(goal_id=self.goal_id)
        if self.op == "set_status":
            _require_fields(requested_status=self.requested_status)
        return self


class CommitmentOperation(ProtocolModel):
    operation_id: UUID
    op: Literal["create", "revise", "set_status"]
    commitment_id: UUID | None
    counterparty_entity_id: UUID | None
    title: str | None
    terms: str | None
    requested_status: CommitmentStatus | None
    due_at: UTCDateTime | None
    rationale: str
    evidence_refs: list[Ref]

    @model_validator(mode="after")
    def coherent_operation(self) -> Self:
        if self.op == "create":
            _require_fields(title=self.title, terms=self.terms)
        else:
            _require_fields(commitment_id=self.commitment_id)
        if self.op == "set_status":
            _require_fields(requested_status=self.requested_status)
        return self


class BeliefOperation(ProtocolModel):
    operation_id: UUID
    op: Literal["create", "set_status", "supersede"]
    belief_id: UUID | None
    proposition: str | None
    subject_entity_id: UUID | None
    topic: str | None
    requested_status: BeliefStatus | None
    supporting_evidence: list[Ref]
    contradicting_evidence: list[Ref]
    supersedes_belief_id: UUID | None
    rationale: str

    @model_validator(mode="after")
    def coherent_operation(self) -> Self:
        if self.op == "set_status":
            _require_fields(
                belief_id=self.belief_id, requested_status=self.requested_status
            )
        else:
            _require_fields(proposition=self.proposition)
        if self.op == "supersede":
            _require_fields(supersedes_belief_id=self.supersedes_belief_id)
        return self


class EpisodeOperation(ProtocolModel):
    operation_id: UUID
    op: Literal["create"]
    summary: NonEmptyString
    starts_at: UTCDateTime | None
    ends_at: UTCDateTime | None
    evidence_refs: list[Ref]
    entity_refs: list[UUID]
    project_refs: list[UUID]
    salience_factors: list[SalienceFactor] = Field(
        json_schema_extra={"uniqueItems": True}
    )

    @model_validator(mode="after")
    def coherent_episode(self) -> Self:
        if len(self.salience_factors) != len(set(self.salience_factors)):
            raise ValueError("salience_factors must be unique")
        if (
            self.starts_at is not None
            and self.ends_at is not None
            and self.ends_at < self.starts_at
        ):
            raise ValueError("ends_at must not precede starts_at")
        return self


class InterestOperation(ProtocolModel):
    operation_id: UUID
    op: Literal["create_candidate", "establish", "set_dormant", "retire"]
    interest_id: UUID | None
    topic: str | None
    summary: str | None
    evidence_refs: list[Ref]
    rationale: str

    @model_validator(mode="after")
    def coherent_operation(self) -> Self:
        if self.op == "create_candidate":
            _require_fields(topic=self.topic, summary=self.summary)
        else:
            _require_fields(interest_id=self.interest_id)
        return self


class PreferenceOperation(ProtocolModel):
    operation_id: UUID
    op: Literal["create_tentative", "establish", "retire"]
    preference_id: UUID | None
    context: str | None
    statement: str | None
    evidence_refs: list[Ref]
    rationale: str

    @model_validator(mode="after")
    def coherent_operation(self) -> Self:
        if self.op == "create_tentative":
            _require_fields(context=self.context, statement=self.statement)
        else:
            _require_fields(preference_id=self.preference_id)
        return self


class SelfModelOperation(ProtocolModel):
    operation_id: UUID
    layer: Literal["current_identity", "self_belief", "current_value", "narrative"]
    op: Literal["propose_revision"]
    proposed_content: str | JsonObject
    evidence_refs: list[Ref]
    rationale: str


class ActionRequest(ProtocolModel):
    operation_id: UUID
    capability_key: NonEmptyString
    operation: NonEmptyString
    arguments: JsonObject
    intended_effect: NonEmptyString
    verification_expectation: JsonObject
    impetus_refs: list[Ref]
    rationale: str


class WakeRequest(ProtocolModel):
    operation_id: UUID
    not_before: UTCDateTime
    purpose: NonEmptyString
    context_refs: list[Ref]
    coalesce_key: str | None


class CognitionDecisionV1(ProtocolModel):
    schema_version: VersionOne
    decision_id: UUID
    cycle_id: UUID
    turn_id: UUID
    disposition: Literal["continue", "wait", "sleep"]
    rationale_summary: str | None
    current_focus: CurrentFocus | None
    goal_operations: list[GoalOperation]
    commitment_operations: list[CommitmentOperation]
    belief_operations: list[BeliefOperation]
    episode_operations: list[EpisodeOperation]
    interest_operations: list[InterestOperation]
    preference_operations: list[PreferenceOperation]
    self_model_operations: list[SelfModelOperation]
    action_requests: list[ActionRequest]
    wake_requests: list[WakeRequest]
