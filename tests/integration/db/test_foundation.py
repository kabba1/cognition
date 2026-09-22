"""Exercise migrations and transaction boundaries against real PostgreSQL."""

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from cognition.db.schema import check_schema_revision
from cognition.db.session import create_db_engine


def test_foundation_upgrade_downgrade_and_version_table_isolation(
    db_engine: Engine, db_schema: str, alembic_config: Config
) -> None:
    scripts = ScriptDirectory.from_config(alembic_config)
    with db_engine.connect() as connection:
        current = check_schema_revision(
            connection, scripts, version_table_schema=db_schema
        )
        assert current.status == "exact"
    command.downgrade(alembic_config, "base")
    with db_engine.connect() as connection:
        assert (
            check_schema_revision(
                connection, scripts, version_table_schema=db_schema
            ).status
            == "behind"
        )
    command.upgrade(alembic_config, "0001_foundation")
    with db_engine.connect() as connection:
        # Later domain migrations must not change the empty foundation contract.
        assert inspect(connection).get_table_names(schema=db_schema) == [
            "alembic_version"
        ]
        assert (
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == "0001_foundation"
        )
        assert (
            check_schema_revision(
                connection, scripts, "0001_foundation", version_table_schema=db_schema
            ).status
            == "exact"
        )


def test_sessions_commit_and_rollback(
    db_engine: Engine, db_session_factory: sessionmaker[Session]
) -> None:
    with db_engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE transaction_probe (value integer NOT NULL)")
        )
    with db_session_factory.begin() as session:
        session.execute(text("INSERT INTO transaction_probe VALUES (1)"))
    with pytest.raises(RuntimeError, match="force rollback"):
        with db_session_factory.begin() as session:
            session.execute(text("INSERT INTO transaction_probe VALUES (2)"))
            raise RuntimeError("force rollback")
    with db_session_factory() as session:
        assert session.execute(
            text("SELECT value FROM transaction_probe")
        ).scalars().all() == [1]


def test_sessions_use_independent_connections_and_test_schema(
    db_schema: str, db_session_factory: sessionmaker[Session]
) -> None:
    with db_session_factory() as first, db_session_factory() as second:
        first_pid = first.execute(text("SELECT pg_backend_pid()")).scalar_one()
        second_pid = second.execute(text("SELECT pg_backend_pid()")).scalar_one()
        assert first_pid != second_pid
        assert first.execute(text("SHOW TimeZone")).scalar_one() == "UTC"
        assert second.execute(text("SHOW TimeZone")).scalar_one() == "UTC"
        assert first.execute(text("SELECT current_schema()")).scalar_one() == db_schema
        assert second.execute(text("SELECT current_schema()")).scalar_one() == db_schema
        assert first.execute(text("SELECT current_schemas(false)")).scalar_one() == [
            db_schema,
            "pg_catalog",
        ]


def test_unknown_database_revision_is_reported_without_guessing(
    db_engine: Engine, db_schema: str, alembic_config: Config
) -> None:
    with db_engine.begin() as connection:
        connection.execute(
            text("UPDATE alembic_version SET version_num = 'unknown_future'")
        )
    with db_engine.connect() as connection:
        result = check_schema_revision(
            connection,
            ScriptDirectory.from_config(alembic_config),
            version_table_schema=db_schema,
        )
        assert result.status == "unknown"
        assert result.observed_revisions == ("unknown_future",)


def test_supplied_migration_connection_respects_outer_transaction(
    db_engine: Engine, db_schema: str, alembic_config: Config
) -> None:
    scripts = ScriptDirectory.from_config(alembic_config)
    with pytest.raises(RuntimeError, match="roll back migration"):
        with db_engine.begin() as connection:
            alembic_config.attributes["connection"] = connection
            command.downgrade(alembic_config, "base")
            assert (
                check_schema_revision(
                    connection, scripts, version_table_schema=db_schema
                ).status
                == "behind"
            )
            raise RuntimeError("roll back migration")
    with db_engine.connect() as connection:
        assert (
            check_schema_revision(
                connection, scripts, version_table_schema=db_schema
            ).status
            == "exact"
        )


def test_engine_and_revision_check_do_not_create_schema_objects(
    db_url: str, db_schema: str
) -> None:
    engine = create_db_engine(db_url, schema=db_schema)
    try:
        scripts = ScriptDirectory.from_config(Config("alembic.ini"))
        with engine.connect() as connection:
            assert inspect(connection).get_table_names(schema=db_schema) == []
            assert (
                check_schema_revision(
                    connection, scripts, version_table_schema=db_schema
                ).status
                == "behind"
            )
            assert inspect(connection).get_table_names(schema=db_schema) == []
    finally:
        engine.dispose()
