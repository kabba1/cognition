"""Detached attention inputs shared by stores and the pure context compiler."""

from dataclasses import dataclass

from cognition.protocols.model_v1 import ContextSection

ATTENTION_POLICY_VERSION = 1
DIRECT_REF_LIMIT = 32
LINKED_REF_LIMIT = 16
URGENT_DETAIL_LIMIT = 8
URGENT_HORIZON_HOURS = 24
LEXICAL_POLICY_VERSION = 1
LEXICAL_DICTIONARY = "pg_catalog.english"
LEXICAL_TERM_LIMIT = 16
LEXICAL_MATCH_LIMIT = 8
LEXICAL_SOURCE_CHAR_LIMIT = 512
LEXICAL_SOURCE_TOKEN_LIMIT = 32
LEXICAL_RAW_TERM_LIMIT = 64
LEXICAL_TOKEN_CHAR_LIMIT = 64
LEXICAL_WAKE_LIMIT = 16


@dataclass(frozen=True)
class AttentionCandidate:
    section: ContextSection
    reason: str
    mandatory: bool = False
    search_rank: float | None = None


@dataclass(frozen=True)
class PersonalAttention:
    candidates: tuple[AttentionCandidate, ...]
    direct_refs_truncated: int = 0
    unresolved_direct_refs: int = 0
    linked_refs_truncated: int = 0
    urgent_scan_truncated: bool = False
