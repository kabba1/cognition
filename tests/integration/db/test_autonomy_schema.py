"""Durable scheduler continuity and PostgreSQL corruption boundaries."""

from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from schema_metadata import compare_metadata
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from cognition.db.base import Base
from cognition.db.models.runtime import RuntimeConfigRevision
from cognition.protocols.common import new_id
from cognition.stores.identity import create_individual

NOW = datetime(2026, 9, 23, 15, tzinfo=UTC)


def test_autonomy_migration_round_trip_and_metadata(db_engine, alembic_config):
    assert "autonomy_state" in inspect(db_engine).get_table_names()
    with db_engine.begin() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_server_default": True}
        )
        assert compare_metadata(context, Base.metadata) == []
        alembic_config.attributes["connection"] = connection
        command.downgrade(alembic_config, "0009_lexical_retrieval")
        assert "autonomy_state" not in inspect(connection).get_table_names()
        assert "wakes" in inspect(connection).get_table_names()
        command.upgrade(alembic_config, "head")
        assert compare_metadata(context, Base.metadata) == []


@pytest.fixture
def individual(db_session_factory):
    with db_session_factory.begin() as session:
        person = create_individual(
            session,
            individual_id=new_id(),
            birth_at=NOW,
            birth_name="Scheduler schema individual",
            founding_orientation="Preserve continuity",
            creator_provenance={},
        )
        config = RuntimeConfigRevision(
            individual_id=person.individual_id,
            config_schema_version=1,
            sanitized_config={},
            content_hash="schema fixture",
            created_at=NOW,
            activated_at=NOW,
        )
        session.add(config)
        session.flush()
        return person.individual_id, config.config_revision_id


@pytest.fixture
def state(db_session_factory, individual):
    from cognition.db.models import AutonomyState

    person, config = individual
    with db_session_factory.begin() as session:
        row = AutonomyState(
            individual_id=person,
            interval_seconds=1.5,
            anchor_at=NOW,
            config_revision_id=config,
        )
        session.add(row)
        session.flush()
        yield session, row


def test_individual_creation_does_not_invent_scheduler_state(
    db_session_factory, individual
):
    with db_session_factory() as session:
        assert session.scalar(text("SELECT count(*) FROM autonomy_state")) == 0


def test_nullable_deferred_state_and_defaults_round_trip(state):
    session, row = state
    session.expire(row)
    assert row.policy_version == row.revision == 1
    assert row.interval_seconds == 1.5
    assert row.anchor_at == NOW and row.anchor_at.tzinfo is not None
    assert row.managed_wake_id is row.last_completed_cycle_id is None
    assert row.materialization_pending is True


@pytest.mark.parametrize(
    "assignment,constraint",
    [
        ("interval_seconds = 0", "interval_finite_positive"),
        ("interval_seconds = 0.5", "interval_finite_positive"),
        ("interval_seconds = '-Infinity'::float8", "interval_finite_positive"),
        ("interval_seconds = 'Infinity'::float8", "interval_finite_positive"),
        ("interval_seconds = 'NaN'::float8", "interval_finite_positive"),
        ("policy_version = 2", "policy_version"),
        ("revision = 0", "revision_positive"),
        ("materialization_pending = false", "managed_wake_or_pending"),
    ],
)
def test_invalid_scheduler_values_rejected(state, assignment, constraint):
    session, _ = state
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(text(f"UPDATE autonomy_state SET {assignment}"))
    assert error.value.orig.diag.constraint_name == f"ck_autonomy_state_{constraint}"


@pytest.mark.parametrize(
    "column,table",
    [
        ("individual_id", "individuals"),
        ("managed_wake_id", "wakes"),
        ("last_completed_cycle_id", "cognition_cycles"),
        ("config_revision_id", "runtime_config_revisions"),
    ],
)
def test_scheduler_references_require_existing_rows(state, column, table):
    session, _ = state
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(
            text(f"UPDATE autonomy_state SET {column} = :missing"),
            {"missing": new_id()},
        )
    assert (
        error.value.orig.diag.constraint_name == f"fk_autonomy_state_{column}_{table}"
    )


def test_scheduler_primary_key_and_required_columns(state, db_engine):
    session, row = state
    values = {
        column.name: getattr(row, column.name) for column in row.__table__.columns
    }
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(row.__table__.insert().values(**values))
    assert error.value.orig.diag.constraint_name == "pk_autonomy_state"
    columns = {
        column["name"]: column
        for column in inspect(db_engine).get_columns("autonomy_state")
    }
    assert columns["anchor_at"]["type"].timezone is True
    assert str(columns["revision"]["type"]) == "BIGINT"
    for name, column in columns.items():
        assert column["nullable"] == (
            name in {"managed_wake_id", "last_completed_cycle_id"}
        )


def test_managed_pointer_is_unique_and_allows_deferred_consumed_wake(state):
    from cognition.db.models import AutonomyState, Wake

    session, row = state
    wake = Wake(
        individual_id=row.individual_id,
        kind="heartbeat",
        status="consumed",
        due_at=NOW,
        purpose="Sleep permitted",
        context_refs=[],
        claimed_at=NOW,
        consumed_at=NOW,
    )
    session.add(wake)
    session.flush()
    row.managed_wake_id = wake.wake_id
    session.flush()
    assert row.materialization_pending is True
    # A materialized pointer may also coexist with pending=False. Whether the
    # wake status is appropriate is an owned, cross-table store/diagnostic check.
    row.materialization_pending = False
    session.flush()
    other = create_individual(
        session,
        individual_id=new_id(),
        birth_at=NOW,
        birth_name="Other scheduler individual",
        founding_orientation="Preserve continuity",
        creator_provenance={},
    )
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(
            AutonomyState.__table__.insert().values(
                individual_id=other.individual_id,
                interval_seconds=1,
                anchor_at=NOW,
                config_revision_id=row.config_revision_id,
                managed_wake_id=wake.wake_id,
            )
        )
    assert error.value.orig.diag.constraint_name == "uq_autonomy_state_managed_wake_id"


def test_database_defaults_and_huge_finite_interval(db_engine, individual):
    person, config = individual
    with db_engine.begin() as connection:
        row = (
            connection.execute(
                text(
                    "INSERT INTO autonomy_state "
                    "(individual_id, interval_seconds, anchor_at, config_revision_id) "
                    "VALUES (:person, 1e308, :anchor, :config) RETURNING *"
                ),
                {"person": person, "anchor": NOW, "config": config},
            )
            .mappings()
            .one()
        )
        assert row["policy_version"] == row["revision"] == 1
        assert row["materialization_pending"] is True
        assert row["interval_seconds"] == 1e308
        assert row["managed_wake_id"] is row["last_completed_cycle_id"] is None


def test_populated_downgrade_refuses_without_losing_continuity(
    db_engine, db_session_factory, alembic_config, individual
):
    from cognition.db.models import AutonomyState

    person, config = individual
    with db_session_factory.begin() as session:
        session.add(
            AutonomyState(
                individual_id=person,
                interval_seconds=12,
                anchor_at=NOW,
                config_revision_id=config,
            )
        )
    with pytest.raises(RuntimeError, match="scheduler state"):
        with db_engine.begin() as connection:
            alembic_config.attributes["connection"] = connection
            command.downgrade(alembic_config, "0009_lexical_retrieval")
    with db_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT interval_seconds FROM autonomy_state")) == 12
        )
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == ScriptDirectory.from_config(alembic_config).get_current_head()
        )
