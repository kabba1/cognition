"""Explicit synchronous PostgreSQL engines and independent transaction sessions."""

import re

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker

_SCHEMA_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z")


def create_db_engine(url: str | URL, schema: str | None = None) -> Engine:
    """Create a lazy Psycopg engine; never connect or migrate during construction.

    An optional lowercase PostgreSQL schema isolates every pooled connection's
    search path. Public-schema fallback is intentionally excluded when supplied.
    The caller owns environment lookup, credentials, and engine disposal.
    """
    parsed = make_url(url)
    if parsed.drivername != "postgresql+psycopg":
        raise ValueError("database URL must use postgresql+psycopg")
    if schema is not None and _SCHEMA_NAME.fullmatch(schema) is None:
        raise ValueError(
            "schema must be a lowercase PostgreSQL identifier, <=63 characters"
        )
    options = "-ctimezone=UTC"
    if schema is not None:
        options += f" -csearch_path={schema},pg_catalog"
    connect_args = {"options": options}
    engine = create_engine(
        parsed,
        connect_args=connect_args,
        pool_pre_ping=True,
        hide_parameters=True,
    )
    if schema is not None:
        engine.update_execution_options(cognition_schema=schema)
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return independent sessions; ``with factory.begin()`` commits or rolls back."""
    return sessionmaker(bind=engine, expire_on_commit=False)
