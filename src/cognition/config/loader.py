"""Synchronous TOML loading with no environment or credential resolution."""

import os
import tomllib

from cognition.config.schema import Configuration, parse_config


def load_config(path: str | os.PathLike[str]) -> Configuration:
    """Read UTF-8 TOML with explicit schema dispatch; propagate validation errors."""
    with open(path, "rb") as stream:
        return parse_config(tomllib.load(stream))
