"""Immutable exploration allowances and exact frozen control."""

import hashlib
import json
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from cognition.db.models.attention import Wake
from cognition.db.models.cognition import CognitionCycle, CycleWake
from cognition.db.models.evidence import Event
from cognition.db.models.exploration import ExplorationGrant, ExplorationState
from cognition.db.models.governance import GovernanceState
from cognition.db.models.identity import Individual
from cognition.domain.exploration import (
    MAX_ATTEMPTS,
    MAX_SECONDS,
    MAX_TURNS,
    MAX_WAKES,
    InvalidInternalExplorationPolicy,
    parse_internal_exploration,
)
from cognition.domain.heartbeat import HeartbeatPolicy
from cognition.protocols.common import JsonObject, Ref, normalize_utc
from cognition.protocols.model_v1 import ContextSection
from cognition.stores.autonomy import _configuration

EXPLORATION_MARKER_TYPE = "attention.exploration_scheduled"
EXPLORATION_MARKER_SOURCE = "attention.exploration"
_DEADLINE = HeartbeatPolicy(1, MAX_SECONDS, 1)


@dataclass(frozen=True)
class ExplorationGrantRecord:
    wake_id: UUID
    individual_id: UUID
    policy_version: int
    authorizing_governance_revision: int
    policy_snapshot: JsonObject
    created_at: datetime
    not_before_at: datetime
    scope: str
    max_turns: int
    max_attempts_per_turn: int
    max_seconds: int
    max_wakes: int
    content_hash: str


@dataclass(frozen=True)
class CycleExploration:
    grant: ExplorationGrantRecord
    cycle_id: UUID
    started_at: datetime
    deadline_at: datetime
    max_turns: int
    max_attempts_per_turn: int
    max_wakes: int


@dataclass(frozen=True)
class ExplorationDiscovery:
    managed_wake_ids: tuple[UUID, ...] = ()
    eligible: ExplorationGrantRecord | None = None


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _fields(
    *,
    wake_id: UUID,
    individual_id: UUID,
    policy_version: int,
    authorizing_governance_revision: int,
    policy_snapshot: JsonObject,
    created_at: datetime,
    not_before_at: datetime,
    scope: str,
    max_turns: int,
    max_attempts_per_turn: int,
    max_seconds: int,
    max_wakes: int,
) -> JsonObject:
    for value, expected in (
        (policy_version, 1),
        (max_turns, MAX_TURNS),
        (max_attempts_per_turn, MAX_ATTEMPTS),
        (max_seconds, MAX_SECONDS),
        (max_wakes, MAX_WAKES),
    ):
        if type(value) is not int or value != expected:
            raise ValueError("Invalid immutable exploration policy or limit")
    if (
        type(authorizing_governance_revision) is not int
        or authorizing_governance_revision < 1
    ):
        raise ValueError("Invalid exploration governance revision")
    if not isinstance(wake_id, UUID) or not isinstance(individual_id, UUID):
        raise ValueError("Exploration identities must be UUIDs")
    if not parse_internal_exploration(
        {"internal_exploration": policy_snapshot}
    ).enabled:
        raise ValueError("Exploration grant requires explicit enabled policy")
    created_at, not_before_at = normalize_utc(created_at), normalize_utc(not_before_at)
    if not_before_at < created_at or scope != "internal":
        raise ValueError("Invalid exploration scope or timing")
    return dict(
        wake_id=str(wake_id),
        individual_id=str(individual_id),
        policy_version=policy_version,
        authorizing_governance_revision=authorizing_governance_revision,
        policy_snapshot=deepcopy(policy_snapshot),
        created_at=created_at.isoformat(),
        not_before_at=not_before_at.isoformat(),
        scope=scope,
        max_turns=max_turns,
        max_attempts_per_turn=max_attempts_per_turn,
        max_seconds=max_seconds,
        max_wakes=max_wakes,
    )


def exploration_grant_hash(**fields: Any) -> str:
    """Hash every immutable grant field after strict policy and cap validation."""
    return hashlib.sha256(
        _canonical_json(_fields(**fields)).encode("utf-8")
    ).hexdigest()


def validate_exploration_grant_snapshot(
    *, content_hash: str, **fields: Any
) -> ExplorationGrantRecord:
    if exploration_grant_hash(**fields) != content_hash:
        raise ValueError("Exploration grant content hash mismatch")
    return ExplorationGrantRecord(
        **{
            **fields,
            "created_at": normalize_utc(fields["created_at"]),
            "not_before_at": normalize_utc(fields["not_before_at"]),
            "policy_snapshot": deepcopy(fields["policy_snapshot"]),
        },
        content_hash=content_hash,
    )


def get_exploration_grant(
    session: Session, individual_id: UUID, wake_id: UUID
) -> ExplorationGrantRecord | None:
    """Classify by grant, retained marker or pointer before mutable wake filters."""
    with session.no_autoflush:
        wake = (
            session.execute(select(Wake.__table__).where(Wake.wake_id == wake_id))
            .mappings()
            .one_or_none()
        )
        if wake is None:
            raise ValueError("Exploration wake is missing")
        grant = (
            session.execute(
                select(ExplorationGrant.__table__).where(
                    ExplorationGrant.wake_id == wake_id
                )
            )
            .mappings()
            .one_or_none()
        )
        pointers = session.scalars(
            select(ExplorationState.individual_id).where(
                ExplorationState.managed_wake_id == wake_id
            )
        ).all()
        markers = (
            session.execute(
                select(Event.__table__)
                .where(
                    Event.event_type == EXPLORATION_MARKER_TYPE,
                    or_(
                        Event.event_id == wake.cause_event_id,
                        (Event.subject_kind == "wake") & (Event.subject_id == wake_id),
                    ),
                )
                .limit(2)
            )
            .mappings()
            .all()
        )
        if grant is None and not pointers and not markers:
            return None
        if (
            grant is None
            or grant.individual_id != individual_id
            or wake.individual_id != individual_id
            or wake.kind != "routine"
            or wake.coalesce_key is not None
            or wake.context_refs != []
            or any(owner != individual_id for owner in pointers)
            or len(markers) != 1
        ):
            raise ValueError("Managed exploration identity is missing or inconsistent")
        marker = markers[0]
        if (
            marker.individual_id != individual_id
            or marker.source_kind != "runtime"
            or marker.source_id != EXPLORATION_MARKER_SOURCE
            or marker.subject_kind != "wake"
            or marker.subject_id != wake_id
            or wake.cause_event_id != marker.event_id
        ):
            raise ValueError("Managed exploration creation marker is inconsistent")
        record = validate_exploration_grant_snapshot(**dict(grant))
        if wake.status in {"pending", "claimed"} and pointers:
            next_eligible = session.scalar(
                select(ExplorationState.next_eligible_at).where(
                    ExplorationState.individual_id == individual_id
                )
            )
            if next_eligible is not None and record.not_before_at < next_eligible:
                raise ValueError("Live exploration grant precedes retained cadence")
        if (
            wake.due_at != record.not_before_at
            or marker.occurred_at != record.created_at
        ):
            raise ValueError(
                "Managed exploration timing differs from its immutable grant"
            )
        return record


def live_exploration_grants(
    session: Session, individual_id: UUID
) -> tuple[ExplorationGrantRecord, ...]:
    """Validate the bounded live set, including missing metadata and hidden pointers."""
    with session.no_autoflush:
        marker = exists(
            select(Event.event_id).where(
                Event.event_type == EXPLORATION_MARKER_TYPE,
                or_(
                    Event.event_id == Wake.cause_event_id,
                    (Event.subject_kind == "wake") & (Event.subject_id == Wake.wake_id),
                ),
            )
        )
        owned_marker = exists(
            select(Event.event_id).where(
                Event.event_type == EXPLORATION_MARKER_TYPE,
                Event.individual_id == individual_id,
                or_(
                    Event.event_id == Wake.cause_event_id,
                    (Event.subject_kind == "wake") & (Event.subject_id == Wake.wake_id),
                ),
            )
        )
        any_pointer = exists(
            select(ExplorationState.individual_id).where(
                ExplorationState.managed_wake_id == Wake.wake_id
            )
        )
        pointer = session.scalar(
            select(ExplorationState.managed_wake_id).where(
                ExplorationState.individual_id == individual_id
            )
        )
        ids = tuple(
            session.scalars(
                select(Wake.wake_id)
                .outerjoin(ExplorationGrant, ExplorationGrant.wake_id == Wake.wake_id)
                .where(
                    Wake.status.in_(("pending", "claimed")),
                    or_(
                        Wake.individual_id == individual_id,
                        ExplorationGrant.individual_id == individual_id,
                        Wake.wake_id == pointer,
                        owned_marker,
                    ),
                    or_(
                        ExplorationGrant.wake_id.is_not(None),
                        marker,
                        any_pointer,
                        Wake.wake_id == pointer,
                    ),
                )
                .order_by(Wake.wake_id)
                .limit(2)
            )
        )
        if len(ids) > 1 or (ids and ids != (pointer,)):
            raise ValueError("Invalid managed exploration live cardinality")
        records = []
        for identity in ids:
            record = get_exploration_grant(session, individual_id, identity)
            if record is None:
                raise ValueError("Missing managed exploration grant")
            records.append(record)
        return tuple(records)


def current_exploration_denial(session: Session, individual_id: UUID) -> str | None:
    """Read committed permission; malformed reserved policy denies only exploration."""
    with session.no_autoflush:
        row = session.execute(
            select(GovernanceState.budget_policy).where(
                GovernanceState.individual_id == individual_id
            )
        ).one_or_none()
        if row is None:
            raise ValueError("Missing exploration governance owner")
        try:
            policy = parse_internal_exploration(row.budget_policy)
        except InvalidInternalExplorationPolicy:
            return "internal_exploration_policy_invalid"
        return None if policy.enabled else "internal_exploration_disabled"


def discover_exploration(
    session: Session, individual_id: UUID, now: datetime
) -> ExplorationDiscovery:
    now = normalize_utc(now)
    with session.no_autoflush:
        grants = live_exploration_grants(session, individual_id)
        ids = tuple(grant.wake_id for grant in grants)
        if not grants or current_exploration_denial(session, individual_id) is not None:
            return ExplorationDiscovery(ids)
        lifecycle = session.scalar(
            select(Individual.operational_status).where(
                Individual.individual_id == individual_id
            )
        )
        blocked = session.scalar(
            select(GovernanceState.inference_blocked).where(
                GovernanceState.individual_id == individual_id
            )
        )
        wake_status = session.scalar(
            select(Wake.status).where(Wake.wake_id == grants[0].wake_id)
        )
        eligible = (
            lifecycle == "active"
            and blocked is False
            and wake_status == "pending"
            and grants[0].not_before_at <= now
            and _configuration(session, individual_id) is not None
        )
        return ExplorationDiscovery(ids, grants[0] if eligible else None)


def get_cycle_exploration(session: Session, cycle_id: UUID) -> CycleExploration | None:
    """Validate all membership before treating a wake as ordinary or managed."""
    with session.no_autoflush:
        cycle = (
            session.execute(
                select(CognitionCycle.__table__).where(
                    CognitionCycle.cycle_id == cycle_id
                )
            )
            .mappings()
            .one_or_none()
        )
        if cycle is None:
            raise ValueError("Exploration cycle does not exist")
        wake_ids = tuple(
            session.scalars(
                select(CycleWake.wake_id).where(CycleWake.cycle_id == cycle_id)
            )
        )
        grants = [
            grant
            for wake_id in wake_ids
            if (grant := get_exploration_grant(session, cycle.individual_id, wake_id))
            is not None
        ]
        if not grants:
            return None
        if len(grants) != 1 or len(wake_ids) != 1:
            raise ValueError(
                "Exploration cycle must contain exactly its one managed wake"
            )
        grant = grants[0]
        if (
            not 1 <= cycle.max_turns <= grant.max_turns
            or not 1 <= cycle.max_attempts_per_turn <= grant.max_attempts_per_turn
            or cycle.max_wakes != 1
            or cycle.started_at < grant.not_before_at
            or cycle.deadline_at <= cycle.started_at
            or cycle.deadline_at
            > _DEADLINE.schedule(cycle.started_at, grant.max_seconds).due_at
        ):
            raise ValueError("Exploration cycle exceeds its retained grant")
        return CycleExploration(
            grant,
            cycle_id,
            normalize_utc(cycle.started_at),
            normalize_utc(cycle.deadline_at),
            cycle.max_turns,
            cycle.max_attempts_per_turn,
            cycle.max_wakes,
        )


def exploration_start_denial(session: Session, cycle_id: UUID) -> str | None:
    with session.no_autoflush:
        cycle = get_cycle_exploration(session, cycle_id)
        return (
            None
            if cycle is None
            else current_exploration_denial(session, cycle.grant.individual_id)
        )


def exploration_control(cycle: CycleExploration) -> ContextSection:
    return ContextSection(
        name="internal_exploration",
        category="control",
        refs=[Ref(kind="wake", id=cycle.grant.wake_id)],
        content={
            "policy_version": cycle.grant.policy_version,
            "grant_id": str(cycle.grant.wake_id),
            "individual_id": str(cycle.grant.individual_id),
            "cycle_id": str(cycle.cycle_id),
            "scope": "internal",
            "grant_content_hash": cycle.grant.content_hash,
            "authorizing_governance_revision": (
                cycle.grant.authorizing_governance_revision
            ),
            "not_before_at": cycle.grant.not_before_at.isoformat(),
            "effective_limits": {
                "max_turns": cycle.max_turns,
                "max_attempts_per_turn": cycle.max_attempts_per_turn,
                "max_wakes": cycle.max_wakes,
                "max_seconds": (cycle.deadline_at - cycle.started_at).total_seconds(),
                "started_at": cycle.started_at.isoformat(),
                "deadline_at": cycle.deadline_at.isoformat(),
            },
            "wake_requests_allowed": False,
        },
    )


def validate_exploration_control(
    cycle: CycleExploration | None, sections: Sequence[ContextSection]
) -> None:
    controls = [
        section for section in sections if section.name == "internal_exploration"
    ]
    if cycle is None:
        if controls:
            raise ValueError("Unexpected internal exploration control")
        return
    if len(controls) != 1 or _canonical_json(
        controls[0].model_dump(mode="json")
    ) != _canonical_json(exploration_control(cycle).model_dump(mode="json")):
        raise ValueError("Missing or inconsistent internal exploration control")
