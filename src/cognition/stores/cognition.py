"""Durable cognition stages; every mutation joins the caller's transaction."""

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from cognition.config.revisions import behavior_hash
from cognition.config.schema import cognition_protocol_version, parse_behavior_config
from cognition.db.models import Event, GovernanceState, Individual, Wake
from cognition.db.models.cognition import (
    AppliedOperation,
    AttentionState,
    CognitionCycle,
    CognitionTurn,
    ContextSnapshot,
    CycleWake,
    ModelInvocation,
)
from cognition.db.models.runtime import RuntimeConfigRevision
from cognition.policy.cognition import validate_decision
from cognition.protocols.cognition_v1 import CurrentFocus
from cognition.protocols.common import JsonObject, Ref, new_id
from cognition.protocols.events_v1 import EventContent, EventEnvelopeV1, EventSource
from cognition.protocols.executive import (
    CognitionDecision,
    IncompatibleExecutiveContract,
    ModelRequest,
    ModelResult,
    parse_decision,
    parse_request,
    validate_request_contract,
)
from cognition.protocols.model_v1 import ModelError
from cognition.protocols.wakes_v1 import WakeV1
from cognition.stores.attention import create_or_merge_pending_wake, load_wake
from cognition.stores.autonomy import record_cycle_outcome
from cognition.stores.evidence import StoredEvent, append_event, load_event
from cognition.stores.personal import (
    apply_personal_operations,
    personal_reference_exists,
    validate_personal_operations,
)
from cognition.stores.reflection import record_reflection_outcome

_PERSONAL_FAMILIES = (
    "goal_operations",
    "commitment_operations",
    "belief_operations",
    "episode_operations",
    "interest_operations",
    "preference_operations",
    "self_model_operations",
    "entity_operations",
    "project_operations",
    "relationship_operations",
    "relationship_thread_operations",
)
_CONTRACT_PERSONAL_FAMILIES = {
    "2.0": frozenset(),
    "3.0": frozenset(_PERSONAL_FAMILIES[:4]),
    "3.1": frozenset(_PERSONAL_FAMILIES[:7]),
    "3.2": frozenset(_PERSONAL_FAMILIES),
}


@dataclass(frozen=True)
class CycleLimits:
    max_turns: int = 3
    max_attempts_per_turn: int = 2
    max_wakes: int = 16
    max_seconds: float = 120
    min_wake_delay_seconds: float = 1

    def __post_init__(self) -> None:
        for value in (self.max_turns, self.max_attempts_per_turn, self.max_wakes):
            if type(value) is not int or value < 1:
                raise ValueError("Cycle limits must be positive integers")
        if (
            not math.isfinite(self.max_seconds)
            or self.max_seconds <= 0
            or not math.isfinite(self.min_wake_delay_seconds)
            or self.min_wake_delay_seconds < 0
        ):
            raise ValueError("Cycle time limits must be finite and nonnegative")


@dataclass(frozen=True)
class CycleRecord:
    cycle_id: UUID
    individual_id: UUID
    status: str
    terminal_reason: str | None
    deadline_at: datetime
    max_turns: int
    max_attempts_per_turn: int
    max_wakes: int
    min_wake_delay_seconds: float


@dataclass(frozen=True)
class TurnRecord:
    turn_id: UUID
    cycle_id: UUID
    ordinal: int
    status: str
    decision: CognitionDecision | None


def canonical_json(value: JsonObject) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def content_hash(value: JsonObject) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _cycle(row: CognitionCycle) -> CycleRecord:
    return CycleRecord(
        row.cycle_id,
        row.individual_id,
        row.status,
        row.terminal_reason,
        row.deadline_at,
        row.max_turns,
        row.max_attempts_per_turn,
        row.max_wakes,
        row.min_wake_delay_seconds,
    )


def load_cycle(session: Session, cycle_id: UUID) -> CycleRecord:
    row = session.get(CognitionCycle, cycle_id, populate_existing=True)
    if row is None:
        raise LookupError("Cognition cycle does not exist")
    return _cycle(row)


def execution_allowed(session: Session, individual_id: UUID) -> bool:
    # All mutation paths lock identity before governance, matching administration.
    individual = session.scalar(
        select(Individual)
        .where(
            Individual.individual_id == individual_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    governance = session.scalar(
        select(GovernanceState)
        .where(
            GovernanceState.individual_id == individual_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if individual is None or governance is None:
        raise LookupError("Individual or governance state does not exist")
    return (
        individual.operational_status == "active" and not governance.inference_blocked
    )


def record_execution_event(
    session: Session,
    individual_id: UUID,
    cycle_id: UUID | None,
    event_type: str,
    now: datetime,
    payload: JsonObject,
    *,
    source_kind: str = "runtime",
) -> UUID:
    envelope = EventEnvelopeV1.model_validate(
        {
            "schema_version": 1,
            "event_id": new_id(),
            "individual_id": individual_id,
            "event_type": event_type,
            "occurred_at": now,
            "observed_at": now,
            "recorded_at": now,
            "source": EventSource.model_validate(
                {
                    "kind": source_kind,
                    "source_id": "cognition",
                    "binding_id": None,
                }
            ),
            "actor_entity_id": None,
            "causation_event_id": None,
            "correlation_id": cycle_id,
            "subject": None if cycle_id is None else Ref(kind="cycle", id=cycle_id),
            "provenance": {"runtime_contract_version": "3.1"},
            "content": EventContent(
                content_type="application/json",
                payload=payload,
                text=None,
                blob_ref=None,
                content_hash=None,
                sensitivity="internal",
                retention_class="history",
                retain_until=None,
            ),
            "runtime_version": "0.1.0",
        }
    )
    append_event(session, envelope)
    return envelope.event_id


def claim_or_resume(
    session: Session,
    individual_id: UUID,
    now: datetime,
    limits: CycleLimits,
) -> CycleRecord | None:
    # Caller checks governance under the same transaction before invoking this.
    _recover_orphan_wakes(session, individual_id, now, limits.max_wakes)
    row = session.scalar(
        select(CognitionCycle)
        .where(
            CognitionCycle.individual_id == individual_id,
            CognitionCycle.status == "active",
        )
        .with_for_update()
    )
    if row is not None:
        return _cycle(row)
    wakes = session.scalars(
        select(Wake)
        .where(
            Wake.individual_id == individual_id,
            Wake.status == "pending",
            Wake.due_at <= now,
        )
        .order_by(Wake.due_at, Wake.wake_id)
        .limit(limits.max_wakes)
        .with_for_update(skip_locked=True)
    ).all()
    if not wakes:
        return None
    row = CognitionCycle(
        cycle_id=new_id(),
        individual_id=individual_id,
        status="active",
        started_at=now,
        completed_at=None,
        terminal_reason=None,
        max_turns=limits.max_turns,
        max_attempts_per_turn=limits.max_attempts_per_turn,
        max_wakes=limits.max_wakes,
        deadline_at=now + timedelta(seconds=limits.max_seconds),
        min_wake_delay_seconds=limits.min_wake_delay_seconds,
        revision=1,
    )
    session.add(row)
    session.flush()
    for wake in wakes:
        wake.status, wake.claimed_at = "claimed", now
        wake.revision += 1
        session.add(CycleWake(cycle_id=row.cycle_id, wake_id=wake.wake_id))
    _new_turn(session, row.cycle_id, 1, now)
    record_execution_event(
        session,
        individual_id,
        row.cycle_id,
        "cognition.cycle_started",
        now,
        {"wake_ids": [str(wake.wake_id) for wake in wakes]},
    )
    session.flush()
    return _cycle(row)


def _recover_orphan_wakes(
    session: Session,
    individual_id: UUID,
    now: datetime,
    limit: int,
) -> None:
    owned = select(CycleWake.wake_id).where(CycleWake.wake_id == Wake.wake_id).exists()
    orphans = session.scalars(
        select(Wake)
        .where(
            Wake.individual_id == individual_id,
            Wake.status == "claimed",
            ~owned,
        )
        .order_by(Wake.due_at, Wake.wake_id)
        .limit(limit)
        .with_for_update()
    ).all()
    for orphan in orphans:
        original = load_wake(session, orphan.wake_id).wake
        orphan.status = "superseded"
        orphan.revision += 1
        replacement = original.model_copy(
            update={
                "wake_id": new_id(),
                "context_refs": [
                    *original.context_refs,
                    Ref(kind="wake", id=original.wake_id),
                ],
            }
        )
        recovered_id = create_or_merge_pending_wake(session, replacement)
        record_execution_event(
            session,
            individual_id,
            None,
            "cognition.orphan_wake_recovered",
            now,
            {
                "original_wake_id": str(original.wake_id),
                "recovered_wake_id": str(recovered_id),
            },
        )
    session.flush()


def _new_turn(session: Session, cycle_id: UUID, ordinal: int, now: datetime) -> None:
    session.add(
        CognitionTurn(
            turn_id=new_id(),
            cycle_id=cycle_id,
            ordinal=ordinal,
            status="prepared",
            created_at=now,
            completed_at=None,
            decision_id=None,
            decision_json=None,
            decision_hash=None,
            validation_errors=[],
            disposition=None,
        )
    )
    session.flush()


def latest_turn(session: Session, cycle_id: UUID) -> TurnRecord:
    row = session.scalar(
        select(CognitionTurn)
        .where(CognitionTurn.cycle_id == cycle_id)
        .order_by(CognitionTurn.ordinal.desc())
        .limit(1)
    )
    if row is None:
        raise LookupError("Cycle has no cognition turn")
    if (
        row.decision_json is not None
        and content_hash(row.decision_json) != row.decision_hash
    ):
        raise ValueError("Persisted decision integrity check failed")
    return TurnRecord(
        row.turn_id,
        row.cycle_id,
        row.ordinal,
        row.status,
        None if row.decision_json is None else parse_decision(row.decision_json),
    )


def context_sources(
    session: Session,
    cycle: CycleRecord,
) -> tuple[list[WakeV1], list[StoredEvent], CurrentFocus | None]:
    ids = session.scalars(
        select(CycleWake.wake_id)
        .where(
            CycleWake.cycle_id == cycle.cycle_id,
        )
        .order_by(CycleWake.wake_id)
    ).all()
    wakes = [load_wake(session, wake_id).wake for wake_id in ids]
    attention = session.get(AttentionState, cycle.individual_id)
    focus = (
        None
        if attention is None or attention.current_focus is None
        else CurrentFocus.model_validate(attention.current_focus)
    )
    referenced_ids = {
        ref.id for wake in wakes for ref in wake.context_refs if ref.kind == "event"
    }
    if focus is not None:
        referenced_ids.update(ref.id for ref in focus.refs if ref.kind == "event")
    referenced = session.scalars(
        select(Event.event_id)
        .where(
            Event.individual_id == cycle.individual_id,
            Event.event_id.in_(referenced_ids),
        )
        .order_by(Event.event_sequence.desc())
        .limit(64)
    ).all()
    recent = session.scalars(
        select(Event.event_id)
        .where(
            Event.individual_id == cycle.individual_id,
        )
        .order_by(Event.event_sequence.desc())
        .limit(32)
    ).all()
    event_ids = list(
        dict.fromkeys(
            [wake.cause_event_id for wake in wakes if wake.cause_event_id is not None]
            + list(referenced)
            + list(recent)
        )
    )
    evidence = [load_event(session, event_id) for event_id in event_ids]
    return wakes, evidence, focus


def load_request(session: Session, turn_id: UUID) -> ModelRequest | None:
    snapshot = session.scalar(
        select(ContextSnapshot).where(ContextSnapshot.turn_id == turn_id)
    )
    if snapshot is None:
        return None
    # Both stored representations must describe the same hashed request.
    digest = hashlib.sha256(snapshot.rendered_context.encode("utf-8")).hexdigest()
    if snapshot.content_hash != digest:
        raise ValueError("Persisted context integrity check failed")
    # Python object equality treats JSON true and 1 as equal; JSON types matter.
    if canonical_json(json.loads(snapshot.rendered_context)) != canonical_json(
        snapshot.request_json
    ):
        raise ValueError("Persisted request does not match its context snapshot")
    try:
        request = parse_request(snapshot.request_json)
    except (ValueError, TypeError) as error:
        raise IncompatibleExecutiveContract("Invalid frozen request") from error
    config = session.get(RuntimeConfigRevision, snapshot.config_revision_id)
    turn = session.get(CognitionTurn, turn_id)
    cycle = None if turn is None else session.get(CognitionCycle, turn.cycle_id)
    if config is None or turn is None or cycle is None:
        raise IncompatibleExecutiveContract(
            "Frozen request lacks configuration or cycle"
        )
    try:
        behavior = parse_behavior_config(config.sanitized_config)
        protocol = cognition_protocol_version(behavior)
        if (
            config.config_schema_version != behavior.config_schema_version
            or config.content_hash != behavior_hash(behavior)
        ):
            raise ValueError("Configuration revision disagrees with its content")
    except (ValueError, TypeError) as error:
        raise IncompatibleExecutiveContract("Invalid frozen configuration") from error
    validate_request_contract(
        request,
        config_schema_version=config.config_schema_version,
        configured_protocol=protocol,
    )
    controls = [
        section
        for section in request.context_sections
        if section.name == "runtime_control"
    ]
    if (
        len(controls) != 1
        or controls[0].category != "control"
        or not isinstance(controls[0].content, dict)
        or controls[0].content.get("config_revision_id")
        != str(config.config_revision_id)
        or controls[0].content.get("config_content_hash") != config.content_hash
    ):
        raise IncompatibleExecutiveContract(
            "Frozen configuration linkage is inconsistent"
        )
    if (
        request.runtime_contract_version != snapshot.runtime_contract_version
        or request.turn_id != turn_id
        or request.cycle_id != cycle.cycle_id
        or request.individual_id != cycle.individual_id
        or config.individual_id != cycle.individual_id
        or snapshot.model_adapter != behavior.model.adapter
        or snapshot.requested_model != behavior.model.requested_model
    ):
        raise IncompatibleExecutiveContract("Frozen request linkage is inconsistent")
    return request


def save_context(
    session: Session,
    *,
    turn_id: UUID,
    config_revision_id: UUID,
    request: ModelRequest,
    rendered_context: str,
    context_hash: str,
    selected_refs: tuple[Ref, ...],
    retrieval_reasons: dict[str, str],
    estimated_input_tokens: int,
    adapter: str,
    requested_model: str,
    now: datetime,
    retain_until: datetime | None,
) -> None:
    session.add(
        ContextSnapshot(
            snapshot_id=new_id(),
            turn_id=turn_id,
            config_revision_id=config_revision_id,
            runtime_contract_version=request.runtime_contract_version,
            model_adapter=adapter,
            requested_model=requested_model,
            request_json=request.model_dump(mode="json"),
            rendered_context=rendered_context,
            content_hash=context_hash,
            selected_refs=[ref.model_dump(mode="json") for ref in selected_refs],
            retrieval_reasons=retrieval_reasons,
            estimated_input_tokens=estimated_input_tokens,
            created_at=now,
            retain_until=retain_until,
        )
    )
    session.flush()


def finish_cycle(
    session: Session,
    cycle_id: UUID,
    now: datetime,
    reason: str,
    *,
    failed: bool = False,
) -> None:
    row = session.get(CognitionCycle, cycle_id)
    if row is None:
        raise LookupError("Cycle does not exist")
    if row.status != "active":
        return
    row.status = "failed" if failed else "completed"
    row.completed_at, row.terminal_reason = now, reason
    row.revision += 1
    wakes = session.scalars(
        select(Wake)
        .join(CycleWake, CycleWake.wake_id == Wake.wake_id)
        .where(CycleWake.cycle_id == cycle_id)
        .with_for_update()
    ).all()
    for wake in wakes:
        if wake.status == "claimed":
            wake.status, wake.consumed_at = "consumed", now
            wake.revision += 1
    record_execution_event(
        session,
        row.individual_id,
        cycle_id,
        "cognition.cycle_finished",
        now,
        {"status": row.status, "reason": reason},
    )
    session.flush()
    record_cycle_outcome(session, cycle_id, now)
    record_reflection_outcome(session, cycle_id, now)


def start_invocation(
    session: Session,
    cycle: CycleRecord,
    turn: TurnRecord,
    now: datetime,
) -> UUID | None:
    # Abandoned inference may be repeated. Previously committed decisions may not.
    if turn.status not in ("prepared", "invoking"):
        raise ValueError("Turn is not eligible for model invocation")
    attempts = session.scalars(
        select(ModelInvocation)
        .where(
            ModelInvocation.turn_id == turn.turn_id,
        )
        .order_by(ModelInvocation.attempt_number)
    ).all()
    for attempt in attempts:
        if attempt.status == "started":
            attempt.status, attempt.completed_at = "abandoned", now
            attempt.error_code = "interrupted"
    row = session.get(CognitionTurn, turn.turn_id)
    assert row is not None
    if len(attempts) >= cycle.max_attempts_per_turn or now >= cycle.deadline_at:
        row.status, row.completed_at = "failed", now
        finish_cycle(
            session,
            cycle.cycle_id,
            now,
            "attempt_limit"
            if len(attempts) >= cycle.max_attempts_per_turn
            else "deadline",
            failed=True,
        )
        return None
    invocation = ModelInvocation(
        invocation_id=new_id(),
        turn_id=turn.turn_id,
        attempt_number=len(attempts) + 1,
        status="started",
        started_at=now,
        completed_at=None,
        result_json=None,
        error_code=None,
    )
    row.status = "invoking"
    session.add(invocation)
    session.flush()
    return invocation.invocation_id


def fail_turn(
    session: Session,
    cycle_id: UUID,
    turn_id: UUID,
    now: datetime,
    reason: str,
) -> None:
    row = session.get(CognitionTurn, turn_id)
    if row is None or row.cycle_id != cycle_id:
        raise LookupError("Cognition turn does not exist")
    row.status, row.completed_at = "failed", now
    finish_cycle(session, cycle_id, now, reason, failed=True)


def record_invocation_failure(
    session: Session,
    invocation_id: UUID,
    now: datetime,
    error_code: str,
) -> None:
    invocation = session.get(ModelInvocation, invocation_id)
    if invocation is None or invocation.status != "started":
        raise ValueError("Invocation is not in progress")
    invocation.status, invocation.completed_at = "failed", now
    invocation.error_code = error_code
    session.flush()


def record_result(
    session: Session,
    cycle: CycleRecord,
    turn: TurnRecord,
    invocation_id: UUID,
    request: ModelRequest,
    result: ModelResult,
    now: datetime,
) -> None:
    invocation = session.get(ModelInvocation, invocation_id)
    row = session.get(CognitionTurn, turn.turn_id)
    if invocation is None or row is None or invocation.status != "started":
        raise ValueError("Invocation is not in progress")
    if result.request_id != request.request_id:
        record_invocation_failure(session, invocation_id, now, "request_id_mismatch")
        return
    if result.schema_version != request.cognition_protocol_version or (
        result.decision is not None
        and result.decision.schema_version != request.cognition_protocol_version
    ):
        record_invocation_failure(session, invocation_id, now, "protocol_mismatch")
        return
    retained_result = result.model_copy(deep=True)
    if retained_result.error is not None:
        # Transport diagnostics may contain credentials, URLs, or response bodies.
        retained_result.error = ModelError(
            code="provider_reported_error",
            message="Provider reported an error",
            retryable=retained_result.error.retryable,
        )
    invocation.result_json = retained_result.model_dump(mode="json")
    invocation.completed_at = now
    if result.status != "completed":
        invocation.status, invocation.error_code = "failed", result.status
        # Refusal/nonretryable failure is terminal, not a reason to pressure the model.
        if (
            result.status == "refused"
            or result.error is None
            or not result.error.retryable
        ):
            row.status, row.completed_at = "failed", now
            finish_cycle(session, cycle.cycle_id, now, result.status, failed=True)
    else:
        decision = result.decision
        assert decision is not None
        duplicate = session.scalar(
            select(CognitionTurn.turn_id).where(
                CognitionTurn.decision_id == decision.decision_id,
            )
        )
        if duplicate is not None:
            invocation.status, invocation.error_code = "failed", "duplicate_decision_id"
            row.status, row.completed_at = "failed", now
            finish_cycle(
                session, cycle.cycle_id, now, "duplicate_decision_id", failed=True
            )
            return
        invocation.status = "completed"
        row.decision_id, row.decision_json = (
            decision.decision_id,
            decision.model_dump(mode="json"),
        )
        row.decision_hash, row.disposition = (
            content_hash(row.decision_json),
            decision.disposition,
        )
        row.status = "decided"
        record_execution_event(
            session,
            cycle.individual_id,
            cycle.cycle_id,
            "cognition.decision_recorded",
            now,
            {
                "decision_id": str(decision.decision_id),
                "turn_id": str(turn.turn_id),
                "decision_hash": row.decision_hash,
            },
            source_kind="model",
        )
    session.flush()


def known_reference(session: Session, individual_id: UUID, ref: Ref) -> bool:
    if ref.kind == "individual":
        return ref.id == individual_id
    if ref.kind == "governance":
        return (
            ref.id == individual_id and session.get(GovernanceState, ref.id) is not None
        )
    if ref.kind == "event":
        return (
            session.scalar(select(Event.individual_id).where(Event.event_id == ref.id))
            == individual_id
        )
    if ref.kind == "wake":
        return (
            session.scalar(select(Wake.individual_id).where(Wake.wake_id == ref.id))
            == individual_id
        )
    if ref.kind == "cycle":
        return (
            session.scalar(
                select(CognitionCycle.individual_id).where(
                    CognitionCycle.cycle_id == ref.id
                )
            )
            == individual_id
        )
    return personal_reference_exists(session, individual_id, ref)


def apply_decision(
    session: Session,
    cycle: CycleRecord,
    turn: TurnRecord,
    now: datetime,
) -> None:
    if turn.status == "applied":
        return
    decision = turn.decision
    if turn.status != "decided" or decision is None:
        raise ValueError("A persisted decision is required before application")
    frozen_request = load_request(session, turn.turn_id)
    if (
        frozen_request is None
        or decision.schema_version != frozen_request.cognition_protocol_version
    ):
        raise IncompatibleExecutiveContract("Decision differs from frozen protocol")
    errors = list(
        validate_decision(
            decision,
            cycle_id=cycle.cycle_id,
            turn_id=turn.turn_id,
            known_ref=lambda ref: known_reference(session, cycle.individual_id, ref),
        )
    )
    personal_operations = {
        family for family in _PERSONAL_FAMILIES if getattr(decision, family, ())
    }
    if personal_operations:
        snapshot = session.scalar(
            select(ContextSnapshot).where(ContextSnapshot.turn_id == turn.turn_id)
        )
        supported = _CONTRACT_PERSONAL_FAMILIES.get(
            "" if snapshot is None else snapshot.runtime_contract_version, frozenset()
        )
        if not personal_operations <= supported:
            errors.append("unsupported_operations_for_frozen_contract")
        errors.extend(
            validate_personal_operations(session, cycle.individual_id, decision, now)
        )
    for request in decision.wake_requests:
        if session.get(AppliedOperation, request.operation_id) is not None:
            errors.append("operation_id_already_applied")
        if session.get(Wake, request.operation_id) is not None:
            errors.append("wake_id_already_exists")
    row = session.get(CognitionTurn, turn.turn_id)
    assert row is not None
    if errors:
        row.status, row.completed_at = "rejected", now
        row.validation_errors = [error for error in errors]
        finish_cycle(session, cycle.cycle_id, now, "decision_rejected", failed=True)
        return
    if personal_operations:
        apply_personal_operations(session, cycle.individual_id, decision, now)
    state = session.get(AttentionState, cycle.individual_id)
    if state is None:
        state = AttentionState(
            individual_id=cycle.individual_id,
            current_focus=None,
            last_cycle_id=None,
            last_cognition_at=None,
            revision=1,
        )
        session.add(state)
        session.flush()
    if decision.current_focus is not None:
        state.current_focus = decision.current_focus.model_dump(mode="json")
    state.last_cycle_id, state.last_cognition_at = cycle.cycle_id, now
    state.revision += 1
    for request in decision.wake_requests:
        due = max(
            request.not_before, now + timedelta(seconds=cycle.min_wake_delay_seconds)
        )
        create_or_merge_pending_wake(
            session,
            WakeV1(
                schema_version=1,
                wake_id=request.operation_id,
                individual_id=cycle.individual_id,
                kind="self_scheduled",
                due_at=due,
                purpose=request.purpose,
                cause_event_id=None,
                context_refs=request.context_refs,
                coalesce_key=request.coalesce_key,
            ),
        )
        session.add(
            AppliedOperation(
                operation_id=request.operation_id,
                turn_id=turn.turn_id,
                individual_id=cycle.individual_id,
                kind="wake_request",
                applied_at=now,
            )
        )
    row.status, row.completed_at = "applied", now
    if decision.disposition != "continue":
        finish_cycle(session, cycle.cycle_id, now, decision.disposition)
    elif turn.ordinal >= cycle.max_turns or now >= cycle.deadline_at:
        finish_cycle(
            session,
            cycle.cycle_id,
            now,
            "turn_limit" if turn.ordinal >= cycle.max_turns else "deadline",
        )
    else:
        _new_turn(session, cycle.cycle_id, turn.ordinal + 1, now)
    session.flush()
