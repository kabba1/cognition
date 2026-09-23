"""Retain adaptive heartbeat outcomes and deferred wake materialization."""

import sqlalchemy as sa
from alembic import op

revision = "0010_autonomy_state"
down_revision = "0009_lexical_retrieval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "autonomy_state",
        sa.Column("individual_id", sa.Uuid(), nullable=False),
        sa.Column(
            "policy_version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column("interval_seconds", sa.Float(), nullable=False),
        sa.Column("anchor_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("managed_wake_id", sa.Uuid(), nullable=True),
        sa.Column("last_completed_cycle_id", sa.Uuid(), nullable=True),
        sa.Column("config_revision_id", sa.Uuid(), nullable=False),
        sa.Column(
            "materialization_pending",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "revision", sa.BigInteger(), server_default=sa.text("1"), nullable=False
        ),
        sa.PrimaryKeyConstraint("individual_id", name=op.f("pk_autonomy_state")),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_autonomy_state_individual_id_individuals"),
        ),
        sa.ForeignKeyConstraint(
            ["managed_wake_id"],
            ["wakes.wake_id"],
            name=op.f("fk_autonomy_state_managed_wake_id_wakes"),
        ),
        sa.ForeignKeyConstraint(
            ["last_completed_cycle_id"],
            ["cognition_cycles.cycle_id"],
            name=op.f("fk_autonomy_state_last_completed_cycle_id_cognition_cycles"),
        ),
        sa.ForeignKeyConstraint(
            ["config_revision_id"],
            ["runtime_config_revisions.config_revision_id"],
            name=op.f("fk_autonomy_state_config_revision_id_runtime_config_revisions"),
        ),
        sa.UniqueConstraint(
            "managed_wake_id", name=op.f("uq_autonomy_state_managed_wake_id")
        ),
        sa.CheckConstraint(
            "policy_version = 1", name=op.f("ck_autonomy_state_policy_version")
        ),
        sa.CheckConstraint(
            "interval_seconds >= 1 AND interval_seconds < 'Infinity'::float8",
            name=op.f("ck_autonomy_state_interval_finite_positive"),
        ),
        sa.CheckConstraint(
            "revision >= 1", name=op.f("ck_autonomy_state_revision_positive")
        ),
        sa.CheckConstraint(
            "managed_wake_id IS NOT NULL OR materialization_pending",
            name=op.f("ck_autonomy_state_managed_wake_or_pending"),
        ),
    )


def downgrade() -> None:
    # Serialize the check with inserts so continuity cannot disappear on downgrade.
    op.execute("LOCK TABLE autonomy_state IN ACCESS EXCLUSIVE MODE")
    populated = op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM autonomy_state)")
    )
    if populated:
        raise RuntimeError(
            "Cannot downgrade while scheduler state exists; no continuity was deleted"
        )
    op.drop_table("autonomy_state")
