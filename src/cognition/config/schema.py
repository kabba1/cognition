"""Strict deployment configuration and its persistable behavioral subset."""

from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from cognition.protocols.common import (
    CognitionId,
    NonEmptyString,
    ProtocolModel,
    VersionOne,
    VersionTwo,
)

type ConfigId = Annotated[CognitionId, Field(strict=False)]
type PositiveInteger = Annotated[int, Field(gt=0)]
type NonnegativeInteger = Annotated[int, Field(ge=0)]
type PositiveSeconds = Annotated[float, Field(gt=0)]


class ConfigSection(ProtocolModel):
    """Reject unknown fields, nonfinite numbers, and scalar type coercions."""

    model_config = ConfigDict(strict=True)


class InstallationConfig(ConfigSection):
    installation_id: ConfigId
    environment: NonEmptyString


class DatabaseConfig(ConfigSection):
    """An environment reference only; loading does not resolve its value."""

    url_env: NonEmptyString


class RuntimeConfig(ConfigSection):
    individual_id: ConfigId
    poll_interval_seconds: PositiveSeconds


class ModelConfig(ConfigSection):
    adapter: NonEmptyString
    requested_model: NonEmptyString
    max_output_tokens: PositiveInteger
    # TOML has no null literal, so omission expresses no requested preference.
    reasoning_effort: NonEmptyString | None = None


class AttentionConfig(ConfigSection):
    context_budget_tokens: PositiveInteger
    heartbeat_min_seconds: PositiveSeconds
    heartbeat_max_seconds: PositiveSeconds
    heartbeat_backoff_factor: Annotated[float, Field(ge=1)]

    @model_validator(mode="after")
    def validate_heartbeat_range(self) -> Self:
        if self.heartbeat_max_seconds < self.heartbeat_min_seconds:
            raise ValueError("heartbeat_max_seconds must be >= heartbeat_min_seconds")
        return self


class RetentionConfig(ConfigSection):
    """Zero days is an explicit immediate-expiry policy, not indefinite retention."""

    default_payload_days: NonnegativeInteger
    context_snapshot_days: NonnegativeInteger


class WorkspaceConfig(ConfigSection):
    root: NonEmptyString


class SandboxConfig(ConfigSection):
    enabled: bool


class LoggingConfig(ConfigSection):
    level: Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"]


class ConfigV1(ConfigSection):
    """Full deployment input; identity and paths are local operational choices."""

    config_schema_version: VersionOne
    installation: InstallationConfig
    database: DatabaseConfig
    runtime: RuntimeConfig
    model: ModelConfig
    attention: AttentionConfig
    retention: RetentionConfig
    workspace: WorkspaceConfig
    sandbox: SandboxConfig
    logging: LoggingConfig


class BehaviorConfig(ConfigSection):
    """Allowlisted behavior revision; never include resolved secrets or bindings.

    Installation identity, individual identity, polling cadence, database reference,
    workspace path, and logging are deployment concerns. A future runtime-contract
    selection must be added here explicitly when that contract exists.
    """

    config_schema_version: VersionOne
    model: ModelConfig
    attention: AttentionConfig
    retention: RetentionConfig
    sandbox: SandboxConfig


class ExecutionConfigV2(ConfigSection):
    """Explicit selection of the executive protocol introduced by schema 2."""

    cognition_protocol_version: VersionTwo


class ConfigV2(ConfigSection):
    """Deployment configuration with an explicit versioned executive contract."""

    config_schema_version: VersionTwo
    installation: InstallationConfig
    database: DatabaseConfig
    runtime: RuntimeConfig
    model: ModelConfig
    attention: AttentionConfig
    retention: RetentionConfig
    workspace: WorkspaceConfig
    sandbox: SandboxConfig
    logging: LoggingConfig
    execution: ExecutionConfigV2


class BehaviorConfigV2(ConfigSection):
    """Persistable behavior, including the selected executive protocol."""

    config_schema_version: VersionTwo
    model: ModelConfig
    attention: AttentionConfig
    retention: RetentionConfig
    sandbox: SandboxConfig
    execution: ExecutionConfigV2


type Configuration = ConfigV1 | ConfigV2
type BehaviorConfiguration = BehaviorConfig | BehaviorConfigV2


def _config_version(data: object) -> int:
    if not isinstance(data, dict):
        raise ValueError("Configuration must be an object")
    version = data.get("config_schema_version")
    if type(version) is not int or version not in (1, 2):
        raise ValueError("Unsupported configuration schema version")
    return version


def parse_config(data: object) -> Configuration:
    """Select an exact known schema without coercing or upgrading its version."""
    version = _config_version(data)
    return (
        ConfigV1.model_validate(data) if version == 1 else ConfigV2.model_validate(data)
    )


def parse_behavior_config(data: object) -> BehaviorConfiguration:
    """Parse an exact retained behavior revision without adding new fields."""
    version = _config_version(data)
    return (
        BehaviorConfig.model_validate(data)
        if version == 1
        else BehaviorConfigV2.model_validate(data)
    )


def cognition_protocol_version(config: BehaviorConfiguration) -> int:
    """Resolve selection from a revalidated behavioral configuration."""
    checked = parse_behavior_config(config.model_dump(warnings=False))
    return (
        1
        if isinstance(checked, BehaviorConfig)
        else checked.execution.cognition_protocol_version
    )
