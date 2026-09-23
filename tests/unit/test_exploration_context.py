"""Exploration physics remain mandatory under tight optional-context budgets."""

from dataclasses import replace
from datetime import timedelta

import pytest
from test_context import inputs

from cognition.protocols.model_v1 import ContextSection
from cognition.runtime.context import ContextBudgetExceeded, compile_request
from cognition.stores.exploration_scope import (
    CycleExploration,
    exploration_grant_hash,
    validate_exploration_control,
    validate_exploration_grant_snapshot,
)


def scoped_inputs():
    values = inputs()
    now = values["present_time"]
    fields = dict(
        wake_id=values["wakes"][0].wake_id,
        individual_id=values["individual"].individual_id,
        policy_version=1,
        authorizing_governance_revision=2,
        policy_snapshot={"schema_version": 1, "enabled": True},
        created_at=now,
        not_before_at=now,
        scope="internal",
        max_turns=1,
        max_attempts_per_turn=2,
        max_seconds=120,
        max_wakes=1,
    )
    grant = validate_exploration_grant_snapshot(
        **fields, content_hash=exploration_grant_hash(**fields)
    )
    values["exploration"] = CycleExploration(
        grant=grant,
        cycle_id=values["cycle_id"],
        started_at=now,
        deadline_at=now + timedelta(seconds=60),
        max_turns=1,
        max_attempts_per_turn=1,
        max_wakes=1,
    )
    return values


def test_exploration_control_reserves_budget_before_optional_packing():
    values = scoped_inputs()
    baseline = compile_request(**values)
    values["config"].sanitized_config.attention.context_budget_tokens = (
        baseline.estimated_input_tokens + 100
    )
    optional = ContextSection(
        name="optional", category="evidence", content="x" * 5000, refs=[]
    )
    compiled = compile_request(**values, personal_sections=[optional])
    validate_exploration_control(
        values["exploration"], compiled.request.context_sections
    )
    assert optional not in compiled.request.context_sections
    assert compiled.estimated_input_tokens <= compiled.request.input_token_budget
    assert str(values["exploration"].grant.wake_id) in compiled.rendered_context


def test_insufficient_mandatory_budget_never_drops_exploration_control():
    values = scoped_inputs()
    baseline = compile_request(**values)
    values["config"].sanitized_config.attention.context_budget_tokens = (
        baseline.estimated_input_tokens - 100
    )
    with pytest.raises(ContextBudgetExceeded):
        compile_request(**values)


@pytest.mark.parametrize("changed", ["cycle", "owner", "membership"])
def test_detached_exploration_scope_cannot_cross_compilation_identity(changed):
    from cognition.protocols.common import new_id

    values = scoped_inputs()
    scope = values["exploration"]
    if changed == "cycle":
        values["exploration"] = replace(scope, cycle_id=new_id())
    elif changed == "owner":
        values["exploration"] = replace(
            scope, grant=replace(scope.grant, individual_id=new_id())
        )
    else:
        values["wakes"] = []
    with pytest.raises(ValueError):
        compile_request(**values)


def test_ordinary_compilation_has_no_exploration_control():
    compiled = compile_request(**inputs())
    validate_exploration_control(None, compiled.request.context_sections)
