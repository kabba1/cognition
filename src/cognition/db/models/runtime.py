"""Configuration history and runtime observability, never singleton authority."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cognition.db.base import Base
from cognition.protocols.common import JsonObject, new_id


class RuntimeConfigRevision(Base):
    __tablename__ = "runtime_config_revisions"
    __table_args__ = (
        CheckConstraint("config_schema_version = 1", name="schema_version"),
        Index(
            "uq_runtime_config_revisions_active",
            "individual_id",
            unique=True,
            postgresql_where=text("activated_at IS NOT NULL AND superseded_at IS NULL"),
        ),
    )

    config_revision_id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    individual_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "individuals.individual_id",
            name="fk_runtime_config_revisions_individual_id_individuals",
        )
    )
    config_schema_version: Mapped[int] = mapped_column(Integer)
    sanitized_config: Mapped[JsonObject] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RuntimeInstance(Base):
    __tablename__ = "runtime_instances"
    __table_args__ = (
        CheckConstraint(
            "status IN ('starting', 'running', 'stopping', 'stopped', 'crashed')",
            name="status",
        ),
    )

    runtime_instance_id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    individual_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "individuals.individual_id",
            name="fk_runtime_instances_individual_id_individuals",
        )
    )
    status: Mapped[str] = mapped_column(Text)
    host_id: Mapped[str] = mapped_column(Text)
    process_id: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    runtime_version: Mapped[str] = mapped_column(Text)
