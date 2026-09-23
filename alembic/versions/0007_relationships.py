"""Grounded relationships and open social threads."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_relationships"
down_revision = "0006_identity_development"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "relationships",
        sa.Column("relationship_id", sa.Uuid(), nullable=False),
        sa.Column("individual_id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("narrative", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column(
            "evidence_refs",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "revision", sa.BigInteger(), server_default=sa.text("1"), nullable=False
        ),
        sa.CheckConstraint(
            "narrative ~ '[^[:space:]]'",
            name=op.f("ck_relationships_narrative_nonblank"),
        ),
        sa.CheckConstraint(
            "rationale ~ '[^[:space:]]'",
            name=op.f("ck_relationships_rationale_nonblank"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_refs) = 'array'",
            name=op.f("ck_relationships_evidence_refs_array"),
        ),
        sa.CheckConstraint(
            "updated_at >= created_at", name=op.f("ck_relationships_time_order")
        ),
        sa.CheckConstraint(
            "revision >= 1", name=op.f("ck_relationships_revision_positive")
        ),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_relationships_individual_id_individuals"),
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.entity_id"],
            name=op.f("fk_relationships_entity_id_entities"),
        ),
        sa.PrimaryKeyConstraint("relationship_id", name=op.f("pk_relationships")),
        sa.UniqueConstraint(
            "individual_id", "entity_id", name="uq_relationships_individual_entity"
        ),
    )
    op.create_table(
        "relationship_threads",
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("individual_id", sa.Uuid(), nullable=False),
        sa.Column("relationship_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("commitment_id", sa.Uuid(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column(
            "evidence_refs",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "revision", sa.BigInteger(), server_default=sa.text("1"), nullable=False
        ),
        sa.CheckConstraint(
            "title ~ '[^[:space:]]'",
            name=op.f("ck_relationship_threads_title_nonblank"),
        ),
        sa.CheckConstraint(
            "summary ~ '[^[:space:]]'",
            name=op.f("ck_relationship_threads_summary_nonblank"),
        ),
        sa.CheckConstraint(
            "rationale ~ '[^[:space:]]'",
            name=op.f("ck_relationship_threads_rationale_nonblank"),
        ),
        sa.CheckConstraint(
            "status IN ('open','resolved','abandoned')",
            name=op.f("ck_relationship_threads_status"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_refs) = 'array'",
            name=op.f("ck_relationship_threads_evidence_refs_array"),
        ),
        sa.CheckConstraint(
            "updated_at >= created_at", name=op.f("ck_relationship_threads_time_order")
        ),
        sa.CheckConstraint(
            "revision >= 1", name=op.f("ck_relationship_threads_revision_positive")
        ),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_relationship_threads_individual_id_individuals"),
        ),
        sa.ForeignKeyConstraint(
            ["relationship_id"],
            ["relationships.relationship_id"],
            name=op.f("fk_relationship_threads_relationship_id_relationships"),
        ),
        sa.ForeignKeyConstraint(
            ["commitment_id"],
            ["commitments.commitment_id"],
            name=op.f("fk_relationship_threads_commitment_id_commitments"),
        ),
        sa.PrimaryKeyConstraint("thread_id", name=op.f("pk_relationship_threads")),
    )


def downgrade() -> None:
    op.drop_table("relationship_threads")
    op.drop_table("relationships")
