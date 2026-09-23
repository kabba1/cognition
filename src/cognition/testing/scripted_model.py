"""Deterministic executive-model results, errors, and explicit request matching."""

from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass

from cognition.models.base import ExecutiveModel
from cognition.protocols.executive import ModelRequest, ModelResult, parse_result
from cognition.protocols.model_v1 import ModelResultV1
from cognition.protocols.model_v2 import ModelResultV2

type ModelOutcome = ModelResult | Exception | Callable[[ModelRequest], ModelResult]
type RequestPredicate = Callable[[ModelRequest], bool]


class ModelScriptExhausted(RuntimeError):
    """An unexpected extra inference attempt exceeded the configured script."""


class ScriptedProviderError(RuntimeError):
    """A raised provider error with an explicit retry classification."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class ModelScriptStep:
    """An ordinal outcome optionally guarded by a deterministic predicate."""

    outcome: ModelOutcome
    matches: RequestPredicate | None = None


class ScriptedModelAdapter(ExecutiveModel):
    """Record complete request snapshots; return one scripted outcome per call.

    Matching failures and exhaustion are recorded too. A mismatched request does
    not consume its step; configured provider exceptions do. Callbacks receive
    independent copies and cannot rewrite the saved request or another callback's
    input. Returned results are validated and independent of the stored script.
    """

    def __init__(self, script: Sequence[ModelOutcome | ModelScriptStep]) -> None:
        self._script: list[ModelScriptStep] = []
        for value in script:
            step = (
                value if isinstance(value, ModelScriptStep) else ModelScriptStep(value)
            )
            outcome = step.outcome
            if isinstance(outcome, (ModelResultV1, ModelResultV2)):
                outcome = deepcopy(outcome)
            self._script.append(ModelScriptStep(outcome, step.matches))
        self._position = 0
        self._requests: list[ModelRequest] = []

    @property
    def requests(self) -> tuple[ModelRequest, ...]:
        """Defensive copies of all complete requests in call order."""
        return deepcopy(tuple(self._requests))

    def decide(self, request: ModelRequest) -> ModelResult:
        """Observe one model result without applying any resulting proposal."""
        snapshot = deepcopy(request)
        self._requests.append(snapshot)
        if self._position >= len(self._script):
            raise ModelScriptExhausted("model script exhausted")
        step = self._script[self._position]
        if step.matches is not None and not step.matches(deepcopy(snapshot)):
            raise AssertionError(
                f"request predicate failed at step {self._position + 1}"
            )
        self._position += 1
        if isinstance(step.outcome, Exception):
            raise step.outcome
        result = (
            step.outcome(deepcopy(snapshot))
            if callable(step.outcome)
            else deepcopy(step.outcome)
        )
        validated = parse_result(result)
        if validated.request_id != snapshot.request_id:
            raise ValueError("scripted result request_id does not match the request")
        return validated
