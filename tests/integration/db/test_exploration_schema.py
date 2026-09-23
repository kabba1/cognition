"""Exploration grant limits and continuity are enforced by PostgreSQL."""

from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.migration import MigrationContext
from schema_metadata import compare_metadata
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from cognition.db.base import Base
from cognition.db.models.attention import Wake
from cognition.protocols.common import new_id
from cognition.stores.identity import create_individual

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)
TABLES = {"exploration_state", "exploration_grants"}


def test_exploration_migration_round_trip_and_metadata(db_engine, alembic_config):
    assert TABLES <= set(inspect(db_engine).get_table_names())
    with db_engine.begin() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_server_default": True}
        )
        assert compare_metadata(context, Base.metadata) == []
        alembic_config.attributes["connection"] = connection
        command.downgrade(alembic_config, "0011_reflection_state")
        assert not TABLES.intersection(inspect(connection).get_table_names())
        assert "reflection_state" in inspect(connection).get_table_names()
        command.upgrade(alembic_config, "head")
        assert compare_metadata(context, Base.metadata) == []


def add_person(session):
    return create_individual(
        session,
        individual_id=new_id(),
        birth_at=NOW,
        birth_name="Exploration schema individual",
        founding_orientation="Keep bounded opportunities explicit",
        creator_provenance={},
    ).individual_id


@pytest.fixture
def person(db_session_factory):
    with db_session_factory.begin() as session:
        return add_person(session)


def add_grant(session, person):
    from cognition.db.models import ExplorationGrant

    wake = Wake(
        individual_id=person,
        kind="routine",
        status="pending",
        due_at=NOW,
        purpose="An internal opportunity permits no action",
        context_refs=[],
        coalesce_key=None,
    )
    session.add(wake)
    session.flush()
    grant = ExplorationGrant(
        wake_id=wake.wake_id,
        individual_id=person,
        authorizing_governance_revision=1,
        policy_snapshot={"schema_version": 1, "enabled": True},
        created_at=NOW,
        not_before_at=NOW,
        scope="internal",
        max_turns=1,
        max_attempts_per_turn=2,
        max_seconds=120,
        max_wakes=1,
        content_hash="a" * 64,
    )
    session.add(grant)
    session.flush()
    return grant


@pytest.fixture
def graph(db_session_factory, person):
    from cognition.db.models import ExplorationState

    with db_session_factory.begin() as session:
        grant = add_grant(session, person)
        state = ExplorationState(
            individual_id=person, next_eligible_at=NOW, managed_wake_id=grant.wake_id
        )
        session.add(state)
        session.flush()
        yield session, state, grant


def test_creation_does_not_enable_or_fabricate_exploration(db_engine, person):
    with db_engine.connect() as connection:
        for table in TABLES:
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0


def test_round_trip_and_disabled_state_without_managed_wake(graph):
    session, state, grant = graph
    session.expire_all()
    assert state.policy_version == state.revision == grant.policy_version == 1
    assert state.materialization_pending is True
    assert state.last_terminal_cycle_id is state.last_outcome_event_id is None
    assert grant.created_at == grant.not_before_at == state.next_eligible_at == NOW
    assert grant.policy_snapshot == {"schema_version": 1, "enabled": True}
    assert grant.authorizing_governance_revision == 1
    state.managed_wake_id, state.materialization_pending = None, False
    session.flush()
    session.expire(state)
    assert state.managed_wake_id is None and state.materialization_pending is False


@pytest.mark.parametrize(
    "table,assignment,constraint",
    [
        ("exploration_state", "policy_version = 2", "policy_version"),
        ("exploration_state", "revision = 0", "revision_positive"),
        ("exploration_grants", "policy_version = 2", "policy_version"),
        (
            "exploration_grants",
            "authorizing_governance_revision = 0",
            "governance_revision_positive",
        ),
        ("exploration_grants", "scope = 'external'", "scope"),
        ("exploration_grants", "max_turns = 2", "max_turns"),
        ("exploration_grants", "max_attempts_per_turn = 3", "max_attempts_per_turn"),
        ("exploration_grants", "max_seconds = 121", "max_seconds"),
        ("exploration_grants", "max_wakes = 2", "max_wakes"),
        (
            "exploration_grants",
            "policy_snapshot = '[]'::jsonb",
            "policy_snapshot_object",
        ),
        (
            "exploration_grants",
            "policy_snapshot = 'null'::jsonb",
            "policy_snapshot_object",
        ),
        ("exploration_grants", "content_hash = repeat('a', 63)", "content_hash"),
        ("exploration_grants", "content_hash = repeat('A', 64)", "content_hash"),
        ("exploration_grants", "content_hash = repeat('z', 64)", "content_hash"),
        (
            "exploration_grants",
            "not_before_at = created_at - interval '1 second'",
            "time_order",
        ),
    ],
)
def test_invalid_exploration_metadata_rejected(graph, table, assignment, constraint):
    session, _, _ = graph
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(text(f"UPDATE {table} SET {assignment}"))
    assert error.value.orig.diag.constraint_name == f"ck_{table}_{constraint}"


@pytest.mark.parametrize(
    "table,column,target",
    [
        ("exploration_state", "individual_id", "individuals"),
        ("exploration_state", "managed_wake_id", "wakes"),
        ("exploration_state", "last_terminal_cycle_id", "cognition_cycles"),
        ("exploration_state", "last_outcome_event_id", "events"),
        ("exploration_grants", "individual_id", "individuals"),
        ("exploration_grants", "wake_id", "wakes"),
    ],
)
def test_exploration_references_require_existing_rows(graph, table, column, target):
    session, _, _ = graph
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(
            text(f"UPDATE {table} SET {column} = :missing"), {"missing": new_id()}
        )
    assert error.value.orig.diag.constraint_name == f"fk_{table}_{column}_{target}"


def test_keys_and_unique_managed_pointer(graph):
    session, state, grant = graph
    for row in (state, grant):
        values = {
            column.name: getattr(row, column.name) for column in row.__table__.columns
        }
        with pytest.raises(IntegrityError) as error, session.begin_nested():
            session.execute(row.__table__.insert().values(**values))
        assert error.value.orig.diag.constraint_name == f"pk_{row.__tablename__}"
    other = add_person(session)
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(
            state.__table__.insert().values(
                individual_id=other, next_eligible_at=NOW, managed_wake_id=grant.wake_id
            )
        )
    assert (
        error.value.orig.diag.constraint_name == "uq_exploration_state_managed_wake_id"
    )


def test_types_nullability_owner_index_and_sql_defaults(db_engine, person):
    schema = inspect(db_engine)
    optional = {"managed_wake_id", "last_terminal_cycle_id", "last_outcome_event_id"}
    for table in TABLES:
        for column in schema.get_columns(table):
            assert column["nullable"] == (
                table == "exploration_state" and column["name"] in optional
            )
            if column["name"].endswith("_at"):
                assert column["type"].timezone is True
            if column["name"] in {"revision", "authorizing_governance_revision"}:
                assert str(column["type"]) == "BIGINT"
            if column["name"].startswith("max_"):
                assert str(column["type"]) == "INTEGER"
        for foreign in schema.get_foreign_keys(table):
            assert foreign["options"].get("ondelete") in (None, "NO ACTION")
    assert "ix_exploration_grants_individual_id" in {
        index["name"] for index in schema.get_indexes("exploration_grants")
    }
    with db_engine.begin() as connection:
        row = (
            connection.execute(
                text(
                    "INSERT INTO exploration_state (individual_id, next_eligible_at) "
                    "VALUES (:person, :now) RETURNING *"
                ),
                {"person": person, "now": NOW},
            )
            .mappings()
            .one()
        )
        assert row.policy_version == row.revision == 1
        assert row.materialization_pending is True


def test_future_eligibility_and_large_historical_revision_round_trip(graph):
    session, _, grant = graph
    grant.not_before_at = NOW + timedelta(days=7)
    grant.authorizing_governance_revision = 2**53 + 1
    session.flush()
    session.expire(grant)
    assert grant.not_before_at == NOW + timedelta(days=7)
    assert grant.authorizing_governance_revision == 2**53 + 1


@pytest.mark.parametrize("populated", ["state", "grant", "both"])
def test_downgrade_refuses_current_state_or_historical_grant_without_data_loss(
    db_engine, db_session_factory, alembic_config, person, populated
):
    from cognition.db.models import ExplorationState

    with db_session_factory.begin() as session:
        if populated in {"state", "both"}:
            session.add(ExplorationState(individual_id=person, next_eligible_at=NOW))
        if populated in {"grant", "both"}:
            add_grant(session, person)
    with db_engine.connect() as connection:
        before = {
            table: connection.execute(text(f"SELECT * FROM {table}")).mappings().all()
            for table in TABLES
        }
    with pytest.raises(RuntimeError, match="exploration"):
        with db_engine.begin() as connection:
            alembic_config.attributes["connection"] = connection
            command.downgrade(alembic_config, "0011_reflection_state")
    with db_engine.connect() as connection:
        for table in TABLES:
            assert (
                connection.execute(text(f"SELECT * FROM {table}")).mappings().all()
                == before[table]
            )
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0012_exploration_state"
        )
