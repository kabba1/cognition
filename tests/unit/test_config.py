"""Configuration input validation and sanitized revision identity."""

import hashlib
import json
import tomllib
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from cognition.config.loader import load_config
from cognition.config.revisions import behavior_config, behavior_hash, canonical_json
from cognition.config.schema import BehaviorConfig, ConfigV1

FIXTURES = Path(__file__).parents[1] / "fixtures" / "config"


@pytest.fixture
def data() -> dict[str, Any]:
    with (FIXTURES / "valid.toml").open("rb") as stream:
        return tomllib.load(stream)


def test_load_valid_toml() -> None:
    config = load_config(FIXTURES / "valid.toml")
    assert config.config_schema_version == 1
    assert str(config.runtime.individual_id) == "00000000-0000-4000-8000-000000000002"
    assert config.model.max_output_tokens == 1024


@pytest.mark.parametrize("version", [0, 2, True, 1.0, "1"])
def test_reject_unsupported_or_coerced_version(
    data: dict[str, Any], version: Any
) -> None:
    data["config_schema_version"] = version
    with pytest.raises(ValidationError):
        ConfigV1.model_validate(data)


@pytest.mark.parametrize(
    "section",
    [
        "config_schema_version",
        "installation",
        "database",
        "runtime",
        "model",
        "attention",
        "retention",
        "workspace",
        "sandbox",
        "logging",
    ],
)
def test_require_config_sections(data: dict[str, Any], section: str) -> None:
    del data[section]
    with pytest.raises(ValidationError):
        ConfigV1.model_validate(data)


def test_require_nested_fields(data: dict[str, Any]) -> None:
    del data["model"]["requested_model"]
    with pytest.raises(ValidationError):
        ConfigV1.model_validate(data)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("attention", "heartbeat_max_seconds", 59),
        ("attention", "heartbeat_min_seconds", 0),
        ("attention", "heartbeat_backoff_factor", 0.5),
        ("attention", "context_budget_tokens", 0),
        ("attention", "context_budget_tokens", True),
        ("attention", "heartbeat_max_seconds", float("inf")),
        ("attention", "heartbeat_min_seconds", float("nan")),
        ("attention", "heartbeat_backoff_factor", float("-inf")),
        ("runtime", "poll_interval_seconds", True),
        ("runtime", "poll_interval_seconds", "1.0"),
        ("runtime", "poll_interval_seconds", 0),
        ("model", "max_output_tokens", -1),
        ("model", "max_output_tokens", 1.5),
        ("model", "max_output_tokens", "1024"),
        ("model", "adapter", ""),
        ("model", "requested_model", 1),
        ("model", "reasoning_effort", ""),
        ("retention", "default_payload_days", -1),
        ("retention", "context_snapshot_days", True),
        ("sandbox", "enabled", "true"),
        ("sandbox", "enabled", 1),
        ("database", "url_env", ""),
        ("runtime", "individual_id", "invalid-id"),
        ("logging", "level", "UNKNOWN"),
    ],
)
def test_reject_invalid_values(
    data: dict[str, Any], section: str, field: str, value: Any
) -> None:
    data[section][field] = value
    with pytest.raises(ValidationError):
        ConfigV1.model_validate(data)


@pytest.mark.parametrize("section", [None, "model", "attention", "database", "sandbox"])
def test_reject_unknown_or_secret_fields(
    data: dict[str, Any], section: str | None
) -> None:
    target = data if section is None else data[section]
    target["api_key"] = "secret-value"
    with pytest.raises(ValidationError):
        ConfigV1.model_validate(data)


def test_optional_reasoning_effort_and_boundary_policy(data: dict[str, Any]) -> None:
    del data["model"]["reasoning_effort"]
    data["retention"]["default_payload_days"] = 0
    data["attention"]["heartbeat_backoff_factor"] = 1
    data["attention"]["heartbeat_max_seconds"] = 60
    config = ConfigV1.model_validate(data)
    assert config.model.reasoning_effort is None
    assert config.retention.default_payload_days == 0


def test_hash_stable_across_format_order_and_deployment_changes(
    data: dict[str, Any],
) -> None:
    original = load_config(FIXTURES / "valid.toml")
    reordered = ConfigV1.model_validate(dict(reversed(data.items())))
    equivalent = load_config(FIXTURES / "equivalent.toml")
    assert (
        behavior_hash(original) == behavior_hash(reordered) == behavior_hash(equivalent)
    )


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("model", "adapter", "other-adapter"),
        ("model", "requested_model", "other-model"),
        ("model", "max_output_tokens", 2048),
        ("model", "reasoning_effort", None),
        ("attention", "context_budget_tokens", 16384),
        ("attention", "heartbeat_min_seconds", 120),
        ("attention", "heartbeat_max_seconds", 7200),
        ("attention", "heartbeat_backoff_factor", 3.0),
        ("retention", "default_payload_days", 60),
        ("retention", "context_snapshot_days", 14),
        ("sandbox", "enabled", False),
    ],
)
def test_behavior_change_changes_hash(
    data: dict[str, Any], section: str, field: str, value: Any
) -> None:
    before = behavior_hash(ConfigV1.model_validate(data))
    data[section][field] = value
    assert behavior_hash(ConfigV1.model_validate(data)) != before


def test_logging_change_preserves_hash(data: dict[str, Any]) -> None:
    before = behavior_hash(ConfigV1.model_validate(data))
    data["logging"]["level"] = "DEBUG"
    assert behavior_hash(ConfigV1.model_validate(data)) == before


def test_sanitized_subset_never_resolves_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "COGNITION_TEST_DATABASE_URL", "postgresql://secret:password@host/db"
    )
    config = load_config(FIXTURES / "valid.toml")
    sanitized = behavior_config(config)
    assert isinstance(sanitized, BehaviorConfig)
    assert set(sanitized.model_dump()) == {
        "config_schema_version",
        "model",
        "attention",
        "retention",
        "sandbox",
    }
    assert "secret" not in canonical_json(config)
    assert "COGNITION_TEST_DATABASE_URL" not in canonical_json(config)
    assert sanitized.model_dump() == json.loads(canonical_json(config))
    with pytest.raises(ValidationError):
        BehaviorConfig.model_validate({**sanitized.model_dump(), "secret": "value"})


def test_canonical_json_is_compact_sorted_unicode_and_sha256(
    data: dict[str, Any],
) -> None:
    data["model"]["requested_model"] = "modèle"
    config = ConfigV1.model_validate(data)
    expected = (
        '{"attention":{"context_budget_tokens":8192,"heartbeat_backoff_factor":2.0,'
        '"heartbeat_max_seconds":3600.0,"heartbeat_min_seconds":60.0},'
        '"config_schema_version":1,"model":{"adapter":"scripted",'
        '"max_output_tokens":1024,"reasoning_effort":"medium","requested_model":"modèle"},'
        '"retention":{"context_snapshot_days":7,"default_payload_days":30},'
        '"sandbox":{"enabled":true}}'
    )
    assert canonical_json(config) == expected
    assert behavior_hash(config) == hashlib.sha256(expected.encode("utf-8")).hexdigest()
    assert canonical_json(behavior_config(config)) == expected


def test_loader_propagates_invalid_toml_and_missing_file(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.toml"
    invalid.write_text("[broken", encoding="utf-8")
    with pytest.raises(tomllib.TOMLDecodeError):
        load_config(invalid)
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "missing.toml")
