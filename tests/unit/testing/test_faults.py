"""Explicit deterministic faults have no dependency on scheduling or real time."""

import pytest

from cognition.testing.faults import FAILPOINTS, FaultInjected, FaultInjector


@pytest.mark.parametrize("name", FAILPOINTS)
def test_named_fault_fires_once_by_default(name):
    faults = FaultInjector()
    faults.configure(name)
    with pytest.raises(FaultInjected, match=name):
        faults.hit(name)
    faults.hit(name)
    assert faults.hits(name) == 2


def test_always_and_exact_hit_count_are_deterministic():
    faults = FaultInjector()
    faults.configure(
        "during_model_call", error=TimeoutError("provider timeout"), mode="always"
    )
    faults.configure("after_decision_commit", mode="hit_count", hit_count=3)
    for _ in range(4):
        with pytest.raises(TimeoutError, match="provider timeout"):
            faults.hit("during_model_call")
    faults.hit("after_decision_commit")
    faults.hit("after_decision_commit")
    with pytest.raises(FaultInjected):
        faults.hit("after_decision_commit")
    faults.hit("after_decision_commit")
    assert faults.hits("after_decision_commit") == 4


def test_unconfigured_boundaries_only_count_hits():
    faults = FaultInjector()
    faults.hit("after_wake_claim")
    assert faults.hits("after_wake_claim") == 1
    assert faults.hits("during_model_call") == 0


@pytest.mark.parametrize(
    "options",
    [
        {"mode": "random"},
        {"mode": "hit_count"},
        {"mode": "hit_count", "hit_count": 0},
        {"mode": "once", "hit_count": 2},
    ],
)
def test_invalid_fault_rules_rejected(options):
    with pytest.raises(ValueError):
        FaultInjector().configure("during_model_call", **options)


def test_unknown_boundary_rejected_instead_of_silently_misspelling():
    faults = FaultInjector()
    with pytest.raises(ValueError):
        faults.configure("after_decison_commit")
    with pytest.raises(ValueError):
        faults.hit("after_decison_commit")


def test_reconfiguring_once_starts_a_new_rule_without_erasing_hit_history():
    faults = FaultInjector()
    faults.configure("during_model_call")
    with pytest.raises(FaultInjected):
        faults.hit("during_model_call")
    faults.configure("during_model_call")
    with pytest.raises(FaultInjected):
        faults.hit("during_model_call")
    assert faults.hits("during_model_call") == 2
