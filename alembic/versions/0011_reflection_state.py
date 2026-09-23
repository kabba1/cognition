"""Retain reflection scheduling continuity and immutable historical batch scope."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_reflection_state"
down_revision = "0010_autonomy_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reflection_state",
        sa.Column("individual_id", sa.Uuid(), nullable=False),
        sa.Column(
            "policy_version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("managed_wake_id", sa.Uuid(), nullable=True),
        sa.Column("last_completed_cycle_id", sa.Uuid(), nullable=True),
        sa.Column("interest_cursor", sa.Uuid(), nullable=True),
        sa.Column("preference_cursor", sa.Uuid(), nullable=True),
        sa.Column("self_state_cursor", sa.Uuid(), nullable=True),
        sa.Column(
            "materialization_pending",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "revision", sa.BigInteger(), server_default=sa.text("1"), nullable=False
        ),
        sa.PrimaryKeyConstraint("individual_id", name=op.f("pk_reflection_state")),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_reflection_state_individual_id_individuals"),
        ),
        sa.ForeignKeyConstraint(
            ["managed_wake_id"],
            ["wakes.wake_id"],
            name=op.f("fk_reflection_state_managed_wake_id_wakes"),
        ),
        sa.ForeignKeyConstraint(
            ["last_completed_cycle_id"],
            ["cognition_cycles.cycle_id"],
            name=op.f("fk_reflection_state_last_completed_cycle_id_cognition_cycles"),
        ),
        sa.UniqueConstraint(
            "managed_wake_id", name=op.f("uq_reflection_state_managed_wake_id")
        ),
        sa.CheckConstraint(
            "policy_version = 1", name=op.f("ck_reflection_state_policy_version")
        ),
        sa.CheckConstraint(
            "revision >= 1", name=op.f("ck_reflection_state_revision_positive")
        ),
    )
    op.create_table(
        "managed_reflection_batches",
        sa.Column("wake_id", sa.Uuid(), nullable=False),
        sa.Column("individual_id", sa.Uuid(), nullable=False),
        sa.Column(
            "policy_version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column("target_metadata", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("wake_id", name=op.f("pk_managed_reflection_batches")),
        sa.ForeignKeyConstraint(
            ["wake_id"],
            ["wakes.wake_id"],
            name=op.f("fk_managed_reflection_batches_wake_id_wakes"),
        ),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_managed_reflection_batches_individual_id_individuals"),
        ),
        sa.CheckConstraint(
            "policy_version = 1",
            name=op.f("ck_managed_reflection_batches_policy_version"),
        ),
        sa.CheckConstraint(
            "CASE WHEN jsonb_typeof(target_metadata) = 'array' "
            "THEN jsonb_array_length(target_metadata) BETWEEN 1 AND 8 ELSE false END",
            name=op.f("ck_managed_reflection_batches_target_metadata_bounds"),
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_managed_reflection_batches_content_hash"),
        ),
    )
    op.create_index(
        "ix_managed_reflection_batches_individual_id",
        "managed_reflection_batches",
        ["individual_id"],
    )


def downgrade() -> None:
    # Lock both before inspecting either; no insert may race the continuity check.
    op.execute(
        "LOCK TABLE reflection_state, managed_reflection_batches "
        "IN ACCESS EXCLUSIVE MODE"
    )
    populated = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM reflection_state) "
            "OR EXISTS (SELECT 1 FROM managed_reflection_batches)"
        )
    )
    if populated:
        raise RuntimeError(
            "Cannot downgrade while reflection state or historical batches exist; "
            "no continuity or deliberation scope was deleted"
        )
    op.drop_table("managed_reflection_batches")
    op.drop_table("reflection_state")
