"""Retain exploration cadence, immutable authorization and physical limits."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012_exploration_state"
down_revision = "0011_reflection_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exploration_state",
        sa.Column("individual_id", sa.Uuid(), nullable=False),
        sa.Column(
            "policy_version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column("next_eligible_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("managed_wake_id", sa.Uuid(), nullable=True),
        sa.Column("last_terminal_cycle_id", sa.Uuid(), nullable=True),
        sa.Column("last_outcome_event_id", sa.Uuid(), nullable=True),
        sa.Column(
            "materialization_pending",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "revision", sa.BigInteger(), server_default=sa.text("1"), nullable=False
        ),
        sa.PrimaryKeyConstraint("individual_id", name=op.f("pk_exploration_state")),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_exploration_state_individual_id_individuals"),
        ),
        sa.ForeignKeyConstraint(
            ["managed_wake_id"],
            ["wakes.wake_id"],
            name=op.f("fk_exploration_state_managed_wake_id_wakes"),
        ),
        sa.ForeignKeyConstraint(
            ["last_terminal_cycle_id"],
            ["cognition_cycles.cycle_id"],
            name=op.f("fk_exploration_state_last_terminal_cycle_id_cognition_cycles"),
        ),
        sa.ForeignKeyConstraint(
            ["last_outcome_event_id"],
            ["events.event_id"],
            name=op.f("fk_exploration_state_last_outcome_event_id_events"),
        ),
        sa.UniqueConstraint(
            "managed_wake_id", name=op.f("uq_exploration_state_managed_wake_id")
        ),
        sa.CheckConstraint(
            "policy_version = 1", name=op.f("ck_exploration_state_policy_version")
        ),
        sa.CheckConstraint(
            "revision >= 1", name=op.f("ck_exploration_state_revision_positive")
        ),
    )
    op.create_table(
        "exploration_grants",
        sa.Column("wake_id", sa.Uuid(), nullable=False),
        sa.Column("individual_id", sa.Uuid(), nullable=False),
        sa.Column(
            "policy_version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column("authorizing_governance_revision", sa.BigInteger(), nullable=False),
        sa.Column("policy_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("not_before_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("max_turns", sa.Integer(), nullable=False),
        sa.Column("max_attempts_per_turn", sa.Integer(), nullable=False),
        sa.Column("max_seconds", sa.Integer(), nullable=False),
        sa.Column("max_wakes", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("wake_id", name=op.f("pk_exploration_grants")),
        sa.ForeignKeyConstraint(
            ["wake_id"],
            ["wakes.wake_id"],
            name=op.f("fk_exploration_grants_wake_id_wakes"),
        ),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_exploration_grants_individual_id_individuals"),
        ),
        sa.CheckConstraint(
            "policy_version = 1", name=op.f("ck_exploration_grants_policy_version")
        ),
        sa.CheckConstraint(
            "authorizing_governance_revision >= 1",
            name=op.f("ck_exploration_grants_governance_revision_positive"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(policy_snapshot) = 'object'",
            name=op.f("ck_exploration_grants_policy_snapshot_object"),
        ),
        sa.CheckConstraint(
            "scope = 'internal'", name=op.f("ck_exploration_grants_scope")
        ),
        sa.CheckConstraint(
            "max_turns = 1", name=op.f("ck_exploration_grants_max_turns")
        ),
        sa.CheckConstraint(
            "max_attempts_per_turn = 2",
            name=op.f("ck_exploration_grants_max_attempts_per_turn"),
        ),
        sa.CheckConstraint(
            "max_seconds = 120", name=op.f("ck_exploration_grants_max_seconds")
        ),
        sa.CheckConstraint(
            "max_wakes = 1", name=op.f("ck_exploration_grants_max_wakes")
        ),
        sa.CheckConstraint(
            "not_before_at >= created_at", name=op.f("ck_exploration_grants_time_order")
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_exploration_grants_content_hash"),
        ),
    )
    op.create_index(
        "ix_exploration_grants_individual_id", "exploration_grants", ["individual_id"]
    )


def downgrade() -> None:
    # Protect both continuity and historical authorization against concurrent writes.
    op.execute(
        "LOCK TABLE exploration_state, exploration_grants IN ACCESS EXCLUSIVE MODE"
    )
    populated = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM exploration_state) "
            "OR EXISTS (SELECT 1 FROM exploration_grants)"
        )
    )
    if populated:
        raise RuntimeError(
            "Cannot downgrade while exploration state or historical grants exist; "
            "no continuity or authorization was deleted"
        )
    op.drop_table("exploration_grants")
    op.drop_table("exploration_state")
