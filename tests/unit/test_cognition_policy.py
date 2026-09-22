"""Bounded proposal checks reject invalid effects before any state application."""

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from cognition.protocols.cognition_v1 import CognitionDecisionV1, CurrentFocus
from cognition.protocols.common import Ref

GOLDEN = Path(__file__).parents[1] / "golden" / "cognition_decision_v1.json"
CATEGORIES = (
    "goal_operations",
    "commitment_operations",
    "belief_operations",
    "episode_operations",
    "interest_operations",
    "preference_operations",
    "self_model_operations",
    "action_requests",
    "wake_requests",
)
CYCLE = UUID("22222222-2222-4222-8222-222222222222")
TURN = UUID("33333333-3333-4333-8333-333333333333")


def example(*categories: str) -> CognitionDecisionV1:
    data = json.loads(GOLDEN.read_text())
    for category in CATEGORIES:
        if category not in categories:
            data[category] = []
    return CognitionDecisionV1.model_validate(data)


def validate(decision: CognitionDecisionV1, known: tuple[Ref, ...] = ()):
    from cognition.policy.cognition import validate_decision

    return validate_decision(
        decision, cycle_id=CYCLE, turn_id=TURN, known_ref=lambda ref: ref in known
    )


@pytest.mark.parametrize("disposition", ["continue", "wait", "sleep"])
def test_empty_operations_are_valid_for_every_disposition(disposition):
    decision = example().model_copy(update={"disposition": disposition})
    assert validate(decision) == ()


@pytest.mark.parametrize("disposition", ["continue", "wait", "sleep"])
def test_focus_and_wakes_are_valid_for_every_disposition(disposition):
    ref = Ref(kind="event", id=uuid4())
    decision = example("wake_requests")
    decision.disposition = disposition
    decision.current_focus = CurrentFocus(summary="Supported focus", refs=[ref])
    decision.wake_requests[0].context_refs = [ref]
    before = decision.model_dump()
    assert validate(decision, (ref,)) == ()
    assert decision.model_dump() == before


@pytest.mark.parametrize(
    "field,code", [("cycle_id", "cycle_id_mismatch"), ("turn_id", "turn_id_mismatch")]
)
def test_decision_cannot_target_another_cycle_or_turn(field, code):
    decision = example().model_copy(update={field: uuid4()})
    assert validate(decision) == (code,)


@pytest.mark.parametrize("category", CATEGORIES[4:-1])
def test_each_unimplemented_category_is_explicitly_rejected(category):
    assert validate(example(category)) == ("unsupported_operations",)


@pytest.mark.parametrize("category", CATEGORIES[:4])
def test_grounded_personal_families_have_semantic_handlers(category):
    assert validate(example(category)) == ()


@pytest.mark.parametrize("category", CATEGORIES[:-1])
def test_operation_identity_must_be_unique_across_categories(category):
    decision = example(category, "wake_requests")
    decision.wake_requests[0].operation_id = getattr(decision, category)[0].operation_id
    assert validate(decision) == (
        ("duplicate_operation_id",)
        if category in CATEGORIES[:4]
        else ("duplicate_operation_id", "unsupported_operations")
    )


@pytest.mark.parametrize("category", CATEGORIES[:4])
def test_personal_evidence_references_must_resolve(category):
    decision = example(category)
    operation = getattr(decision, category)[0]
    field = (
        "supporting_evidence" if category == "belief_operations" else "evidence_refs"
    )
    setattr(operation, field, [Ref(kind="event", id=uuid4())])
    assert validate(decision) == ("unknown_ref",)


def test_total_semantic_effects_are_bounded():
    decision = example("goal_operations")
    template = decision.goal_operations[0]
    decision.goal_operations = [
        template.model_copy(update={"operation_id": uuid4()}) for _ in range(65)
    ]
    assert validate(decision) == ("too_many_operations",)


def test_operation_identity_must_be_unique_within_a_category():
    decision = example("wake_requests")
    decision.wake_requests.append(decision.wake_requests[0].model_copy())
    assert validate(decision) == ("duplicate_operation_id",)


@pytest.mark.parametrize("location", ["focus", "wake"])
def test_every_applied_reference_must_resolve(location):
    known = Ref(kind="event", id=uuid4())
    # The same UUID under another kind does not establish a valid reference.
    unknown = Ref(kind="unsupported_kind", id=known.id)
    decision = example("wake_requests")
    if location == "focus":
        decision.current_focus = CurrentFocus(summary="Focus", refs=[known, unknown])
    else:
        decision.wake_requests[0].context_refs = [known, unknown]
    assert validate(decision, (known,)) == ("unknown_ref",)


@pytest.mark.parametrize("count,errors", [(16, ()), (17, ("too_many_wake_requests",))])
def test_self_scheduled_wake_count_is_bounded(count, errors):
    decision = example("wake_requests")
    template = decision.wake_requests[0]
    decision.wake_requests = [
        template.model_copy(update={"operation_id": uuid4()}) for _ in range(count)
    ]
    assert validate(decision) == errors


@pytest.mark.parametrize("mutation", ["disposition", "focus", "ref", "wake", "array"])
def test_unvalidated_model_copy_or_nested_mutation_is_revalidated(mutation):
    decision = example("wake_requests")
    if mutation == "disposition":
        decision = decision.model_copy(update={"disposition": "secret invalid value"})
    elif mutation == "focus":
        decision.current_focus = CurrentFocus.model_construct(summary=123, refs=[])
    elif mutation == "ref":
        decision.current_focus = CurrentFocus(
            summary="Focus", refs=[Ref(kind="event", id=uuid4())]
        )
        decision.current_focus.refs[0].kind = ""
    elif mutation == "wake":
        decision.wake_requests[0].purpose = ""
    else:
        decision = decision.model_copy(update={"wake_requests": None})
    assert validate(decision) == ("invalid_decision",)


def test_multiple_failures_have_stable_deduplicated_codes():
    decision = example(*CATEGORIES)
    decision.cycle_id = uuid4()
    decision.turn_id = uuid4()
    decision.wake_requests *= 17
    ref = Ref(kind="event", id=uuid4())
    decision.current_focus = CurrentFocus(summary="Unresolved", refs=[ref, ref])
    decision.wake_requests[0].context_refs = [ref]
    assert validate(decision) == (
        "cycle_id_mismatch",
        "turn_id_mismatch",
        "duplicate_operation_id",
        "unsupported_operations",
        "unknown_ref",
        "too_many_wake_requests",
    )
