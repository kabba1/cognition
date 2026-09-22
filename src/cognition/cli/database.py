"""Explicit local database configuration with a read-only schema gate."""

import os

from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine

from cognition.db.session import create_db_engine

SUPPORTED_SCHEMA_REVISION = "0005_personal_state"


def configured_engine() -> Engine:
    url = os.environ.get("COGNITION_DATABASE_URL")
    if not url:
        raise ValueError("Set COGNITION_DATABASE_URL before using this command")
    engine = create_db_engine(url)
    try:
        require_supported_schema(engine)
    except BaseException:
        engine.dispose()
        raise
    return engine


def require_supported_schema(engine: Engine) -> None:
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={
                "version_table_schema": engine.get_execution_options().get(
                    "cognition_schema"
                ),
            },
        )
        if context.get_current_heads() != (SUPPORTED_SCHEMA_REVISION,):
            raise ValueError("Database schema is incompatible; run explicit migrations")
