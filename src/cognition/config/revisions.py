"""Pure sanitized configuration serialization and SHA-256 revision identity."""

import hashlib
import json

from cognition.config.schema import (
    BehaviorConfiguration,
    Configuration,
    ConfigV1,
    ConfigV2,
    parse_behavior_config,
)


def behavior_config(config: Configuration) -> BehaviorConfiguration:
    """Copy only explicitly allowlisted behavior fields into a validated model."""
    return parse_behavior_config(
        config.model_dump(
            warnings=False,
            include={
                "config_schema_version",
                "model",
                "attention",
                "retention",
                "sandbox",
                "execution",
            },
        )
    )


def canonical_json(config: Configuration | BehaviorConfiguration) -> str:
    """Return compact, key-sorted, Unicode JSON for the sanitized behavior subset.

    Typed validation normalizes integer-valued TOML numbers for float fields before
    serialization. JSON text is encoded as UTF-8 by ``behavior_hash``; no locale,
    environment lookup, machine path, or insertion ordering influences identity.
    """
    sanitized = (
        behavior_config(config)
        if isinstance(config, (ConfigV1, ConfigV2))
        else parse_behavior_config(config.model_dump(warnings=False))
    )
    return json.dumps(
        sanitized.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def behavior_hash(config: Configuration | BehaviorConfiguration) -> str:
    """Return the lowercase SHA-256 hex digest of canonical UTF-8 behavior JSON."""
    return hashlib.sha256(canonical_json(config).encode("utf-8")).hexdigest()
