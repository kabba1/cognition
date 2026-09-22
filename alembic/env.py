"""Explicit migration entrypoint; supports externally managed test transactions."""

import os

from alembic import context
from sqlalchemy import Connection, Engine

from cognition.db import models  # noqa: F401 -- register table metadata
from cognition.db.base import Base
from cognition.db.session import create_db_engine

config = context.config
target_metadata = Base.metadata


def configure_connection(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=config.attributes.get("version_table_schema"),
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def migrate_online() -> None:
    supplied_connection = config.attributes.get("connection")
    if isinstance(supplied_connection, Connection):
        configure_connection(supplied_connection)
        return
    supplied_engine = config.attributes.get("engine")
    if isinstance(supplied_engine, Engine):
        with supplied_engine.connect() as connection:
            configure_connection(connection)
        return
    url = os.environ.get("COGNITION_DATABASE_URL")
    if not url:
        raise RuntimeError("Set COGNITION_DATABASE_URL before running migrations")
    engine = create_db_engine(url)
    try:
        with engine.connect() as connection:
            configure_connection(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    context.configure(
        dialect_name="postgresql",
        target_metadata=target_metadata,
        version_table_schema=config.attributes.get("version_table_schema"),
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    migrate_online()
