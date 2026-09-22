"""Gradually established interests, preferences, and layered self-state."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_identity_development"
down_revision = "0005_personal_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "interests",
        sa.Column("interest_id", sa.Uuid(), nullable=False),
        sa.Column("individual_id", sa.Uuid(), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column(
            "evidence_refs",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("promotion_not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retirement_not_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "revision", sa.BigInteger(), server_default=sa.text("1"), nullable=False
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_refs) = 'array'",
            name=op.f("ck_interests_evidence_refs_array"),
        ),
        sa.CheckConstraint(
            "status IN ('candidate','established','dormant','retired')",
            name=op.f("ck_interests_status"),
        ),
        sa.CheckConstraint(
            "promotion_not_before >= created_at",
            name=op.f("ck_interests_promotion_eligibility"),
        ),
        sa.CheckConstraint(
            "retirement_not_before IS NULL OR retirement_not_before >= created_at",
            name=op.f("ck_interests_retirement_eligibility"),
        ),
        sa.CheckConstraint(
            "revision >= 1", name=op.f("ck_interests_revision_positive")
        ),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_interests_individual_id_individuals"),
        ),
        sa.PrimaryKeyConstraint("interest_id", name=op.f("pk_interests")),
    )
    op.create_table(
        "preferences",
        sa.Column("preference_id", sa.Uuid(), nullable=False),
        sa.Column("individual_id", sa.Uuid(), nullable=False),
        sa.Column("context", sa.Text(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column(
            "evidence_refs",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("promotion_not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retirement_not_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "revision", sa.BigInteger(), server_default=sa.text("1"), nullable=False
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_refs) = 'array'",
            name=op.f("ck_preferences_evidence_refs_array"),
        ),
        sa.CheckConstraint(
            "status IN ('tentative','established','retired')",
            name=op.f("ck_preferences_status"),
        ),
        sa.CheckConstraint(
            "promotion_not_before >= created_at",
            name=op.f("ck_preferences_promotion_eligibility"),
        ),
        sa.CheckConstraint(
            "retirement_not_before IS NULL OR retirement_not_before >= created_at",
            name=op.f("ck_preferences_retirement_eligibility"),
        ),
        sa.CheckConstraint(
            "revision >= 1", name=op.f("ck_preferences_revision_positive")
        ),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_preferences_individual_id_individuals"),
        ),
        sa.PrimaryKeyConstraint("preference_id", name=op.f("pk_preferences")),
    )
    op.create_table(
        "self_states",
        sa.Column("self_state_id", sa.Uuid(), nullable=False),
        sa.Column("individual_id", sa.Uuid(), nullable=False),
        sa.Column("layer", sa.Text(), nullable=False),
        sa.Column(
            "content",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "evidence_refs",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "pending_content",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "pending_evidence_refs",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("pending_not_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "revision", sa.BigInteger(), server_default=sa.text("1"), nullable=False
        ),
        sa.CheckConstraint(
            "content IS NULL OR jsonb_typeof(content) = 'object'",
            name=op.f("ck_self_states_content_object"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_refs) = 'array'",
            name=op.f("ck_self_states_evidence_refs_array"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(pending_evidence_refs) = 'array'",
            name=op.f("ck_self_states_pending_evidence_refs_array"),
        ),
        sa.CheckConstraint(
            "layer IN ('current_identity','self_belief','current_value','narrative')",
            name=op.f("ck_self_states_layer"),
        ),
        sa.CheckConstraint(
            "pending_content IS NULL OR jsonb_typeof(pending_content) = 'object'",
            name=op.f("ck_self_states_pending_content_object"),
        ),
        sa.CheckConstraint(
            "(pending_content IS NULL) = (pending_not_before IS NULL)",
            name=op.f("ck_self_states_pending_pair"),
        ),
        sa.CheckConstraint(
            "content IS NOT NULL OR pending_content IS NOT NULL",
            name=op.f("ck_self_states_content_present"),
        ),
        sa.CheckConstraint(
            "pending_not_before IS NULL OR pending_not_before >= created_at",
            name=op.f("ck_self_states_pending_eligibility"),
        ),
        sa.CheckConstraint(
            "revision >= 1", name=op.f("ck_self_states_revision_positive")
        ),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_self_states_individual_id_individuals"),
        ),
        sa.PrimaryKeyConstraint("self_state_id", name=op.f("pk_self_states")),
        sa.UniqueConstraint(
            "individual_id", "layer", name="uq_self_states_individual_layer"
        ),
    )


def downgrade() -> None:
    op.drop_table("self_states")
    op.drop_table("preferences")
    op.drop_table("interests")
