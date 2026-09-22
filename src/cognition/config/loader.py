"""Synchronous TOML loading with no environment or credential resolution."""

import os
import tomllib

from cognition.config.schema import ConfigV1


def load_config(path: str | os.PathLike[str]) -> ConfigV1:
    """Read UTF-8 TOML and validate v1; propagate I/O, TOML, and validation errors."""
    with open(path, "rb") as stream:
        return ConfigV1.model_validate(tomllib.load(stream))
