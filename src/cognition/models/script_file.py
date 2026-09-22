"""Bounded local JSON decision fixtures; this adapter performs no inference."""

import json
from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from cognition.models.base import ExecutiveModel
from cognition.protocols.cognition_v1 import CognitionDecisionV1
from cognition.protocols.common import JsonObject, new_id
from cognition.protocols.model_v1 import ModelRequestV1, ModelResultV1

MAX_SCRIPT_BYTES = 1024 * 1024
MAX_SCRIPT_STEPS = 100


class InvalidScriptFile(ValueError):
    """A local fixture is malformed or exceeds the bounded data contract."""


class ScriptFileExhausted(RuntimeError):
    """No fixture decision remains for this inference ordinal."""


class ScriptFileModel(ExecutiveModel):
    """Consume one detached decision template per call, in array order.

    Cycle and turn IDs always bind to the incoming request. An omitted decision ID
    receives a fresh UUID for that call. Explicit decision and operation IDs are
    preserved; the runtime remains responsible for semantic validation and apply.
    All other CognitionDecisionV1 fields are required, with no hidden defaults.
    """

    def __init__(self, steps: Sequence[JsonObject]) -> None:
        if not 1 <= len(steps) <= MAX_SCRIPT_STEPS:
            raise InvalidScriptFile("Script requires between 1 and 100 decisions")
        self._steps: list[JsonObject] = []
        for step in steps:
            try:
                candidate = deepcopy(step)
                candidate["cycle_id"] = candidate["turn_id"] = str(UUID(int=0))
                candidate.setdefault("decision_id", str(UUID(int=0)))
                CognitionDecisionV1.model_validate(candidate)
            except (TypeError, ValueError) as error:
                raise InvalidScriptFile(
                    "Script contains an invalid decision"
                ) from error
            self._steps.append(deepcopy(step))
        self._position = 0

    def decide(self, request: ModelRequestV1) -> ModelResultV1:
        if self._position >= len(self._steps):
            raise ScriptFileExhausted("Local decision script exhausted")
        value = deepcopy(self._steps[self._position])
        self._position += 1
        value["cycle_id"] = str(request.cycle_id)
        value["turn_id"] = str(request.turn_id)
        if "decision_id" not in value:
            value["decision_id"] = str(new_id())
        decision = CognitionDecisionV1.model_validate(value)
        return ModelResultV1(
            schema_version=1,
            status="completed",
            request_id=request.request_id,
            decision=decision,
            provider="script-file",
            requested_model="script-file",
            resolved_model=None,
            provider_request_id=None,
            usage=None,
            finish_reason="fixture_decision",
            error=None,
        )


def _reject_constant(value: str) -> None:
    raise InvalidScriptFile("Script must contain finite JSON values")


def load_script_file(path: Path) -> ScriptFileModel:
    """Read at most one MiB of UTF-8 JSON, without executing fixture contents."""
    with path.open("rb") as stream:
        data = stream.read(MAX_SCRIPT_BYTES + 1)
    if len(data) > MAX_SCRIPT_BYTES:
        raise InvalidScriptFile("Script exceeds the one MiB limit")
    try:
        value = json.loads(data.decode("utf-8"), parse_constant=_reject_constant)
        steps = TypeAdapter(list[JsonObject]).validate_python(value)
    except (ValueError, ValidationError, RecursionError) as error:
        raise InvalidScriptFile("Script must be an array of JSON decisions") from error
    return ScriptFileModel(steps)
