"""Pure bounded proposal validation, without application authority."""

from collections.abc import Callable
from uuid import UUID

from cognition.protocols.cognition_v1 import CognitionDecisionV1
from cognition.protocols.common import Ref

_PERSONAL_CATEGORIES = (
    "goal_operations",
    "commitment_operations",
    "belief_operations",
    "episode_operations",
)
_UNSUPPORTED_CATEGORIES = (
    "interest_operations",
    "preference_operations",
    "self_model_operations",
    "action_requests",
)
_OPERATION_CATEGORIES = (
    *_PERSONAL_CATEGORIES,
    *_UNSUPPORTED_CATEGORIES,
    "wake_requests",
)


def validate_decision(
    decision: CognitionDecisionV1,
    *,
    cycle_id: UUID,
    turn_id: UUID,
    known_ref: Callable[[Ref], bool],
) -> tuple[str, ...]:
    """Return unique stable errors in check order; an empty tuple passes checks.

    Revalidate mutable protocol objects before checking identities, operation
    support, references, and wake count. This does not establish factual truth,
    governance permission, or operation uniqueness against persistent state.
    ``known_ref`` is supplied by the caller; failures in that lookup propagate
    rather than being misreported as an invalid model proposal.
    """
    try:
        # Passing a model instance directly skips nested field revalidation.
        checked = CognitionDecisionV1.model_validate(
            decision.model_dump(mode="python", warnings="none")
        )
    except (ValueError, TypeError):
        return ("invalid_decision",)

    errors: list[str] = []
    if checked.cycle_id != cycle_id:
        errors.append("cycle_id_mismatch")
    if checked.turn_id != turn_id:
        errors.append("turn_id_mismatch")

    operation_ids = [
        operation.operation_id
        for category in _OPERATION_CATEGORIES
        for operation in getattr(checked, category)
    ]
    if len(operation_ids) != len(set(operation_ids)):
        errors.append("duplicate_operation_id")
    if any(getattr(checked, category) for category in _UNSUPPORTED_CATEGORIES):
        errors.append("unsupported_operations")

    refs = list(checked.current_focus.refs) if checked.current_focus else []
    for wake in checked.wake_requests:
        refs.extend(wake.context_refs)
    for goal in checked.goal_operations:
        refs.extend(goal.evidence_refs)
    for commitment in checked.commitment_operations:
        refs.extend(commitment.evidence_refs)
    for belief in checked.belief_operations:
        refs.extend(belief.supporting_evidence)
        refs.extend(belief.contradicting_evidence)
    for episode in checked.episode_operations:
        refs.extend(episode.evidence_refs)
    if any(not known_ref(ref) for ref in refs):
        errors.append("unknown_ref")
    if len(checked.wake_requests) > 16:
        errors.append("too_many_wake_requests")
    if len(operation_ids) > 64:
        errors.append("too_many_operations")
    return tuple(errors)
