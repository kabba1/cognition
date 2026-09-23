"""Detached attention inputs shared by stores and the pure context compiler."""

from dataclasses import dataclass

from cognition.protocols.model_v1 import ContextSection

ATTENTION_POLICY_VERSION = 1
DIRECT_REF_LIMIT = 32
LINKED_REF_LIMIT = 16
URGENT_DETAIL_LIMIT = 8
URGENT_HORIZON_HOURS = 24


@dataclass(frozen=True)
class AttentionCandidate:
    section: ContextSection
    reason: str
    mandatory: bool = False


@dataclass(frozen=True)
class PersonalAttention:
    candidates: tuple[AttentionCandidate, ...]
    direct_refs_truncated: int = 0
    unresolved_direct_refs: int = 0
    linked_refs_truncated: int = 0
    urgent_scan_truncated: bool = False
