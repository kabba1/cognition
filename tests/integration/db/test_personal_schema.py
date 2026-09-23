"""Native PostgreSQL constraints for grounded personal projections and history."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.migration import MigrationContext
from schema_metadata import compare_metadata
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from cognition.db.base import Base
from cognition.db.models import (
    AppliedOperation,
    CognitionCycle,
    CognitionTurn,
    Event,
    Individual,
)
from cognition.protocols.common import new_id

NOW = datetime(2026, 9, 22, 22, tzinfo=UTC)
TABLES = {
    "entities",
    "projects",
    "goals",
    "commitments",
    "beliefs",
    "episodes",
    "personal_state_revisions",
}


def test_personal_schema_migration_round_trip_leaves_empty_projections(
    db_engine, alembic_config
):
    assert TABLES <= set(inspect(db_engine).get_table_names())
    with db_engine.begin() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_server_default": True}
        )
        assert compare_metadata(context, Base.metadata) == []
        for table in TABLES:
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0
        alembic_config.attributes["connection"] = connection
        command.downgrade(alembic_config, "0004_cognition")
        assert not TABLES.intersection(inspect(connection).get_table_names())
        assert "cognition_turns" in inspect(connection).get_table_names()
        command.upgrade(alembic_config, "0005_personal_state")
        assert TABLES <= set(inspect(connection).get_table_names())
        for table in TABLES:
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0


@pytest.fixture
def graph(db_session_factory):
    from cognition.db.models.personal import (
        Belief,
        Commitment,
        Entity,
        Episode,
        Goal,
        PersonalStateRevision,
        Project,
    )

    with db_session_factory() as session, session.begin():
        person = Individual(
            birth_at=NOW,
            birth_name="Schema individual",
            founding_orientation="Explore carefully",
            creator_provenance={},
            operational_status="active",
        )
        session.add(person)
        session.flush()
        evidence = Event(
            individual_id=person.individual_id,
            event_type="personal.test",
            observed_at=NOW,
            recorded_at=NOW,
            source_kind="runtime",
            provenance={},
            runtime_version="test",
        )
        cycle = CognitionCycle(
            individual_id=person.individual_id,
            status="active",
            started_at=NOW,
            max_turns=3,
            max_attempts_per_turn=2,
            max_wakes=16,
            deadline_at=NOW + timedelta(seconds=120),
            min_wake_delay_seconds=1,
        )
        session.add_all([evidence, cycle])
        session.flush()
        turn = CognitionTurn(
            cycle_id=cycle.cycle_id,
            ordinal=1,
            status="prepared",
            created_at=NOW,
        )
        session.add(turn)
        session.flush()
        operation = AppliedOperation(
            operation_id=new_id(),
            turn_id=turn.turn_id,
            individual_id=person.individual_id,
            kind="goal_create",
            applied_at=NOW,
        )
        entity = Entity(
            individual_id=person.individual_id,
            kind="person",
            display_name="Observer",
            created_at=NOW,
            updated_at=NOW,
        )
        project = Project(
            individual_id=person.individual_id,
            title="Inquiry",
            desired_state="Understand",
            status="active",
            rationale="Chosen inquiry",
            evidence_refs=[],
            created_at=NOW,
            updated_at=NOW,
        )
        session.add_all([operation, entity, project])
        session.flush()
        goal = Goal(
            individual_id=person.individual_id,
            project_id=project.project_id,
            title="Observe",
            desired_state="A grounded account",
            status="active",
            origin="self_generated",
            rationale="Curiosity",
            evidence_refs=[],
            created_at=NOW,
            updated_at=NOW,
        )
        commitment = Commitment(
            individual_id=person.individual_id,
            counterparty_entity_id=entity.entity_id,
            title="Follow up",
            terms="Review evidence",
            status="proposed",
            due_at=None,
            rationale="Offer",
            evidence_refs=[],
            created_at=NOW,
            updated_at=NOW,
        )
        belief = Belief(
            individual_id=person.individual_id,
            proposition="A tentative interpretation",
            subject_entity_id=entity.entity_id,
            topic=None,
            status="tentative",
            supporting_evidence=[],
            contradicting_evidence=[],
            supersedes_belief_id=None,
            rationale="Observation",
            created_at=NOW,
            updated_at=NOW,
        )
        episode = Episode(
            individual_id=person.individual_id,
            summary="An interpreted observation",
            starts_at=NOW - timedelta(minutes=1),
            ends_at=NOW,
            evidence_refs=[{"kind": "event", "id": str(evidence.event_id)}],
            entity_refs=[str(entity.entity_id)],
            project_refs=[str(project.project_id)],
            salience_factors=["novelty"],
            created_at=NOW,
        )
        session.add_all([goal, commitment, belief, episode])
        session.flush()
        history = PersonalStateRevision(
            individual_id=person.individual_id,
            object_kind="goal",
            object_id=goal.goal_id,
            revision=1,
            operation_id=operation.operation_id,
            turn_id=turn.turn_id,
            event_id=evidence.event_id,
            before_json=None,
            after_json={
                "goal_id": str(goal.goal_id),
                "status": "active",
                "revision": 1,
            },
            created_at=NOW,
        )
        session.add(history)
        session.flush()
        yield (
            session,
            {
                row.__tablename__: row
                for row in (entity, project, goal, commitment, belief, episode, history)
            },
        )


def test_personal_fields_round_trip_without_invented_optional_content(graph):
    session, rows = graph
    session.expire_all()
    for row in rows.values():
        assert row.revision == 1
        assert row.created_at == NOW and row.created_at.utcoffset() == timedelta(0)
    assert rows["entities"].entity_id.version == 4
    assert rows["beliefs"].topic is None
    assert rows["beliefs"].supersedes_belief_id is None
    assert rows["commitments"].due_at is None
    assert rows["personal_state_revisions"].before_json is None
    assert rows["episodes"].entity_refs == [str(rows["entities"].entity_id)]
    assert rows["episodes"].project_refs == [str(rows["projects"].project_id)]


def test_personal_schema_uses_named_keys_bigint_revisions_and_utc_columns(db_engine):
    schema = inspect(db_engine)
    for table in TABLES:
        assert schema.get_pk_constraint(table)["name"] == f"pk_{table}"
        for constraint in schema.get_check_constraints(table):
            assert constraint["name"].startswith(f"ck_{table}_")
        for foreign_key in schema.get_foreign_keys(table):
            assert foreign_key["name"].startswith(f"fk_{table}_")
            assert foreign_key["options"].get("ondelete") in (None, "NO ACTION")
        for column in schema.get_columns(table):
            if column["name"].endswith("_at"):
                assert column["type"].timezone is True
            if column["name"] == "revision":
                assert str(column["type"]) == "BIGINT"
            if column["name"] in ("status", "origin", "kind", "object_kind"):
                assert str(column["type"]) == "TEXT"


@pytest.mark.parametrize("table", sorted(TABLES))
def test_personal_object_primary_keys_cannot_be_reused(graph, table):
    session, rows = graph
    row = rows[table]
    values = {
        column.name: getattr(row, column.name) for column in row.__table__.columns
    }
    if table == "personal_state_revisions":
        # Keep the other unique keys distinct so this exercises the primary ID.
        values.update(object_id=new_id(), operation_id=None)
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(row.__table__.insert().values(**values))
    assert error.value.orig.diag.constraint_name == f"pk_{table}"


@pytest.mark.parametrize("table", sorted(TABLES))
def test_individual_foreign_keys_reject_orphaned_personal_state(graph, table):
    session, _ = graph
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(
            text(f"UPDATE {table} SET individual_id = :missing"), {"missing": new_id()}
        )
    assert (
        error.value.orig.diag.constraint_name == f"fk_{table}_individual_id_individuals"
    )


@pytest.mark.parametrize(
    "table,column,target",
    [
        ("goals", "project_id", "projects"),
        ("commitments", "counterparty_entity_id", "entities"),
        ("beliefs", "subject_entity_id", "entities"),
        ("beliefs", "supersedes_belief_id", "beliefs"),
        ("personal_state_revisions", "operation_id", "applied_operations"),
        ("personal_state_revisions", "turn_id", "cognition_turns"),
        ("personal_state_revisions", "event_id", "events"),
    ],
)
def test_provenance_and_object_links_require_existing_rows(
    graph, table, column, target
):
    session, _ = graph
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(
            text(f"UPDATE {table} SET {column} = :missing"), {"missing": new_id()}
        )
    assert error.value.orig.diag.constraint_name.startswith(
        f"fk_{table}_{column}_{target}"[:55]
    )


@pytest.mark.parametrize("table", sorted(TABLES))
def test_revision_cannot_be_zero(graph, table):
    session, _ = graph
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(text(f"UPDATE {table} SET revision = 0"))
    suffix = "revision_one" if table == "episodes" else "revision_positive"
    assert error.value.orig.diag.constraint_name == f"ck_{table}_{suffix}"


@pytest.mark.parametrize("table", ["projects", "goals", "commitments", "beliefs"])
def test_status_vocabulary_rejects_unknown_values(graph, table):
    session, _ = graph
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(text(f"UPDATE {table} SET status = 'imagined'"))
    assert error.value.orig.diag.constraint_name == f"ck_{table}_status"


@pytest.mark.parametrize(
    "table,statuses",
    [
        ("projects", ["active", "paused", "blocked", "completed", "abandoned"]),
        ("goals", ["active", "paused", "blocked", "completed", "abandoned"]),
        (
            "commitments",
            ["proposed", "active", "fulfilled", "released", "broken", "disputed"],
        ),
        ("beliefs", ["tentative", "accepted", "disputed", "superseded", "withdrawn"]),
    ],
)
def test_all_protocol_lifecycle_values_can_be_persisted(graph, table, statuses):
    session, rows = graph
    for status in statuses:
        row = rows[table]
        row.status = status
        session.flush()
        session.expire(row)
        assert row.status == status


def test_goal_origins_match_v1_and_reject_unknown_origin(graph):
    session, rows = graph
    goal = rows["goals"]
    for origin in (
        "self_generated",
        "founding_orientation",
        "interest_derived",
        "external_request_adopted",
        "relationship_commitment",
        "project_dependency",
    ):
        goal.origin = origin
        session.flush()
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(text("UPDATE goals SET origin = 'administrator_authority'"))
    assert error.value.orig.diag.constraint_name == "ck_goals_origin"


@pytest.mark.parametrize(
    "table,column",
    [
        ("projects", "evidence_refs"),
        ("goals", "evidence_refs"),
        ("commitments", "evidence_refs"),
        ("beliefs", "supporting_evidence"),
        ("beliefs", "contradicting_evidence"),
        ("episodes", "evidence_refs"),
        ("episodes", "entity_refs"),
        ("episodes", "project_refs"),
        ("episodes", "salience_factors"),
    ],
)
def test_json_collections_require_arrays(graph, table, column):
    session, _ = graph
    with pytest.raises(IntegrityError), session.begin_nested():
        session.execute(text(f"UPDATE {table} SET {column} = '{{}}'::jsonb"))


@pytest.mark.parametrize("column", ["entity_refs", "project_refs"])
@pytest.mark.parametrize("value", [[12], [None], ["not-a-uuid"]])
def test_episode_object_references_are_uuid_string_arrays(graph, column, value):
    session, _ = graph
    with pytest.raises(IntegrityError), session.begin_nested():
        session.execute(
            text(f"UPDATE episodes SET {column} = CAST(:value AS jsonb)"),
            {"value": json.dumps(value)},
        )


@pytest.mark.parametrize("value", [[12], [None], ["imagined_emotion"]])
def test_episode_salience_uses_protocol_categories(graph, value):
    session, _ = graph
    with pytest.raises(IntegrityError), session.begin_nested():
        session.execute(
            text("UPDATE episodes SET salience_factors = CAST(:value AS jsonb)"),
            {"value": json.dumps(value)},
        )


@pytest.mark.parametrize("column", ["before_json", "after_json"])
def test_history_snapshots_require_json_objects(graph, column):
    session, _ = graph
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(
            text(f"UPDATE personal_state_revisions SET {column} = '[]'::jsonb")
        )
    assert (
        error.value.orig.diag.constraint_name
        == f"ck_personal_state_revisions_{column}_object"
    )


def test_history_event_and_after_state_cannot_be_missing(graph):
    session, _ = graph
    for column in ("event_id", "after_json"):
        with pytest.raises(IntegrityError) as error, session.begin_nested():
            session.execute(
                text(f"UPDATE personal_state_revisions SET {column} = NULL")
            )
        assert error.value.orig.diag.column_name == column


def test_episode_span_and_immutable_revision_constraints(graph):
    session, rows = graph
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(
            text("UPDATE episodes SET ends_at = starts_at - interval '1 second'")
        )
    assert error.value.orig.diag.constraint_name == "ck_episodes_time_span"
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(text("UPDATE episodes SET revision = 2"))
    assert error.value.orig.diag.constraint_name == "ck_episodes_revision_one"
    episode = rows["episodes"]
    episode.starts_at = None
    session.flush()
    episode.ends_at = None
    session.flush()
    session.expire(episode)
    assert episode.starts_at is episode.ends_at is None


def test_history_revision_identity_and_operation_identity_are_unique(graph):
    session, rows = graph
    row = rows["personal_state_revisions"]
    values = {
        column.name: getattr(row, column.name) for column in row.__table__.columns
    }
    values.update(revision_id=new_id(), operation_id=None)
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(row.__table__.insert().values(**values))
    assert (
        error.value.orig.diag.constraint_name
        == "uq_personal_state_revisions_object_revision"
    )
    values.update(object_id=new_id(), operation_id=row.operation_id)
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(row.__table__.insert().values(**values))
    assert (
        error.value.orig.diag.constraint_name
        == "uq_personal_state_revisions_operation_id"
    )


def test_nonexecutive_and_superseded_history_can_omit_operation_identity(graph):
    session, rows = graph
    row = rows["personal_state_revisions"]
    values = {
        column.name: getattr(row, column.name) for column in row.__table__.columns
    }
    values.update(
        revision_id=new_id(),
        operation_id=None,
        object_id=new_id(),
        object_kind="belief",
    )
    # The old belief's supersession history retains its turn and event, while the
    # new belief's revision alone owns the unique applied operation reference.
    session.execute(row.__table__.insert().values(**values))
    values.update(
        revision_id=new_id(),
        turn_id=None,
        object_id=rows["entities"].entity_id,
        object_kind="entity",
    )
    session.execute(row.__table__.insert().values(**values))
    values.update(
        revision_id=new_id(),
        object_id=rows["projects"].project_id,
        object_kind="project",
    )
    session.execute(row.__table__.insert().values(**values))


def test_history_operation_requires_a_turn(graph):
    session, _ = graph
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(text("UPDATE personal_state_revisions SET turn_id = NULL"))
    assert (
        error.value.orig.diag.constraint_name
        == "ck_personal_state_revisions_operation_turn"
    )
