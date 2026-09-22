"""Semantic proposal boundaries, independent of database state or authority."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

GOLDEN = Path(__file__).parents[1] / "golden"
CATEGORIES = [
    "goal_operations",
    "commitment_operations",
    "belief_operations",
    "episode_operations",
    "interest_operations",
    "preference_operations",
    "self_model_operations",
    "action_requests",
    "wake_requests",
]


def example() -> dict:
    return json.loads((GOLDEN / "cognition_decision_v1.json").read_text())


def validate(data: dict):
    from cognition.protocols.cognition_v1 import CognitionDecisionV1

    return CognitionDecisionV1.model_validate(data)


def test_all_operation_categories_round_trip_and_schema() -> None:
    decision = validate(example())
    assert decision.model_dump(mode="json") == example()
    assert type(decision).model_validate_json(decision.model_dump_json()) == decision
    assert decision.model_json_schema() == json.loads(
        (GOLDEN / "cognition_decision_v1.schema.json").read_text()
    )


def test_sleep_requires_no_operations() -> None:
    data = example()
    data.update({category: [] for category in CATEGORIES})
    data.update(disposition="sleep", rationale_summary=None, current_focus=None)
    assert validate(data).disposition == "sleep"


@pytest.mark.parametrize("category", CATEGORIES)
def test_every_operation_requires_identity_and_rejects_unknown_fields(category: str):
    data = example()
    del data[category][0]["operation_id"]
    with pytest.raises(ValidationError):
        validate(data)
    data = example()
    data[category][0]["raw_database_update"] = {}
    with pytest.raises(ValidationError):
        validate(data)


@pytest.mark.parametrize("field", list(example()))
def test_decision_fields_remain_explicit(field: str):
    data = example()
    del data[field]
    with pytest.raises(ValidationError):
        validate(data)


@pytest.mark.parametrize(
    "category,field,value",
    [
        ("goal_operations", "title", None),
        ("goal_operations", "desired_state", ""),
        ("goal_operations", "requested_status", "fulfilled"),
        ("goal_operations", "origin", "human_order"),
        ("commitment_operations", "terms", None),
        ("belief_operations", "proposition", None),
        ("interest_operations", "topic", None),
        ("preference_operations", "statement", None),
        ("self_model_operations", "layer", "genesis"),
        ("self_model_operations", "layer", "runtime_fact"),
        ("self_model_operations", "proposed_content", []),
        ("action_requests", "effect_class", "read_only"),
        ("action_requests", "arguments", []),
        ("wake_requests", "purpose", ""),
        ("episode_operations", "salience_factors", ["novelty", "novelty"]),
        ("episode_operations", "salience_factors", ["motivation"]),
        ("episode_operations", "ends_at", "2026-09-21T00:00:00Z"),
    ],
)
def test_incoherent_or_unauthorized_operation_rejected(category, field, value):
    data = example()
    data[category][0][field] = value
    with pytest.raises(ValidationError):
        validate(data)


@pytest.mark.parametrize(
    "category,id_field,op",
    [
        ("goal_operations", "goal_id", "revise"),
        ("goal_operations", "goal_id", "set_status"),
        ("commitment_operations", "commitment_id", "revise"),
        ("commitment_operations", "commitment_id", "set_status"),
        ("belief_operations", "belief_id", "set_status"),
        ("interest_operations", "interest_id", "establish"),
        ("interest_operations", "interest_id", "set_dormant"),
        ("interest_operations", "interest_id", "retire"),
        ("preference_operations", "preference_id", "establish"),
        ("preference_operations", "preference_id", "retire"),
    ],
)
def test_existing_entity_operations_require_target(category, id_field, op):
    data = example()
    data[category][0].update(op=op)
    data[category][0][id_field] = None
    with pytest.raises(ValidationError):
        validate(data)
    data[category][0][id_field] = data["decision_id"]
    assert validate(data)


@pytest.mark.parametrize(
    "category,id_field",
    [
        ("goal_operations", "goal_id"),
        ("commitment_operations", "commitment_id"),
        ("belief_operations", "belief_id"),
    ],
)
def test_status_operation_requires_status(category, id_field):
    data = example()
    data[category][0].update(op="set_status", requested_status=None)
    data[category][0][id_field] = data["decision_id"]
    with pytest.raises(ValidationError):
        validate(data)


def test_superseding_belief_requires_previous_belief_and_replacement():
    data = example()
    belief = data["belief_operations"][0]
    belief["op"] = "supersede"
    with pytest.raises(ValidationError):
        validate(data)
    belief["supersedes_belief_id"] = data["decision_id"]
    assert validate(data)
    belief["proposition"] = None
    with pytest.raises(ValidationError):
        validate(data)


@pytest.mark.parametrize(
    "category,field",
    [
        ("commitment_operations", "due_at"),
        ("episode_operations", "starts_at"),
        ("episode_operations", "ends_at"),
        ("wake_requests", "not_before"),
    ],
)
def test_operation_dates_require_awareness_and_normalize(category, field):
    data = example()
    data[category][0][field] = "2026-09-22T12:00:00"
    with pytest.raises(ValidationError):
        validate(data)
    data[category][0][field] = "2026-09-22T12:00:00+02:00"
    actual = getattr(getattr(validate(data), category)[0], field)
    assert actual == datetime(2026, 9, 22, 10, tzinfo=UTC)
    assert actual.tzinfo is UTC


@pytest.mark.parametrize("value", [True, 1.0, "1", 2])
def test_decision_version_is_exact(value):
    data = example()
    data["schema_version"] = value
    with pytest.raises(ValidationError):
        validate(data)


def test_extra_top_level_fields_rejected():
    data = deepcopy(example())
    data["chain_of_thought"] = "not a protocol field"
    with pytest.raises(ValidationError):
        validate(data)


@pytest.mark.parametrize("category", CATEGORIES)
def test_nullable_operation_fields_are_required(category):
    for field in example()[category][0]:
        data = example()
        del data[category][0][field]
        with pytest.raises(ValidationError):
            validate(data)


@pytest.mark.parametrize(
    "category,id_field,status",
    [
        ("goal_operations", "goal_id", "completed"),
        ("commitment_operations", "commitment_id", "fulfilled"),
        ("belief_operations", "belief_id", "withdrawn"),
    ],
)
def test_status_updates_do_not_require_creation_fields(category, id_field, status):
    data = example()
    operation = data[category][0]
    operation.update(op="set_status", requested_status=status)
    operation[id_field] = data["decision_id"]
    for field in ("title", "desired_state", "terms", "proposition"):
        if field in operation:
            operation[field] = None
    assert validate(data)


@pytest.mark.parametrize("disposition", ["continue", "wait", "sleep"])
def test_all_dispositions_are_available(disposition):
    data = example()
    data["disposition"] = disposition
    assert validate(data).disposition == disposition
