"""Experimental policy 1 for gradually established personal interpretations."""

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from cognition.db.models.attention import Wake
from cognition.db.models.cognition import AppliedOperation, CycleWake
from cognition.db.models.development import Interest, Preference, SelfState
from cognition.db.models.evidence import Event
from cognition.db.models.personal import Episode, PersonalStateRevision
from cognition.protocols.cognition_v1 import (
    InterestOperation,
    PreferenceOperation,
)
from cognition.protocols.common import JsonObject, Ref, normalize_utc
from cognition.protocols.executive import CognitionDecision
from cognition.protocols.model_v1 import ContextSection
from cognition.stores.personal_core import (
    PlannedOperation,
    change,
    owned_row,
    refs,
    snapshot,
    union_refs,
)

POLICY_VERSION = 1
_DAY = timedelta(hours=24)
_WEEK = timedelta(days=7)
_ATTENTION_POLICY = (
    "Qualifying attention: a claimed reflection wake, or claimed self_scheduled "
    "wake with this exact target. Schedule using wake_requests.context_refs."
)
_GROUNDING_POLICY: JsonObject = {
    "eligible_sources": [
        "connector",
        "capability",
        "history-linked model goal/commitment choices",
    ],
    "episode_expansion_levels": 1,
    "two_anchor_requirement": (
        "Two owned original events recorded >=24h apart; no future events."
    ),
    "new_anchor_requirement": (
        "An original qualifying event ID absent from prior anchors, supplied now. "
        "Wrapping old evidence in an episode does not make it new."
    ),
    "excluded": (
        "Admin/import/runtime, bookkeeping, beliefs and development-change feedback."
    ),
}
type ReferenceLookup = Callable[[Session, UUID, Ref], bool]


def _anchors(
    session: Session, individual_id: UUID, evidence: Sequence[JsonObject], now: datetime
) -> dict[UUID, datetime]:
    """Resolve one episode level and an explicit allowlist of original events."""
    event_ids: set[UUID] = set()
    for value in evidence:
        ref = Ref.model_validate(value)
        if ref.kind == "event":
            event_ids.add(ref.id)
        elif ref.kind == "episode":
            episode = cast(
                Episode | None, owned_row(session, Episode, ref.id, individual_id)
            )
            if episode is not None:
                for nested_value in episode.evidence_refs:
                    nested = Ref.model_validate(nested_value)
                    if nested.kind == "event":
                        event_ids.add(nested.id)
    if not event_ids:
        return {}
    rows = (
        session.execute(
            select(Event.__table__).where(
                Event.event_id.in_(event_ids),
                Event.individual_id == individual_id,
                Event.recorded_at <= now,
            )
        )
        .mappings()
        .all()
    )
    anchors: dict[UUID, datetime] = {}
    for event in rows:
        if event.event_type.startswith(
            (
                "cognition.",
                "personal.interest.",
                "personal.preference.",
                "personal.self_state.",
                "personal.self_model.",
            )
        ):
            continue
        eligible = event.source_kind in {"connector", "capability"}
        if event.source_kind == "model":
            parts = event.event_type.split(".")
            if (
                len(parts) != 3
                or parts[0] != "personal"
                or parts[1] not in {"goal", "commitment"}
                or parts[2] not in {"create", "revise", "set_status"}
            ):
                continue
            linked_revision = session.scalar(
                select(PersonalStateRevision.revision_id)
                .join(
                    AppliedOperation,
                    AppliedOperation.operation_id == PersonalStateRevision.operation_id,
                )
                .where(
                    PersonalStateRevision.event_id == event.event_id,
                    PersonalStateRevision.individual_id == individual_id,
                    PersonalStateRevision.object_kind == parts[1],
                    PersonalStateRevision.object_id == event.subject_id,
                    PersonalStateRevision.turn_id == AppliedOperation.turn_id,
                    AppliedOperation.individual_id == individual_id,
                    AppliedOperation.kind == f"{parts[1]}_operation",
                )
                .limit(1)
            )
            eligible = linked_revision is not None and event.subject_kind == parts[1]
        if eligible:
            anchors[event.event_id] = normalize_utc(event.recorded_at)
    return anchors


def _separated(anchors: dict[UUID, datetime]) -> bool:
    return len(anchors) >= 2 and max(anchors.values()) - min(anchors.values()) >= _DAY


def _reflection(
    session: Session, individual_id: UUID, cycle_id: UUID, kind: str, identity: UUID
) -> bool:
    wakes = session.execute(
        select(Wake.kind, Wake.context_refs)
        .join(CycleWake, CycleWake.wake_id == Wake.wake_id)
        .where(
            CycleWake.cycle_id == cycle_id,
            Wake.individual_id == individual_id,
            Wake.status == "claimed",
        )
    ).all()
    target = Ref(kind=kind, id=identity)
    for wake in wakes:
        if wake.kind == "reflection":
            return True
        if wake.kind == "self_scheduled":
            for value in wake.context_refs:
                if Ref.model_validate(value) == target:
                    return True
    return False


def plan_development_operations(
    session: Session,
    individual_id: UUID,
    decision: CognitionDecision,
    now: datetime,
    *,
    reference_exists: ReferenceLookup,
    touch: Callable[[str, UUID], None],
) -> tuple[tuple[str, ...], list[PlannedOperation]]:
    """Build plans only; caller holds the individual lock and applies all families."""
    now = normalize_utc(now)
    errors: list[str] = []
    plans: list[PlannedOperation] = []

    def validate_refs(values: Sequence[Ref]) -> None:
        if any(not reference_exists(session, individual_id, value) for value in values):
            errors.append("unknown_ref")

    def rationale(value: str) -> None:
        if not value.strip():
            errors.append("development_rationale_required")

    def deliberate(kind: str, identity: UUID) -> None:
        if not _reflection(session, individual_id, decision.cycle_id, kind, identity):
            errors.append("development_reflection_required")

    def ground(
        old: list[JsonObject], incoming: Sequence[Ref], *, separated: bool, novel: bool
    ) -> None:
        anchors = _anchors(session, individual_id, union_refs(old, incoming), now)
        if separated and not _separated(anchors):
            errors.append("development_grounding_required")
        if novel and not (
            _anchors(session, individual_id, refs(incoming), now).keys()
            - _anchors(session, individual_id, old, now).keys()
        ):
            errors.append("development_new_anchor_required")

    operations: list[InterestOperation | PreferenceOperation] = [
        *decision.interest_operations,
        *decision.preference_operations,
    ]
    for operation in operations:
        is_interest = hasattr(operation, "interest_id")
        kind = "interest" if is_interest else "preference"
        model = Interest if is_interest else Preference
        target_id = cast(UUID | None, getattr(operation, f"{kind}_id"))
        identity = target_id or operation.operation_id
        touch(kind, identity)
        validate_refs(operation.evidence_refs)
        rationale(operation.rationale)
        fields = ("topic", "summary") if is_interest else ("context", "statement")
        is_create = operation.op in {"create_candidate", "create_tentative"}
        row = cast(
            Interest | Preference | None,
            owned_row(session, model, target_id, individual_id),
        )
        if is_create:
            if session.get(model, identity, populate_existing=True) is not None:
                errors.append("personal_id_collision")
            if any(
                not str(getattr(operation, field) or "").strip() for field in fields
            ):
                errors.append("empty_personal_content")
            values: dict[str, Any] = {
                f"{kind}_id": identity,
                "individual_id": individual_id,
                "status": "candidate" if is_interest else "tentative",
                "rationale": operation.rationale,
                "evidence_refs": refs(operation.evidence_refs),
                "promotion_not_before": now + _DAY,
                "retirement_not_before": None,
                "created_at": now,
                "updated_at": now,
                "revision": 1,
                **{field: getattr(operation, field) for field in fields},
            }
            planned = change(kind, model, identity, None, values)
        else:
            if any(getattr(operation, field) is not None for field in fields):
                errors.append("incompatible_operation_fields")
            if row is None:
                errors.append("unknown_personal_target")
                continue
            values = {
                "rationale": operation.rationale,
                "evidence_refs": union_refs(row.evidence_refs, operation.evidence_refs),
                "updated_at": now,
                "revision": row.revision + 1,
            }
            initial = "candidate" if is_interest else "tentative"
            if operation.op == "establish":
                if row.status == initial:
                    if now < row.promotion_not_before:
                        errors.append("development_not_yet_eligible")
                    deliberate(kind, identity)
                    ground(
                        row.evidence_refs,
                        operation.evidence_refs,
                        separated=True,
                        novel=False,
                    )
                elif is_interest and row.status == "dormant":
                    deliberate(kind, identity)
                    ground(
                        row.evidence_refs,
                        operation.evidence_refs,
                        separated=False,
                        novel=True,
                    )
                else:
                    errors.append("invalid_development_transition")
                values.update(
                    status="established",
                    retirement_not_before=None if is_interest else now + _WEEK,
                )
            elif operation.op == "set_dormant":
                if not is_interest or row.status != "established":
                    errors.append("invalid_development_transition")
                if not operation.evidence_refs:
                    errors.append("development_evidence_required")
                values.update(status="dormant", retirement_not_before=now + _WEEK)
            else:  # Both protocols restrict this branch to retire.
                if row.status != initial:
                    expected = "dormant" if is_interest else "established"
                    if row.status != expected:
                        errors.append("invalid_development_transition")
                    if (
                        row.retirement_not_before is None
                        or now < row.retirement_not_before
                    ):
                        errors.append("development_not_yet_eligible")
                    deliberate(kind, identity)
                    ground(
                        row.evidence_refs,
                        operation.evidence_refs,
                        separated=not is_interest,
                        novel=True,
                    )
                values["status"] = "retired"
            planned = change(kind, model, identity, row, values)
        plans.append(
            PlannedOperation(operation.operation_id, kind, operation.op, [planned])
        )

    layers: set[str] = set()
    for self_op in decision.self_model_operations:
        if self_op.layer in layers:
            errors.append("duplicate_self_layer")
        layers.add(self_op.layer)
        rationale(self_op.rationale)
        validate_refs(self_op.evidence_refs)
        proposed = self_op.proposed_content
        if not proposed or isinstance(proposed, str) and not proposed.strip():
            errors.append("empty_personal_content")
        row_self = session.scalar(
            select(SelfState)
            .where(
                SelfState.individual_id == individual_id,
                SelfState.layer == self_op.layer,
            )
            .execution_options(populate_existing=True)
        )
        identity = self_op.operation_id if row_self is None else row_self.self_state_id
        touch("self_state", identity)
        content: JsonObject = {"value": proposed}
        if row_self is None:
            if session.get(SelfState, identity, populate_existing=True) is not None:
                errors.append("personal_id_collision")
            values = dict(
                self_state_id=identity,
                individual_id=individual_id,
                layer=self_op.layer,
                content=None,
                evidence_refs=[],
                pending_content=None,
                pending_evidence_refs=[],
                pending_not_before=None,
                created_at=now,
                updated_at=now,
                revision=1,
                rationale=self_op.rationale,
            )
        else:
            values = dict(
                updated_at=now,
                revision=row_self.revision + 1,
                rationale=self_op.rationale,
            )
        if self_op.layer in {"current_identity", "narrative"}:
            if self_op.layer == "narrative" and not self_op.evidence_refs:
                errors.append("development_evidence_required")
            if (
                row_self is not None
                and row_self.content == content
                and row_self.pending_content is None
            ):
                errors.append("no_op_personal_revision")
            values.update(
                content=content,
                evidence_refs=refs(self_op.evidence_refs),
                pending_content=None,
                pending_evidence_refs=[],
                pending_not_before=None,
            )
        elif row_self is not None and row_self.pending_content == content:
            assert row_self.pending_not_before is not None
            combined = union_refs(row_self.pending_evidence_refs, self_op.evidence_refs)
            if now < row_self.pending_not_before:
                if combined == row_self.pending_evidence_refs:
                    errors.append("no_op_personal_revision")
                values["pending_evidence_refs"] = combined
            else:
                deliberate("self_state", identity)
                ground(
                    row_self.pending_evidence_refs,
                    self_op.evidence_refs,
                    separated=True,
                    novel=False,
                )
                values.update(
                    content=content,
                    evidence_refs=combined,
                    pending_content=None,
                    pending_evidence_refs=[],
                    pending_not_before=None,
                )
        else:
            if (
                row_self is not None
                and row_self.content == content
                and row_self.pending_content is None
            ):
                errors.append("no_op_personal_revision")
            # A new claim gets its own anchors; abandoned pending anchors remain
            # in history, never silently become evidence for replacement content.
            values.update(
                pending_content=content,
                pending_evidence_refs=refs(self_op.evidence_refs),
                pending_not_before=now + _DAY,
            )
        plans.append(
            PlannedOperation(
                self_op.operation_id,
                "self_state",
                self_op.op,
                [change("self_state", SelfState, identity, row_self, values)],
            )
        )
    return tuple(dict.fromkeys(errors)), plans


def _transition_policy(kind: str, state: str) -> JsonObject:
    if kind == "self_state":
        if state == "current_identity":
            return {
                "new_proposal_delay_hours": 0,
                "guidance": (
                    "Immediate deliberate presentation; "
                    "cannot alter genesis or authority."
                ),
            }
        if state == "narrative":
            return {
                "new_proposal_delay_hours": 0,
                "guidance": (
                    "Immediate subjective narrative; requires an owned evidence ref."
                ),
            }
        return {
            "new_proposal_delay_hours": 24,
            "guidance": (
                "Different content stages pending while current remains unchanged. "
                "Repeat matching pending content after pending_not_before with "
                "qualifying attention and two separated anchors to promote. "
                "Early repeats may add distinct refs without resetting eligibility. "
                "Replacement content starts its own evidence set and deadline."
            ),
        }
    if state in {"candidate", "tentative"}:
        return {
            "new_proposal_delay_hours": 24,
            "guidance": (
                "Establish at promotion_not_before with qualifying attention and "
                "two separated anchors. Tentative retirement needs no time gate."
            ),
        }
    if kind == "interest" and state == "established":
        return {
            "new_proposal_delay_hours": 0,
            "guidance": (
                "No periodic renewal is required. set_dormant needs evidence-linked "
                "rationale and starts the seven-day retirement gate. "
                "Cannot retire directly."
            ),
        }
    return {
        "new_proposal_delay_hours": 0,
        "new_anchor_required_for": ["establish", "retire"]
        if kind == "interest"
        else ["retire"],
        "guidance": (
            "Dormant interest re-establishment needs qualifying attention and a new "
            "anchor, without a new age gate. Retirement needs retirement_not_before, "
            "qualifying attention and a new anchor."
            if kind == "interest"
            else "Established preference retirement needs retirement_not_before, "
            "qualifying attention, two separated anchors, and at least one new anchor."
        ),
    }


def development_context_sections(
    session: Session, individual_id: UUID
) -> list[ContextSection]:
    """Current and pending content retain distinct labels; retrieval has no effects."""
    sections: list[ContextSection] = []
    with session.no_autoflush:
        for model, kind, limit in (
            (Interest, "interest", 8),
            (Preference, "preference", 8),
            (SelfState, "self_state", 4),
        ):
            table = model.__table__
            query = select(table).where(table.c.individual_id == individual_id)
            if kind != "self_state":
                query = query.where(table.c.status != "retired").order_by(
                    case((table.c.status == "established", 0), else_=1)
                )
            rows = (
                session.execute(
                    query.order_by(
                        table.c.updated_at.desc(), table.c[f"{kind}_id"]
                    ).limit(limit)
                )
                .mappings()
                .all()
            )
            for row in rows:
                target = {"kind": kind, "id": str(row[f"{kind}_id"])}
                sections.append(
                    ContextSection.model_validate(
                        {
                            "name": f"Personal {kind} {row[f'{kind}_id']}",
                            "category": "self",
                            "content": {
                                "source": (
                                    "model-derived interpretation; pending content is "
                                    "not established current content"
                                ),
                                "development_policy_version": POLICY_VERSION,
                                "policy": _transition_policy(
                                    kind,
                                    row.layer if kind == "self_state" else row.status,
                                ),
                                "attention_policy": _ATTENTION_POLICY,
                                "self_scheduled_context_refs": [target],
                                "grounding_policy": _GROUNDING_POLICY,
                                "item": snapshot(model(**dict(row))),
                            },
                            "refs": [target],
                        }
                    )
                )
    return sections
