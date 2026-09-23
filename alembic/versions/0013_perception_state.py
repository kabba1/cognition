"""Retain inbound stream identity, bounded membership and ingestion checkpoints."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013_perception_state"
down_revision = "0012_exploration_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connector_bindings",
        sa.Column("connector_binding_id", sa.Uuid(), nullable=False),
        sa.Column("individual_id", sa.Uuid(), nullable=False),
        sa.Column("adapter_id", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column(
            "enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column(
            "cursor_revision",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "revision", sa.BigInteger(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column("pending_wake_id", sa.Uuid(), nullable=True),
        sa.Column("latest_receipt_event_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "cursor_revision <> 0 OR cursor IS NULL",
            name=op.f("ck_connector_bindings_initial_cursor_null"),
        ),
        sa.CheckConstraint(
            "cursor_revision >= 0",
            name=op.f("ck_connector_bindings_cursor_revision_nonnegative"),
        ),
        sa.CheckConstraint(
            "length(adapter_id) > 0",
            name=op.f("ck_connector_bindings_adapter_id_nonempty"),
        ),
        sa.CheckConstraint(
            "length(source_id) > 0",
            name=op.f("ck_connector_bindings_source_id_nonempty"),
        ),
        sa.CheckConstraint(
            "revision >= 1", name=op.f("ck_connector_bindings_revision_positive")
        ),
        sa.CheckConstraint(
            "updated_at >= created_at", name=op.f("ck_connector_bindings_time_order")
        ),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_connector_bindings_individual_id_individuals"),
        ),
        sa.PrimaryKeyConstraint(
            "connector_binding_id", name=op.f("pk_connector_bindings")
        ),
        sa.UniqueConstraint(
            "individual_id",
            "adapter_id",
            "source_id",
            name=op.f("uq_connector_bindings_individual_id"),
        ),
    )
    op.create_index(
        "ix_connector_bindings_latest_receipt_event_id",
        "connector_bindings",
        ["latest_receipt_event_id"],
        unique=False,
    )
    op.create_index(
        "ix_connector_bindings_pending_wake_id",
        "connector_bindings",
        ["pending_wake_id"],
        unique=False,
    )
    op.create_table(
        "inbound_wakes",
        sa.Column("wake_id", sa.Uuid(), nullable=False),
        sa.Column("individual_id", sa.Uuid(), nullable=False),
        sa.Column("connector_binding_id", sa.Uuid(), nullable=False),
        sa.Column("creation_event_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "sealed_at IS NULL OR sealed_at >= created_at",
            name=op.f("ck_inbound_wakes_time_order"),
        ),
        sa.ForeignKeyConstraint(
            ["connector_binding_id"],
            ["connector_bindings.connector_binding_id"],
            name=op.f("fk_inbound_wakes_connector_binding_id_connector_bindings"),
        ),
        sa.ForeignKeyConstraint(
            ["creation_event_id"],
            ["events.event_id"],
            name=op.f("fk_inbound_wakes_creation_event_id_events"),
        ),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_inbound_wakes_individual_id_individuals"),
        ),
        sa.ForeignKeyConstraint(
            ["wake_id"], ["wakes.wake_id"], name=op.f("fk_inbound_wakes_wake_id_wakes")
        ),
        sa.PrimaryKeyConstraint("wake_id", name=op.f("pk_inbound_wakes")),
        sa.UniqueConstraint(
            "creation_event_id", name=op.f("uq_inbound_wakes_creation_event_id")
        ),
    )
    op.create_index(
        "ix_inbound_wakes_connector_binding_id",
        "inbound_wakes",
        ["connector_binding_id"],
        unique=False,
    )
    op.create_index(
        "ix_inbound_wakes_individual_id",
        "inbound_wakes",
        ["individual_id"],
        unique=False,
    )
    op.create_index(
        "uq_inbound_wakes_unsealed_binding",
        "inbound_wakes",
        ["connector_binding_id"],
        unique=True,
        postgresql_where=sa.text("sealed_at IS NULL"),
    )
    op.create_table(
        "ingestion_receipts",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("individual_id", sa.Uuid(), nullable=False),
        sa.Column("connector_binding_id", sa.Uuid(), nullable=False),
        sa.Column(
            "policy_version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column("previous_receipt_event_id", sa.Uuid(), nullable=True),
        sa.Column("before_cursor", sa.Text(), nullable=True),
        sa.Column("after_cursor", sa.Text(), nullable=True),
        sa.Column("before_cursor_revision", sa.BigInteger(), nullable=False),
        sa.Column("after_cursor_revision", sa.BigInteger(), nullable=False),
        sa.Column("authorizing_binding_revision", sa.BigInteger(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("new_count", sa.Integer(), nullable=False),
        sa.Column("duplicate_count", sa.Integer(), nullable=False),
        sa.Column("source_page_hash", sa.Text(), nullable=False),
        sa.Column("receipt_hash", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "receipt_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_ingestion_receipts_receipt_hash"),
        ),
        sa.CheckConstraint(
            "source_page_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_ingestion_receipts_source_page_hash"),
        ),
        sa.CheckConstraint(
            "(previous_receipt_event_id IS NULL AND before_cursor_revision = 0 "
            "AND before_cursor IS NULL) OR (previous_receipt_event_id IS NOT NULL "
            "AND before_cursor_revision >= 1)",
            name=op.f("ck_ingestion_receipts_predecessor_initial"),
        ),
        sa.CheckConstraint(
            "after_cursor_revision = before_cursor_revision + 1",
            name=op.f("ck_ingestion_receipts_revision_step"),
        ),
        sa.CheckConstraint(
            "authorizing_binding_revision >= 1",
            name=op.f("ck_ingestion_receipts_binding_revision_positive"),
        ),
        sa.CheckConstraint(
            "before_cursor_revision >= 0",
            name=op.f("ck_ingestion_receipts_before_revision_nonnegative"),
        ),
        sa.CheckConstraint(
            "duplicate_count >= 0",
            name=op.f("ck_ingestion_receipts_duplicate_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "item_count = new_count + duplicate_count",
            name=op.f("ck_ingestion_receipts_count_sum"),
        ),
        sa.CheckConstraint(
            "item_count BETWEEN 0 AND 32", name=op.f("ck_ingestion_receipts_item_count")
        ),
        sa.CheckConstraint(
            "new_count >= 0", name=op.f("ck_ingestion_receipts_new_count_nonnegative")
        ),
        sa.CheckConstraint(
            "policy_version = 1", name=op.f("ck_ingestion_receipts_policy_version")
        ),
        sa.CheckConstraint(
            "recorded_at >= observed_at", name=op.f("ck_ingestion_receipts_time_order")
        ),
        sa.ForeignKeyConstraint(
            ["connector_binding_id"],
            ["connector_bindings.connector_binding_id"],
            name=op.f("fk_ingestion_receipts_connector_binding_id_connector_bindings"),
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.event_id"],
            name=op.f("fk_ingestion_receipts_event_id_events"),
        ),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_ingestion_receipts_individual_id_individuals"),
        ),
        sa.ForeignKeyConstraint(
            ["previous_receipt_event_id"],
            ["ingestion_receipts.event_id"],
            name=op.f(
                "fk_ingestion_receipts_previous_receipt_event_id_ingestion_receipts"
            ),
        ),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_ingestion_receipts")),
        sa.UniqueConstraint(
            "connector_binding_id",
            "after_cursor_revision",
            name=op.f("uq_ingestion_receipts_connector_binding_id"),
        ),
    )
    op.create_index(
        "ix_ingestion_receipts_individual_id",
        "ingestion_receipts",
        ["individual_id"],
        unique=False,
    )
    op.create_index(
        "ix_ingestion_receipts_previous_receipt_event_id",
        "ingestion_receipts",
        ["previous_receipt_event_id"],
        unique=False,
    )
    op.create_table(
        "observations",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("individual_id", sa.Uuid(), nullable=False),
        sa.Column("connector_binding_id", sa.Uuid(), nullable=False),
        sa.Column("external_event_id", sa.Text(), nullable=True),
        sa.Column("dedup_key", sa.Text(), nullable=False),
        sa.Column(
            "fingerprint_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("content_fingerprint", sa.Text(), nullable=False),
        sa.Column("external_content_type", sa.Text(), nullable=True),
        sa.Column("raw_content_hash", sa.Text(), nullable=True),
        sa.Column("authentication", postgresql.JSONB(), nullable=False),
        sa.Column("inbound_wake_id", sa.Uuid(), nullable=False),
        sa.Column("receipt_event_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "content_fingerprint ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_observations_content_fingerprint"),
        ),
        sa.CheckConstraint(
            "dedup_key ~ '^[0-9a-f]{64}$'", name=op.f("ck_observations_dedup_key")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(authentication) = 'object'",
            name=op.f("ck_observations_authentication_object"),
        ),
        sa.CheckConstraint(
            "raw_content_hash IS NULL OR raw_content_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_observations_raw_content_hash"),
        ),
        sa.CheckConstraint(
            "fingerprint_version = 1", name=op.f("ck_observations_fingerprint_version")
        ),
        sa.ForeignKeyConstraint(
            ["connector_binding_id"],
            ["connector_bindings.connector_binding_id"],
            name=op.f("fk_observations_connector_binding_id_connector_bindings"),
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.event_id"],
            name=op.f("fk_observations_event_id_events"),
        ),
        sa.ForeignKeyConstraint(
            ["inbound_wake_id"],
            ["inbound_wakes.wake_id"],
            name=op.f("fk_observations_inbound_wake_id_inbound_wakes"),
        ),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_observations_individual_id_individuals"),
        ),
        sa.ForeignKeyConstraint(
            ["receipt_event_id"],
            ["ingestion_receipts.event_id"],
            name=op.f("fk_observations_receipt_event_id_ingestion_receipts"),
        ),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_observations")),
        sa.UniqueConstraint(
            "connector_binding_id",
            "dedup_key",
            name=op.f("uq_observations_connector_binding_id"),
        ),
    )
    op.create_index(
        "ix_observations_inbound_wake_id",
        "observations",
        ["inbound_wake_id"],
        unique=False,
    )
    op.create_index(
        "ix_observations_individual_id", "observations", ["individual_id"], unique=False
    )
    op.create_index(
        "ix_observations_receipt_event_id",
        "observations",
        ["receipt_event_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_connector_bindings_latest_receipt",
        "connector_bindings",
        "ingestion_receipts",
        ["latest_receipt_event_id"],
        ["event_id"],
        use_alter=True,
    )
    op.create_foreign_key(
        "fk_connector_bindings_pending_wake_id_inbound_wakes",
        "connector_bindings",
        "inbound_wakes",
        ["pending_wake_id"],
        ["wake_id"],
        use_alter=True,
    )


def downgrade() -> None:
    # Protect all retained source identity and evidence before changing even FKs.
    op.execute(
        "LOCK TABLE connector_bindings, inbound_wakes, ingestion_receipts, "
        "observations IN ACCESS EXCLUSIVE MODE"
    )
    populated = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM connector_bindings) "
            "OR EXISTS (SELECT 1 FROM inbound_wakes) "
            "OR EXISTS (SELECT 1 FROM ingestion_receipts) "
            "OR EXISTS (SELECT 1 FROM observations)"
        )
    )
    if populated:
        raise RuntimeError(
            "Cannot downgrade while perception bindings or history exist; "
            "no identity, checkpoint or observation was deleted"
        )
    op.drop_constraint(
        "fk_connector_bindings_pending_wake_id_inbound_wakes",
        "connector_bindings",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_connector_bindings_latest_receipt",
        "connector_bindings",
        type_="foreignkey",
    )
    op.drop_table("observations")
    op.drop_table("ingestion_receipts")
    op.drop_table("inbound_wakes")
    op.drop_table("connector_bindings")
