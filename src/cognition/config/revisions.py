"""Pure sanitized configuration serialization and SHA-256 revision identity."""

import hashlib
import json

from cognition.config.schema import BehaviorConfig, ConfigV1


def behavior_config(config: ConfigV1) -> BehaviorConfig:
    """Copy only explicitly allowlisted behavior fields into a validated model."""
    return BehaviorConfig.model_validate(
        config.model_dump(
            include={
                "config_schema_version",
                "model",
                "attention",
                "retention",
                "sandbox",
            }
        )
    )


def canonical_json(config: ConfigV1 | BehaviorConfig) -> str:
    """Return compact, key-sorted, Unicode JSON for the sanitized behavior subset.

    Typed validation normalizes integer-valued TOML numbers for float fields before
    serialization. JSON text is encoded as UTF-8 by ``behavior_hash``; no locale,
    environment lookup, machine path, or insertion ordering influences identity.
    """
    sanitized = behavior_config(config) if isinstance(config, ConfigV1) else config
    return json.dumps(
        sanitized.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def behavior_hash(config: ConfigV1 | BehaviorConfig) -> str:
    """Return the lowercase SHA-256 hex digest of canonical UTF-8 behavior JSON."""
    return hashlib.sha256(canonical_json(config).encode("utf-8")).hexdigest()
