"""Reflection scheduling continuity and retained, bounded deliberation scope."""

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


class ReflectionState(Base):
    __tablename__ = "reflection_state"
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
    next_review_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    managed_wake_id: Mapped[UUID | None] = mapped_column(ForeignKey("wakes.wake_id"))
    last_completed_cycle_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("cognition_cycles.cycle_id")
    )
    # Cursors retain positions even when the corresponding personal row is gone.
    interest_cursor: Mapped[UUID | None]
    preference_cursor: Mapped[UUID | None]
    self_state_cursor: Mapped[UUID | None]
    materialization_pending: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    revision: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default=text("1")
    )


class ManagedReflectionBatch(Base):
    __tablename__ = "managed_reflection_batches"
    __table_args__ = (
        CheckConstraint("policy_version = 1", name="policy_version"),
        CheckConstraint(
            "CASE WHEN jsonb_typeof(target_metadata) = 'array' "
            "THEN jsonb_array_length(target_metadata) BETWEEN 1 AND 8 ELSE false END",
            name="target_metadata_bounds",
        ),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash"),
        Index("ix_managed_reflection_batches_individual_id", "individual_id"),
    )

    wake_id: Mapped[UUID] = mapped_column(ForeignKey("wakes.wake_id"), primary_key=True)
    individual_id: Mapped[UUID] = mapped_column(ForeignKey("individuals.individual_id"))
    policy_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1")
    )
    target_metadata: Mapped[list[JsonObject]] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(Text)
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
