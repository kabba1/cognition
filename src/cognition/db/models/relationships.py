"""Grounded social interpretations and open threads, without social authority."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cognition.db.base import Base
from cognition.protocols.common import JsonObject, new_id


class Relationship(Base):
    __tablename__ = "relationships"
    __table_args__ = (
        UniqueConstraint(
            "individual_id", "entity_id", name="uq_relationships_individual_entity"
        ),
        CheckConstraint("narrative ~ '[^[:space:]]'", name="narrative_nonblank"),
        CheckConstraint("rationale ~ '[^[:space:]]'", name="rationale_nonblank"),
        CheckConstraint(
            "jsonb_typeof(evidence_refs) = 'array'", name="evidence_refs_array"
        ),
        CheckConstraint("updated_at >= created_at", name="time_order"),
        CheckConstraint("revision >= 1", name="revision_positive"),
    )

    relationship_id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    individual_id: Mapped[UUID] = mapped_column(ForeignKey("individuals.individual_id"))
    entity_id: Mapped[UUID] = mapped_column(ForeignKey("entities.entity_id"))
    narrative: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text)
    evidence_refs: Mapped[list[JsonObject]] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default=text("1")
    )


class RelationshipThread(Base):
    __tablename__ = "relationship_threads"
    __table_args__ = (
        CheckConstraint("title ~ '[^[:space:]]'", name="title_nonblank"),
        CheckConstraint("summary ~ '[^[:space:]]'", name="summary_nonblank"),
        CheckConstraint("rationale ~ '[^[:space:]]'", name="rationale_nonblank"),
        CheckConstraint("status IN ('open','resolved','abandoned')", name="status"),
        CheckConstraint(
            "jsonb_typeof(evidence_refs) = 'array'", name="evidence_refs_array"
        ),
        CheckConstraint("updated_at >= created_at", name="time_order"),
        CheckConstraint("revision >= 1", name="revision_positive"),
    )

    thread_id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    individual_id: Mapped[UUID] = mapped_column(ForeignKey("individuals.individual_id"))
    relationship_id: Mapped[UUID] = mapped_column(
        ForeignKey("relationships.relationship_id")
    )
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    commitment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("commitments.commitment_id")
    )
    rationale: Mapped[str] = mapped_column(Text)
    evidence_refs: Mapped[list[JsonObject]] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default=text("1")
    )
