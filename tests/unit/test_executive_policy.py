"""V2 executive families share v1's whole-decision bounds and references."""

import importlib
import json
from pathlib import Path

import pytest

from cognition.policy.cognition import validate_decision
from cognition.protocols.common import new_id

FAMILIES = ("entity", "project", "relationship", "relationship_thread")


def proposal(**families):
    data = json.loads(
        (Path(__file__).parents[1] / "golden/cognition_decision_v1.json").read_text()
    )
    for key in data:
        if key.endswith("_operations") or key.endswith("_requests"):
            data[key] = []
    data.update(schema_version=2, current_focus=None)
    data.update({f"{family}_operations": [] for family in FAMILIES})
    data.update(families)
    return importlib.import_module("cognition.protocols.executive").parse_decision(data)


def operation(family, **updates):
    data = dict(
        operation_id=new_id(),
        op="create",
        evidence_refs=[],
        rationale="Deliberate choice",
    )
    data.update(
        {
            "entity": dict(entity_id=None, kind="person", display_name="Morgan"),
            "project": dict(
                project_id=None,
                title="Learn",
                desired_state="Understand",
                requested_status=None,
            ),
            "relationship": dict(
                relationship_id=None,
                entity_id=new_id(),
                narrative="A working relationship",
            ),
            "relationship_thread": dict(
                thread_id=None,
                relationship_id=new_id(),
                title="Follow up",
                summary="Discuss next steps",
                commitment_id=None,
                requested_status=None,
            ),
        }[family]
    )
    data.update(updates)
    return data


def validate(value, known=True):
    return validate_decision(
        value,
        cycle_id=value.cycle_id,
        turn_id=value.turn_id,
        known_ref=lambda ref: known,
    )


@pytest.mark.parametrize("family", FAMILIES)
def test_new_family_is_supported_and_evidence_checked(family):
    value = proposal(
        **{
            f"{family}_operations": [
                operation(family, evidence_refs=[{"kind": "event", "id": new_id()}])
            ]
        }
    )
    assert validate(value) == ()
    assert validate(value, False) == ("unknown_ref",)


def test_global_duplicate_id_spans_new_and_old_families():
    identity = new_id()
    value = proposal(
        entity_operations=[operation("entity", operation_id=identity)],
        project_operations=[operation("project", operation_id=identity)],
    )
    assert validate(value) == ("duplicate_operation_id",)


def test_total_operation_bound_includes_new_families():
    value = proposal(
        entity_operations=[operation("entity") for _ in range(33)],
        project_operations=[operation("project") for _ in range(32)],
    )
    assert validate(value) == ("too_many_operations",)


def test_mutated_v2_operation_is_revalidated():
    value = proposal(entity_operations=[operation("entity")])
    value.entity_operations[0].op = "grant_authority"
    assert validate(value) == ("invalid_decision",)
