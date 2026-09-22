"""Administrative changes are explicit, attributable evidence."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cognition.db.base import Base
from cognition.protocols.common import JsonObject, new_id


class AdminAudit(Base):
    __tablename__ = "admin_audit"
    __table_args__ = (
        UniqueConstraint("event_id"),
        CheckConstraint("length(operation) > 0", name="operation_nonempty"),
        CheckConstraint("length(target_kind) > 0", name="target_kind_nonempty"),
        CheckConstraint("length(reason) > 0", name="reason_nonempty"),
        CheckConstraint(
            "jsonb_typeof(before_state) = 'object'", name="before_state_object"
        ),
        CheckConstraint(
            "jsonb_typeof(after_state) = 'object'", name="after_state_object"
        ),
        Index("ix_admin_audit_individual_created_at", "individual_id", "created_at"),
        Index("ix_admin_audit_admin_principal_id", "admin_principal_id"),
        Index("ix_admin_audit_target", "target_kind", "target_id"),
    )

    audit_id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    individual_id: Mapped[UUID] = mapped_column(ForeignKey("individuals.individual_id"))
    admin_principal_id: Mapped[UUID] = mapped_column(
        ForeignKey("admin_principals.admin_principal_id")
    )
    operation: Mapped[str] = mapped_column(Text)
    target_kind: Mapped[str] = mapped_column(Text)
    target_id: Mapped[UUID]
    reason: Mapped[str] = mapped_column(Text)
    before_state: Mapped[JsonObject] = mapped_column(JSONB(none_as_null=True))
    after_state: Mapped[JsonObject] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.event_id"))
