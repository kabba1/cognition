"""Explicit executive selection never rewrites the legacy behavior subset."""

import importlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from cognition.config.loader import load_config
from cognition.config.revisions import behavior_config, behavior_hash, canonical_json
from cognition.config.schema import BehaviorConfig, ConfigV1

FIXTURE = Path(__file__).parents[1] / "fixtures/config/valid.toml"


def api():
    return importlib.import_module("cognition.config.schema")


def v2_data():
    data = load_config(FIXTURE).model_dump()
    data.update(config_schema_version=2, execution={"cognition_protocol_version": 2})
    return data


def test_v2_selection_is_explicit_and_persisted_with_stable_v1_behavior():
    v1 = load_config(FIXTURE)
    before = canonical_json(v1)
    v2 = api().parse_config(v2_data())
    sanitized = behavior_config(v2)
    assert isinstance(v2, api().ConfigV2)
    assert isinstance(sanitized, api().BehaviorConfigV2)
    assert api().cognition_protocol_version(behavior_config(v1)) == 1
    assert api().cognition_protocol_version(sanitized) == 2
    assert sanitized.execution.cognition_protocol_version == 2
    assert set(sanitized.model_dump()) == set(behavior_config(v1).model_dump()) | {
        "execution"
    }
    assert behavior_hash(v1) != behavior_hash(v2)
    assert api().parse_behavior_config(json.loads(canonical_json(v2))) == sanitized
    assert canonical_json(v1) == before
    assert isinstance(api().parse_config(v1.model_dump()), ConfigV1)
    assert isinstance(
        api().parse_behavior_config(behavior_config(v1).model_dump()), BehaviorConfig
    )
    assert "execution" not in json.loads(before)


@pytest.mark.parametrize("version", [None, True, False, 1.0, 2.0, "1", "2", 0, 3])
@pytest.mark.parametrize("parser", ["parse_config", "parse_behavior_config"])
def test_config_dispatch_rejects_noninteger_or_unknown_versions(version, parser):
    data = v2_data()
    data["config_schema_version"] = version
    with pytest.raises(ValueError):
        getattr(api(), parser)(data)


@pytest.mark.parametrize("value", [None, [], "{}", 1])
@pytest.mark.parametrize("parser", ["parse_config", "parse_behavior_config"])
def test_config_dispatch_requires_an_object(value, parser):
    with pytest.raises(ValueError):
        getattr(api(), parser)(value)


@pytest.mark.parametrize(
    "selection",
    [
        None,
        {},
        {"cognition_protocol_version": 1},
        {"cognition_protocol_version": 2.0},
        {"cognition_protocol_version": "2"},
        {"cognition_protocol_version": True},
        {"cognition_protocol_version": 2, "secret": "excluded"},
    ],
)
def test_v2_requires_exact_execution_selection(selection):
    data = v2_data()
    if selection is None:
        del data["execution"]
    else:
        data["execution"] = selection
    with pytest.raises(ValidationError):
        api().parse_config(data)


def test_v1_cannot_silently_enable_the_new_protocol():
    data = v2_data()
    data["config_schema_version"] = 1
    with pytest.raises(ValidationError):
        api().parse_config(data)


def test_toml_loader_selects_v2_and_behavior_excludes_deployment_secrets(tmp_path):
    contents = FIXTURE.read_text().replace(
        "config_schema_version = 1", "config_schema_version = 2"
    )
    target = tmp_path / "v2.toml"
    target.write_text(contents + "\n[execution]\ncognition_protocol_version = 2\n")
    config = load_config(target)
    assert isinstance(config, api().ConfigV2)
    dumped = canonical_json(config)
    assert "execution" in dumped and "COGNITION_TEST_DATABASE_URL" not in dumped
    config.model = {**config.model.model_dump(), "api_key": "MUST_NOT_PERSIST"}
    with pytest.raises(ValidationError):
        behavior_hash(config)
