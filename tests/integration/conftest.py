"""Opt-in PostgreSQL tests; each test owns one disposable, isolated schema."""

import os
import re
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from cognition.db.session import create_db_engine, create_session_factory

REPOSITORY = Path(__file__).resolve().parents[2]
TEST_SCHEMA = re.compile(r"cognition_test_[0-9a-f]{32}\Z")


def migration_config(engine: Engine, schema: str) -> Config:
    """Build config without copying credentials into INI values or output."""
    config = Config(str(REPOSITORY / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY / "alembic"))
    config.attributes["engine"] = engine
    config.attributes["version_table_schema"] = schema
    return config


@pytest.fixture
def db_url() -> str:
    value = os.environ.get("COGNITION_TEST_DATABASE_URL")
    if not value:
        pytest.skip(
            "Set COGNITION_TEST_DATABASE_URL to run PostgreSQL integration tests"
        )
    return value


@pytest.fixture
def db_schema(db_url: str) -> Iterator[str]:
    schema = "cognition_test_" + uuid4().hex
    admin = create_db_engine(db_url)
    created = False
    try:
        with admin.begin() as connection:
            connection.execute(CreateSchema(schema))
        created = True
        yield schema
    finally:
        try:
            if created:
                # Never derive cleanup from a URL, environment, or DB contents.
                if TEST_SCHEMA.fullmatch(schema) is None:
                    raise RuntimeError("Refusing to drop an unverified test schema")
                with admin.begin() as connection:
                    connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
                    connection.execute(DropSchema(schema, cascade=True))
        finally:
            admin.dispose()


@pytest.fixture
def db_engine(db_url: str, db_schema: str) -> Iterator[Engine]:
    engine = create_db_engine(db_url, schema=db_schema)
    try:
        config = migration_config(engine, db_schema)
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_session_factory(db_engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(db_engine)


@pytest.fixture
def alembic_config(db_engine: Engine, db_schema: str) -> Config:
    return migration_config(db_engine, db_schema)
