"""Persist bounded cognition stages and recoverable model decisions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_cognition"
down_revision = "0003_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cognition_cycles",
        sa.Column("cycle_id", sa.Uuid(), nullable=False),
        sa.Column("individual_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_reason", sa.Text(), nullable=True),
        sa.Column("max_turns", sa.Integer(), nullable=False),
        sa.Column("max_attempts_per_turn", sa.Integer(), nullable=False),
        sa.Column("max_wakes", sa.Integer(), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("min_wake_delay_seconds", sa.Float(), nullable=False),
        sa.Column(
            "revision", sa.BigInteger(), server_default=sa.text("1"), nullable=False
        ),
        sa.PrimaryKeyConstraint("cycle_id", name=op.f("pk_cognition_cycles")),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_cognition_cycles_individual_id_individuals"),
        ),
        sa.CheckConstraint(
            "status IN ('active','completed','failed')",
            name=op.f("ck_cognition_cycles_status"),
        ),
        sa.CheckConstraint(
            "max_turns > 0", name=op.f("ck_cognition_cycles_max_turns_positive")
        ),
        sa.CheckConstraint(
            "max_attempts_per_turn > 0",
            name=op.f("ck_cognition_cycles_max_attempts_positive"),
        ),
        sa.CheckConstraint(
            "max_wakes > 0", name=op.f("ck_cognition_cycles_max_wakes_positive")
        ),
        sa.CheckConstraint(
            "min_wake_delay_seconds >= 0",
            name=op.f("ck_cognition_cycles_min_wake_delay_nonnegative"),
        ),
        sa.CheckConstraint(
            "revision >= 1", name=op.f("ck_cognition_cycles_revision_positive")
        ),
    )
    op.create_index(
        "uq_cognition_cycles_active",
        "cognition_cycles",
        ["individual_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "cognition_cycle_wakes",
        sa.Column("cycle_id", sa.Uuid(), nullable=False),
        sa.Column("wake_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint(
            "cycle_id", "wake_id", name=op.f("pk_cognition_cycle_wakes")
        ),
        sa.UniqueConstraint("wake_id", name=op.f("uq_cognition_cycle_wakes_wake_id")),
        sa.ForeignKeyConstraint(
            ["cycle_id"],
            ["cognition_cycles.cycle_id"],
            name=op.f("fk_cognition_cycle_wakes_cycle_id_cognition_cycles"),
        ),
        sa.ForeignKeyConstraint(
            ["wake_id"],
            ["wakes.wake_id"],
            name=op.f("fk_cognition_cycle_wakes_wake_id_wakes"),
        ),
    )
    op.create_table(
        "cognition_turns",
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("cycle_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_id", sa.Uuid(), nullable=True),
        sa.Column("decision_json", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("decision_hash", sa.Text(), nullable=True),
        sa.Column(
            "validation_errors",
            postgresql.JSONB(none_as_null=True),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("disposition", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("turn_id", name=op.f("pk_cognition_turns")),
        sa.UniqueConstraint(
            "cycle_id", "ordinal", name="uq_cognition_turns_cycle_ordinal"
        ),
        sa.UniqueConstraint("decision_id", name=op.f("uq_cognition_turns_decision_id")),
        sa.ForeignKeyConstraint(
            ["cycle_id"],
            ["cognition_cycles.cycle_id"],
            name=op.f("fk_cognition_turns_cycle_id_cognition_cycles"),
        ),
        sa.CheckConstraint(
            "ordinal >= 1", name=op.f("ck_cognition_turns_ordinal_positive")
        ),
        sa.CheckConstraint(
            "status IN ('prepared','invoking','decided','applied','rejected','failed')",
            name=op.f("ck_cognition_turns_status"),
        ),
        sa.CheckConstraint(
            "disposition IN ('continue','wait','sleep')",
            name=op.f("ck_cognition_turns_disposition"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(validation_errors) = 'array'",
            name=op.f("ck_cognition_turns_validation_errors_array"),
        ),
    )
    op.create_table(
        "context_snapshots",
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("config_revision_id", sa.Uuid(), nullable=False),
        sa.Column("runtime_contract_version", sa.Text(), nullable=False),
        sa.Column("model_adapter", sa.Text(), nullable=False),
        sa.Column("requested_model", sa.Text(), nullable=False),
        sa.Column("request_json", postgresql.JSONB(none_as_null=True), nullable=False),
        sa.Column("rendered_context", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("selected_refs", postgresql.JSONB(none_as_null=True), nullable=False),
        sa.Column(
            "retrieval_reasons", postgresql.JSONB(none_as_null=True), nullable=False
        ),
        sa.Column("estimated_input_tokens", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("snapshot_id", name=op.f("pk_context_snapshots")),
        sa.UniqueConstraint("turn_id", name=op.f("uq_context_snapshots_turn_id")),
        sa.ForeignKeyConstraint(
            ["turn_id"],
            ["cognition_turns.turn_id"],
            name=op.f("fk_context_snapshots_turn_id_cognition_turns"),
        ),
        sa.ForeignKeyConstraint(
            ["config_revision_id"],
            ["runtime_config_revisions.config_revision_id"],
            name=op.f(
                "fk_context_snapshots_config_revision_id_runtime_config_revisions"
            ),
        ),
        sa.CheckConstraint(
            "estimated_input_tokens >= 0",
            name=op.f("ck_context_snapshots_input_tokens_nonnegative"),
        ),
    )
    op.create_table(
        "model_invocations",
        sa.Column("invocation_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_json", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("invocation_id", name=op.f("pk_model_invocations")),
        sa.UniqueConstraint(
            "turn_id", "attempt_number", name="uq_model_invocations_turn_attempt"
        ),
        sa.ForeignKeyConstraint(
            ["turn_id"],
            ["cognition_turns.turn_id"],
            name=op.f("fk_model_invocations_turn_id_cognition_turns"),
        ),
        sa.CheckConstraint(
            "attempt_number >= 1", name=op.f("ck_model_invocations_attempt_positive")
        ),
        sa.CheckConstraint(
            "status IN ('started','completed','failed','abandoned')",
            name=op.f("ck_model_invocations_status"),
        ),
    )
    op.create_table(
        "applied_operations",
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("individual_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("operation_id", name=op.f("pk_applied_operations")),
        sa.ForeignKeyConstraint(
            ["turn_id"],
            ["cognition_turns.turn_id"],
            name=op.f("fk_applied_operations_turn_id_cognition_turns"),
        ),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_applied_operations_individual_id_individuals"),
        ),
    )
    op.create_table(
        "attention_state",
        sa.Column("individual_id", sa.Uuid(), nullable=False),
        sa.Column("current_focus", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("last_cycle_id", sa.Uuid(), nullable=True),
        sa.Column("last_cognition_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "revision", sa.BigInteger(), server_default=sa.text("1"), nullable=False
        ),
        sa.PrimaryKeyConstraint("individual_id", name=op.f("pk_attention_state")),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_attention_state_individual_id_individuals"),
        ),
        sa.ForeignKeyConstraint(
            ["last_cycle_id"],
            ["cognition_cycles.cycle_id"],
            name=op.f("fk_attention_state_last_cycle_id_cognition_cycles"),
        ),
        sa.CheckConstraint(
            "revision >= 1", name=op.f("ck_attention_state_revision_positive")
        ),
    )


def downgrade() -> None:
    op.drop_table("attention_state")
    op.drop_table("applied_operations")
    op.drop_table("model_invocations")
    op.drop_table("context_snapshots")
    op.drop_table("cognition_turns")
    op.drop_table("cognition_cycle_wakes")
    op.drop_table("cognition_cycles")
