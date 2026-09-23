"""Attention protects urgent objects and explains bounded optional selection."""

from dataclasses import replace
from uuid import UUID

import pytest
from test_context import event, inputs

from cognition.domain.attention import AttentionCandidate, PersonalAttention
from cognition.protocols.common import Ref
from cognition.protocols.model_v1 import ContextSection
from cognition.runtime.context import ContextBudgetExceeded, compile_request


def candidate(identity, kind="goal", reason="recent_personal", *, size=0, urgent=False):
    return AttentionCandidate(
        ContextSection(
            name=f"{kind}:{identity}",
            category="commitments",
            refs=[Ref(kind=kind, id=UUID(int=identity))],
            content={"item": {"title": f"item-{identity}" + "x" * size}},
        ),
        reason,
        urgent,
    )


def summary(result):
    return next(
        section.content
        for section in result.request.context_sections
        if section.name == "attention_summary"
    )


def test_urgent_and_direct_recall_precede_recent_candidates_with_diversity():
    args = inputs()
    args["events"] = []
    args["attention"] = PersonalAttention(
        (
            candidate(20),
            candidate(21),
            candidate(30, "belief"),
            candidate(40, reason="wake_reference"),
            candidate(50, "commitment", "urgent_commitment", urgent=True),
        ),
        urgent_scan_truncated=True,
    )
    result = compile_request(**args)
    personal = [
        ref.id.int
        for ref in result.selected_refs
        if ref.kind in {"goal", "commitment", "belief"}
    ]
    assert personal == [50, 40, 30, 20, 21]
    assert summary(result)["urgent_scan_truncated"] is True
    assert summary(result)["candidate_count"] == 5
    assert summary(result)["selected_candidate_count"] == 5
    assert result.retrieval_reasons[f"commitment:{UUID(int=50)}"] == "urgent_commitment"


def test_optional_overflow_counts_exactly_and_does_not_hide_smaller_records():
    args = inputs(budget=6500)
    args["events"] = []
    args["attention"] = PersonalAttention(
        (
            candidate(20, reason="wake_reference", size=9000),
            candidate(21),
        ),
        direct_refs_truncated=1234,
        unresolved_direct_refs=2,
    )
    result = compile_request(**args)
    assert Ref(kind="goal", id=UUID(int=20)) not in result.selected_refs
    assert Ref(kind="goal", id=UUID(int=21)) in result.selected_refs
    assert summary(result)["budget_omitted_candidate_count"] == 1
    assert summary(result)["selected_candidate_count"] == 1
    assert summary(result)["direct_refs_truncated"] == 1234
    assert result.estimated_input_tokens <= 6500


def test_urgent_byte_overflow_fails_instead_of_silently_dropping_the_obligation():
    args = inputs(budget=6500)
    args["attention"] = PersonalAttention(
        (candidate(20, "commitment", "urgent_commitment", size=9000, urgent=True),)
    )
    with pytest.raises(ContextBudgetExceeded, match="Mandatory"):
        compile_request(**args)


def test_causal_evidence_precedes_ordinary_personal_content():
    args = inputs()
    args["events"] = [event(10, "The cause"), event(11, "Recent only")]
    args["attention"] = PersonalAttention((candidate(20),))
    result = compile_request(**args)
    names = [section.name for section in result.request.context_sections]
    assert names.index(f"event:{UUID(int=10)}") < names.index("goal:20")
    assert result.retrieval_reasons[f"event:{UUID(int=10)}"] == "wake_cause"


def test_duplicate_candidates_prefer_urgent_reason_and_summary_counts_unique_objects():
    args = inputs()
    args["events"] = []
    args["attention"] = PersonalAttention(
        (
            candidate(20, "commitment"),
            candidate(20, "commitment", "urgent_commitment", urgent=True),
        )
    )
    first = compile_request(**args)
    args["attention"] = PersonalAttention(tuple(reversed(args["attention"].candidates)))
    second = compile_request(**args)
    assert first.rendered_context == second.rendered_context
    assert summary(first)["candidate_count"] == 1
    assert summary(first)["selected_candidate_count"] == 1
    assert first.retrieval_reasons[f"commitment:{UUID(int=20)}"] == "urgent_commitment"


def test_reference_selection_does_not_mutate_input_and_rejects_ambiguous_legacy_input():
    args = inputs()
    original = candidate(20, reason="focus_reference")
    args["attention"] = PersonalAttention((original,))
    result = compile_request(**args)
    original.section.content["item"]["title"] = "changed"
    assert "changed" not in result.rendered_context
    args["personal_sections"] = [original.section]
    with pytest.raises(ValueError, match="personal_sections"):
        compile_request(**args)


@pytest.mark.parametrize("budget", [5200, 5500, 6000, 8000, 15000])
def test_summary_reservation_and_unicode_packing_never_exceed_the_final_budget(budget):
    args = inputs(budget=budget)
    args["events"] = []
    candidates = [candidate(100 + index, size=50) for index in range(12)]
    candidates[0].section.content["item"]["title"] = "界" * 2000
    args["attention"] = PersonalAttention(tuple(candidates))
    result = compile_request(**args)
    counts = summary(result)
    assert counts["candidate_count"] == 12
    assert (
        counts["selected_candidate_count"] + counts["budget_omitted_candidate_count"]
        == 12
    )
    assert result.estimated_input_tokens <= budget
    assert (
        result.estimated_input_tokens
        == len(result.rendered_context.encode("utf-8")) + 256
    )
    assert set(result.retrieval_reasons) == {
        f"{ref.kind}:{ref.id}" for ref in result.selected_refs
    }


def test_shuffled_candidates_and_event_inputs_produce_the_same_snapshot():
    args = inputs()
    args["events"] = [event(10), event(11), event(12)]
    items = tuple(
        candidate(identity, kind)
        for identity, kind in (
            (20, "goal"),
            (21, "goal"),
            (22, "belief"),
            (23, "commitment"),
        )
    )
    args["attention"] = PersonalAttention(items)
    first = compile_request(**args)
    args["attention"] = PersonalAttention(tuple(reversed(items)))
    args["events"].reverse()
    second = compile_request(**args)
    assert first == second


def test_equal_rank_conflicting_duplicate_projections_have_a_canonical_tiebreak():
    args = inputs(budget=6500)
    args["events"] = []
    small, large = candidate(20), candidate(20, size=9000)
    args["attention"] = PersonalAttention((small, large))
    first = compile_request(**args)
    args["attention"] = PersonalAttention((large, small))
    assert first == compile_request(**args)


def test_evidence_sequence_order_preserves_full_postgresql_bigint_precision():
    args = inputs()
    args["events"] = [
        replace(event(11), event_sequence=2**53),
        replace(event(12), event_sequence=2**53 + 1),
    ]
    args["attention"] = PersonalAttention(())
    result = compile_request(**args)
    assert [ref.id.int for ref in result.selected_refs if ref.kind == "event"] == [
        12,
        11,
    ]


@pytest.mark.parametrize(
    "kind,preferred,other",
    [
        ("goal", "active", "paused"),
        ("interest", "established", "candidate"),
    ],
)
def test_recent_family_keeps_active_or_established_priority_before_recency(
    kind, preferred, other
):
    args = inputs()
    args["events"] = []
    older, newer = candidate(20, kind), candidate(21, kind)
    older.section.content["item"].update(
        status=preferred, updated_at="2026-01-01T00:00:00+00:00"
    )
    newer.section.content["item"].update(
        status=other, updated_at="2026-09-01T00:00:00+00:00"
    )
    args["attention"] = PersonalAttention((newer, older))
    result = compile_request(**args)
    assert [ref.id.int for ref in result.selected_refs if ref.kind == kind] == [20, 21]
