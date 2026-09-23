"""PostgreSQL retains inbound identity, bounded receipts and wake membership."""

from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.migration import MigrationContext
from schema_metadata import compare_metadata
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from cognition.db.base import Base
from cognition.db.models.attention import Wake
from cognition.db.models.evidence import Event
from cognition.protocols.common import new_id
from cognition.stores.identity import create_individual

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
TABLES = {"connector_bindings", "inbound_wakes", "observations", "ingestion_receipts"}


def test_perception_migration_round_trip_and_metadata(db_engine, alembic_config):
    assert TABLES <= set(inspect(db_engine).get_table_names())
    with db_engine.begin() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_server_default": True}
        )
        assert compare_metadata(context, Base.metadata) == []
        alembic_config.attributes["connection"] = connection
        command.downgrade(alembic_config, "0012_exploration_state")
        assert not TABLES.intersection(inspect(connection).get_table_names())
        assert "exploration_state" in inspect(connection).get_table_names()
        command.upgrade(alembic_config, "head")
        assert compare_metadata(context, Base.metadata) == []


@pytest.fixture
def person(db_session_factory):
    with db_session_factory.begin() as session:
        return create_individual(
            session,
            individual_id=new_id(),
            birth_at=NOW,
            birth_name="Perception schema individual",
            founding_orientation="Retain inbound facts without granting authority",
            creator_provenance={},
        ).individual_id


def add_event(session, person):
    row = Event(
        individual_id=person,
        event_type="schema.fixture",
        observed_at=NOW,
        recorded_at=NOW,
        source_kind="runtime",
        provenance={},
        runtime_version="test",
    )
    session.add(row)
    session.flush()
    return row.event_id


def add_binding(session, person, **changes):
    from cognition.db.models import ConnectorBinding

    row = ConnectorBinding(
        individual_id=person,
        adapter_id="local_json_v1",
        source_id="schema-stream",
        created_at=NOW,
        updated_at=NOW,
        **changes,
    )
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def graph(db_session_factory, person):
    from cognition.db.models import InboundWake, IngestionReceipt, Observation

    with db_session_factory.begin() as session:
        binding = add_binding(session, person)
        wake = Wake(
            individual_id=person,
            kind="external_event",
            status="pending",
            due_at=NOW,
            purpose="Inbound evidence",
            context_refs=[],
        )
        session.add(wake)
        session.flush()
        inbound = InboundWake(
            wake_id=wake.wake_id,
            individual_id=person,
            connector_binding_id=binding.connector_binding_id,
            creation_event_id=add_event(session, person),
            created_at=NOW,
        )
        session.add(inbound)
        session.flush()
        receipt = IngestionReceipt(
            event_id=add_event(session, person),
            individual_id=person,
            connector_binding_id=binding.connector_binding_id,
            before_cursor=None,
            after_cursor="checkpoint-1",
            before_cursor_revision=0,
            after_cursor_revision=1,
            authorizing_binding_revision=1,
            observed_at=NOW,
            recorded_at=NOW,
            item_count=1,
            new_count=1,
            duplicate_count=0,
            source_page_hash="a" * 64,
            receipt_hash="b" * 64,
        )
        session.add(receipt)
        session.flush()
        observation = Observation(
            event_id=add_event(session, person),
            individual_id=person,
            connector_binding_id=binding.connector_binding_id,
            external_event_id="外部-event",
            dedup_key="c" * 64,
            content_fingerprint="d" * 64,
            external_content_type="application/json",
            authentication={"authenticated": False},
            inbound_wake_id=inbound.wake_id,
            receipt_event_id=receipt.event_id,
        )
        session.add(observation)
        binding.pending_wake_id = inbound.wake_id
        binding.latest_receipt_event_id = receipt.event_id
        binding.cursor = receipt.after_cursor
        binding.cursor_revision = receipt.after_cursor_revision
        session.flush()
        yield session, binding, inbound, receipt, observation


def test_individual_creation_has_no_fabricated_perception(db_engine, person):
    with db_engine.connect() as connection:
        for table in TABLES:
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0


def test_round_trip_defaults_and_separated_content(graph):
    session, binding, inbound, receipt, observation = graph
    session.expire_all()
    assert binding.enabled is False and binding.revision == 1
    assert receipt.policy_version == observation.fingerprint_version == 1
    assert inbound.sealed_at is None
    assert observation.external_event_id == "外部-event"
    assert observation.authentication == {"authenticated": False}
    assert observation.raw_content_hash is None
    assert binding.pending_wake_id == observation.inbound_wake_id == inbound.wake_id
    assert binding.latest_receipt_event_id == observation.receipt_event_id
    assert not {"text", "payload", "content"}.intersection(
        observation.__table__.columns.keys()
    )


@pytest.mark.parametrize(
    "table,assignment",
    [
        ("connector_bindings", "adapter_id = ''"),
        ("connector_bindings", "source_id = ''"),
        ("connector_bindings", "cursor_revision = -1"),
        ("connector_bindings", "cursor_revision = 0"),
        ("connector_bindings", "revision = 0"),
        ("connector_bindings", "updated_at = created_at - interval '1 second'"),
        ("inbound_wakes", "sealed_at = created_at - interval '1 second'"),
        ("observations", "fingerprint_version = 2"),
        ("observations", "dedup_key = repeat('A', 64)"),
        ("observations", "content_fingerprint = repeat('a', 63)"),
        ("observations", "authentication = '[]'::jsonb"),
        ("observations", "authentication = 'null'::jsonb"),
        ("observations", "raw_content_hash = repeat('z', 64)"),
        ("ingestion_receipts", "policy_version = 2"),
        ("ingestion_receipts", "before_cursor_revision = -1"),
        ("ingestion_receipts", "after_cursor_revision = 2"),
        ("ingestion_receipts", "authorizing_binding_revision = 0"),
        ("ingestion_receipts", "recorded_at = observed_at - interval '1 second'"),
        ("ingestion_receipts", "item_count = -1"),
        ("ingestion_receipts", "item_count = 33, new_count = 33"),
        ("ingestion_receipts", "new_count = -1, duplicate_count = 2"),
        ("ingestion_receipts", "duplicate_count = -1, new_count = 2"),
        ("ingestion_receipts", "item_count = 2"),
        ("ingestion_receipts", "source_page_hash = repeat('a', 63)"),
        ("ingestion_receipts", "receipt_hash = repeat('A', 64)"),
        ("ingestion_receipts", "before_cursor = 'invented initial checkpoint'"),
    ],
)
def test_invalid_perception_metadata_rejected(graph, table, assignment):
    session, *_ = graph
    with pytest.raises(IntegrityError), session.begin_nested():
        session.execute(text(f"UPDATE {table} SET {assignment}"))


@pytest.mark.parametrize(
    "table,column",
    [
        ("connector_bindings", "individual_id"),
        ("connector_bindings", "pending_wake_id"),
        ("connector_bindings", "latest_receipt_event_id"),
        ("inbound_wakes", "wake_id"),
        ("inbound_wakes", "individual_id"),
        ("inbound_wakes", "connector_binding_id"),
        ("inbound_wakes", "creation_event_id"),
        ("observations", "event_id"),
        ("observations", "individual_id"),
        ("observations", "connector_binding_id"),
        ("observations", "inbound_wake_id"),
        ("observations", "receipt_event_id"),
        ("ingestion_receipts", "event_id"),
        ("ingestion_receipts", "individual_id"),
        ("ingestion_receipts", "connector_binding_id"),
        ("ingestion_receipts", "previous_receipt_event_id"),
    ],
)
def test_perception_references_require_existing_rows(graph, table, column):
    session, *_ = graph
    with pytest.raises(IntegrityError), session.begin_nested():
        adjustment = (
            ", before_cursor_revision = 1, after_cursor_revision = 2"
            if column == "previous_receipt_event_id"
            else ""
        )
        session.execute(
            text(f"UPDATE {table} SET {column} = :missing{adjustment}"),
            {"missing": new_id()},
        )


def test_binding_identity_is_unique_with_separate_owned_streams(graph):
    session, binding, *_ = graph
    with pytest.raises(IntegrityError), session.begin_nested():
        add_binding(session, binding.individual_id)
    copy = {
        column.name: getattr(binding, column.name)
        for column in binding.__table__.columns
    }
    copy.update(connector_binding_id=new_id(), source_id="another-stream")
    session.execute(binding.__table__.insert().values(**copy))


def test_only_one_unsealed_inbound_wake_per_binding(graph):
    session, binding, inbound, *_ = graph
    wake = Wake(
        individual_id=binding.individual_id,
        kind="external_event",
        status="pending",
        due_at=NOW,
        purpose="Next inbound group",
        context_refs=[],
    )
    session.add(wake)
    session.flush()
    values = {
        column.name: getattr(inbound, column.name)
        for column in inbound.__table__.columns
    }
    values.update(
        wake_id=wake.wake_id,
        creation_event_id=add_event(session, binding.individual_id),
    )
    with pytest.raises(IntegrityError), session.begin_nested():
        session.execute(inbound.__table__.insert().values(**values))
    inbound.sealed_at = NOW + timedelta(seconds=1)
    session.flush()
    session.execute(inbound.__table__.insert().values(**values))


def test_dedup_and_cursor_revision_uniqueness(graph):
    session, binding, _, receipt, observation = graph
    for row in (receipt, observation):
        values = {
            column.name: getattr(row, column.name) for column in row.__table__.columns
        }
        values["event_id"] = add_event(session, binding.individual_id)
        with pytest.raises(IntegrityError), session.begin_nested():
            session.execute(row.__table__.insert().values(**values))


def test_empty_cursor_advance_and_maximum_duplicate_page_are_valid(graph):
    session, _, _, receipt, _ = graph
    receipt.item_count = receipt.new_count = receipt.duplicate_count = 0
    session.flush()
    session.expire(receipt)
    assert receipt.item_count == 0 and receipt.after_cursor == "checkpoint-1"
    receipt.item_count = receipt.duplicate_count = 32
    receipt.authorizing_binding_revision = 2**53 + 1
    session.flush()
    session.expire(receipt)
    assert receipt.item_count == receipt.duplicate_count == 32
    assert receipt.authorizing_binding_revision == 2**53 + 1


def test_receipt_successor_requires_noninitial_revision_and_retained_predecessor(graph):
    session, binding, _, receipt, _ = graph
    values = {
        column.name: getattr(receipt, column.name)
        for column in receipt.__table__.columns
    }
    values.update(
        event_id=add_event(session, binding.individual_id),
        previous_receipt_event_id=receipt.event_id,
        before_cursor=receipt.after_cursor,
        before_cursor_revision=1,
        after_cursor_revision=2,
    )
    session.execute(receipt.__table__.insert().values(**values))
    with pytest.raises(IntegrityError), session.begin_nested():
        session.execute(
            receipt.__table__.update()
            .where(receipt.__table__.c.event_id == values["event_id"])
            .values(previous_receipt_event_id=None)
        )


def test_perception_types_nullability_and_initial_sql_defaults(db_engine, person):
    optional = {
        "connector_bindings": {"cursor", "pending_wake_id", "latest_receipt_event_id"},
        "inbound_wakes": {"sealed_at"},
        "observations": {
            "external_event_id",
            "external_content_type",
            "raw_content_hash",
        },
        "ingestion_receipts": {
            "previous_receipt_event_id",
            "before_cursor",
            "after_cursor",
        },
    }
    schema = inspect(db_engine)
    for table in TABLES:
        for column in schema.get_columns(table):
            assert column["nullable"] == (column["name"] in optional[table])
            if column["name"].endswith("_at"):
                assert column["type"].timezone is True
            if column["name"].endswith("revision"):
                assert str(column["type"]) == "BIGINT"
        for foreign in schema.get_foreign_keys(table):
            assert foreign["options"].get("ondelete") in (None, "NO ACTION")
    with db_engine.begin() as connection:
        row = (
            connection.execute(
                text(
                    "INSERT INTO connector_bindings "
                    "(connector_binding_id, individual_id, adapter_id, source_id, "
                    "created_at, updated_at) VALUES (:id, :person, 'local_json_v1', "
                    "'empty-stream', :now, :now) RETURNING *"
                ),
                {"id": new_id(), "person": person, "now": NOW},
            )
            .mappings()
            .one()
        )
        assert row.enabled is False
        assert row.cursor_revision == 0 and row.revision == 1
        assert row.cursor is row.latest_receipt_event_id is row.pending_wake_id is None


@pytest.mark.parametrize("history", [False, True])
def test_downgrade_refuses_retained_perception_without_deleting_rows_or_fks(
    db_engine, db_session_factory, alembic_config, person, history, request
):
    if history:
        request.getfixturevalue("graph")
        # The graph's transaction belongs to its fixture; commit before DDL.
        request.getfixturevalue("graph")[0].commit()
    else:
        with db_session_factory.begin() as session:
            add_binding(session, person)
    schema = inspect(db_engine)
    before_fks = {table: schema.get_foreign_keys(table) for table in TABLES}
    with db_engine.connect() as connection:
        before = {
            table: connection.execute(text(f"SELECT * FROM {table}")).mappings().all()
            for table in TABLES
        }
    with pytest.raises(RuntimeError, match="perception"):
        with db_engine.begin() as connection:
            alembic_config.attributes["connection"] = connection
            command.downgrade(alembic_config, "0012_exploration_state")
    schema = inspect(db_engine)
    assert {table: schema.get_foreign_keys(table) for table in TABLES} == before_fks
    with db_engine.connect() as connection:
        for table in TABLES:
            assert (
                connection.execute(text(f"SELECT * FROM {table}")).mappings().all()
                == before[table]
            )
