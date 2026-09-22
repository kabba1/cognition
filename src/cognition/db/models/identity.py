"""Durable birth identity and lineage, separate from current personality."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cognition.db.base import Base
from cognition.protocols.common import JsonObject, new_id


class Individual(Base):
    __tablename__ = "individuals"
    __table_args__ = (
        CheckConstraint(
            "operational_status IN "
            "('active', 'paused', 'quiescing', 'quiescent', 'retired')",
            name="operational_status",
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
    )

    individual_id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    birth_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    birth_name: Mapped[str] = mapped_column(Text)
    founding_orientation: Mapped[str] = mapped_column(Text)
    creator_provenance: Mapped[JsonObject] = mapped_column(JSONB)
    temperament_seed: Mapped[JsonObject | None] = mapped_column(
        JSONB(none_as_null=True)
    )
    founding_value_seed: Mapped[JsonObject | None] = mapped_column(
        JSONB(none_as_null=True)
    )
    parent_individual_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "individuals.individual_id",
            name="fk_individuals_parent_individual_id_individuals",
        )
    )
    fork_event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "events.event_id",
            name="fk_individuals_fork_event_id_events",
            use_alter=True,
        )
    )
    operational_status: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default=text("1")
    )
