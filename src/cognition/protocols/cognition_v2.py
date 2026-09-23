"""Explicit social and project proposals, preserving the v1 operation contracts."""

from typing import Literal, Self
from uuid import UUID

from pydantic import model_validator

from cognition.protocols.cognition_v1 import (
    ActionRequest,
    BeliefOperation,
    CommitmentOperation,
    CurrentFocus,
    EpisodeOperation,
    GoalOperation,
    GoalStatus,
    InterestOperation,
    PreferenceOperation,
    SelfModelOperation,
    WakeRequest,
)
from cognition.protocols.common import ProtocolModel, Ref, VersionTwo

type RelationshipThreadStatus = Literal["open", "resolved", "abandoned"]


def _require_fields(**fields: object) -> None:
    for name, value in fields.items():
        if value is None or value == "":
            raise ValueError(f"{name} is required for this operation")


class EntityOperation(ProtocolModel):
    operation_id: UUID
    op: Literal["create", "revise"]
    entity_id: UUID | None
    kind: str | None
    display_name: str | None
    evidence_refs: list[Ref]
    rationale: str

    @model_validator(mode="after")
    def coherent_operation(self) -> Self:
        if self.op == "create":
            _require_fields(kind=self.kind, display_name=self.display_name)
        else:
            _require_fields(entity_id=self.entity_id)
        return self


class ProjectOperation(ProtocolModel):
    operation_id: UUID
    op: Literal["create", "revise", "set_status"]
    project_id: UUID | None
    title: str | None
    desired_state: str | None
    requested_status: GoalStatus | None
    evidence_refs: list[Ref]
    rationale: str

    @model_validator(mode="after")
    def coherent_operation(self) -> Self:
        if self.op == "create":
            _require_fields(title=self.title, desired_state=self.desired_state)
        else:
            _require_fields(project_id=self.project_id)
        if self.op == "set_status":
            _require_fields(requested_status=self.requested_status)
        return self


class RelationshipOperation(ProtocolModel):
    operation_id: UUID
    op: Literal["create", "revise"]
    relationship_id: UUID | None
    entity_id: UUID | None
    narrative: str | None
    evidence_refs: list[Ref]
    rationale: str

    @model_validator(mode="after")
    def coherent_operation(self) -> Self:
        if self.op == "create":
            _require_fields(entity_id=self.entity_id, narrative=self.narrative)
        else:
            _require_fields(relationship_id=self.relationship_id)
        return self


class RelationshipThreadOperation(ProtocolModel):
    operation_id: UUID
    op: Literal["create", "revise", "set_status"]
    thread_id: UUID | None
    relationship_id: UUID | None
    title: str | None
    summary: str | None
    commitment_id: UUID | None
    requested_status: RelationshipThreadStatus | None
    evidence_refs: list[Ref]
    rationale: str

    @model_validator(mode="after")
    def coherent_operation(self) -> Self:
        if self.op == "create":
            _require_fields(
                relationship_id=self.relationship_id,
                title=self.title,
                summary=self.summary,
            )
        else:
            _require_fields(thread_id=self.thread_id)
        if self.op == "set_status":
            _require_fields(requested_status=self.requested_status)
        return self


class CognitionDecisionV2(ProtocolModel):
    schema_version: VersionTwo
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
    entity_operations: list[EntityOperation]
    project_operations: list[ProjectOperation]
    relationship_operations: list[RelationshipOperation]
    relationship_thread_operations: list[RelationshipThreadOperation]
    action_requests: list[ActionRequest]
    wake_requests: list[WakeRequest]
