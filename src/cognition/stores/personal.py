"""Owned, revisable personal interpretations with transactional provenance."""

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Table, case, select
from sqlalchemy.orm import Session

from cognition.db.base import Base
from cognition.db.models.attention import Wake
from cognition.db.models.cognition import (
    AppliedOperation,
    CognitionCycle,
    CognitionTurn,
)
from cognition.db.models.evidence import Event
from cognition.db.models.governance import GovernanceState
from cognition.db.models.identity import Individual
from cognition.db.models.personal import (
    Belief,
    Commitment,
    Entity,
    Episode,
    Goal,
    PersonalStateRevision,
    Project,
)
from cognition.protocols.cognition_v1 import (
    CognitionDecisionV1,
    GoalStatus,
)
from cognition.protocols.common import JsonObject, Ref, new_id, normalize_utc
from cognition.protocols.events_v1 import EventEnvelopeV1
from cognition.protocols.model_v1 import ContextSection
from cognition.stores.evidence import append_event

_PERSONAL_MODELS = (Entity, Project, Goal, Commitment, Belief, Episode)
_REFERENCE_MODELS: dict[str, type[Base]] = {
    "individual": Individual,
    "governance": GovernanceState,
    "event": Event,
    "wake": Wake,
    "cycle": CognitionCycle,
    "entity": Entity,
    "project": Project,
    "goal": Goal,
    "commitment": Commitment,
    "belief": Belief,
    "episode": Episode,
}
_GOAL_NEXT = {
    "active": {"paused", "blocked", "completed", "abandoned"},
    "paused": {"active", "blocked", "completed", "abandoned"},
    "blocked": {"active", "paused", "completed", "abandoned"},
    "completed": set(),
    "abandoned": set(),
}
_COMMITMENT_NEXT = {
    "proposed": {"active", "released"},
    "active": {"fulfilled", "released", "broken", "disputed"},
    "disputed": {"active", "released", "broken", "fulfilled"},
    "fulfilled": set(),
    "released": set(),
    "broken": set(),
}
_BELIEF_NEXT = {
    "tentative": {"accepted", "disputed", "withdrawn"},
    "accepted": {"tentative", "disputed", "withdrawn"},
    "disputed": {"tentative", "accepted", "withdrawn"},
    "superseded": set(),
    "withdrawn": set(),
}
_ALL_ARRAYS = (
    "goal_operations",
    "commitment_operations",
    "belief_operations",
    "episode_operations",
    "interest_operations",
    "preference_operations",
    "self_model_operations",
    "action_requests",
    "wake_requests",
)


@dataclass
class _Change:
    kind: str
    identity: UUID
    model: type[Base]
    row: Base | None
    values: dict[str, Any]
    before: JsonObject | None


@dataclass
class _PlannedOperation:
    operation_id: UUID
    kind: str
    action: str
    changes: list[_Change]


def _snapshot(row: Base) -> JsonObject:
    values: JsonObject = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        if isinstance(value, UUID):
            value = str(value)
        elif isinstance(value, datetime):
            value = normalize_utc(value).isoformat()
        values[column.name] = value
    return values


def _lock_individual(session: Session, individual_id: UUID) -> None:
    # Refreshing dirty state could erase a caller's uncommitted changes. Refuse it.
    conflicting = (
        *_PERSONAL_MODELS,
        PersonalStateRevision,
        AppliedOperation,
        Individual,
        CognitionTurn,
        CognitionCycle,
    )
    if any(
        isinstance(row, conflicting)
        for row in session.new | session.dirty | session.deleted
    ):
        raise ValueError("unflushed_personal_state")
    with session.no_autoflush:
        individual = session.scalar(
            select(Individual)
            .where(Individual.individual_id == individual_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    if individual is None:
        raise ValueError("unknown_individual")


def personal_reference_exists(session: Session, individual_id: UUID, ref: Ref) -> bool:
    """Resolve a preexisting, owned reference without granting authority."""
    try:
        checked = Ref.model_validate(ref.model_dump(warnings="none"))
    except (ValueError, TypeError):
        return False
    model = _REFERENCE_MODELS.get(checked.kind)
    if model is None:
        return False
    primary = next(iter(cast(Table, model.__table__).primary_key.columns))
    with session.no_autoflush:
        owner = session.scalar(
            select(model.__table__.c.individual_id).where(primary == checked.id)
        )
    return owner == individual_id


def _refs(refs: Sequence[Ref]) -> list[JsonObject]:
    return [ref.model_dump(mode="json") for ref in refs]


def _union_refs(old: list[JsonObject], new: Sequence[Ref]) -> list[JsonObject]:
    result = list(old)
    for ref in _refs(new):
        if ref not in result:
            result.append(ref)
    return result


def _owned_row(
    session: Session, model: type[Base], identity: UUID | None, individual_id: UUID
) -> Base | None:
    if identity is None:
        return None
    row = session.get(model, identity, populate_existing=True)
    return (
        row
        if row is not None and cast(Any, row).individual_id == individual_id
        else None
    )


def _change(
    kind: str,
    model: type[Base],
    identity: UUID,
    row: Base | None,
    values: dict[str, Any],
) -> _Change:
    return _Change(
        kind, identity, model, row, values, None if row is None else _snapshot(row)
    )


def _plan(
    session: Session,
    individual_id: UUID,
    decision: CognitionDecisionV1,
    now: datetime,
) -> tuple[tuple[str, ...], list[_PlannedOperation]]:
    errors: list[str] = []
    plans: list[_PlannedOperation] = []
    status: str
    all_ops = [op for name in _ALL_ARRAYS for op in getattr(decision, name)]
    ids = [op.operation_id for op in all_ops]
    if len(ids) > 64:
        return ("too_many_operations",), []
    if len(set(ids)) != len(ids):
        errors.append("duplicate_operation_id")
    if any(getattr(decision, name) for name in _ALL_ARRAYS[4:8]):
        errors.append("unsupported_operations")
    if (
        ids
        and session.scalar(
            select(AppliedOperation.operation_id)
            .where(AppliedOperation.operation_id.in_(ids))
            .limit(1)
        )
        is not None
    ):
        errors.append("operation_id_already_applied")
    turn_owner = session.execute(
        select(CognitionTurn.cycle_id, CognitionCycle.individual_id)
        .join(CognitionCycle, CognitionCycle.cycle_id == CognitionTurn.cycle_id)
        .where(CognitionTurn.turn_id == decision.turn_id)
    ).first()
    if (
        turn_owner is None
        or turn_owner.cycle_id != decision.cycle_id
        or turn_owner.individual_id != individual_id
    ):
        errors.append("invalid_personal_turn")

    touched: set[tuple[str, UUID]] = set()

    def touch(kind: str, identity: UUID) -> None:
        target = (kind, identity)
        if target in touched:
            errors.append("duplicate_personal_target")
        touched.add(target)

    def references(refs: Iterable[Ref]) -> None:
        if any(
            not personal_reference_exists(session, individual_id, ref) for ref in refs
        ):
            errors.append("unknown_ref")

    def linked(kind: str, identity: UUID | None) -> None:
        if identity is not None:
            references([Ref(kind=kind, id=identity)])

    def base_values(kind: str, identity: UUID) -> dict[str, Any]:
        return {
            f"{kind}_id": identity,
            "individual_id": individual_id,
            "created_at": now,
            "updated_at": now,
            "revision": 1,
        }

    def collision(model: type[Base], identity: UUID) -> None:
        if session.get(model, identity, populate_existing=True) is not None:
            errors.append("personal_id_collision")

    def nonempty(*values: str | None) -> None:
        if any(value is not None and not value.strip() for value in values):
            errors.append("empty_personal_content")

    for goal in decision.goal_operations:
        identity = goal.goal_id or goal.operation_id
        touch("goal", identity)
        references(goal.evidence_refs)
        linked("project", goal.project_id)
        nonempty(goal.title, goal.desired_state)
        goal_row = cast(
            Goal | None, _owned_row(session, Goal, goal.goal_id, individual_id)
        )
        if goal.op == "create":
            collision(Goal, identity)
            status = goal.requested_status or "active"
            if status not in {"active", "paused", "blocked"}:
                errors.append("invalid_goal_transition")
            values = dict(
                base_values("goal", identity),
                title=goal.title,
                desired_state=goal.desired_state,
                project_id=goal.project_id,
                status=status,
                origin=goal.origin or "self_generated",
                rationale=goal.rationale,
                evidence_refs=_refs(goal.evidence_refs),
            )
            change = _change("goal", Goal, identity, None, values)
        else:
            if goal_row is None:
                errors.append("unknown_personal_target")
                continue
            if goal.origin is not None:
                errors.append("incompatible_operation_fields")
            values = {
                "rationale": goal.rationale,
                "evidence_refs": _refs(goal.evidence_refs),
            }
            if goal.op == "set_status":
                if any(
                    value is not None
                    for value in (goal.title, goal.desired_state, goal.project_id)
                ):
                    errors.append("incompatible_operation_fields")
                if goal.requested_status not in _GOAL_NEXT[goal_row.status]:
                    errors.append("invalid_goal_transition")
                values["status"] = goal.requested_status
            else:
                if goal.requested_status is not None:
                    errors.append("incompatible_operation_fields")
                if goal_row.status in {"completed", "abandoned"}:
                    errors.append("invalid_goal_transition")
                for field in ("title", "desired_state", "project_id"):
                    if getattr(goal, field) is not None:
                        values[field] = getattr(goal, field)
            if all(
                getattr(goal_row, field) == value for field, value in values.items()
            ):
                errors.append("no_op_personal_revision")
            values.update(updated_at=now, revision=goal_row.revision + 1)
            change = _change("goal", Goal, identity, goal_row, values)
        plans.append(_PlannedOperation(goal.operation_id, "goal", goal.op, [change]))

    for commitment in decision.commitment_operations:
        identity = commitment.commitment_id or commitment.operation_id
        touch("commitment", identity)
        references(commitment.evidence_refs)
        linked("entity", commitment.counterparty_entity_id)
        nonempty(commitment.title, commitment.terms)
        commitment_row = cast(
            Commitment | None,
            _owned_row(session, Commitment, commitment.commitment_id, individual_id),
        )
        if commitment.op == "create":
            collision(Commitment, identity)
            status = commitment.requested_status or "proposed"
            if status not in {"proposed", "active"}:
                errors.append("invalid_commitment_transition")
            values = dict(
                base_values("commitment", identity),
                title=commitment.title,
                terms=commitment.terms,
                counterparty_entity_id=commitment.counterparty_entity_id,
                due_at=commitment.due_at,
                status=status,
                rationale=commitment.rationale,
                evidence_refs=_refs(commitment.evidence_refs),
            )
            change = _change("commitment", Commitment, identity, None, values)
        else:
            if commitment_row is None:
                errors.append("unknown_personal_target")
                continue
            values = {
                "rationale": commitment.rationale,
                "evidence_refs": _refs(commitment.evidence_refs),
            }
            if commitment.op == "set_status":
                if any(
                    value is not None
                    for value in (
                        commitment.title,
                        commitment.terms,
                        commitment.counterparty_entity_id,
                        commitment.due_at,
                    )
                ):
                    errors.append("incompatible_operation_fields")
                if (
                    commitment.requested_status
                    not in _COMMITMENT_NEXT[commitment_row.status]
                ):
                    errors.append("invalid_commitment_transition")
                values["status"] = commitment.requested_status
            else:
                if commitment.requested_status is not None:
                    errors.append("incompatible_operation_fields")
                if commitment_row.status in {"fulfilled", "released", "broken"}:
                    errors.append("invalid_commitment_transition")
                for field in ("title", "terms", "counterparty_entity_id", "due_at"):
                    if getattr(commitment, field) is not None:
                        values[field] = getattr(commitment, field)
            if all(
                getattr(commitment_row, field) == value
                for field, value in values.items()
            ):
                errors.append("no_op_personal_revision")
            values.update(updated_at=now, revision=commitment_row.revision + 1)
            change = _change("commitment", Commitment, identity, commitment_row, values)
        plans.append(
            _PlannedOperation(
                commitment.operation_id, "commitment", commitment.op, [change]
            )
        )

    for belief in decision.belief_operations:
        identity = belief.belief_id or belief.operation_id
        touch("belief", identity)
        references([*belief.supporting_evidence, *belief.contradicting_evidence])
        linked("entity", belief.subject_entity_id)
        nonempty(belief.proposition)
        belief_row = cast(
            Belief | None, _owned_row(session, Belief, belief.belief_id, individual_id)
        )
        changes: list[_Change] = []
        if belief.op == "set_status":
            if belief_row is None:
                errors.append("unknown_personal_target")
                continue
            if any(
                value is not None
                for value in (
                    belief.proposition,
                    belief.subject_entity_id,
                    belief.topic,
                    belief.supersedes_belief_id,
                )
            ):
                errors.append("incompatible_operation_fields")
            if belief.requested_status not in _BELIEF_NEXT[belief_row.status]:
                errors.append("invalid_belief_transition")
            supporting = _union_refs(
                belief_row.supporting_evidence, belief.supporting_evidence
            )
            contradicting = _union_refs(
                belief_row.contradicting_evidence, belief.contradicting_evidence
            )
            status = belief.requested_status or belief_row.status
            values = dict(
                status=status,
                supporting_evidence=supporting,
                contradicting_evidence=contradicting,
                rationale=belief.rationale,
                updated_at=now,
                revision=belief_row.revision + 1,
            )
            changes.append(_change("belief", Belief, identity, belief_row, values))
        else:
            collision(Belief, identity)
            supporting, contradicting = (
                _refs(belief.supporting_evidence),
                _refs(belief.contradicting_evidence),
            )
            status = belief.requested_status or "tentative"
            if status in {"superseded", "withdrawn"}:
                errors.append("invalid_belief_transition")
            if belief.op == "create" and belief.supersedes_belief_id is not None:
                errors.append("incompatible_operation_fields")
            values = dict(
                base_values("belief", identity),
                proposition=belief.proposition,
                subject_entity_id=belief.subject_entity_id,
                topic=belief.topic,
                status=status,
                supporting_evidence=supporting,
                contradicting_evidence=contradicting,
                supersedes_belief_id=belief.supersedes_belief_id,
                rationale=belief.rationale,
            )
            changes.append(_change("belief", Belief, identity, None, values))
            if belief.op == "supersede":
                assert belief.supersedes_belief_id is not None
                touch("belief", belief.supersedes_belief_id)
                previous = cast(
                    Belief | None,
                    _owned_row(
                        session, Belief, belief.supersedes_belief_id, individual_id
                    ),
                )
                if previous is None:
                    errors.append("unknown_personal_target")
                elif previous.status in {"superseded", "withdrawn"}:
                    errors.append("invalid_belief_transition")
                else:
                    changes.append(
                        _change(
                            "belief",
                            Belief,
                            previous.belief_id,
                            previous,
                            {
                                "status": "superseded",
                                "updated_at": now,
                                "revision": previous.revision + 1,
                            },
                        )
                    )
                if not supporting and not contradicting:
                    errors.append("belief_evidence_required")
        if (
            status == "accepted"
            and not supporting
            or status == "disputed"
            and not contradicting
        ):
            errors.append("belief_evidence_required")
        plans.append(
            _PlannedOperation(belief.operation_id, "belief", belief.op, changes)
        )

    for episode in decision.episode_operations:
        identity = episode.operation_id
        touch("episode", identity)
        collision(Episode, identity)
        references(episode.evidence_refs)
        for entity in episode.entity_refs:
            linked("entity", entity)
        for project in episode.project_refs:
            linked("project", project)
        if not episode.evidence_refs:
            errors.append("episode_evidence_required")
        if any(
            value is not None and value > now
            for value in (episode.starts_at, episode.ends_at)
        ):
            errors.append("episode_future_span")
        values = dict(
            episode_id=identity,
            individual_id=individual_id,
            summary=episode.summary,
            starts_at=episode.starts_at,
            ends_at=episode.ends_at,
            evidence_refs=_refs(episode.evidence_refs),
            entity_refs=[str(value) for value in episode.entity_refs],
            project_refs=[str(value) for value in episode.project_refs],
            salience_factors=list(episode.salience_factors),
            created_at=now,
            revision=1,
        )
        plans.append(
            _PlannedOperation(
                episode.operation_id,
                "episode",
                episode.op,
                [_change("episode", Episode, identity, None, values)],
            )
        )
    return tuple(dict.fromkeys(errors)), plans


def _prepare(
    session: Session, individual_id: UUID, decision: CognitionDecisionV1, now: datetime
) -> tuple[tuple[str, ...], list[_PlannedOperation]]:
    _lock_individual(session, individual_id)
    try:
        checked = CognitionDecisionV1.model_validate(
            decision.model_dump(mode="python", warnings="none")
        )
        now = normalize_utc(now)
    except (TypeError, ValueError):
        return ("invalid_decision",), []
    with session.no_autoflush:
        return _plan(session, individual_id, checked, now)


def validate_personal_operations(
    session: Session, individual_id: UUID, decision: CognitionDecisionV1, now: datetime
) -> tuple[str, ...]:
    """Validate the whole batch under the individual's transaction-scoped lock."""
    return _prepare(session, individual_id, decision, now)[0]


def _write_changes(
    session: Session,
    individual_id: UUID,
    changes: list[_Change],
    now: datetime,
    *,
    action: str,
    operation_id: UUID | None = None,
    turn_id: UUID | None = None,
    cycle_id: UUID | None = None,
) -> None:
    after: list[JsonObject] = []
    for change in changes:
        if change.row is None:
            row = change.model(**change.values)
            session.add(row)
        else:
            row = change.row
            for key, value in change.values.items():
                setattr(row, key, value)
        session.flush()
        after.append(_snapshot(row))
    event_id = new_id()
    payload: JsonObject = {
        "operation_id": None if operation_id is None else str(operation_id),
        "turn_id": None if turn_id is None else str(turn_id),
        "changes": [
            {
                "object_kind": change.kind,
                "object_id": str(change.identity),
                "before": change.before,
                "after": snapshot,
            }
            for change, snapshot in zip(changes, after, strict=True)
        ],
    }
    append_event(
        session,
        EventEnvelopeV1.model_validate(
            {
                "schema_version": 1,
                "event_id": event_id,
                "individual_id": individual_id,
                "event_type": f"personal.{changes[0].kind}.{action}",
                "occurred_at": now,
                "observed_at": now,
                "recorded_at": now,
                "source": {
                    "kind": "model" if operation_id else "runtime",
                    "source_id": "personal_state",
                    "binding_id": None,
                },
                "actor_entity_id": None,
                "causation_event_id": None,
                "correlation_id": cycle_id,
                "subject": {"kind": changes[0].kind, "id": changes[0].identity},
                "provenance": {
                    "interpretation": "model-derived"
                    if operation_id
                    else "store-supplied"
                },
                "content": {
                    "content_type": "application/json",
                    "payload": payload,
                    "text": None,
                    "blob_ref": None,
                    "content_hash": None,
                    "sensitivity": "internal",
                    "retention_class": "history",
                    "retain_until": None,
                },
                "runtime_version": "0.1.0",
            }
        ),
    )
    for index, (change, snapshot) in enumerate(zip(changes, after, strict=True)):
        session.add(
            PersonalStateRevision(
                revision_id=new_id(),
                individual_id=individual_id,
                object_kind=change.kind,
                object_id=change.identity,
                revision=snapshot["revision"],
                operation_id=operation_id if index == 0 else None,
                turn_id=turn_id,
                event_id=event_id,
                before_json=change.before,
                after_json=snapshot,
                created_at=now,
            )
        )
    session.flush()


def apply_personal_operations(
    session: Session, individual_id: UUID, decision: CognitionDecisionV1, now: datetime
) -> None:
    """Revalidate before any write; commit and rollback belong to the caller."""
    errors, plans = _prepare(session, individual_id, decision, now)
    if errors:
        raise ValueError(",".join(errors))
    # Application consumes the exact committed proposal. Validation alone can be
    # prospective, but cannot manufacture an executable turn or replace its D1.
    decision = CognitionDecisionV1.model_validate(
        decision.model_dump(mode="python", warnings="none")
    )
    with session.no_autoflush:
        recorded = (
            session.execute(
                select(
                    CognitionTurn.__table__,
                    CognitionCycle.status.label("cycle_status"),
                    CognitionCycle.individual_id.label("cycle_individual_id"),
                )
                .join(CognitionCycle, CognitionCycle.cycle_id == CognitionTurn.cycle_id)
                .where(CognitionTurn.turn_id == decision.turn_id)
            )
            .mappings()
            .first()
        )
    payload = decision.model_dump(mode="json")
    digest = hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    if (
        recorded is None
        or recorded.status != "decided"
        or recorded.cycle_status != "active"
        or recorded.cycle_individual_id != individual_id
        or recorded.cycle_id != decision.cycle_id
        or recorded.decision_id != decision.decision_id
        or recorded.disposition != decision.disposition
        or recorded.decision_json != payload
        or recorded.decision_hash != digest
    ):
        raise ValueError("unrecorded_personal_decision")
    now = normalize_utc(now)
    for plan in plans:
        session.add(
            AppliedOperation(
                operation_id=plan.operation_id,
                individual_id=individual_id,
                turn_id=decision.turn_id,
                kind=f"{plan.kind}_operation",
                applied_at=now,
            )
        )
        session.flush()
        _write_changes(
            session,
            individual_id,
            plan.changes,
            now,
            action=plan.action,
            operation_id=plan.operation_id,
            turn_id=decision.turn_id,
            cycle_id=decision.cycle_id,
        )


def create_entity(
    session: Session,
    individual_id: UUID,
    *,
    kind: str,
    display_name: str,
    now: datetime,
    entity_id: UUID | None = None,
) -> UUID:
    """Register an observed entity; this conveys no authenticated authority."""
    _lock_individual(session, individual_id)
    now = normalize_utc(now)
    identity = entity_id or new_id()
    with session.no_autoflush:
        if not kind.strip() or not display_name.strip():
            raise ValueError("empty_personal_content")
        if session.get(Entity, identity, populate_existing=True) is not None:
            raise ValueError("personal_id_collision")
    values = dict(
        entity_id=identity,
        individual_id=individual_id,
        kind=kind,
        display_name=display_name,
        created_at=now,
        updated_at=now,
        revision=1,
    )
    _write_changes(
        session,
        individual_id,
        [_change("entity", Entity, identity, None, values)],
        now,
        action="create",
    )
    return identity


def _project_refs(
    session: Session, individual_id: UUID, refs: Sequence[Ref]
) -> list[JsonObject]:
    try:
        checked = [Ref.model_validate(ref.model_dump(warnings="none")) for ref in refs]
    except (TypeError, ValueError):
        raise ValueError("invalid_reference") from None
    if any(
        not personal_reference_exists(session, individual_id, ref) for ref in checked
    ):
        raise ValueError("unknown_ref")
    return _refs(checked)


def create_project(
    session: Session,
    individual_id: UUID,
    *,
    title: str,
    desired_state: str,
    rationale: str,
    evidence_refs: Sequence[Ref],
    now: datetime,
    status: GoalStatus = "active",
    project_id: UUID | None = None,
) -> UUID:
    """Create a substrate project with provenance in the caller's transaction."""
    _lock_individual(session, individual_id)
    now = normalize_utc(now)
    identity = project_id or new_id()
    with session.no_autoflush:
        if not title.strip() or not desired_state.strip():
            raise ValueError("empty_personal_content")
        if status not in {"active", "paused", "blocked"}:
            raise ValueError("invalid_project_transition")
        if session.get(Project, identity, populate_existing=True) is not None:
            raise ValueError("personal_id_collision")
        refs = _project_refs(session, individual_id, evidence_refs)
    values = dict(
        project_id=identity,
        individual_id=individual_id,
        title=title,
        desired_state=desired_state,
        status=status,
        rationale=rationale,
        evidence_refs=refs,
        created_at=now,
        updated_at=now,
        revision=1,
    )
    _write_changes(
        session,
        individual_id,
        [_change("project", Project, identity, None, values)],
        now,
        action="create",
    )
    return identity


def revise_project(
    session: Session,
    individual_id: UUID,
    project_id: UUID,
    *,
    rationale: str,
    evidence_refs: Sequence[Ref],
    now: datetime,
    title: str | None = None,
    desired_state: str | None = None,
    status: GoalStatus | None = None,
) -> None:
    """Revise an owned project without silently clearing nullable inputs."""
    _lock_individual(session, individual_id)
    now = normalize_utc(now)
    with session.no_autoflush:
        row = cast(
            Project | None, _owned_row(session, Project, project_id, individual_id)
        )
        if row is None:
            raise ValueError("unknown_personal_target")
        if (
            row.status in {"completed", "abandoned"}
            or status is not None
            and status not in _GOAL_NEXT[row.status]
        ):
            raise ValueError("invalid_project_transition")
        if any(
            value is not None and not value.strip() for value in (title, desired_state)
        ):
            raise ValueError("empty_personal_content")
        values: dict[str, Any] = {
            "rationale": rationale,
            "evidence_refs": _project_refs(session, individual_id, evidence_refs),
        }
        for field, value in (
            ("title", title),
            ("desired_state", desired_state),
            ("status", status),
        ):
            if value is not None:
                values[field] = value
        if all(getattr(row, field) == value for field, value in values.items()):
            raise ValueError("no_op_personal_revision")
        values.update(updated_at=now, revision=row.revision + 1)
        change = _change("project", Project, project_id, row, values)
    _write_changes(session, individual_id, [change], now, action="revise")


def personal_context_sections(
    session: Session, individual_id: UUID
) -> list[ContextSection]:
    """Read at most eight rows per family; retrieval never increases strength."""
    sections: list[ContextSection] = []
    specs = (
        (Commitment, "commitment", "commitments", {"proposed", "active", "disputed"}),
        (Goal, "goal", "commitments", {"active", "paused", "blocked"}),
        (Belief, "belief", "memory", {"tentative", "accepted", "disputed"}),
        (Episode, "episode", "memory", None),
    )
    with session.no_autoflush:
        for model, kind, category, statuses in specs:
            table = model.__table__
            query = select(table).where(table.c.individual_id == individual_id)
            if statuses is not None:
                query = query.where(table.c.status.in_(statuses))
            if kind in {"goal", "commitment"}:
                query = query.order_by(case((table.c.status == "active", 0), else_=1))
            order_time = table.c.created_at if kind == "episode" else table.c.updated_at
            rows = (
                session.execute(
                    query.order_by(order_time.desc(), table.c[f"{kind}_id"]).limit(8)
                )
                .mappings()
                .all()
            )
            for row in rows:
                # Each object can fit or be dropped independently by the compiler.
                content = {
                    "source": (
                        "model-derived personal interpretations; "
                        "evidence links are provenance, not truth"
                    ),
                    "item": _snapshot(model(**dict(row))),
                }
                sections.append(
                    ContextSection.model_validate(
                        {
                            "name": f"Personal {kind} {row[f'{kind}_id']}",
                            "category": category,
                            "content": content,
                            "refs": [{"kind": kind, "id": row[f"{kind}_id"]}],
                        }
                    )
                )
    return sections
