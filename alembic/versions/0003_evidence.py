"""Separate append-oriented event metadata, content, and administrative audit."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_evidence"
down_revision = "0002_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column(
            "event_sequence", sa.BigInteger(), sa.Identity(always=True), nullable=False
        ),
        sa.Column("individual_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=True),
        sa.Column("source_binding_id", sa.Uuid(), nullable=True),
        sa.Column("actor_entity_id", sa.Uuid(), nullable=True),
        sa.Column("causation_event_id", sa.Uuid(), nullable=True),
        sa.Column("correlation_id", sa.Uuid(), nullable=True),
        sa.Column("subject_kind", sa.Text(), nullable=True),
        sa.Column("subject_id", sa.Uuid(), nullable=True),
        sa.Column("provenance", postgresql.JSONB(none_as_null=True), nullable=False),
        sa.Column("runtime_version", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_events")),
        sa.UniqueConstraint("event_sequence", name=op.f("uq_events_event_sequence")),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_events_individual_id_individuals"),
        ),
        sa.ForeignKeyConstraint(
            ["causation_event_id"],
            ["events.event_id"],
            name=op.f("fk_events_causation_event_id_events"),
        ),
        sa.CheckConstraint("schema_version = 1", name=op.f("ck_events_schema_version")),
        sa.CheckConstraint(
            "length(event_type) > 0", name=op.f("ck_events_event_type_nonempty")
        ),
        sa.CheckConstraint(
            "length(runtime_version) > 0",
            name=op.f("ck_events_runtime_version_nonempty"),
        ),
        sa.CheckConstraint(
            "source_kind IN "
            "('runtime','model','connector','capability','admin','import')",
            name=op.f("ck_events_source_kind"),
        ),
        sa.CheckConstraint(
            "(subject_kind IS NULL AND subject_id IS NULL) OR "
            "(subject_kind IS NOT NULL AND length(subject_kind) > 0 "
            "AND subject_id IS NOT NULL)",
            name=op.f("ck_events_subject_pair"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(provenance) = 'object'",
            name=op.f("ck_events_provenance_object"),
        ),
    )
    op.create_index(
        "ix_events_individual_sequence", "events", ["individual_id", "event_sequence"]
    )
    op.create_index(
        "ix_events_individual_recorded_at", "events", ["individual_id", "recorded_at"]
    )
    op.create_index("ix_events_causation_event_id", "events", ["causation_event_id"])
    op.create_index("ix_events_correlation_id", "events", ["correlation_id"])
    op.create_index("ix_events_subject", "events", ["subject_kind", "subject_id"])
    op.create_table(
        "admin_audit",
        sa.Column("audit_id", sa.Uuid(), nullable=False),
        sa.Column("individual_id", sa.Uuid(), nullable=False),
        sa.Column("admin_principal_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("target_kind", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("before_state", postgresql.JSONB(none_as_null=True), nullable=False),
        sa.Column("after_state", postgresql.JSONB(none_as_null=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("audit_id", name=op.f("pk_admin_audit")),
        sa.UniqueConstraint("event_id", name=op.f("uq_admin_audit_event_id")),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_admin_audit_individual_id_individuals"),
        ),
        sa.ForeignKeyConstraint(
            ["admin_principal_id"],
            ["admin_principals.admin_principal_id"],
            name=op.f("fk_admin_audit_admin_principal_id_admin_principals"),
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.event_id"],
            name=op.f("fk_admin_audit_event_id_events"),
        ),
        sa.CheckConstraint(
            "length(operation) > 0", name=op.f("ck_admin_audit_operation_nonempty")
        ),
        sa.CheckConstraint(
            "length(target_kind) > 0", name=op.f("ck_admin_audit_target_kind_nonempty")
        ),
        sa.CheckConstraint(
            "length(reason) > 0", name=op.f("ck_admin_audit_reason_nonempty")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(before_state) = 'object'",
            name=op.f("ck_admin_audit_before_state_object"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(after_state) = 'object'",
            name=op.f("ck_admin_audit_after_state_object"),
        ),
    )
    op.create_index(
        "ix_admin_audit_individual_created_at",
        "admin_audit",
        ["individual_id", "created_at"],
    )
    op.create_index(
        "ix_admin_audit_admin_principal_id", "admin_audit", ["admin_principal_id"]
    )
    op.create_index(
        "ix_admin_audit_target", "admin_audit", ["target_kind", "target_id"]
    )
    op.create_table(
        "event_contents",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("blob_ref", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("sensitivity", sa.Text(), nullable=False),
        sa.Column("retention_class", sa.Text(), nullable=False),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redaction_audit_id", sa.Uuid(), nullable=True),
        sa.Column(
            "content_schema_version", sa.Integer(), server_default="1", nullable=False
        ),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_event_contents")),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.event_id"],
            name=op.f("fk_event_contents_event_id_events"),
        ),
        sa.ForeignKeyConstraint(
            ["redaction_audit_id"],
            ["admin_audit.audit_id"],
            name=op.f("fk_event_contents_redaction_audit_id_admin_audit"),
        ),
        sa.CheckConstraint(
            "content_schema_version = 1",
            name=op.f("ck_event_contents_content_schema_version"),
        ),
        sa.CheckConstraint(
            "length(content_type) > 0",
            name=op.f("ck_event_contents_content_type_nonempty"),
        ),
        sa.CheckConstraint(
            "sensitivity IN ('public','internal','sensitive')",
            name=op.f("ck_event_contents_sensitivity"),
        ),
        sa.CheckConstraint(
            "retention_class IN ('history','standard','ephemeral')",
            name=op.f("ck_event_contents_retention_class"),
        ),
        sa.CheckConstraint(
            "payload IS NULL OR jsonb_typeof(payload) = 'object'",
            name=op.f("ck_event_contents_payload_object"),
        ),
    )
    op.create_index(
        "ix_event_contents_retain_until", "event_contents", ["retain_until"]
    )
    op.create_index(
        "ix_event_contents_redaction_audit_id", "event_contents", ["redaction_audit_id"]
    )
    op.create_foreign_key(
        op.f("fk_individuals_fork_event_id_events"),
        "individuals",
        "events",
        ["fork_event_id"],
        ["event_id"],
    )
    op.create_foreign_key(
        op.f("fk_wakes_cause_event_id_events"),
        "wakes",
        "events",
        ["cause_event_id"],
        ["event_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_wakes_cause_event_id_events"), "wakes", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk_individuals_fork_event_id_events"), "individuals", type_="foreignkey"
    )
    op.drop_table("event_contents")
    op.drop_table("admin_audit")
    op.drop_table("events")
