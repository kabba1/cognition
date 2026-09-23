"""Operational heartbeat continuity, separate from personality and governance."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from cognition.db.base import Base


class AutonomyState(Base):
    __tablename__ = "autonomy_state"
    __table_args__ = (
        CheckConstraint("policy_version = 1", name="policy_version"),
        CheckConstraint(
            "interval_seconds >= 1 AND interval_seconds < 'Infinity'::float8",
            name="interval_finite_positive",
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint(
            "managed_wake_id IS NOT NULL OR materialization_pending",
            name="managed_wake_or_pending",
        ),
        UniqueConstraint("managed_wake_id"),
    )

    individual_id: Mapped[UUID] = mapped_column(
        ForeignKey("individuals.individual_id"), primary_key=True
    )
    policy_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1")
    )
    interval_seconds: Mapped[float] = mapped_column(Float)
    anchor_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    managed_wake_id: Mapped[UUID | None] = mapped_column(ForeignKey("wakes.wake_id"))
    last_completed_cycle_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("cognition_cycles.cycle_id")
    )
    config_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("runtime_config_revisions.config_revision_id")
    )
    materialization_pending: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    revision: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default=text("1")
    )
