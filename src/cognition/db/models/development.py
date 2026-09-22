"""Gradually established interests, preferences, and layered self-interpretation."""

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


class Interest(Base):
    __tablename__ = "interests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('candidate','established','dormant','retired')", name="status"
        ),
        CheckConstraint(
            "jsonb_typeof(evidence_refs) = 'array'", name="evidence_refs_array"
        ),
        CheckConstraint(
            "promotion_not_before >= created_at", name="promotion_eligibility"
        ),
        CheckConstraint(
            "retirement_not_before IS NULL OR retirement_not_before >= created_at",
            name="retirement_eligibility",
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
    )

    interest_id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    individual_id: Mapped[UUID] = mapped_column(ForeignKey("individuals.individual_id"))
    topic: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text)
    evidence_refs: Mapped[list[JsonObject]] = mapped_column(JSONB(none_as_null=True))
    promotion_not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    retirement_not_before: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default=text("1")
    )


class Preference(Base):
    __tablename__ = "preferences"
    __table_args__ = (
        CheckConstraint(
            "status IN ('tentative','established','retired')", name="status"
        ),
        CheckConstraint(
            "jsonb_typeof(evidence_refs) = 'array'", name="evidence_refs_array"
        ),
        CheckConstraint(
            "promotion_not_before >= created_at", name="promotion_eligibility"
        ),
        CheckConstraint(
            "retirement_not_before IS NULL OR retirement_not_before >= created_at",
            name="retirement_eligibility",
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
    )

    preference_id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    individual_id: Mapped[UUID] = mapped_column(ForeignKey("individuals.individual_id"))
    context: Mapped[str] = mapped_column(Text)
    statement: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text)
    evidence_refs: Mapped[list[JsonObject]] = mapped_column(JSONB(none_as_null=True))
    promotion_not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    retirement_not_before: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default=text("1")
    )


class SelfState(Base):
    __tablename__ = "self_states"
    __table_args__ = (
        UniqueConstraint(
            "individual_id", "layer", name="uq_self_states_individual_layer"
        ),
        CheckConstraint(
            "layer IN ('current_identity','self_belief','current_value','narrative')",
            name="layer",
        ),
        CheckConstraint(
            "content IS NULL OR jsonb_typeof(content) = 'object'", name="content_object"
        ),
        CheckConstraint(
            "jsonb_typeof(evidence_refs) = 'array'", name="evidence_refs_array"
        ),
        CheckConstraint(
            "pending_content IS NULL OR jsonb_typeof(pending_content) = 'object'",
            name="pending_content_object",
        ),
        CheckConstraint(
            "jsonb_typeof(pending_evidence_refs) = 'array'",
            name="pending_evidence_refs_array",
        ),
        CheckConstraint(
            "(pending_content IS NULL) = (pending_not_before IS NULL)",
            name="pending_pair",
        ),
        CheckConstraint(
            "content IS NOT NULL OR pending_content IS NOT NULL", name="content_present"
        ),
        CheckConstraint(
            "pending_not_before IS NULL OR pending_not_before >= created_at",
            name="pending_eligibility",
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
    )

    self_state_id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    individual_id: Mapped[UUID] = mapped_column(ForeignKey("individuals.individual_id"))
    layer: Mapped[str] = mapped_column(Text)
    content: Mapped[JsonObject | None] = mapped_column(JSONB(none_as_null=True))
    evidence_refs: Mapped[list[JsonObject]] = mapped_column(JSONB(none_as_null=True))
    pending_content: Mapped[JsonObject | None] = mapped_column(JSONB(none_as_null=True))
    pending_evidence_refs: Mapped[list[JsonObject]] = mapped_column(
        JSONB(none_as_null=True)
    )
    pending_not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rationale: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default=text("1")
    )
