"""Operational cadence and immutable grants for bounded internal exploration."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cognition.db.base import Base
from cognition.protocols.common import JsonObject


class ExplorationState(Base):
    __tablename__ = "exploration_state"
    __table_args__ = (
        CheckConstraint("policy_version = 1", name="policy_version"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        UniqueConstraint("managed_wake_id"),
    )

    individual_id: Mapped[UUID] = mapped_column(
        ForeignKey("individuals.individual_id"), primary_key=True
    )
    policy_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1")
    )
    next_eligible_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    managed_wake_id: Mapped[UUID | None] = mapped_column(ForeignKey("wakes.wake_id"))
    last_terminal_cycle_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("cognition_cycles.cycle_id")
    )
    last_outcome_event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("events.event_id")
    )
    materialization_pending: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    revision: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default=text("1")
    )


class ExplorationGrant(Base):
    __tablename__ = "exploration_grants"
    __table_args__ = (
        CheckConstraint("policy_version = 1", name="policy_version"),
        CheckConstraint(
            "authorizing_governance_revision >= 1", name="governance_revision_positive"
        ),
        CheckConstraint(
            "jsonb_typeof(policy_snapshot) = 'object'", name="policy_snapshot_object"
        ),
        CheckConstraint("scope = 'internal'", name="scope"),
        CheckConstraint("max_turns = 1", name="max_turns"),
        CheckConstraint("max_attempts_per_turn = 2", name="max_attempts_per_turn"),
        CheckConstraint("max_seconds = 120", name="max_seconds"),
        CheckConstraint("max_wakes = 1", name="max_wakes"),
        CheckConstraint("not_before_at >= created_at", name="time_order"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash"),
        Index("ix_exploration_grants_individual_id", "individual_id"),
    )

    wake_id: Mapped[UUID] = mapped_column(ForeignKey("wakes.wake_id"), primary_key=True)
    individual_id: Mapped[UUID] = mapped_column(ForeignKey("individuals.individual_id"))
    policy_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1")
    )
    authorizing_governance_revision: Mapped[int] = mapped_column(BigInteger)
    policy_snapshot: Mapped[JsonObject] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    not_before_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scope: Mapped[str] = mapped_column(Text)
    max_turns: Mapped[int] = mapped_column(Integer)
    max_attempts_per_turn: Mapped[int] = mapped_column(Integer)
    max_seconds: Mapped[int] = mapped_column(Integer)
    max_wakes: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(Text)
