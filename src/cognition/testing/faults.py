"""Deterministic named boundaries for tests, without sleeps or random failures."""

from dataclasses import dataclass
from typing import Literal

FAILPOINTS = (
    "after_wake_claim",
    "after_context_commit",
    "during_model_call",
    "after_decision_commit",
    "during_decision_apply",
    "after_action_authorization",
    "after_dispatch_state_commit",
    "before_external_side_effect",
    "after_external_side_effect",
    "after_world_persisted_before_response",
    "after_response_constructed",
    "before_external_result_commit",
    "after_external_result_commit",
    "verification_timeout",
    "during_reconciliation",
)
type FaultMode = Literal["once", "always", "hit_count"]


class FaultInjected(RuntimeError):
    """A configured test boundary was reached."""


@dataclass
class _FaultRule:
    error: Exception
    mode: FaultMode
    hit_count: int | None
    hits: int = 0


class FaultInjector:
    """Count every hit and apply rules relative to their last configuration."""

    def __init__(self) -> None:
        self._rules: dict[str, _FaultRule] = {}
        self._hits: dict[str, int] = {}

    @staticmethod
    def _check_name(name: str) -> None:
        if name not in FAILPOINTS:
            raise ValueError(f"unknown failpoint: {name}")

    def configure(
        self,
        name: str,
        *,
        error: Exception | None = None,
        mode: FaultMode = "once",
        hit_count: int | None = None,
    ) -> None:
        """Arm a fresh rule; hit_count is one-based since this configuration."""
        self._check_name(name)
        if mode not in ("once", "always", "hit_count"):
            raise ValueError(f"unsupported fault mode: {mode}")
        if mode == "hit_count":
            if type(hit_count) is not int or hit_count < 1:
                raise ValueError("hit_count mode requires a positive integer")
        elif hit_count is not None:
            raise ValueError("hit_count is only valid for hit_count mode")
        self._rules[name] = _FaultRule(
            error if error is not None else FaultInjected(name), mode, hit_count
        )

    def hit(self, name: str) -> None:
        """Record a boundary and raise its configured exception when due."""
        self._check_name(name)
        self._hits[name] = self._hits.get(name, 0) + 1
        rule = self._rules.get(name)
        if rule is None:
            return
        rule.hits += 1
        if (
            rule.mode == "always"
            or (rule.mode == "once" and rule.hits == 1)
            or (rule.mode == "hit_count" and rule.hits == rule.hit_count)
        ):
            raise rule.error

    def hits(self, name: str) -> int:
        """Return lifetime boundary hits, including those without a rule."""
        self._check_name(name)
        return self._hits.get(name, 0)
