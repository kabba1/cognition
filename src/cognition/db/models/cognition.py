"""Durable cognition stages, frozen requests, and exactly-once internal effects."""

from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
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


class CognitionCycle(Base):
    __tablename__ = "cognition_cycles"
    __table_args__ = (
        CheckConstraint("status IN ('active','completed','failed')", name="status"),
        CheckConstraint("max_turns > 0", name="max_turns_positive"),
        CheckConstraint("max_attempts_per_turn > 0", name="max_attempts_positive"),
        CheckConstraint("max_wakes > 0", name="max_wakes_positive"),
        CheckConstraint(
            "min_wake_delay_seconds >= 0", name="min_wake_delay_nonnegative"
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
        Index(
            "uq_cognition_cycles_active",
            "individual_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    cycle_id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    individual_id: Mapped[UUID] = mapped_column(ForeignKey("individuals.individual_id"))
    status: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terminal_reason: Mapped[str | None] = mapped_column(Text)
    max_turns: Mapped[int] = mapped_column(Integer)
    max_attempts_per_turn: Mapped[int] = mapped_column(Integer)
    max_wakes: Mapped[int] = mapped_column(Integer)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    min_wake_delay_seconds: Mapped[float] = mapped_column(Float)
    revision: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default=text("1")
    )


class CycleWake(Base):
    __tablename__ = "cognition_cycle_wakes"
    __table_args__ = (UniqueConstraint("wake_id"),)

    cycle_id: Mapped[UUID] = mapped_column(
        ForeignKey("cognition_cycles.cycle_id"), primary_key=True
    )
    wake_id: Mapped[UUID] = mapped_column(ForeignKey("wakes.wake_id"), primary_key=True)


class CognitionTurn(Base):
    __tablename__ = "cognition_turns"
    __table_args__ = (
        UniqueConstraint(
            "cycle_id", "ordinal", name="uq_cognition_turns_cycle_ordinal"
        ),
        UniqueConstraint("decision_id"),
        CheckConstraint("ordinal >= 1", name="ordinal_positive"),
        CheckConstraint(
            "status IN ('prepared','invoking','decided','applied','rejected','failed')",
            name="status",
        ),
        CheckConstraint(
            "disposition IN ('continue','wait','sleep')", name="disposition"
        ),
        CheckConstraint(
            "jsonb_typeof(validation_errors) = 'array'", name="validation_errors_array"
        ),
    )

    turn_id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    cycle_id: Mapped[UUID] = mapped_column(ForeignKey("cognition_cycles.cycle_id"))
    ordinal: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_id: Mapped[UUID | None]
    decision_json: Mapped[JsonObject | None] = mapped_column(JSONB(none_as_null=True))
    decision_hash: Mapped[str | None] = mapped_column(Text)
    validation_errors: Mapped[list[JsonValue]] = mapped_column(
        JSONB(none_as_null=True), default=list, server_default=text("'[]'::jsonb")
    )
    disposition: Mapped[str | None] = mapped_column(Text)


class ContextSnapshot(Base):
    __tablename__ = "context_snapshots"
    __table_args__ = (
        UniqueConstraint("turn_id"),
        CheckConstraint("estimated_input_tokens >= 0", name="input_tokens_nonnegative"),
    )

    snapshot_id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    turn_id: Mapped[UUID] = mapped_column(ForeignKey("cognition_turns.turn_id"))
    config_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("runtime_config_revisions.config_revision_id")
    )
    runtime_contract_version: Mapped[str] = mapped_column(Text)
    model_adapter: Mapped[str] = mapped_column(Text)
    requested_model: Mapped[str] = mapped_column(Text)
    request_json: Mapped[JsonObject] = mapped_column(JSONB(none_as_null=True))
    rendered_context: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text)
    selected_refs: Mapped[list[JsonObject]] = mapped_column(JSONB(none_as_null=True))
    retrieval_reasons: Mapped[JsonObject] = mapped_column(JSONB(none_as_null=True))
    estimated_input_tokens: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    retain_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ModelInvocation(Base):
    __tablename__ = "model_invocations"
    __table_args__ = (
        UniqueConstraint(
            "turn_id", "attempt_number", name="uq_model_invocations_turn_attempt"
        ),
        CheckConstraint("attempt_number >= 1", name="attempt_positive"),
        CheckConstraint(
            "status IN ('started','completed','failed','abandoned')", name="status"
        ),
    )

    invocation_id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    turn_id: Mapped[UUID] = mapped_column(ForeignKey("cognition_turns.turn_id"))
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_json: Mapped[JsonObject | None] = mapped_column(JSONB(none_as_null=True))
    error_code: Mapped[str | None] = mapped_column(Text)


class AppliedOperation(Base):
    __tablename__ = "applied_operations"

    operation_id: Mapped[UUID] = mapped_column(primary_key=True)
    turn_id: Mapped[UUID] = mapped_column(ForeignKey("cognition_turns.turn_id"))
    individual_id: Mapped[UUID] = mapped_column(ForeignKey("individuals.individual_id"))
    kind: Mapped[str] = mapped_column(Text)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AttentionState(Base):
    __tablename__ = "attention_state"
    __table_args__ = (CheckConstraint("revision >= 1", name="revision_positive"),)

    individual_id: Mapped[UUID] = mapped_column(
        ForeignKey("individuals.individual_id"), primary_key=True
    )
    current_focus: Mapped[JsonObject | None] = mapped_column(JSONB(none_as_null=True))
    last_cycle_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("cognition_cycles.cycle_id")
    )
    last_cognition_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default=text("1")
    )
