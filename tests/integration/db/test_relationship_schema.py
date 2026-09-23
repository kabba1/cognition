"""PostgreSQL schema for grounded relationships and open social threads."""

from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from cognition.db.base import Base
from cognition.protocols.common import new_id
from cognition.stores.identity import create_individual

NOW = datetime(2026, 9, 22, 22, tzinfo=UTC)
TABLES = {"relationships", "relationship_threads"}


def test_relationship_migration_round_trip_and_metadata(db_engine, alembic_config):
    assert TABLES <= set(inspect(db_engine).get_table_names())
    with db_engine.begin() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_server_default": True}
        )
        assert compare_metadata(context, Base.metadata) == []
        alembic_config.attributes["connection"] = connection
        command.downgrade(alembic_config, "0006_identity_development")
        assert not TABLES.intersection(inspect(connection).get_table_names())
        assert {"entities", "self_states"} <= set(inspect(connection).get_table_names())
        command.upgrade(alembic_config, "head")
        assert compare_metadata(context, Base.metadata) == []
        for table in TABLES:
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0


@pytest.fixture
def individual(db_session_factory):
    with db_session_factory() as session, session.begin():
        person = create_individual(
            session,
            individual_id=new_id(),
            birth_at=NOW,
            birth_name="Social individual",
            founding_orientation="Explore carefully",
            creator_provenance={},
            temperament_seed={"curiosity": "seed"},
            founding_value_seed={"care": "seed"},
        )
        yield session, person


def test_birth_does_not_invent_relationships(individual):
    session, _ = individual
    for table in TABLES:
        assert session.scalar(text(f"SELECT count(*) FROM {table}")) == 0


@pytest.fixture
def graph(individual):
    from cognition.db.models.personal import Commitment, Entity
    from cognition.db.models.relationships import Relationship, RelationshipThread

    session, person = individual
    common = dict(individual_id=person.individual_id, created_at=NOW, updated_at=NOW)
    entity = Entity(**common, kind="person", display_name="Collaborator")
    session.add(entity)
    session.flush()
    commitment = Commitment(
        **common,
        counterparty_entity_id=entity.entity_id,
        title="Discuss findings",
        terms="Review the results together",
        status="active",
        rationale="Chosen commitment",
        evidence_refs=[],
    )
    relationship = Relationship(
        **common,
        entity_id=entity.entity_id,
        narrative="We have begun collaborating",
        rationale="An interpretation of the encounter",
        evidence_refs=[{"kind": "entity", "id": str(entity.entity_id)}],
    )
    session.add_all([commitment, relationship])
    session.flush()
    thread = RelationshipThread(
        **common,
        relationship_id=relationship.relationship_id,
        title="Findings review",
        summary="Arrange a discussion",
        status="open",
        commitment_id=commitment.commitment_id,
        rationale="A concrete topic remains open",
        evidence_refs=[{"kind": "entity", "id": str(entity.entity_id)}],
    )
    session.add(thread)
    session.flush()
    yield session, {row.__tablename__: row for row in (relationship, thread)}


def test_relationship_fields_round_trip(graph):
    session, rows = graph
    session.expire_all()
    for row in rows.values():
        assert row.revision == 1
        assert row.created_at == row.updated_at == NOW
        assert row.created_at.utcoffset() == timedelta(0)
        assert len(row.evidence_refs) == 1
    relationship = rows["relationships"]
    thread = rows["relationship_threads"]
    assert relationship.relationship_id.version == thread.thread_id.version == 4
    assert thread.relationship_id == relationship.relationship_id
    thread.commitment_id = None
    session.flush()
    session.expire(thread)
    assert thread.commitment_id is None


def test_relationship_named_keys_and_types(db_engine):
    schema = inspect(db_engine)
    for table in TABLES:
        primary = schema.get_pk_constraint(table)
        assert primary["name"] == f"pk_{table}"
        assert primary["constrained_columns"] == [
            "thread_id" if table == "relationship_threads" else "relationship_id"
        ]
        for check in schema.get_check_constraints(table):
            assert check["name"].startswith(f"ck_{table}_")
        for foreign_key in schema.get_foreign_keys(table):
            assert foreign_key["name"].startswith(f"fk_{table}_")
            assert foreign_key["options"].get("ondelete") in (None, "NO ACTION")
        for column in schema.get_columns(table):
            if column["name"].endswith("_at"):
                assert column["type"].timezone is True
            if column["name"] == "revision":
                assert str(column["type"]) == "BIGINT"


@pytest.mark.parametrize("table", sorted(TABLES))
def test_relationship_primary_keys_are_enforced(graph, table):
    session, rows = graph
    row = rows[table]
    values = {
        column.name: getattr(row, column.name) for column in row.__table__.columns
    }
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(row.__table__.insert().values(**values))
    assert error.value.orig.diag.constraint_name == f"pk_{table}"


@pytest.mark.parametrize(
    "table,column,target",
    [
        ("relationships", "individual_id", "individuals"),
        ("relationships", "entity_id", "entities"),
        ("relationship_threads", "individual_id", "individuals"),
        ("relationship_threads", "relationship_id", "relationships"),
        ("relationship_threads", "commitment_id", "commitments"),
    ],
)
def test_relationship_foreign_keys_reject_missing_links(graph, table, column, target):
    session, _ = graph
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(
            text(f"UPDATE {table} SET {column} = :missing"), {"missing": new_id()}
        )
    assert error.value.orig.diag.constraint_name == f"fk_{table}_{column}_{target}"


@pytest.mark.parametrize(
    "table,assignment,constraint",
    [
        *((table, "revision = 0", "revision_positive") for table in sorted(TABLES)),
        *(
            (table, "evidence_refs = '{}'::jsonb", "evidence_refs_array")
            for table in sorted(TABLES)
        ),
        *(
            (table, "evidence_refs = 'null'::jsonb", "evidence_refs_array")
            for table in sorted(TABLES)
        ),
        ("relationships", "narrative = '   '", "narrative_nonblank"),
        ("relationships", "rationale = E'\\t\\n'", "rationale_nonblank"),
        ("relationship_threads", "title = ''", "title_nonblank"),
        ("relationship_threads", "summary = E'\\t\\n'", "summary_nonblank"),
        ("relationship_threads", "rationale = '  '", "rationale_nonblank"),
        ("relationship_threads", "status = 'fulfilled'", "status"),
        *(
            (table, "updated_at = created_at - interval '1 second'", "time_order")
            for table in sorted(TABLES)
        ),
    ],
)
def test_relationship_constraints_reject_invalid_states(
    graph, table, assignment, constraint
):
    session, _ = graph
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(text(f"UPDATE {table} SET {assignment}"))
    assert error.value.orig.diag.constraint_name == f"ck_{table}_{constraint}"


def test_relationship_entity_is_unique_per_individual(graph):
    session, rows = graph
    row = rows["relationships"]
    values = {
        column.name: getattr(row, column.name) for column in row.__table__.columns
    }
    values["relationship_id"] = new_id()
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(row.__table__.insert().values(**values))
    assert error.value.orig.diag.constraint_name == "uq_relationships_individual_entity"


def test_thread_status_vocabulary_and_parent_deletion_protection(graph):
    session, rows = graph
    thread = rows["relationship_threads"]
    for status in ("open", "resolved", "abandoned"):
        thread.status = status
        session.flush()
        session.expire(thread)
        assert thread.status == status
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(text("DELETE FROM relationships"))
    assert (
        error.value.orig.diag.constraint_name
        == "fk_relationship_threads_relationship_id_relationships"
    )
