"""Local decision fixtures are bounded data and honest about their provenance."""

import importlib
import json
from pathlib import Path
from uuid import UUID

import pytest

from cognition.protocols.model_v1 import ModelRequestV1

GOLDEN = Path(__file__).parents[1] / "golden"


def decision():
    value = json.loads((GOLDEN / "model_result_v1.json").read_text("utf-8"))["decision"]
    for key in ("decision_id", "cycle_id", "turn_id"):
        value.pop(key)
    return value


def request():
    return ModelRequestV1.model_validate_json(
        (GOLDEN / "model_request_v1.json").read_text("utf-8")
    )


def module():
    assert importlib.util.find_spec("cognition.models.script_file") is not None
    return importlib.import_module("cognition.models.script_file")


def test_fixtures_bind_request_ids_and_generate_fresh_decision_ids():
    adapter = module().ScriptFileModel([decision(), decision()])
    first_request = request()
    first = adapter.decide(first_request)
    second_request = request().model_copy(update={"turn_id": UUID(int=555)})
    second = adapter.decide(second_request)
    assert first.request_id == first_request.request_id
    assert first.decision.cycle_id == first_request.cycle_id
    assert first.decision.turn_id == first_request.turn_id
    assert second.decision.turn_id == UUID(int=555)
    assert first.decision.decision_id != second.decision.decision_id
    assert first.provider == first.requested_model == "script-file"
    assert first.usage is None
    assert first.resolved_model is None
    with pytest.raises(module().ScriptFileExhausted, match="exhausted"):
        adapter.decide(first_request)


def test_explicit_decision_and_operation_ids_survive_with_detached_data():
    step = json.loads((GOLDEN / "cognition_decision_v1.json").read_text("utf-8"))
    expected_id = step["decision_id"]
    expected_operation_id = step["wake_requests"][0]["operation_id"]
    adapter = module().ScriptFileModel([step])
    step["wake_requests"].clear()
    result = adapter.decide(request())
    assert str(result.decision.decision_id) == expected_id
    assert str(result.decision.wake_requests[0].operation_id) == expected_operation_id
    assert result.decision.cycle_id == request().cycle_id


@pytest.mark.parametrize(
    "steps", [[], [{}], [decision()] * 101, [{"secret": "hide-me"}]]
)
def test_invalid_script_rejected_before_runtime_with_safe_error(steps):
    with pytest.raises(module().InvalidScriptFile) as error:
        module().ScriptFileModel(steps)
    assert "hide-me" not in str(error.value)


@pytest.mark.parametrize(
    "payload",
    [b"{}", b"[NaN]", b"[", b"\xff", b"x" * 1048577],
    ids=["object", "nonfinite", "incomplete", "encoding", "oversized"],
)
def test_file_reader_rejects_invalid_or_oversized_data(tmp_path, payload):
    path = tmp_path / "script.json"
    path.write_bytes(payload)
    with pytest.raises(module().InvalidScriptFile):
        module().load_script_file(path)


def test_file_reader_loads_json_decisions(tmp_path):
    path = tmp_path / "script.json"
    path.write_text(json.dumps([decision()]), encoding="utf-8")
    result = module().load_script_file(path).decide(request())
    assert result.decision.disposition == "sleep"
