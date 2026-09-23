"""Already-spent durable budgets settle before requiring a provider or context."""

from datetime import timedelta

import pytest
import test_lexical_recall
from sqlalchemy import func, select

from cognition.db.models.cognition import ContextSnapshot, ModelInvocation
from cognition.models.base import ModelUnavailable
from cognition.stores.cognition import CycleLimits, claim_or_resume

clock = test_lexical_recall.clock
state = test_lexical_recall.state
run = test_lexical_recall.run


class FiniteFailingProvider:
    def __init__(self, available):
        self.available = available
        self.calls = 0

    def validate_request(self, request):
        if self.calls >= self.available:
            raise ModelUnavailable("No remaining provider result")

    def decide(self, request):
        self.calls += 1
        raise RuntimeError("Deterministic provider failure")


class ProcessInterrupted(BaseException):
    pass


class InterruptedProvider:
    def decide(self, request):
        raise ProcessInterrupted()


def test_exhausted_attempt_budget_finishes_before_adapter_preflight(
    db_engine, db_session_factory, state, clock
):
    provider = FiniteFailingProvider(2)
    result = run(db_engine, state, clock, provider)
    assert (result.status, result.reason) == ("failed", "attempt_limit")
    assert provider.calls == 2
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ModelInvocation)) == 2


def test_unspent_attempt_budget_preserves_no_attempt_preflight_block(
    db_engine, db_session_factory, state, clock
):
    provider = FiniteFailingProvider(1)
    result = run(db_engine, state, clock, provider)
    assert (result.status, result.reason) == ("blocked", "model_unavailable")
    assert provider.calls == 1
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ModelInvocation)) == 1


def test_expired_prepared_cycle_finishes_without_compilation_or_model(
    db_engine, db_session_factory, state, clock
):
    with db_session_factory.begin() as session:
        claim_or_resume(session, state[0].individual_id, clock.now(), CycleLimits())
    clock.advance(timedelta(seconds=121))
    result = run(db_engine, state, clock, None)
    assert (result.status, result.reason) == ("failed", "deadline")
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ContextSnapshot)) == 0
        assert session.scalar(select(func.count()).select_from(ModelInvocation)) == 0


def test_two_interrupted_starts_finish_without_model_or_third_attempt(
    db_engine, db_session_factory, state, clock
):
    for _ in range(2):
        with pytest.raises(ProcessInterrupted):
            run(db_engine, state, clock, InterruptedProvider())
    result = run(db_engine, state, clock, None)
    assert (result.status, result.reason) == ("failed", "attempt_limit")
    with db_session_factory() as session:
        attempts = session.scalars(select(ModelInvocation)).all()
        assert len(attempts) == 2
        assert all(attempt.status == "abandoned" for attempt in attempts)
