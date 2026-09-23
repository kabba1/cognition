"""Detached v2 executive plans; validation neither writes nor grants authority."""

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from cognition.db.base import Base
from cognition.db.models.personal import Entity, Project
from cognition.db.models.relationships import Relationship, RelationshipThread
from cognition.protocols.cognition_v2 import CognitionDecisionV2
from cognition.protocols.common import Ref
from cognition.stores.personal_core import PlannedOperation, change, owned_row, refs

EXECUTIVE_ARRAYS = (
    "entity_operations",
    "project_operations",
    "relationship_operations",
    "relationship_thread_operations",
)
_PROJECT_NEXT = {
    "active": {"paused", "blocked", "completed", "abandoned"},
    "paused": {"active", "blocked", "completed", "abandoned"},
    "blocked": {"active", "paused", "completed", "abandoned"},
    "completed": set(),
    "abandoned": set(),
}


def plan_executive_operations(
    session: Session,
    individual_id: UUID,
    decision: CognitionDecisionV2,
    now: datetime,
    *,
    reference_exists: Callable[[Session, UUID, Ref], bool],
    touch: Callable[[str, UUID], None],
) -> tuple[tuple[str, ...], list[PlannedOperation]]:
    """Validate only preexisting owned objects and return unexecuted changes."""
    errors: list[str] = []
    plans: list[PlannedOperation] = []

    def references(values: Iterable[Ref]) -> None:
        if any(not reference_exists(session, individual_id, ref) for ref in values):
            errors.append("unknown_ref")

    def linked(kind: str, identity: UUID | None) -> None:
        if identity is not None:
            references([Ref(kind=kind, id=identity)])

    def nonempty(*values: str | None) -> None:
        if any(value is not None and not value.strip() for value in values):
            errors.append("empty_personal_content")

    def incompatible(*values: object) -> None:
        if any(value is not None for value in values):
            errors.append("incompatible_operation_fields")

    def collision(model: type[Base], identity: UUID) -> None:
        if session.get(model, identity, populate_existing=True) is not None:
            errors.append("personal_id_collision")

    def initial(kind: str, identity: UUID) -> dict[str, Any]:
        return {
            "thread_id" if kind == "relationship_thread" else f"{kind}_id": identity,
            "individual_id": individual_id,
            "created_at": now,
            "updated_at": now,
            "revision": 1,
        }

    def revised(row: Any, values: dict[str, Any]) -> dict[str, Any]:
        if now < row.updated_at:
            errors.append("retrograde_personal_revision")
        if all(getattr(row, field) == value for field, value in values.items()):
            errors.append("no_op_personal_revision")
        return {**values, "updated_at": now, "revision": row.revision + 1}

    def relationship_parent(identity: UUID | None) -> None:
        linked("relationship", identity)
        row = cast(
            Relationship | None,
            owned_row(session, Relationship, identity, individual_id),
        )
        if row is not None:
            linked("entity", row.entity_id)

    for entity in decision.entity_operations:
        identity = entity.entity_id or entity.operation_id
        touch("entity", identity)
        references(entity.evidence_refs)
        nonempty(entity.kind, entity.display_name, entity.rationale)
        entity_row = cast(
            Entity | None, owned_row(session, Entity, entity.entity_id, individual_id)
        )
        if entity.op == "create":
            collision(Entity, identity)
            values = dict(
                initial("entity", identity),
                kind=entity.kind,
                display_name=entity.display_name,
            )
            entity_row = None
        else:
            if entity_row is None:
                errors.append("unknown_personal_target")
                continue
            incompatible(entity.kind)
            values = revised(
                entity_row,
                {"display_name": entity.display_name}
                if entity.display_name is not None
                else {},
            )
        plans.append(
            PlannedOperation(
                entity.operation_id,
                "entity",
                entity.op,
                [change("entity", Entity, identity, entity_row, values)],
            )
        )

    for project in decision.project_operations:
        identity = project.project_id or project.operation_id
        touch("project", identity)
        references(project.evidence_refs)
        nonempty(project.title, project.desired_state, project.rationale)
        project_row = cast(
            Project | None,
            owned_row(session, Project, project.project_id, individual_id),
        )
        if project.op == "create":
            collision(Project, identity)
            status = project.requested_status or "active"
            if status not in {"active", "paused", "blocked"}:
                errors.append("invalid_project_transition")
            values = dict(
                initial("project", identity),
                title=project.title,
                desired_state=project.desired_state,
                status=status,
                rationale=project.rationale,
                evidence_refs=refs(project.evidence_refs),
            )
            project_row = None
        else:
            if project_row is None:
                errors.append("unknown_personal_target")
                continue
            values = {
                "rationale": project.rationale,
                "evidence_refs": refs(project.evidence_refs),
            }
            if project.op == "set_status":
                incompatible(project.title, project.desired_state)
                if project.requested_status not in _PROJECT_NEXT[project_row.status]:
                    errors.append("invalid_project_transition")
                values["status"] = project.requested_status
            else:
                incompatible(project.requested_status)
                if project_row.status in {"completed", "abandoned"}:
                    errors.append("invalid_project_transition")
                for field in ("title", "desired_state"):
                    if getattr(project, field) is not None:
                        values[field] = getattr(project, field)
            values = revised(project_row, values)
        plans.append(
            PlannedOperation(
                project.operation_id,
                "project",
                project.op,
                [change("project", Project, identity, project_row, values)],
            )
        )

    relationship_entities: set[UUID] = set()
    for relationship in decision.relationship_operations:
        identity = relationship.relationship_id or relationship.operation_id
        touch("relationship", identity)
        references(relationship.evidence_refs)
        if not relationship.evidence_refs:
            errors.append("missing_relationship_evidence")
        nonempty(relationship.narrative, relationship.rationale)
        relationship_row = cast(
            Relationship | None,
            owned_row(
                session, Relationship, relationship.relationship_id, individual_id
            ),
        )
        if relationship.op == "create":
            collision(Relationship, identity)
            linked("entity", relationship.entity_id)
            assert relationship.entity_id is not None
            if (
                relationship.entity_id in relationship_entities
                or session.scalar(
                    select(Relationship.relationship_id).where(
                        Relationship.individual_id == individual_id,
                        Relationship.entity_id == relationship.entity_id,
                    )
                )
                is not None
            ):
                errors.append("relationship_already_exists")
            relationship_entities.add(relationship.entity_id)
            values = dict(
                initial("relationship", identity),
                entity_id=relationship.entity_id,
                narrative=relationship.narrative,
                rationale=relationship.rationale,
                evidence_refs=refs(relationship.evidence_refs),
            )
            relationship_row = None
        else:
            if relationship_row is None:
                errors.append("unknown_personal_target")
                continue
            incompatible(relationship.entity_id)
            linked("entity", relationship_row.entity_id)
            values = {
                "rationale": relationship.rationale,
                "evidence_refs": refs(relationship.evidence_refs),
            }
            if relationship.narrative is not None:
                values["narrative"] = relationship.narrative
            values = revised(relationship_row, values)
        plans.append(
            PlannedOperation(
                relationship.operation_id,
                "relationship",
                relationship.op,
                [
                    change(
                        "relationship", Relationship, identity, relationship_row, values
                    )
                ],
            )
        )

    for thread in decision.relationship_thread_operations:
        identity = thread.thread_id or thread.operation_id
        touch("relationship_thread", identity)
        references(thread.evidence_refs)
        if not thread.evidence_refs:
            errors.append("missing_relationship_evidence")
        nonempty(thread.title, thread.summary, thread.rationale)
        linked("commitment", thread.commitment_id)
        thread_row = cast(
            RelationshipThread | None,
            owned_row(session, RelationshipThread, thread.thread_id, individual_id),
        )
        if thread.op == "create":
            collision(RelationshipThread, identity)
            relationship_parent(thread.relationship_id)
            if thread.requested_status not in {None, "open"}:
                errors.append("invalid_relationship_thread_transition")
            values = dict(
                initial("relationship_thread", identity),
                relationship_id=thread.relationship_id,
                title=thread.title,
                summary=thread.summary,
                commitment_id=thread.commitment_id,
                status="open",
                rationale=thread.rationale,
                evidence_refs=refs(thread.evidence_refs),
            )
            thread_row = None
        else:
            if thread_row is None:
                errors.append("unknown_personal_target")
                continue
            incompatible(thread.relationship_id)
            relationship_parent(thread_row.relationship_id)
            linked("commitment", thread.commitment_id or thread_row.commitment_id)
            if thread_row.status != "open":
                errors.append("invalid_relationship_thread_transition")
            values = {
                "rationale": thread.rationale,
                "evidence_refs": refs(thread.evidence_refs),
            }
            if thread.op == "set_status":
                incompatible(thread.title, thread.summary, thread.commitment_id)
                if thread.requested_status not in {"resolved", "abandoned"}:
                    errors.append("invalid_relationship_thread_transition")
                values["status"] = thread.requested_status
            else:
                incompatible(thread.requested_status)
                for field in ("title", "summary", "commitment_id"):
                    if getattr(thread, field) is not None:
                        values[field] = getattr(thread, field)
            values = revised(thread_row, values)
        plans.append(
            PlannedOperation(
                thread.operation_id,
                "relationship_thread",
                thread.op,
                [
                    change(
                        "relationship_thread",
                        RelationshipThread,
                        identity,
                        thread_row,
                        values,
                    )
                ],
            )
        )
    return tuple(dict.fromkeys(errors)), plans
