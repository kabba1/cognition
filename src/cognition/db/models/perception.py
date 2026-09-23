"""Owned inbound streams, immutable observations and event-backed checkpoints."""

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
from cognition.protocols.common import JsonObject, new_id


class ConnectorBinding(Base):
    __tablename__ = "connector_bindings"
    __table_args__ = (
        UniqueConstraint("individual_id", "adapter_id", "source_id"),
        CheckConstraint("length(adapter_id) > 0", name="adapter_id_nonempty"),
        CheckConstraint("length(source_id) > 0", name="source_id_nonempty"),
        CheckConstraint("cursor_revision >= 0", name="cursor_revision_nonnegative"),
        CheckConstraint(
            "cursor_revision <> 0 OR cursor IS NULL", name="initial_cursor_null"
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("updated_at >= created_at", name="time_order"),
        Index("ix_connector_bindings_pending_wake_id", "pending_wake_id"),
        Index(
            "ix_connector_bindings_latest_receipt_event_id", "latest_receipt_event_id"
        ),
    )

    connector_binding_id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    individual_id: Mapped[UUID] = mapped_column(ForeignKey("individuals.individual_id"))
    adapter_id: Mapped[str] = mapped_column(Text)
    source_id: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    cursor: Mapped[str | None] = mapped_column(Text)
    cursor_revision: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0")
    )
    revision: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default=text("1")
    )
    pending_wake_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "inbound_wakes.wake_id",
            name="fk_connector_bindings_pending_wake_id_inbound_wakes",
            use_alter=True,
        )
    )
    latest_receipt_event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "ingestion_receipts.event_id",
            name="fk_connector_bindings_latest_receipt",
            use_alter=True,
        )
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InboundWake(Base):
    __tablename__ = "inbound_wakes"
    __table_args__ = (
        UniqueConstraint("creation_event_id"),
        CheckConstraint(
            "sealed_at IS NULL OR sealed_at >= created_at", name="time_order"
        ),
        Index("ix_inbound_wakes_individual_id", "individual_id"),
        Index("ix_inbound_wakes_connector_binding_id", "connector_binding_id"),
        Index(
            "uq_inbound_wakes_unsealed_binding",
            "connector_binding_id",
            unique=True,
            postgresql_where=text("sealed_at IS NULL"),
        ),
    )

    wake_id: Mapped[UUID] = mapped_column(ForeignKey("wakes.wake_id"), primary_key=True)
    individual_id: Mapped[UUID] = mapped_column(ForeignKey("individuals.individual_id"))
    connector_binding_id: Mapped[UUID] = mapped_column(
        ForeignKey("connector_bindings.connector_binding_id")
    )
    creation_event_id: Mapped[UUID] = mapped_column(ForeignKey("events.event_id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IngestionReceipt(Base):
    __tablename__ = "ingestion_receipts"
    __table_args__ = (
        UniqueConstraint("connector_binding_id", "after_cursor_revision"),
        CheckConstraint("policy_version = 1", name="policy_version"),
        CheckConstraint(
            "before_cursor_revision >= 0", name="before_revision_nonnegative"
        ),
        CheckConstraint(
            "after_cursor_revision = before_cursor_revision + 1", name="revision_step"
        ),
        CheckConstraint(
            "(previous_receipt_event_id IS NULL AND before_cursor_revision = 0 "
            "AND before_cursor IS NULL) OR (previous_receipt_event_id IS NOT NULL "
            "AND before_cursor_revision >= 1)",
            name="predecessor_initial",
        ),
        CheckConstraint(
            "authorizing_binding_revision >= 1", name="binding_revision_positive"
        ),
        CheckConstraint("recorded_at >= observed_at", name="time_order"),
        CheckConstraint("item_count BETWEEN 0 AND 32", name="item_count"),
        CheckConstraint("new_count >= 0", name="new_count_nonnegative"),
        CheckConstraint("duplicate_count >= 0", name="duplicate_count_nonnegative"),
        CheckConstraint("item_count = new_count + duplicate_count", name="count_sum"),
        CheckConstraint("source_page_hash ~ '^[0-9a-f]{64}$'", name="source_page_hash"),
        CheckConstraint("receipt_hash ~ '^[0-9a-f]{64}$'", name="receipt_hash"),
        Index("ix_ingestion_receipts_individual_id", "individual_id"),
        Index(
            "ix_ingestion_receipts_previous_receipt_event_id",
            "previous_receipt_event_id",
        ),
    )

    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.event_id"), primary_key=True
    )
    individual_id: Mapped[UUID] = mapped_column(ForeignKey("individuals.individual_id"))
    connector_binding_id: Mapped[UUID] = mapped_column(
        ForeignKey("connector_bindings.connector_binding_id")
    )
    policy_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1")
    )
    previous_receipt_event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ingestion_receipts.event_id")
    )
    before_cursor: Mapped[str | None] = mapped_column(Text)
    after_cursor: Mapped[str | None] = mapped_column(Text)
    before_cursor_revision: Mapped[int] = mapped_column(BigInteger)
    after_cursor_revision: Mapped[int] = mapped_column(BigInteger)
    authorizing_binding_revision: Mapped[int] = mapped_column(BigInteger)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    item_count: Mapped[int] = mapped_column(Integer)
    new_count: Mapped[int] = mapped_column(Integer)
    duplicate_count: Mapped[int] = mapped_column(Integer)
    source_page_hash: Mapped[str] = mapped_column(Text)
    receipt_hash: Mapped[str] = mapped_column(Text)


class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (
        UniqueConstraint("connector_binding_id", "dedup_key"),
        CheckConstraint("fingerprint_version = 1", name="fingerprint_version"),
        CheckConstraint("dedup_key ~ '^[0-9a-f]{64}$'", name="dedup_key"),
        CheckConstraint(
            "content_fingerprint ~ '^[0-9a-f]{64}$'", name="content_fingerprint"
        ),
        CheckConstraint(
            "jsonb_typeof(authentication) = 'object'", name="authentication_object"
        ),
        CheckConstraint(
            "raw_content_hash IS NULL OR raw_content_hash ~ '^[0-9a-f]{64}$'",
            name="raw_content_hash",
        ),
        Index("ix_observations_individual_id", "individual_id"),
        Index("ix_observations_inbound_wake_id", "inbound_wake_id"),
        Index("ix_observations_receipt_event_id", "receipt_event_id"),
    )

    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.event_id"), primary_key=True
    )
    individual_id: Mapped[UUID] = mapped_column(ForeignKey("individuals.individual_id"))
    connector_binding_id: Mapped[UUID] = mapped_column(
        ForeignKey("connector_bindings.connector_binding_id")
    )
    external_event_id: Mapped[str | None] = mapped_column(Text)
    dedup_key: Mapped[str] = mapped_column(Text)
    fingerprint_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1")
    )
    content_fingerprint: Mapped[str] = mapped_column(Text)
    external_content_type: Mapped[str | None] = mapped_column(Text)
    raw_content_hash: Mapped[str | None] = mapped_column(Text)
    authentication: Mapped[JsonObject] = mapped_column(JSONB)
    inbound_wake_id: Mapped[UUID] = mapped_column(ForeignKey("inbound_wakes.wake_id"))
    receipt_event_id: Mapped[UUID] = mapped_column(
        ForeignKey("ingestion_receipts.event_id")
    )
