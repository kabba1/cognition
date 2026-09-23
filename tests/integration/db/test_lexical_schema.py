"""Bounded derived lexical indexes preserve unrestricted canonical source text."""

import importlib
import json
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.migration import MigrationContext
from schema_metadata import compare_metadata
from sqlalchemy import inspect, text

from cognition.db.base import Base
from cognition.db.models.evidence import Event, EventContent
from cognition.db.models.personal import Belief, Episode
from cognition.protocols.common import new_id
from cognition.stores.identity import create_individual

NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)
CORPORA = [
    ("beliefs", "belief_id", "BELIEF_VECTOR_SQL", "ix_beliefs_lexical"),
    ("episodes", "episode_id", "EPISODE_VECTOR_SQL", "ix_episodes_lexical"),
    ("event_contents", "event_id", "EVENT_VECTOR_SQL", "ix_event_contents_lexical"),
]


def test_lexical_indexes_exist_as_configured_gin_expressions(db_engine):
    with db_engine.connect() as connection:
        for table, _, _, index in CORPORA:
            indexes = {
                row["name"]: row for row in inspect(connection).get_indexes(table)
            }
            assert index in indexes
            assert indexes[index]["dialect_options"]["postgresql_using"] == "gin"
            definition = connection.scalar(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE schemaname = current_schema() AND indexname = :index"
                ),
                {"index": index},
            )
            assert "8192" in definition and "english" in definition
            assert (
                "1024" in definition if table == "beliefs" else "1024" not in definition
            )


@pytest.fixture
def individual(db_session_factory):
    identity = new_id()
    with db_session_factory.begin() as session:
        create_individual(
            session,
            individual_id=identity,
            birth_at=NOW,
            birth_name="Lexical schema individual",
            founding_orientation="Keep evidence grounded",
            creator_provenance={},
        )
    return identity


def add_records(factory, individual, primary, topic=None):
    with factory.begin() as session:
        belief = Belief(
            individual_id=individual,
            proposition=primary,
            topic=topic,
            status="tentative",
            supporting_evidence=[],
            contradicting_evidence=[],
            rationale="Schema fixture",
            created_at=NOW,
            updated_at=NOW,
        )
        episode = Episode(
            individual_id=individual,
            summary=primary,
            evidence_refs=[],
            entity_refs=[],
            project_refs=[],
            salience_factors=[],
            created_at=NOW,
        )
        event = Event(
            individual_id=individual,
            event_type="observation.text",
            observed_at=NOW,
            recorded_at=NOW,
            source_kind="connector",
            provenance={},
            runtime_version="test",
        )
        session.add_all([belief, episode, event])
        session.flush()
        session.add(
            EventContent(
                event_id=event.event_id,
                content_type="text/plain",
                payload=None,
                text=primary,
                sensitivity="internal",
                retention_class="history",
            )
        )
        return belief.belief_id, episode.episode_id, event.event_id


def matches(connection, table, expression_name, term):
    expression = getattr(
        importlib.import_module("cognition.db.search"), expression_name
    )
    return connection.scalar(
        text(
            f"SELECT ({expression}) @@ "
            "plainto_tsquery('pg_catalog.english'::regconfig, :term) "
            f"FROM {table}"
        ),
        {"term": term},
    )


def test_lexical_migration_backfills_huge_unicode_without_changing_source_rows(
    db_engine, db_session_factory, alembic_config, individual
):
    with db_engine.begin() as connection:
        alembic_config.attributes["connection"] = connection
        command.downgrade(alembic_config, "0008_executive_configuration")
        for table, _, _, index in CORPORA:
            assert index not in {
                row["name"] for row in inspect(connection).get_indexes(table)
            }
    primary = "continuity café 💭 " * 100000 + " afterprefixneedle"
    topic = "reciprocity " + "topic " * 1000 + " outsidetopicneedle"
    identities = add_records(db_session_factory, individual, primary, topic)
    with db_engine.begin() as connection:
        alembic_config.attributes["connection"] = connection
        command.upgrade(alembic_config, "head")
        context = MigrationContext.configure(
            connection, opts={"compare_server_default": True}
        )
        assert compare_metadata(context, Base.metadata) == []
        for table, _, expression, _ in CORPORA:
            assert matches(connection, table, expression, "continuity") is True
            assert matches(connection, table, expression, "afterprefixneedle") is False
        assert (
            matches(connection, "beliefs", "BELIEF_VECTOR_SQL", "reciprocity") is True
        )
        assert (
            matches(connection, "beliefs", "BELIEF_VECTOR_SQL", "outsidetopicneedle")
            is False
        )
    with db_session_factory() as session:
        assert session.get(Belief, identities[0]).proposition == primary
        assert session.get(Belief, identities[0]).topic == topic
        assert session.get(Episode, identities[1]).summary == primary
        assert session.get(EventContent, identities[2]).text == primary


def test_oversized_writes_and_null_source_updates_are_safe(
    db_engine, db_session_factory, individual
):
    primary = "continuity café 💭 " * 100000
    identities = add_records(db_session_factory, individual, primary)
    with db_engine.begin() as connection:
        for table, _, expression, _ in CORPORA:
            assert matches(connection, table, expression, "continuity") is True
    with db_session_factory.begin() as session:
        session.get(Belief, identities[0]).proposition = "reciprocity"
        session.get(EventContent, identities[2]).text = None
    with db_engine.begin() as connection:
        assert (
            matches(connection, "beliefs", "BELIEF_VECTOR_SQL", "reciprocity") is True
        )
        assert (
            matches(connection, "beliefs", "BELIEF_VECTOR_SQL", "continuity") is False
        )
        assert (
            matches(connection, "event_contents", "EVENT_VECTOR_SQL", "continuity")
            is False
        )


def test_downgrade_removes_only_search_indexes(
    db_engine, db_session_factory, alembic_config, individual
):
    identities = add_records(db_session_factory, individual, "Canonical continuity")
    with db_engine.begin() as connection:
        alembic_config.attributes["connection"] = connection
        command.downgrade(alembic_config, "0008_executive_configuration")
        for (table, column, _, index), identity in zip(
            CORPORA, identities, strict=True
        ):
            assert index not in {
                item["name"] for item in inspect(connection).get_indexes(table)
            }
            assert (
                connection.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE {column} = :identity"),
                    {"identity": identity},
                )
                == 1
            )
        assert connection.scalar(text("SELECT text FROM event_contents")) == (
            "Canonical continuity"
        )


@pytest.mark.parametrize("table,column,expression_name,index", CORPORA)
def test_literal_search_expression_is_index_eligible(
    db_engine, db_session_factory, individual, table, column, expression_name, index
):
    add_records(db_session_factory, individual, "Distinctive continuity observation")
    expression = getattr(
        importlib.import_module("cognition.db.search"), expression_name
    )
    with db_engine.begin() as connection:
        # Verify eligibility, without claiming a production latency benchmark.
        connection.execute(text("SET LOCAL enable_seqscan = off"))
        plan = connection.scalar(
            text(
                f"EXPLAIN (FORMAT JSON) SELECT {column} FROM {table} "
                f"WHERE ({expression}) @@ "
                "plainto_tsquery('pg_catalog.english'::regconfig, :term)"
            ),
            {"term": "continuity"},
        )
        assert index in json.dumps(plan)


def test_prefix_limits_count_characters_not_utf8_bytes(
    db_engine, db_session_factory, individual
):
    primary = "é " * 3000 + "continuity " + "q " * 2000 + "afterprefixneedle"
    add_records(db_session_factory, individual, primary, "é " * 300 + "reciprocity")
    with db_engine.begin() as connection:
        for table, _, expression, _ in CORPORA:
            assert matches(connection, table, expression, "continuity") is True
            assert matches(connection, table, expression, "afterprefixneedle") is False
        assert (
            matches(connection, "beliefs", "BELIEF_VECTOR_SQL", "reciprocity") is True
        )


@pytest.mark.parametrize("drift", ["prefix", "dictionary", "method", "extra_index"])
def test_semantic_metadata_comparison_keeps_real_schema_drift(db_engine, drift):
    expression = importlib.import_module("cognition.db.search").BELIEF_VECTOR_SQL
    with db_engine.begin() as connection:
        if drift == "extra_index":
            connection.exec_driver_sql(
                "CREATE INDEX unexpected_test_index ON beliefs (status)"
            )
        else:
            connection.exec_driver_sql("DROP INDEX ix_beliefs_lexical")
            if drift == "prefix":
                expression = expression.replace("8192", "4096")
            elif drift == "dictionary":
                expression = expression.replace(
                    "pg_catalog.english", "pg_catalog.simple"
                )
            method = "btree" if drift == "method" else "gin"
            connection.exec_driver_sql(
                "CREATE INDEX ix_beliefs_lexical ON beliefs "
                f"USING {method} ({expression})"
            )
        context = MigrationContext.configure(connection)
        differences = compare_metadata(context, Base.metadata)
        expected = (
            "unexpected_test_index" if drift == "extra_index" else "ix_beliefs_lexical"
        )
        assert any(item[1].name == expected for item in differences)
        for table, _, _, _ in CORPORA:
            assert not any(
                item["name"].startswith("lexical_compare_")
                for item in inspect(connection).get_indexes(table)
            )
