"""Only explicit, strict operator policy enables internal exploration."""

import pytest

from cognition.domain.exploration import (
    CADENCE_SECONDS,
    MAX_ATTEMPTS,
    MAX_SECONDS,
    MAX_TURNS,
    MAX_WAKES,
    InvalidInternalExplorationPolicy,
    parse_internal_exploration,
)


def test_absent_policy_is_disabled_and_unrelated_budget_keys_are_ignored():
    assert not parse_internal_exploration({}).enabled
    assert not parse_internal_exploration({"other": {"enabled": True}}).enabled
    assert (CADENCE_SECONDS, MAX_TURNS, MAX_ATTEMPTS, MAX_SECONDS, MAX_WAKES) == (
        604800,
        1,
        2,
        120,
        1,
    )


@pytest.mark.parametrize("enabled", [False, True])
def test_exact_version_one_boolean_policy(enabled):
    source = {"internal_exploration": {"schema_version": 1, "enabled": enabled}}
    result = parse_internal_exploration(source)
    assert result.schema_version == 1 and result.enabled is enabled
    source["internal_exploration"]["enabled"] = not enabled
    assert result.enabled is enabled


@pytest.mark.parametrize(
    "policy",
    [
        None,
        [],
        True,
        {},
        {"enabled": True},
        {"schema_version": 1},
        {"schema_version": True, "enabled": True},
        {"schema_version": 1.0, "enabled": True},
        {"schema_version": "1", "enabled": True},
        {"schema_version": 2, "enabled": True},
        {"schema_version": 1, "enabled": 1},
        {"schema_version": 1, "enabled": "false"},
        {"schema_version": 1, "enabled": False, "max_turns": 99},
    ],
)
def test_reserved_subtree_rejects_missing_fields_coercions_and_extensions(policy):
    with pytest.raises(InvalidInternalExplorationPolicy):
        parse_internal_exploration({"internal_exploration": policy})


@pytest.mark.parametrize("policy", [None, [], False])
def test_budget_policy_must_be_an_object(policy):
    with pytest.raises(InvalidInternalExplorationPolicy):
        parse_internal_exploration(policy)
