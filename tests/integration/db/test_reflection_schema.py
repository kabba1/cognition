"""Reflection continuity and immutable-scope storage survive schema transitions."""

import json
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from schema_metadata import compare_metadata
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from cognition.db.base import Base
from cognition.db.models.attention import Wake
from cognition.protocols.common import new_id
from cognition.stores.identity import create_individual

NOW = datetime(2026, 9, 27, 12, tzinfo=UTC)
TABLES = {"reflection_state", "managed_reflection_batches"}


def test_reflection_migration_round_trip_and_metadata(db_engine, alembic_config):
    assert TABLES <= set(inspect(db_engine).get_table_names())
    with db_engine.begin() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_server_default": True}
        )
        assert compare_metadata(context, Base.metadata) == []
        alembic_config.attributes["connection"] = connection
        command.downgrade(alembic_config, "0010_autonomy_state")
        assert not TABLES.intersection(inspect(connection).get_table_names())
        assert "autonomy_state" in inspect(connection).get_table_names()
        command.upgrade(alembic_config, "head")
        assert compare_metadata(context, Base.metadata) == []


def add_person(session):
    return create_individual(
        session,
        individual_id=new_id(),
        birth_at=NOW,
        birth_name="Reflection schema individual",
        founding_orientation="Keep interpretations grounded",
        creator_provenance={},
    ).individual_id


@pytest.fixture
def person(db_session_factory):
    with db_session_factory.begin() as session:
        return add_person(session)


def metadata(count=1):
    return [
        {
            "kind": "interest",
            "id": str(new_id()),
            "revision": 1,
            "eligible_at": NOW.isoformat(),
        }
        for _ in range(count)
    ]


def add_wake(session, person):
    wake = Wake(
        individual_id=person,
        kind="reflection",
        status="pending",
        due_at=NOW,
        purpose="Review only; no change is required",
        context_refs=[],
        coalesce_key=None,
    )
    session.add(wake)
    session.flush()
    return wake.wake_id


@pytest.fixture
def graph(db_session_factory, person):
    from cognition.db.models import ManagedReflectionBatch, ReflectionState

    with db_session_factory.begin() as session:
        wake_id = add_wake(session, person)
        state = ReflectionState(
            individual_id=person, next_review_at=NOW, managed_wake_id=wake_id
        )
        batch = ManagedReflectionBatch(
            wake_id=wake_id,
            individual_id=person,
            target_metadata=metadata(),
            content_hash="a" * 64,
            selected_at=NOW,
        )
        session.add_all([state, batch])
        session.flush()
        yield session, state, batch


def test_individual_creation_does_not_invent_reflection_rows(db_engine, person):
    with db_engine.connect() as connection:
        for table in TABLES:
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0


def test_state_and_batch_round_trip_with_nullable_position_cursors(graph):
    session, state, batch = graph
    session.expire_all()
    assert state.policy_version == state.revision == batch.policy_version == 1
    assert state.materialization_pending is True
    assert state.next_review_at == batch.selected_at == NOW
    assert state.last_completed_cycle_id is None
    assert (
        state.interest_cursor
        is state.preference_cursor
        is state.self_state_cursor
        is None
    )
    original = batch.target_metadata
    cursors = (new_id(), new_id(), new_id())
    state.interest_cursor, state.preference_cursor, state.self_state_cursor = cursors
    state.managed_wake_id = None
    state.materialization_pending = False
    session.flush()
    session.expire_all()
    assert (
        state.interest_cursor,
        state.preference_cursor,
        state.self_state_cursor,
    ) == cursors
    assert state.managed_wake_id is None and state.materialization_pending is False
    assert batch.target_metadata == original


@pytest.mark.parametrize(
    "table,assignment,constraint",
    [
        ("reflection_state", "policy_version = 2", "policy_version"),
        ("reflection_state", "revision = 0", "revision_positive"),
        ("managed_reflection_batches", "policy_version = 2", "policy_version"),
        ("managed_reflection_batches", "content_hash = ''", "content_hash"),
        (
            "managed_reflection_batches",
            "content_hash = repeat('a', 63)",
            "content_hash",
        ),
        (
            "managed_reflection_batches",
            "content_hash = repeat('A', 64)",
            "content_hash",
        ),
        (
            "managed_reflection_batches",
            "content_hash = repeat('z', 64)",
            "content_hash",
        ),
    ],
)
def test_invalid_scalar_metadata_rejected(graph, table, assignment, constraint):
    session, _, _ = graph
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(text(f"UPDATE {table} SET {assignment}"))
    assert error.value.orig.diag.constraint_name == f"ck_{table}_{constraint}"


@pytest.mark.parametrize("value", [None, {}, "text", 1, [], metadata(9)])
def test_batch_scope_requires_bounded_json_array(graph, value):
    session, _, _ = graph
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(
            text(
                "UPDATE managed_reflection_batches "
                "SET target_metadata = CAST(:value AS jsonb)"
            ),
            {"value": json.dumps(value)},
        )
    assert (
        error.value.orig.diag.constraint_name
        == "ck_managed_reflection_batches_target_metadata_bounds"
    )


def test_eight_target_metadata_rows_are_retained_in_order(graph):
    session, _, batch = graph
    expected = metadata(8)
    batch.target_metadata = expected
    session.flush()
    session.expire(batch)
    assert batch.target_metadata == expected


@pytest.mark.parametrize(
    "table,column,target",
    [
        ("reflection_state", "individual_id", "individuals"),
        ("reflection_state", "managed_wake_id", "wakes"),
        ("reflection_state", "last_completed_cycle_id", "cognition_cycles"),
        ("managed_reflection_batches", "individual_id", "individuals"),
        ("managed_reflection_batches", "wake_id", "wakes"),
    ],
)
def test_reflection_references_require_existing_rows(graph, table, column, target):
    session, _, _ = graph
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(
            text(f"UPDATE {table} SET {column} = :missing"), {"missing": new_id()}
        )
    assert error.value.orig.diag.constraint_name == f"fk_{table}_{column}_{target}"


def test_one_state_per_individual_one_batch_per_wake_and_unique_pointer(graph):
    session, state, batch = graph
    for row in (state, batch):
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
                individual_id=other,
                next_review_at=NOW,
                managed_wake_id=state.managed_wake_id,
            )
        )
    assert (
        error.value.orig.diag.constraint_name == "uq_reflection_state_managed_wake_id"
    )


def test_required_columns_types_indexes_and_server_defaults(db_engine, person):
    schema = inspect(db_engine)
    optional = {
        "managed_wake_id",
        "last_completed_cycle_id",
        "interest_cursor",
        "preference_cursor",
        "self_state_cursor",
    }
    for table in TABLES:
        for column in schema.get_columns(table):
            assert column["nullable"] == (
                table == "reflection_state" and column["name"] in optional
            )
            if column["name"].endswith("_at"):
                assert column["type"].timezone is True
            if column["name"] == "revision":
                assert str(column["type"]) == "BIGINT"
        for foreign in schema.get_foreign_keys(table):
            assert foreign["options"].get("ondelete") in (None, "NO ACTION")
    assert "ix_managed_reflection_batches_individual_id" in {
        index["name"] for index in schema.get_indexes("managed_reflection_batches")
    }
    with db_engine.begin() as connection:
        row = (
            connection.execute(
                text(
                    "INSERT INTO reflection_state (individual_id, next_review_at) "
                    "VALUES (:person, :now) RETURNING *"
                ),
                {"person": person, "now": NOW},
            )
            .mappings()
            .one()
        )
        assert row.policy_version == row.revision == 1
        assert row.materialization_pending is True


@pytest.mark.parametrize("populated", ["state", "batch", "both"])
def test_downgrade_guards_both_current_and_historical_continuity(
    db_engine, db_session_factory, alembic_config, person, populated
):
    from cognition.db.models import ManagedReflectionBatch, ReflectionState

    with db_session_factory.begin() as session:
        if populated in {"state", "both"}:
            session.add(ReflectionState(individual_id=person, next_review_at=NOW))
        if populated in {"batch", "both"}:
            wake_id = add_wake(session, person)
            session.add(
                ManagedReflectionBatch(
                    wake_id=wake_id,
                    individual_id=person,
                    target_metadata=metadata(),
                    content_hash="a" * 64,
                    selected_at=NOW,
                )
            )
    with db_engine.connect() as connection:
        before = {
            table: connection.execute(text(f"SELECT * FROM {table}")).mappings().all()
            for table in TABLES
        }
    with pytest.raises(RuntimeError, match="reflection"):
        with db_engine.begin() as connection:
            alembic_config.attributes["connection"] = connection
            command.downgrade(alembic_config, "0010_autonomy_state")
    with db_engine.connect() as connection:
        for table in TABLES:
            assert (
                connection.execute(text(f"SELECT * FROM {table}")).mappings().all()
                == before[table]
            )
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == ScriptDirectory.from_config(alembic_config).get_current_head()
        )
