"""Lexical context is bounded data, ranked within diverse memory corpora."""

from types import SimpleNamespace
from uuid import UUID

import pytest
from test_attention_context import candidate, summary
from test_context import event, inputs

from cognition.domain.attention import PersonalAttention
from cognition.protocols.common import Ref
from cognition.runtime.context import compile_request
from cognition.stores.lexical import LexicalSelection


def selection(*matches, term="mangroves"):
    return SimpleNamespace(
        matches=tuple(
            SimpleNamespace(content=content, rank=rank) for content, rank in matches
        ),
        query_terms=(term,),
        query_sources=("focus",),
        effective_query=f"'{term}'",
    )


def test_lexical_rank_and_corpus_diversity_precede_recent_content():
    args = inputs()
    args["events"] = []
    args["attention"] = PersonalAttention((candidate(20, "goal"),))
    args["lexical"] = selection(
        (candidate(30, "belief").section, 0.2),
        (candidate(31, "belief").section, 0.9),
        (candidate(40, "episode").section, 0.1),
        (event(11), 0.4),
    )
    result = compile_request(**args)
    assert [
        (ref.kind, ref.id.int)
        for ref in result.selected_refs
        if ref.kind in {"goal", "belief", "episode", "event"}
    ] == [("belief", 31), ("episode", 40), ("event", 11), ("belief", 30), ("goal", 20)]
    assert summary(result)["lexical_candidate_count"] == 4
    assert summary(result)["lexical_query_term_count"] == 1
    assert result.retrieval_reasons[f"belief:{UUID(int=31)}"] == "lexical_match"


def test_direct_reference_reason_wins_over_lexical_and_recent_duplicate():
    args = inputs()
    args["events"] = []
    direct = candidate(30, "belief", "wake_reference")
    args["attention"] = PersonalAttention((candidate(30, "belief"), direct))
    args["lexical"] = selection((direct.section, 0.9))
    result = compile_request(**args)
    assert summary(result)["candidate_count"] == 1
    assert summary(result)["selected_candidate_count"] == 1
    assert result.retrieval_reasons[f"belief:{UUID(int=30)}"] == "wake_reference"


def test_effective_query_is_retained_as_data_with_no_claimed_extra_refs():
    args = inputs()
    args["events"] = []
    args["lexical"] = selection(term="untrusted-query-phrase")
    result = compile_request(**args)
    query = next(
        section
        for section in result.request.context_sections
        if section.name == "lexical_query"
    )
    assert query.category == "present" and query.content["authority"] == "none"
    assert query.content["effective_tsquery"] == "'untrusted-query-phrase'"
    assert query.content["query_sources"] == ["focus"]
    assert query.refs == []
    assert all(
        "untrusted-query-phrase" not in section.model_dump_json()
        for section in result.request.context_sections
        if section.category == "control"
    )


def test_lexical_event_uses_the_same_private_metadata_sanitizer():
    args = inputs()
    args["events"] = []
    args["lexical"] = selection((event(11, "matched public text"), 0.5))
    result = compile_request(**args)
    assert "matched public text" in result.rendered_context
    assert "do-not-expose" not in result.rendered_context
    assert Ref(kind="event", id=UUID(int=11)) in result.selected_refs


def test_oversized_lexical_match_is_omitted_while_smaller_match_and_summary_fit():
    args = inputs(budget=6500)
    args["events"] = []
    args["lexical"] = selection(
        (candidate(30, "belief", size=9000).section, 0.9),
        (candidate(31, "belief").section, 0.1),
    )
    result = compile_request(**args)
    assert Ref(kind="belief", id=UUID(int=30)) not in result.selected_refs
    assert Ref(kind="belief", id=UUID(int=31)) in result.selected_refs
    assert summary(result)["budget_omitted_candidate_count"] == 1
    assert result.estimated_input_tokens <= 6500


def test_equal_lexical_event_rank_uses_exact_descending_event_sequence():
    args = inputs()
    args["events"] = []
    args["lexical"] = selection((event(11), 0.5), (event(12), 0.5))
    result = compile_request(**args)
    assert [ref.id.int for ref in result.selected_refs if ref.kind == "event"] == [
        12,
        11,
    ]


@pytest.mark.parametrize("rank", [None, float("nan"), float("inf"), -1.0])
def test_invalid_detached_rank_cannot_create_nondeterministic_selection(rank):
    args = inputs()
    args["lexical"] = selection((candidate(30, "belief").section, rank))
    with pytest.raises(ValueError, match="finite nonnegative"):
        compile_request(**args)


def test_empty_query_omits_query_section_but_retains_explicit_policy_caps():
    args = inputs()
    args["lexical"] = LexicalSelection()
    result = compile_request(**args)
    assert all(s.name != "lexical_query" for s in result.request.context_sections)
    assert summary(result)["lexical_query_term_count"] == 0
    assert summary(result)["lexical_query_source_limits"] == {
        "wakes": 16,
        "characters_per_source": 512,
        "words_per_source": 32,
        "characters_per_word": 64,
        "raw_terms": 64,
    }


def test_foreign_lexical_event_rejects_like_other_foreign_context_inputs():
    args = inputs()
    foreign = event(11)
    foreign.envelope.individual_id = UUID(int=999)
    args["lexical"] = selection((foreign, 0.5))
    with pytest.raises(ValueError, match="same individual"):
        compile_request(**args)
