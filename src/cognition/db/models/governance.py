"""Authority and authenticated administrator identity, not personal state."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
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


class GovernanceState(Base):
    __tablename__ = "governance_state"
    __table_args__ = (CheckConstraint("revision >= 1", name="revision_positive"),)

    individual_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "individuals.individual_id",
            name="fk_governance_state_individual_id_individuals",
        ),
        primary_key=True,
    )
    external_actions_blocked: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    inference_blocked: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    reconciliation_required: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    hard_boundaries: Mapped[JsonObject] = mapped_column(JSONB)
    budget_policy: Mapped[JsonObject] = mapped_column(JSONB)
    revision: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default=text("1")
    )


class AdminPrincipal(Base):
    __tablename__ = "admin_principals"
    __table_args__ = (
        UniqueConstraint(
            "individual_id",
            "authn_provider",
            "subject",
            name="uq_admin_principals_identity",
        ),
        CheckConstraint("length(role) > 0", name="role_nonempty"),
    )

    admin_principal_id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    individual_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "individuals.individual_id",
            name="fk_admin_principals_individual_id_individuals",
        )
    )
    authn_provider: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    principal_metadata: Mapped[JsonObject] = mapped_column("metadata", JSONB)
