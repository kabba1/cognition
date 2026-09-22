"""Migration-backed PostgreSQL constraints for developing personal state."""

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
TABLES = {"interests", "preferences", "self_states"}


def test_development_migration_round_trip_and_metadata(db_engine, alembic_config):
    assert TABLES <= set(inspect(db_engine).get_table_names())
    with db_engine.begin() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_server_default": True}
        )
        assert compare_metadata(context, Base.metadata) == []
        alembic_config.attributes["connection"] = connection
        command.downgrade(alembic_config, "0005_personal_state")
        assert not TABLES.intersection(inspect(connection).get_table_names())
        assert "personal_state_revisions" in inspect(connection).get_table_names()
        command.upgrade(alembic_config, "0006_identity_development")
        assert compare_metadata(context, Base.metadata) == []


@pytest.fixture
def individual(db_session_factory):
    with db_session_factory() as session, session.begin():
        person = create_individual(
            session,
            individual_id=new_id(),
            birth_at=NOW,
            birth_name="Developing individual",
            founding_orientation="Explore carefully",
            creator_provenance={},
            temperament_seed={"curiosity": "seed"},
            founding_value_seed={"care": "seed"},
        )
        yield session, person


def test_birth_does_not_invent_development_state(individual):
    session, _ = individual
    for table in TABLES:
        assert session.scalar(text(f"SELECT count(*) FROM {table}")) == 0


@pytest.fixture
def graph(individual):
    from cognition.db.models.development import Interest, Preference, SelfState

    session, person = individual
    common = dict(
        individual_id=person.individual_id,
        rationale="Grounded possibility",
        evidence_refs=[],
        created_at=NOW,
        updated_at=NOW,
    )
    interest = Interest(
        **common,
        topic="Patterns",
        summary="Explore patterns",
        status="candidate",
        promotion_not_before=NOW + timedelta(days=1),
    )
    preference = Preference(
        **common,
        context="Learning",
        statement="Consider examples",
        status="tentative",
        promotion_not_before=NOW + timedelta(days=1),
    )
    self_state = SelfState(
        **common,
        layer="self_belief",
        content=None,
        pending_content={"value": "I may like patterns"},
        pending_evidence_refs=[],
        pending_not_before=NOW + timedelta(days=1),
    )
    rows = {row.__tablename__: row for row in (interest, preference, self_state)}
    session.add_all(rows.values())
    session.flush()
    yield session, rows


def test_development_fields_round_trip_preserve_tentative_and_pending(graph):
    session, rows = graph
    session.expire_all()
    for row in rows.values():
        assert row.revision == 1
        assert row.created_at == NOW
        assert row.created_at.utcoffset() == timedelta(0)
    assert rows["interests"].interest_id.version == 4
    assert rows["interests"].retirement_not_before is None
    assert rows["preferences"].retirement_not_before is None
    assert rows["self_states"].content is None
    assert rows["self_states"].pending_content == {"value": "I may like patterns"}


def test_development_named_keys_and_column_types(db_engine):
    schema = inspect(db_engine)
    for table in TABLES:
        assert schema.get_pk_constraint(table)["name"] == f"pk_{table}"
        for check in schema.get_check_constraints(table):
            assert check["name"].startswith(f"ck_{table}_")
        for foreign_key in schema.get_foreign_keys(table):
            assert foreign_key["name"] == f"fk_{table}_individual_id_individuals"
            assert foreign_key["options"].get("ondelete") in (None, "NO ACTION")
        for column in schema.get_columns(table):
            if column["name"].endswith(("_at", "_not_before")):
                assert column["type"].timezone is True
            if column["name"] == "revision":
                assert str(column["type"]) == "BIGINT"


@pytest.mark.parametrize("table", sorted(TABLES))
def test_development_primary_keys_and_ownership_are_enforced(graph, table):
    session, rows = graph
    row = rows[table]
    values = {
        column.name: getattr(row, column.name) for column in row.__table__.columns
    }
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(row.__table__.insert().values(**values))
    assert error.value.orig.diag.constraint_name == f"pk_{table}"
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(
            text(f"UPDATE {table} SET individual_id = :missing"), {"missing": new_id()}
        )
    assert (
        error.value.orig.diag.constraint_name == f"fk_{table}_individual_id_individuals"
    )


@pytest.mark.parametrize(
    "table,assignment,constraint",
    [
        *((table, "revision = 0", "revision_positive") for table in sorted(TABLES)),
        ("interests", "status = 'imagined'", "status"),
        ("preferences", "status = 'dormant'", "status"),
        ("self_states", "layer = 'genesis'", "layer"),
        *(
            (table, "evidence_refs = '{}'::jsonb", "evidence_refs_array")
            for table in sorted(TABLES)
        ),
        (
            "self_states",
            "pending_evidence_refs = '{}'::jsonb",
            "pending_evidence_refs_array",
        ),
        ("self_states", "content = '[]'::jsonb", "content_object"),
        ("self_states", "content = 'null'::jsonb", "content_object"),
        ("self_states", "pending_content = '[]'::jsonb", "pending_content_object"),
        ("self_states", "pending_not_before = NULL", "pending_pair"),
        (
            "self_states",
            "content = '{}'::jsonb, pending_content = NULL",
            "pending_pair",
        ),
        (
            "self_states",
            "pending_content = NULL, pending_not_before = NULL",
            "content_present",
        ),
        (
            "self_states",
            "pending_not_before = created_at - interval '1 second'",
            "pending_eligibility",
        ),
        *(
            (
                table,
                "promotion_not_before = created_at - interval '1 second'",
                "promotion_eligibility",
            )
            for table in ("interests", "preferences")
        ),
        *(
            (
                table,
                "retirement_not_before = created_at - interval '1 second'",
                "retirement_eligibility",
            )
            for table in ("interests", "preferences")
        ),
    ],
)
def test_invalid_development_states_are_rejected(graph, table, assignment, constraint):
    session, _ = graph
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(text(f"UPDATE {table} SET {assignment}"))
    assert error.value.orig.diag.constraint_name == f"ck_{table}_{constraint}"


@pytest.mark.parametrize(
    "table,statuses",
    [
        ("interests", ("candidate", "established", "dormant", "retired")),
        ("preferences", ("tentative", "established", "retired")),
    ],
)
def test_development_lifecycle_vocabulary(graph, table, statuses):
    session, rows = graph
    for status in statuses:
        rows[table].status = status
        session.flush()
        session.expire(rows[table])
        assert rows[table].status == status


def test_self_layers_are_unique_per_individual_and_current_can_replace_pending(graph):
    session, rows = graph
    row = rows["self_states"]
    values = {
        column.name: getattr(row, column.name) for column in row.__table__.columns
    }
    values["self_state_id"] = new_id()
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(row.__table__.insert().values(**values))
    assert error.value.orig.diag.constraint_name == "uq_self_states_individual_layer"
    for layer in ("current_identity", "current_value", "narrative"):
        values.update(self_state_id=new_id(), layer=layer)
        session.execute(row.__table__.insert().values(**values))
    row.content = {"value": "A considered interpretation"}
    row.pending_content = None
    row.pending_not_before = None
    session.flush()
    session.expire(row)
    assert row.pending_content is row.pending_not_before is None
