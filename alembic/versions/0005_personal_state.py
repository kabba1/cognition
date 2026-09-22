"""Grounded personal projections with append-oriented before/after history."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_personal_state"
down_revision = "0004_cognition"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "entities",
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("individual_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "revision", sa.BigInteger(), server_default=sa.text("1"), nullable=False
        ),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_entities_revision_positive")),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_entities_individual_id_individuals"),
        ),
        sa.PrimaryKeyConstraint("entity_id", name=op.f("pk_entities")),
    )
    op.create_table(
        "projects",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("individual_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("desired_state", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
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
            "jsonb_typeof(evidence_refs) = 'array'",
            name=op.f("ck_projects_evidence_refs_array"),
        ),
        sa.CheckConstraint(
            "status IN ('active','paused','blocked','completed','abandoned')",
            name=op.f("ck_projects_status"),
        ),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_projects_revision_positive")),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_projects_individual_id_individuals"),
        ),
        sa.PrimaryKeyConstraint("project_id", name=op.f("pk_projects")),
    )
    op.create_table(
        "goals",
        sa.Column("goal_id", sa.Uuid(), nullable=False),
        sa.Column("individual_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("desired_state", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
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
            "jsonb_typeof(evidence_refs) = 'array'",
            name=op.f("ck_goals_evidence_refs_array"),
        ),
        sa.CheckConstraint(
            "origin IN ('self_generated','founding_orientation','interest_derived',"
            "'external_request_adopted','relationship_commitment','project_dependency')",
            name=op.f("ck_goals_origin"),
        ),
        sa.CheckConstraint(
            "status IN ('active','paused','blocked','completed','abandoned')",
            name=op.f("ck_goals_status"),
        ),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_goals_revision_positive")),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_goals_individual_id_individuals"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.project_id"],
            name=op.f("fk_goals_project_id_projects"),
        ),
        sa.PrimaryKeyConstraint("goal_id", name=op.f("pk_goals")),
    )
    op.create_table(
        "commitments",
        sa.Column("commitment_id", sa.Uuid(), nullable=False),
        sa.Column("individual_id", sa.Uuid(), nullable=False),
        sa.Column("counterparty_entity_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("terms", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
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
            "jsonb_typeof(evidence_refs) = 'array'",
            name=op.f("ck_commitments_evidence_refs_array"),
        ),
        sa.CheckConstraint(
            "status IN ('proposed','active','fulfilled','released','broken',"
            "'disputed')",
            name=op.f("ck_commitments_status"),
        ),
        sa.CheckConstraint(
            "revision >= 1", name=op.f("ck_commitments_revision_positive")
        ),
        sa.ForeignKeyConstraint(
            ["counterparty_entity_id"],
            ["entities.entity_id"],
            name=op.f("fk_commitments_counterparty_entity_id_entities"),
        ),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_commitments_individual_id_individuals"),
        ),
        sa.PrimaryKeyConstraint("commitment_id", name=op.f("pk_commitments")),
    )
    op.create_table(
        "beliefs",
        sa.Column("belief_id", sa.Uuid(), nullable=False),
        sa.Column("individual_id", sa.Uuid(), nullable=False),
        sa.Column("proposition", sa.Text(), nullable=False),
        sa.Column("subject_entity_id", sa.Uuid(), nullable=True),
        sa.Column("topic", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "supporting_evidence",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "contradicting_evidence",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("supersedes_belief_id", sa.Uuid(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "revision", sa.BigInteger(), server_default=sa.text("1"), nullable=False
        ),
        sa.CheckConstraint(
            "jsonb_typeof(contradicting_evidence) = 'array'",
            name=op.f("ck_beliefs_contradicting_evidence_array"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(supporting_evidence) = 'array'",
            name=op.f("ck_beliefs_supporting_evidence_array"),
        ),
        sa.CheckConstraint(
            "status IN ('tentative','accepted','disputed','superseded','withdrawn')",
            name=op.f("ck_beliefs_status"),
        ),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_beliefs_revision_positive")),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_beliefs_individual_id_individuals"),
        ),
        sa.ForeignKeyConstraint(
            ["subject_entity_id"],
            ["entities.entity_id"],
            name=op.f("fk_beliefs_subject_entity_id_entities"),
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_belief_id"],
            ["beliefs.belief_id"],
            name=op.f("fk_beliefs_supersedes_belief_id_beliefs"),
        ),
        sa.PrimaryKeyConstraint("belief_id", name=op.f("pk_beliefs")),
    )
    op.create_table(
        "episodes",
        sa.Column("episode_id", sa.Uuid(), nullable=False),
        sa.Column("individual_id", sa.Uuid(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "evidence_refs",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "entity_refs",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "project_refs",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "salience_factors",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "revision", sa.BigInteger(), server_default=sa.text("1"), nullable=False
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_refs) = 'array'",
            name=op.f("ck_episodes_evidence_refs_array"),
        ),
        sa.CheckConstraint("ends_at >= starts_at", name=op.f("ck_episodes_time_span")),
        sa.CheckConstraint(
            "jsonb_typeof(entity_refs) = 'array' AND NOT jsonb_path_exists("
            'entity_refs, \'$[*] ? (@.type() != "string" || '
            '!(@ like_regex "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
            "[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$\"))')",
            name=op.f("ck_episodes_entity_refs_uuid_array"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(project_refs) = 'array' AND NOT jsonb_path_exists("
            'project_refs, \'$[*] ? (@.type() != "string" || '
            '!(@ like_regex "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
            "[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$\"))')",
            name=op.f("ck_episodes_project_refs_uuid_array"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(salience_factors) = 'array' AND salience_factors <@ "
            '\'["consequence","novelty","active_commitment","relationship",'
            '"self_change","unresolved"]\'::jsonb',
            name=op.f("ck_episodes_salience_factors_array"),
        ),
        sa.CheckConstraint("revision = 1", name=op.f("ck_episodes_revision_one")),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_episodes_individual_id_individuals"),
        ),
        sa.PrimaryKeyConstraint("episode_id", name=op.f("pk_episodes")),
    )
    op.create_table(
        "personal_state_revisions",
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("individual_id", sa.Uuid(), nullable=False),
        sa.Column("object_kind", sa.Text(), nullable=False),
        sa.Column("object_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=True),
        sa.Column("turn_id", sa.Uuid(), nullable=True),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column(
            "before_json",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "after_json",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "before_json IS NULL OR jsonb_typeof(before_json) = 'object'",
            name=op.f("ck_personal_state_revisions_before_json_object"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(after_json) = 'object'",
            name=op.f("ck_personal_state_revisions_after_json_object"),
        ),
        sa.CheckConstraint(
            "operation_id IS NULL OR turn_id IS NOT NULL",
            name=op.f("ck_personal_state_revisions_operation_turn"),
        ),
        sa.CheckConstraint(
            "revision >= 1", name=op.f("ck_personal_state_revisions_revision_positive")
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.event_id"],
            name=op.f("fk_personal_state_revisions_event_id_events"),
        ),
        sa.ForeignKeyConstraint(
            ["individual_id"],
            ["individuals.individual_id"],
            name=op.f("fk_personal_state_revisions_individual_id_individuals"),
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["applied_operations.operation_id"],
            name=op.f("fk_personal_state_revisions_operation_id_applied_operations"),
        ),
        sa.ForeignKeyConstraint(
            ["turn_id"],
            ["cognition_turns.turn_id"],
            name=op.f("fk_personal_state_revisions_turn_id_cognition_turns"),
        ),
        sa.PrimaryKeyConstraint(
            "revision_id", name=op.f("pk_personal_state_revisions")
        ),
        sa.UniqueConstraint(
            "object_kind",
            "object_id",
            "revision",
            name="uq_personal_state_revisions_object_revision",
        ),
        sa.UniqueConstraint(
            "operation_id", name=op.f("uq_personal_state_revisions_operation_id")
        ),
    )


def downgrade() -> None:
    op.drop_table("personal_state_revisions")
    op.drop_table("episodes")
    op.drop_table("beliefs")
    op.drop_table("commitments")
    op.drop_table("goals")
    op.drop_table("projects")
    op.drop_table("entities")
