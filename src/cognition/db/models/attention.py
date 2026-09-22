"""Durable wake storage for atomic bootstrap birth; no wake processing here."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cognition.db.base import Base
from cognition.protocols.common import JsonObject, new_id


class Wake(Base):
    __tablename__ = "wakes"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('bootstrap', 'external_event', 'action_result', "
            "'commitment_due', "
            "'self_scheduled', 'goal_review', 'routine', 'reflection', 'maintenance', "
            "'heartbeat', 'recovery')",
            name="kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'claimed', 'consumed', 'cancelled', 'superseded')",
            name="status",
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
        Index(
            "ix_wakes_pending_due",
            "individual_id",
            "due_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "uq_wakes_pending_coalesce",
            "individual_id",
            "coalesce_key",
            unique=True,
            postgresql_where=text("status = 'pending' AND coalesce_key IS NOT NULL"),
        ),
    )

    wake_id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    individual_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "individuals.individual_id", name="fk_wakes_individual_id_individuals"
        )
    )
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    purpose: Mapped[str] = mapped_column(Text)
    cause_event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "events.event_id", name="fk_wakes_cause_event_id_events", use_alter=True
        )
    )
    context_refs: Mapped[list[JsonObject]] = mapped_column(JSONB)
    coalesce_key: Mapped[str | None] = mapped_column(Text)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default=text("1")
    )
