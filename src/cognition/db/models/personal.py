"""Grounded personal interpretations, chosen objectives, and revision history."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cognition.db.base import Base
from cognition.db.search import BELIEF_VECTOR_SQL, EPISODE_VECTOR_SQL
from cognition.protocols.common import JsonObject, new_id

_UUID_PATTERN = (
    "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class Entity(Base):
    __tablename__ = "entities"
    __table_args__ = (CheckConstraint("revision >= 1", name="revision_positive"),)

    entity_id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    individual_id: Mapped[UUID] = mapped_column(ForeignKey("individuals.individual_id"))
    kind: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default=text("1")
    )


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','paused','blocked','completed','abandoned')",
            name="status",
        ),
        CheckConstraint(
            "jsonb_typeof(evidence_refs) = 'array'", name="evidence_refs_array"
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
    )

    project_id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    individual_id: Mapped[UUID] = mapped_column(ForeignKey("individuals.individual_id"))
    title: Mapped[str] = mapped_column(Text)
    desired_state: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text)
    evidence_refs: Mapped[list[JsonObject]] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default=text("1")
    )


class Goal(Base):
    __tablename__ = "goals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','paused','blocked','completed','abandoned')",
            name="status",
        ),
        CheckConstraint(
            "origin IN ('self_generated','founding_orientation','interest_derived',"
            "'external_request_adopted','relationship_commitment','project_dependency')",
            name="origin",
        ),
        CheckConstraint(
            "jsonb_typeof(evidence_refs) = 'array'", name="evidence_refs_array"
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
    )

    goal_id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    individual_id: Mapped[UUID] = mapped_column(ForeignKey("individuals.individual_id"))
    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("projects.project_id"))
    title: Mapped[str] = mapped_column(Text)
    desired_state: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text)
    evidence_refs: Mapped[list[JsonObject]] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default=text("1")
    )


class Commitment(Base):
    __tablename__ = "commitments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('proposed','active','fulfilled','released','broken',"
            "'disputed')",
            name="status",
        ),
        CheckConstraint(
            "jsonb_typeof(evidence_refs) = 'array'", name="evidence_refs_array"
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
    )

    commitment_id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    individual_id: Mapped[UUID] = mapped_column(ForeignKey("individuals.individual_id"))
    counterparty_entity_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("entities.entity_id")
    )
    title: Mapped[str] = mapped_column(Text)
    terms: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rationale: Mapped[str] = mapped_column(Text)
    evidence_refs: Mapped[list[JsonObject]] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default=text("1")
    )


class Belief(Base):
    __tablename__ = "beliefs"
    __table_args__ = (
        Index("ix_beliefs_lexical", text(BELIEF_VECTOR_SQL), postgresql_using="gin"),
        CheckConstraint(
            "status IN ('tentative','accepted','disputed','superseded','withdrawn')",
            name="status",
        ),
        CheckConstraint(
            "jsonb_typeof(supporting_evidence) = 'array'",
            name="supporting_evidence_array",
        ),
        CheckConstraint(
            "jsonb_typeof(contradicting_evidence) = 'array'",
            name="contradicting_evidence_array",
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
    )

    belief_id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    individual_id: Mapped[UUID] = mapped_column(ForeignKey("individuals.individual_id"))
    proposition: Mapped[str] = mapped_column(Text)
    subject_entity_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("entities.entity_id")
    )
    topic: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    supporting_evidence: Mapped[list[JsonObject]] = mapped_column(
        JSONB(none_as_null=True)
    )
    contradicting_evidence: Mapped[list[JsonObject]] = mapped_column(
        JSONB(none_as_null=True)
    )
    supersedes_belief_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("beliefs.belief_id")
    )
    rationale: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default=text("1")
    )


class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (
        Index("ix_episodes_lexical", text(EPISODE_VECTOR_SQL), postgresql_using="gin"),
        CheckConstraint(
            "jsonb_typeof(evidence_refs) = 'array'", name="evidence_refs_array"
        ),
        CheckConstraint(
            "jsonb_typeof(entity_refs) = 'array' AND NOT jsonb_path_exists("
            'entity_refs, \'$[*] ? (@.type() != "string" || '
            f'!(@ like_regex "{_UUID_PATTERN}"))\')',
            name="entity_refs_uuid_array",
        ),
        CheckConstraint(
            "jsonb_typeof(project_refs) = 'array' AND NOT jsonb_path_exists("
            'project_refs, \'$[*] ? (@.type() != "string" || '
            f'!(@ like_regex "{_UUID_PATTERN}"))\')',
            name="project_refs_uuid_array",
        ),
        CheckConstraint(
            "jsonb_typeof(salience_factors) = 'array' AND salience_factors <@ "
            '\'["consequence","novelty","active_commitment","relationship",'
            '"self_change","unresolved"]\'::jsonb',
            name="salience_factors_array",
        ),
        CheckConstraint("ends_at >= starts_at", name="time_span"),
        CheckConstraint("revision = 1", name="revision_one"),
    )

    episode_id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    individual_id: Mapped[UUID] = mapped_column(ForeignKey("individuals.individual_id"))
    summary: Mapped[str] = mapped_column(Text)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evidence_refs: Mapped[list[JsonObject]] = mapped_column(JSONB(none_as_null=True))
    entity_refs: Mapped[list[str]] = mapped_column(JSONB(none_as_null=True))
    project_refs: Mapped[list[str]] = mapped_column(JSONB(none_as_null=True))
    salience_factors: Mapped[list[str]] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default=text("1")
    )


class PersonalStateRevision(Base):
    __tablename__ = "personal_state_revisions"
    __table_args__ = (
        UniqueConstraint(
            "object_kind",
            "object_id",
            "revision",
            name="uq_personal_state_revisions_object_revision",
        ),
        UniqueConstraint("operation_id"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint(
            "before_json IS NULL OR jsonb_typeof(before_json) = 'object'",
            name="before_json_object",
        ),
        CheckConstraint(
            "jsonb_typeof(after_json) = 'object'", name="after_json_object"
        ),
        CheckConstraint(
            "operation_id IS NULL OR turn_id IS NOT NULL", name="operation_turn"
        ),
    )

    revision_id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    individual_id: Mapped[UUID] = mapped_column(ForeignKey("individuals.individual_id"))
    object_kind: Mapped[str] = mapped_column(Text)
    object_id: Mapped[UUID]
    revision: Mapped[int] = mapped_column(BigInteger)
    operation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("applied_operations.operation_id")
    )
    turn_id: Mapped[UUID | None] = mapped_column(ForeignKey("cognition_turns.turn_id"))
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.event_id"))
    before_json: Mapped[JsonObject | None] = mapped_column(JSONB(none_as_null=True))
    after_json: Mapped[JsonObject] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
