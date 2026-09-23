"""Canonical event metadata and separately redactable content."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cognition.db.base import Base
from cognition.db.search import EVENT_VECTOR_SQL
from cognition.protocols.common import JsonObject, new_id


class Event(Base):
    """Append-oriented facts about what was observed, without interpretation."""

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("event_sequence"),
        CheckConstraint("schema_version = 1", name="schema_version"),
        CheckConstraint("length(event_type) > 0", name="event_type_nonempty"),
        CheckConstraint("length(runtime_version) > 0", name="runtime_version_nonempty"),
        CheckConstraint(
            "source_kind IN "
            "('runtime','model','connector','capability','admin','import')",
            name="source_kind",
        ),
        CheckConstraint(
            "(subject_kind IS NULL AND subject_id IS NULL) OR "
            "(subject_kind IS NOT NULL AND length(subject_kind) > 0 "
            "AND subject_id IS NOT NULL)",
            name="subject_pair",
        ),
        CheckConstraint(
            "jsonb_typeof(provenance) = 'object'", name="provenance_object"
        ),
        Index("ix_events_individual_sequence", "individual_id", "event_sequence"),
        Index("ix_events_individual_recorded_at", "individual_id", "recorded_at"),
        Index("ix_events_causation_event_id", "causation_event_id"),
        Index("ix_events_correlation_id", "correlation_id"),
        Index("ix_events_subject", "subject_kind", "subject_id"),
    )

    event_id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    event_sequence: Mapped[int] = mapped_column(BigInteger, Identity(always=True))
    individual_id: Mapped[UUID] = mapped_column(ForeignKey("individuals.individual_id"))
    event_type: Mapped[str] = mapped_column(Text)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    source_kind: Mapped[str] = mapped_column(Text)
    source_id: Mapped[str | None] = mapped_column(Text)
    source_binding_id: Mapped[UUID | None]
    actor_entity_id: Mapped[UUID | None]
    causation_event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("events.event_id")
    )
    correlation_id: Mapped[UUID | None]
    subject_kind: Mapped[str | None] = mapped_column(Text)
    subject_id: Mapped[UUID | None]
    provenance: Mapped[JsonObject] = mapped_column(JSONB(none_as_null=True))
    runtime_version: Mapped[str] = mapped_column(Text)


class EventContent(Base):
    """Retention and redaction change content without erasing its event."""

    __tablename__ = "event_contents"
    __table_args__ = (
        Index(
            "ix_event_contents_lexical", text(EVENT_VECTOR_SQL), postgresql_using="gin"
        ),
        CheckConstraint("content_schema_version = 1", name="content_schema_version"),
        CheckConstraint("length(content_type) > 0", name="content_type_nonempty"),
        CheckConstraint(
            "sensitivity IN ('public','internal','sensitive')", name="sensitivity"
        ),
        CheckConstraint(
            "retention_class IN ('history','standard','ephemeral')",
            name="retention_class",
        ),
        CheckConstraint(
            "payload IS NULL OR jsonb_typeof(payload) = 'object'", name="payload_object"
        ),
        Index("ix_event_contents_retain_until", "retain_until"),
        Index("ix_event_contents_redaction_audit_id", "redaction_audit_id"),
    )

    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.event_id"), primary_key=True
    )
    content_type: Mapped[str] = mapped_column(Text)
    payload: Mapped[JsonObject | None] = mapped_column(JSONB(none_as_null=True))
    text: Mapped[str | None] = mapped_column(Text)
    blob_ref: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(Text)
    sensitivity: Mapped[str] = mapped_column(Text)
    retention_class: Mapped[str] = mapped_column(Text)
    retain_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    redacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    redaction_audit_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("admin_audit.audit_id")
    )
    content_schema_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1"
    )
