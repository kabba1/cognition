"""Bounded, detached review targets; selection supplies no grounding or authority."""

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.orm import Session

from cognition.db.models.development import Interest, Preference, SelfState
from cognition.protocols.common import Ref, normalize_utc

REFLECTION_FAMILIES = ("interest", "preference", "self_state")
REFLECTION_BATCH_LIMIT = 8


@dataclass(frozen=True)
class ReflectionCandidate:
    ref: Ref
    revision: int
    eligible_at: datetime


@dataclass(frozen=True)
class _Family:
    kind: str
    individual_id: ColumnElement[UUID]
    identity: ColumnElement[UUID]
    revision: ColumnElement[int]
    eligible_at: ColumnElement[datetime]
    pending: ColumnElement[bool]


_INTEREST = Interest.__table__.c
_PREFERENCE = Preference.__table__.c
_SELF_STATE = SelfState.__table__.c
_FAMILIES = (
    _Family(
        "interest",
        _INTEREST.individual_id,
        _INTEREST.interest_id,
        _INTEREST.revision,
        _INTEREST.promotion_not_before,
        _INTEREST.status == "candidate",
    ),
    _Family(
        "preference",
        _PREFERENCE.individual_id,
        _PREFERENCE.preference_id,
        _PREFERENCE.revision,
        _PREFERENCE.promotion_not_before,
        _PREFERENCE.status == "tentative",
    ),
    _Family(
        "self_state",
        _SELF_STATE.individual_id,
        _SELF_STATE.self_state_id,
        _SELF_STATE.revision,
        _SELF_STATE.pending_not_before,
        and_(
            _SELF_STATE.layer.in_(("self_belief", "current_value")),
            _SELF_STATE.pending_content.is_not(None),
            _SELF_STATE.pending_not_before.is_not(None),
        ),
    ),
)


def earliest_reflection_eligibility(
    session: Session, individual_id: UUID
) -> datetime | None:
    """Read three owned aggregate minima, including candidates not yet mature."""
    eligibility: list[datetime] = []
    with session.no_autoflush:
        for family in _FAMILIES:
            earliest = session.scalar(
                select(func.min(family.eligible_at)).where(
                    family.individual_id == individual_id, family.pending
                )
            )
            if earliest is not None:
                eligibility.append(normalize_utc(earliest))
    return min(eligibility, default=None)


def _pool(
    session: Session,
    individual_id: UUID,
    family: _Family,
    eligible_by: datetime,
    cursor: UUID | None,
) -> deque[ReflectionCandidate]:
    query = (
        select(
            family.identity.label("identity"),
            family.revision.label("revision"),
            family.eligible_at.label("eligible_at"),
        )
        .where(
            family.individual_id == individual_id,
            family.pending,
            family.eligible_at <= eligible_by,
        )
        .order_by(family.identity)
        .limit(REFLECTION_BATCH_LIMIT)
    )
    after = query if cursor is None else query.where(family.identity > cursor)
    rows = list(session.execute(after))
    if cursor is not None and len(rows) < REFLECTION_BATCH_LIMIT:
        # Disjoint UUID ranges preserve rotation without asserting cursor ownership.
        rows.extend(session.execute(query.where(family.identity <= cursor)))
    return deque(
        ReflectionCandidate(
            ref=Ref(kind=family.kind, id=row.identity),
            revision=row.revision,
            eligible_at=normalize_utc(row.eligible_at),
        )
        for row in rows
    )


def select_reflection_candidates(
    session: Session,
    individual_id: UUID,
    *,
    eligible_by: datetime,
    cursors: Mapping[str, UUID | None],
) -> tuple[ReflectionCandidate, ...]:
    """Allocate eight review slots across independently rotating family cursors.

    Each family reads at most eight rows after its cursor and eight on wrap;
    missing cursors start at the first UUID. Only a later consumed batch advances
    durable cursors. Core column reads leave dirty or stale ORM objects untouched.
    """
    eligible_by = normalize_utc(eligible_by)
    with session.no_autoflush:
        pools = [
            _pool(session, individual_id, family, eligible_by, cursors.get(family.kind))
            for family in _FAMILIES
        ]
    selected: list[ReflectionCandidate] = []
    while len(selected) < REFLECTION_BATCH_LIMIT and any(pools):
        for pool in pools:
            if pool:
                selected.append(pool.popleft())
                if len(selected) == REFLECTION_BATCH_LIMIT:
                    break
    return tuple(selected)
