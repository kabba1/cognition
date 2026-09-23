"""Config-v2 migration preserves history and never discards incompatible rows."""

import importlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from schema_metadata import compare_metadata
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from cognition.config.loader import load_config
from cognition.config.recording import reconcile_config
from cognition.db.base import Base
from cognition.db.models.evidence import Event, EventContent
from cognition.db.models.runtime import RuntimeConfigRevision
from cognition.stores.identity import create_individual
from cognition.testing.clock import FakeClock

NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)
CONFIG = Path(__file__).parents[2] / "fixtures/config/valid.toml"


@pytest.fixture
def configuration(db_session_factory):
    config = load_config(CONFIG)
    with db_session_factory.begin() as session:
        create_individual(
            session,
            individual_id=config.runtime.individual_id,
            birth_at=NOW,
            birth_name="Configuration individual",
            founding_orientation="Keep explicit contracts",
            creator_provenance={},
        )
    return config


def v2(config):
    data = config.model_dump()
    data.update(config_schema_version=2, execution={"cognition_protocol_version": 2})
    return importlib.import_module("cognition.config.schema").parse_config(data)


def snapshot(factory):
    with factory() as session:
        return {
            model.__tablename__: [
                dict(row) for row in session.execute(select(model.__table__)).mappings()
            ]
            for model in (RuntimeConfigRevision, Event, EventContent)
        }


def test_configuration_migration_preserves_v1_history_and_round_trips(
    db_engine, db_session_factory, alembic_config, configuration
):
    reconcile_config(
        db_session_factory,
        configuration.runtime.individual_id,
        configuration,
        FakeClock(NOW),
    )
    original = snapshot(db_session_factory)
    with db_engine.begin() as connection:
        alembic_config.attributes["connection"] = connection
        command.downgrade(alembic_config, "0007_relationships")
        checks = inspect(connection).get_check_constraints("runtime_config_revisions")
        assert any(check["sqltext"] == "config_schema_version = 1" for check in checks)
        command.upgrade(alembic_config, "head")
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == ScriptDirectory.from_config(alembic_config).get_current_head()
        )
        context = MigrationContext.configure(
            connection, opts={"compare_server_default": True}
        )
        assert compare_metadata(context, Base.metadata) == []
    assert snapshot(db_session_factory) == original


@pytest.mark.parametrize("reactivate_v1", [False, True])
def test_downgrade_refuses_any_v2_history_even_when_superseded(
    db_engine, db_session_factory, alembic_config, configuration, reactivate_v1
):
    reconcile_config(
        db_session_factory,
        configuration.runtime.individual_id,
        configuration,
        FakeClock(NOW),
    )
    reconcile_config(
        db_session_factory,
        configuration.runtime.individual_id,
        v2(configuration),
        FakeClock(NOW + timedelta(seconds=1)),
    )
    if reactivate_v1:
        reconcile_config(
            db_session_factory,
            configuration.runtime.individual_id,
            configuration,
            FakeClock(NOW + timedelta(seconds=2)),
        )
    original = snapshot(db_session_factory)
    with db_engine.begin() as connection:
        alembic_config.attributes["connection"] = connection
        with pytest.raises(RuntimeError, match="v2 configuration history"):
            command.downgrade(alembic_config, "0007_relationships")
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0008_executive_configuration"
        )
    assert snapshot(db_session_factory) == original


def test_configuration_constraint_allows_two_and_rejects_unknown_versions(
    db_session_factory, configuration
):
    reconcile_config(
        db_session_factory,
        configuration.runtime.individual_id,
        v2(configuration),
        FakeClock(NOW),
    )
    with db_session_factory.begin() as session:
        assert session.scalar(select(RuntimeConfigRevision.config_schema_version)) == 2
        with pytest.raises(IntegrityError) as error, session.begin_nested():
            session.execute(
                text("UPDATE runtime_config_revisions SET config_schema_version = 3")
            )
        assert (
            error.value.orig.diag.constraint_name
            == "ck_runtime_config_revisions_schema_version"
        )
