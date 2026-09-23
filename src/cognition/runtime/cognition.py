"""Bounded synchronous cognition with durable stages and recoverable decisions."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from pydantic import JsonValue, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from cognition.db.models.cognition import ContextSnapshot
from cognition.models.base import (
    ExecutiveModel,
    ExecutivePreflight,
    ModelRequestIncompatible,
    ModelUnavailable,
)
from cognition.protocols.common import Clock, new_id, normalize_utc
from cognition.protocols.executive import IncompatibleExecutiveContract, parse_result
from cognition.runtime.context import ContextBudgetExceeded, compile_request
from cognition.runtime.ownership import RuntimeOwnership
from cognition.stores.attention import build_personal_attention
from cognition.stores.autonomy import ensure_heartbeat
from cognition.stores.cognition import (
    CycleLimits,
    apply_decision,
    canonical_json,
    claim_or_resume,
    context_sources,
    execution_allowed,
    fail_turn,
    latest_turn,
    load_cycle,
    load_request,
    record_invocation_failure,
    record_result,
    save_context,
    settle_invocation_budget,
    start_invocation,
)
from cognition.stores.configuration import get_active_config
from cognition.stores.exploration import ensure_exploration
from cognition.stores.exploration_scope import get_cycle_exploration
from cognition.stores.governance import load_governance
from cognition.stores.identity import load_individual
from cognition.stores.lexical import retrieve_lexical_attention
from cognition.stores.reflection import ensure_reflection


@dataclass(frozen=True)
class CognitionRunResult:
    cycle_id: UUID | None
    status: str
    reason: str | None


def _validate_durable_json(value: JsonValue) -> None:
    """Reject JSON strings PostgreSQL cannot retain without changing their meaning."""
    if isinstance(value, str):
        if "\x00" in value:
            raise ValueError("Result contains a nonpersistable string")
        value.encode("utf-8")
    elif isinstance(value, dict):
        for key, child in value.items():
            _validate_durable_json(key)
            _validate_durable_json(child)
    elif isinstance(value, list):
        for child in value:
            _validate_durable_json(child)


class CognitionRuntime:
    """Advance one cycle; external adapters never receive an open transaction."""

    def __init__(
        self,
        owner: RuntimeOwnership,
        individual_id: UUID,
        model: ExecutiveModel | None,
        clock: Clock,
        *,
        limits: CycleLimits | None = None,
        bound_model: tuple[str, str] | None = None,
    ) -> None:
        if owner.individual_id != individual_id:
            raise ValueError("Runtime ownership belongs to another individual")
        self.owner, self.individual_id, self.model, self.clock = (
            owner,
            individual_id,
            model,
            clock,
        )
        self.limits = limits or CycleLimits()
        self.bound_model = bound_model

    @contextmanager
    def _transaction(self, *, coherent: bool = False) -> Iterator[Session]:
        connection = self.owner.connection
        if connection.in_transaction():
            raise RuntimeError("Runtime ownership connection already has a transaction")
        connection.execution_options(
            isolation_level="REPEATABLE READ" if coherent else "READ COMMITTED"
        )
        with (
            Session(bind=connection, expire_on_commit=False) as session,
            session.begin(),
        ):
            yield session

    def run_once(self) -> CognitionRunResult:
        try:
            return self._run_once()
        except IncompatibleExecutiveContract:
            # Stored incompatibility is not permission to resample or rewrite D1.
            with self._transaction() as session:
                from cognition.db.models.cognition import CognitionCycle

                identity = session.scalar(
                    select(CognitionCycle.cycle_id).where(
                        CognitionCycle.individual_id == self.individual_id,
                        CognitionCycle.status == "active",
                    )
                )
            return CognitionRunResult(
                identity, "blocked", "incompatible_executive_contract"
            )

    def _run_once(self) -> CognitionRunResult:
        now = normalize_utc(self.clock.now())
        with self._transaction() as session:
            if not execution_allowed(session, self.individual_id):
                return CognitionRunResult(None, "blocked", "lifecycle_or_governance")
            ensure_heartbeat(session, self.individual_id, now)
            ensure_reflection(session, self.individual_id, now)
            ensure_exploration(session, self.individual_id, now)
            cycle = claim_or_resume(session, self.individual_id, now, self.limits)
        if cycle is None:
            return CognitionRunResult(None, "idle", None)

        while True:
            now = normalize_utc(self.clock.now())
            with self._transaction() as session:
                cycle = load_cycle(session, cycle.cycle_id)
                if cycle.status != "active":
                    return CognitionRunResult(
                        cycle.cycle_id, cycle.status, cycle.terminal_reason
                    )
                if not execution_allowed(session, self.individual_id):
                    return CognitionRunResult(
                        cycle.cycle_id, "blocked", "lifecycle_or_governance"
                    )
                turn = latest_turn(session, cycle.cycle_id)
                if turn.status == "decided":
                    apply_decision(session, cycle, turn, now)
                    continue
                if settle_invocation_budget(session, cycle, turn, now):
                    continue

            # A coherent short snapshot is committed before invocation-start.
            with self._transaction(coherent=True) as session:
                request = load_request(session, turn.turn_id)
                if request is None:
                    config = get_active_config(session, self.individual_id)
                    if config is None:
                        fail_turn(
                            session,
                            cycle.cycle_id,
                            turn.turn_id,
                            now,
                            "missing_configuration",
                        )
                        continue
                    wakes, evidence, focus = context_sources(session, cycle)
                    try:
                        compiled = compile_request(
                            individual=load_individual(session, self.individual_id),
                            governance=load_governance(session, self.individual_id),
                            config=config,
                            wakes=wakes,
                            events=evidence,
                            focus=focus,
                            request_id=new_id(),
                            cycle_id=cycle.cycle_id,
                            turn_id=turn.turn_id,
                            present_time=now,
                            exploration=get_cycle_exploration(session, cycle.cycle_id),
                            attention=build_personal_attention(
                                session,
                                self.individual_id,
                                wakes=wakes,
                                focus=focus,
                                now=now,
                            ),
                            lexical=retrieve_lexical_attention(
                                session,
                                self.individual_id,
                                wakes=wakes,
                                focus=focus,
                            ),
                        )
                    except ContextBudgetExceeded:
                        fail_turn(
                            session, cycle.cycle_id, turn.turn_id, now, "context_budget"
                        )
                        continue
                    save_context(
                        session,
                        turn_id=turn.turn_id,
                        config_revision_id=config.config_revision_id,
                        request=compiled.request,
                        rendered_context=compiled.rendered_context,
                        context_hash=compiled.content_hash,
                        selected_refs=compiled.selected_refs,
                        retrieval_reasons=compiled.retrieval_reasons,
                        estimated_input_tokens=compiled.estimated_input_tokens,
                        adapter=config.sanitized_config.model.adapter,
                        requested_model=config.sanitized_config.model.requested_model,
                        now=now,
                        retain_until=now
                        + timedelta(
                            days=config.sanitized_config.retention.context_snapshot_days,
                        ),
                    )
                    request = load_request(session, turn.turn_id)
                    if request is None:
                        raise IncompatibleExecutiveContract("Missing frozen request")

            with self._transaction() as session:
                if not execution_allowed(session, self.individual_id):
                    return CognitionRunResult(
                        cycle.cycle_id, "blocked", "lifecycle_or_governance"
                    )
                if self.model is None:
                    return CognitionRunResult(
                        cycle.cycle_id, "blocked", "model_unavailable"
                    )
                if self.bound_model is not None:
                    snapshot = session.scalar(
                        select(ContextSnapshot).where(
                            ContextSnapshot.turn_id == turn.turn_id
                        )
                    )
                    if (
                        snapshot is None
                        or (snapshot.model_adapter, snapshot.requested_model)
                        != self.bound_model
                    ):
                        return CognitionRunResult(
                            cycle.cycle_id, "blocked", "model_configuration_mismatch"
                        )
                if isinstance(self.model, ExecutivePreflight):
                    try:
                        self.model.validate_request(request.model_copy(deep=True))
                    except ModelRequestIncompatible:
                        return CognitionRunResult(
                            cycle.cycle_id, "blocked", "model_request_incompatible"
                        )
                    except ModelUnavailable:
                        return CognitionRunResult(
                            cycle.cycle_id, "blocked", "model_unavailable"
                        )
                invocation_id = start_invocation(
                    session, cycle, turn, normalize_utc(self.clock.now())
                )
            if invocation_id is None:
                continue

            try:
                # Pass a detached request so an adapter cannot mutate persisted context.
                observed = self.model.decide(request.model_copy(deep=True))
            except Exception:
                # Process interruptions escape, leaving durable started state.
                with self._transaction() as session:
                    record_invocation_failure(
                        session,
                        invocation_id,
                        normalize_utc(self.clock.now()),
                        "provider_error",
                    )
                continue
            try:
                result = parse_result(observed.model_dump(warnings=False))
                durable_json = result.model_dump(mode="json")
                _validate_durable_json(durable_json)
                canonical_json(durable_json)
            except (ValidationError, AttributeError, TypeError, ValueError):
                with self._transaction() as session:
                    record_invocation_failure(
                        session,
                        invocation_id,
                        normalize_utc(self.clock.now()),
                        "invalid_result",
                    )
                continue
            with self._transaction() as session:
                # A pause in flight defers application, not evidence recording.
                record_result(
                    session,
                    cycle,
                    turn,
                    invocation_id,
                    request,
                    result,
                    normalize_utc(self.clock.now()),
                )
