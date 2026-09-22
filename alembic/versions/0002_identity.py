"""Identity, governance, runtime configuration history, and bootstrap wakes.

Revision ID: 0002_identity
Revises: 0001_foundation

Event references are UUID columns here; 0003_evidence adds their foreign keys
once the events table exists. UUID values are supplied by application code.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_identity"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "individuals",
        sa.Column("individual_id", sa.UUID(), nullable=False),
        sa.Column("birth_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("birth_name", sa.Text(), nullable=False),
        sa.Column("founding_orientation", sa.Text(), nullable=False),
        sa.Column("creator_provenance", postgresql.JSONB(), nullable=False),
        sa.Column("temperament_seed", postgresql.JSONB(), nullable=True),
        sa.Column("founding_value_seed", postgresql.JSONB(), nullable=True),
        sa.Column("parent_individual_id", sa.UUID(), nullable=True),
        sa.Column("fork_event_id", sa.UUID(), nullable=True),
        sa.Column("operational_status", sa.Text(), nullable=False),
        sa.Column(
            "revision", sa.BigInteger(), nullable=False, server_default=sa.text("1")
        ),
        sa.PrimaryKeyConstraint("individual_id", name=op.f("pk_individuals")),
        sa.ForeignKeyConstraint(
            ["parent_individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_individuals_parent_individual_id_individuals"),
        ),
        sa.CheckConstraint(
            "operational_status IN "
            "('active', 'paused', 'quiescing', 'quiescent', 'retired')",
            name=op.f("ck_individuals_operational_status"),
        ),
        sa.CheckConstraint(
            "revision >= 1", name=op.f("ck_individuals_revision_positive")
        ),
    )
    op.create_table(
        "governance_state",
        sa.Column("individual_id", sa.UUID(), nullable=False),
        sa.Column(
            "external_actions_blocked",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "inference_blocked",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "reconciliation_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("hard_boundaries", postgresql.JSONB(), nullable=False),
        sa.Column("budget_policy", postgresql.JSONB(), nullable=False),
        sa.Column(
            "revision", sa.BigInteger(), nullable=False, server_default=sa.text("1")
        ),
        sa.PrimaryKeyConstraint("individual_id", name=op.f("pk_governance_state")),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_governance_state_individual_id_individuals"),
        ),
        sa.CheckConstraint(
            "revision >= 1", name=op.f("ck_governance_state_revision_positive")
        ),
    )
    op.create_table(
        "admin_principals",
        sa.Column("admin_principal_id", sa.UUID(), nullable=False),
        sa.Column("individual_id", sa.UUID(), nullable=False),
        sa.Column("authn_provider", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("admin_principal_id", name=op.f("pk_admin_principals")),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_admin_principals_individual_id_individuals"),
        ),
        sa.UniqueConstraint(
            "individual_id",
            "authn_provider",
            "subject",
            name=op.f("uq_admin_principals_identity"),
        ),
        sa.CheckConstraint(
            "length(role) > 0", name=op.f("ck_admin_principals_role_nonempty")
        ),
    )
    op.create_table(
        "runtime_config_revisions",
        sa.Column("config_revision_id", sa.UUID(), nullable=False),
        sa.Column("individual_id", sa.UUID(), nullable=False),
        sa.Column("config_schema_version", sa.Integer(), nullable=False),
        sa.Column("sanitized_config", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint(
            "config_revision_id", name=op.f("pk_runtime_config_revisions")
        ),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_runtime_config_revisions_individual_id_individuals"),
        ),
        sa.CheckConstraint(
            "config_schema_version = 1",
            name=op.f("ck_runtime_config_revisions_schema_version"),
        ),
    )
    op.create_index(
        "uq_runtime_config_revisions_active",
        "runtime_config_revisions",
        ["individual_id"],
        unique=True,
        postgresql_where=sa.text("activated_at IS NOT NULL AND superseded_at IS NULL"),
    )
    op.create_table(
        "runtime_instances",
        sa.Column("runtime_instance_id", sa.UUID(), nullable=False),
        sa.Column("individual_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("host_id", sa.Text(), nullable=False),
        sa.Column("process_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("runtime_version", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "runtime_instance_id", name=op.f("pk_runtime_instances")
        ),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_runtime_instances_individual_id_individuals"),
        ),
        sa.CheckConstraint(
            "status IN ('starting', 'running', 'stopping', 'stopped', 'crashed')",
            name=op.f("ck_runtime_instances_status"),
        ),
    )
    op.create_table(
        "wakes",
        sa.Column("wake_id", sa.UUID(), nullable=False),
        sa.Column("individual_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("cause_event_id", sa.UUID(), nullable=True),
        sa.Column("context_refs", postgresql.JSONB(), nullable=False),
        sa.Column("coalesce_key", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "revision", sa.BigInteger(), nullable=False, server_default=sa.text("1")
        ),
        sa.PrimaryKeyConstraint("wake_id", name=op.f("pk_wakes")),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_wakes_individual_id_individuals"),
        ),
        sa.CheckConstraint(
            "kind IN ('bootstrap', 'external_event', 'action_result', "
            "'commitment_due', "
            "'self_scheduled', 'goal_review', 'routine', 'reflection', 'maintenance', "
            "'heartbeat', 'recovery')",
            name=op.f("ck_wakes_kind"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'claimed', 'consumed', 'cancelled', 'superseded')",
            name=op.f("ck_wakes_status"),
        ),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_wakes_revision_positive")),
    )
    op.create_index(
        "ix_wakes_pending_due",
        "wakes",
        ["individual_id", "due_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "uq_wakes_pending_coalesce",
        "wakes",
        ["individual_id", "coalesce_key"],
        unique=True,
        postgresql_where=sa.text("status = 'pending' AND coalesce_key IS NOT NULL"),
    )


def downgrade() -> None:
    """Drop Phase 1 identity state in dependency order (intentionally destructive)."""
    op.drop_index("uq_wakes_pending_coalesce", table_name="wakes")
    op.drop_index("ix_wakes_pending_due", table_name="wakes")
    op.drop_table("wakes")
    op.drop_table("runtime_instances")
    op.drop_index(
        "uq_runtime_config_revisions_active", table_name="runtime_config_revisions"
    )
    op.drop_table("runtime_config_revisions")
    op.drop_table("admin_principals")
    op.drop_table("governance_state")
    op.drop_table("individuals")
