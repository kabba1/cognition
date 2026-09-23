"""Strict opt-in operational policy, independent of personal state and runtime."""

from dataclasses import dataclass

from cognition.protocols.common import JsonObject

CADENCE_SECONDS = 604800
MAX_TURNS = 1
MAX_ATTEMPTS = 2
MAX_SECONDS = 120
MAX_WAKES = 1


class InvalidInternalExplorationPolicy(ValueError):
    """The reserved operator policy is malformed, rather than absent/disabled."""


@dataclass(frozen=True)
class InternalExplorationPolicy:
    schema_version: int = 1
    enabled: bool = False

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise InvalidInternalExplorationPolicy(
                "Internal exploration requires integer schema_version 1"
            )
        if type(self.enabled) is not bool:
            raise InvalidInternalExplorationPolicy(
                "Internal exploration enabled must be a boolean"
            )


def parse_internal_exploration(budget_policy: JsonObject) -> InternalExplorationPolicy:
    """Absence disables; present invalid fields never coerce or silently default."""
    if not isinstance(budget_policy, dict):
        raise InvalidInternalExplorationPolicy("Budget policy must be an object")
    if "internal_exploration" not in budget_policy:
        return InternalExplorationPolicy()
    policy = budget_policy["internal_exploration"]
    if not isinstance(policy, dict) or set(policy) != {"schema_version", "enabled"}:
        raise InvalidInternalExplorationPolicy(
            "Internal exploration policy requires exactly schema_version and enabled"
        )
    version, enabled = policy["schema_version"], policy["enabled"]
    if type(version) is not int or version != 1 or type(enabled) is not bool:
        raise InvalidInternalExplorationPolicy(
            "Internal exploration requires integer schema_version 1 and boolean enabled"
        )
    return InternalExplorationPolicy(version, enabled)
