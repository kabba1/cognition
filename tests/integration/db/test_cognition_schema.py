"""PostgreSQL enforces durable cognition identity and recovery constraints."""

from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from cognition.db.base import Base
from cognition.db.models import Individual, RuntimeConfigRevision, Wake
from cognition.protocols.common import new_id

NOW = datetime(2026, 9, 22, 12, tzinfo=UTC)


def individual(session):
    person = Individual(
        birth_at=NOW,
        birth_name="Schema individual",
        founding_orientation="Explore",
        creator_provenance={"source": "integration-test"},
        operational_status="active",
    )
    session.add(person)
    session.flush()
    return person


def dependent(model, individual_id):
    values = {
        RuntimeConfigRevision: dict(
            config_schema_version=1,
            sanitized_config={},
            content_hash="a" * 64,
            created_at=NOW,
        ),
        Wake: dict(
            kind="bootstrap",
            status="pending",
            due_at=NOW,
            purpose="Begin",
            context_refs=[],
        ),
    }[model]
    return model(individual_id=individual_id, **values)


TABLES = {
    "cognition_cycles",
    "cognition_cycle_wakes",
    "cognition_turns",
    "context_snapshots",
    "model_invocations",
    "applied_operations",
    "attention_state",
}


def test_cognition_migration_upgrade_downgrade_and_metadata_parity(
    db_engine, alembic_config
):
    assert TABLES <= set(inspect(db_engine).get_table_names())
    with db_engine.begin() as connection:
        context = MigrationContext.configure(connection)
        assert compare_metadata(context, Base.metadata) == []
        alembic_config.attributes["connection"] = connection
        command.downgrade(alembic_config, "0003_evidence")
        assert not TABLES.intersection(inspect(connection).get_table_names())
        assert "events" in inspect(connection).get_table_names()
        command.upgrade(alembic_config, "0004_cognition")
        assert TABLES <= set(inspect(connection).get_table_names())


@pytest.fixture
def graph(db_session_factory):
    from cognition.db.models.cognition import (
        AppliedOperation,
        AttentionState,
        CognitionCycle,
        CognitionTurn,
        ContextSnapshot,
        CycleWake,
        ModelInvocation,
    )

    with db_session_factory() as session, session.begin():
        person = individual(session)
        config = dependent(RuntimeConfigRevision, person.individual_id)
        wake = dependent(Wake, person.individual_id)
        session.add_all([config, wake])
        session.flush()
        cycle = CognitionCycle(
            individual_id=person.individual_id,
            status="active",
            started_at=NOW,
            max_turns=3,
            max_attempts_per_turn=2,
            max_wakes=16,
            deadline_at=NOW + timedelta(seconds=120),
            min_wake_delay_seconds=1.0,
        )
        session.add(cycle)
        session.flush()
        turn = CognitionTurn(
            cycle_id=cycle.cycle_id, ordinal=1, status="prepared", created_at=NOW
        )
        session.add(turn)
        session.flush()
        rows = [
            cycle,
            turn,
            CycleWake(cycle_id=cycle.cycle_id, wake_id=wake.wake_id),
            ContextSnapshot(
                turn_id=turn.turn_id,
                config_revision_id=config.config_revision_id,
                runtime_contract_version="1",
                model_adapter="scripted",
                requested_model="test",
                request_json={"schema_version": 1},
                rendered_context="Present",
                content_hash="a" * 64,
                selected_refs=[],
                retrieval_reasons={},
                estimated_input_tokens=8,
                created_at=NOW,
            ),
            ModelInvocation(
                turn_id=turn.turn_id, attempt_number=1, status="started", started_at=NOW
            ),
            AppliedOperation(
                operation_id=new_id(),
                turn_id=turn.turn_id,
                individual_id=person.individual_id,
                kind="set_current_focus",
                applied_at=NOW,
            ),
            AttentionState(
                individual_id=person.individual_id, last_cycle_id=cycle.cycle_id
            ),
        ]
        session.add_all(rows)
        session.flush()
        yield session, {row.__tablename__: row for row in rows}


def test_snapshot_and_decision_fields_round_trip_with_nullable_retention(graph):
    session, rows = graph
    turn = rows["cognition_turns"]
    snapshot = rows["context_snapshots"]
    assert turn.validation_errors == []
    assert turn.decision_id is turn.decision_json is turn.decision_hash is None
    assert snapshot.retain_until is None
    turn.decision_id = new_id()
    turn.decision_json = {"schema_version": 1, "disposition": "sleep"}
    turn.decision_hash = "b" * 64
    turn.validation_errors = [{"code": "unsupported_operation"}]
    turn.disposition = "sleep"
    session.flush()
    session.expire_all()
    assert turn.decision_json == {"schema_version": 1, "disposition": "sleep"}
    assert turn.validation_errors == [{"code": "unsupported_operation"}]
    assert snapshot.request_json == {"schema_version": 1}
    assert snapshot.created_at.utcoffset() == timedelta(0)
    assert rows["cognition_cycles"].min_wake_delay_seconds == 1.0
    assert rows["attention_state"].revision == 1


@pytest.mark.parametrize(
    "table,column,value,check",
    [
        ("cognition_cycles", "status", "unknown", "status"),
        ("cognition_cycles", "max_turns", 0, "max_turns_positive"),
        ("cognition_cycles", "max_attempts_per_turn", 0, "max_attempts_positive"),
        ("cognition_cycles", "max_wakes", 0, "max_wakes_positive"),
        (
            "cognition_cycles",
            "min_wake_delay_seconds",
            -1,
            "min_wake_delay_nonnegative",
        ),
        ("cognition_cycles", "revision", 0, "revision_positive"),
        ("cognition_turns", "status", "unknown", "status"),
        ("cognition_turns", "ordinal", 0, "ordinal_positive"),
        ("cognition_turns", "disposition", "unknown", "disposition"),
        ("context_snapshots", "estimated_input_tokens", -1, "input_tokens_nonnegative"),
        ("model_invocations", "attempt_number", 0, "attempt_positive"),
        ("model_invocations", "status", "unknown", "status"),
        ("attention_state", "revision", 0, "revision_positive"),
    ],
)
def test_named_checks_reject_invalid_state(graph, table, column, value, check):
    session, _ = graph
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(text(f"UPDATE {table} SET {column} = :value"), {"value": value})
    assert error.value.orig.diag.constraint_name == f"ck_{table}_{check}"


@pytest.mark.parametrize(
    "table,column,target",
    [
        ("cognition_cycles", "individual_id", "individuals"),
        ("cognition_cycle_wakes", "cycle_id", "cognition_cycles"),
        ("cognition_cycle_wakes", "wake_id", "wakes"),
        ("cognition_turns", "cycle_id", "cognition_cycles"),
        ("context_snapshots", "turn_id", "cognition_turns"),
        ("context_snapshots", "config_revision_id", "runtime_config_revisions"),
        ("model_invocations", "turn_id", "cognition_turns"),
        ("applied_operations", "turn_id", "cognition_turns"),
        ("applied_operations", "individual_id", "individuals"),
        ("attention_state", "individual_id", "individuals"),
        ("attention_state", "last_cycle_id", "cognition_cycles"),
    ],
)
def test_foreign_keys_reject_orphaned_execution_records(graph, table, column, target):
    session, _ = graph
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(text(f"UPDATE {table} SET {column} = :id"), {"id": new_id()})
    expected = f"fk_{table}_{column}_{target}"
    # PostgreSQL truncates long deterministic SQLAlchemy identifiers with a hash.
    assert error.value.orig.diag.constraint_name.startswith(expected[:55])


def test_active_cycle_is_unique_but_terminal_history_is_retained(graph):
    session, rows = graph
    cycle = rows["cognition_cycles"]
    values = {c.name: getattr(cycle, c.name) for c in cycle.__table__.columns}
    values["cycle_id"] = new_id()
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(cycle.__table__.insert().values(**values))
    assert error.value.orig.diag.constraint_name == "uq_cognition_cycles_active"
    cycle.status = "completed"
    session.flush()
    session.execute(cycle.__table__.insert().values(**values))


@pytest.mark.parametrize(
    "table,id_column,unique_constraint",
    [
        ("cognition_turns", "turn_id", "uq_cognition_turns_cycle_ordinal"),
        ("context_snapshots", "snapshot_id", "uq_context_snapshots_turn_id"),
        ("model_invocations", "invocation_id", "uq_model_invocations_turn_attempt"),
        ("applied_operations", None, "pk_applied_operations"),
    ],
)
def test_execution_identity_uniqueness(graph, table, id_column, unique_constraint):
    session, rows = graph
    row = rows[table]
    values = {c.name: getattr(row, c.name) for c in row.__table__.columns}
    if id_column:
        values[id_column] = new_id()
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(row.__table__.insert().values(**values))
    assert error.value.orig.diag.constraint_name == unique_constraint


def test_decision_identity_cannot_be_reused_by_another_turn(graph):
    session, rows = graph
    turn = rows["cognition_turns"]
    turn.decision_id = new_id()
    session.flush()
    values = {c.name: getattr(turn, c.name) for c in turn.__table__.columns}
    values.update(turn_id=new_id(), ordinal=2)
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(turn.__table__.insert().values(**values))
    assert error.value.orig.diag.constraint_name == "uq_cognition_turns_decision_id"


def test_wake_has_only_one_owning_cycle(graph):
    session, rows = graph
    cycle = rows["cognition_cycles"]
    values = {c.name: getattr(cycle, c.name) for c in cycle.__table__.columns}
    other_cycle = new_id()
    values.update(cycle_id=other_cycle, status="completed")
    session.execute(cycle.__table__.insert().values(**values))
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(
            rows["cognition_cycle_wakes"]
            .__table__.insert()
            .values(
                cycle_id=other_cycle,
                wake_id=rows["cognition_cycle_wakes"].wake_id,
            )
        )
    assert error.value.orig.diag.constraint_name == "uq_cognition_cycle_wakes_wake_id"


@pytest.mark.parametrize(
    "table,statuses",
    [
        ("cognition_cycles", ["active", "completed", "failed"]),
        (
            "cognition_turns",
            ["prepared", "invoking", "decided", "applied", "rejected", "failed"],
        ),
        ("model_invocations", ["started", "completed", "failed", "abandoned"]),
    ],
)
def test_all_recovery_states_can_be_persisted(graph, table, statuses):
    session, rows = graph
    row = rows[table]
    for status in statuses:
        row.status = status
        session.flush()
        session.expire(row)
        assert row.status == status


def test_validation_errors_remain_a_json_list(graph):
    session, _ = graph
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(text("UPDATE cognition_turns SET validation_errors = '{}'"))
    assert error.value.orig.diag.constraint_name == (
        "ck_cognition_turns_validation_errors_array"
    )


def test_recovery_limits_are_required_and_zero_delay_is_permitted(graph):
    session, rows = graph
    cycle = rows["cognition_cycles"]
    cycle.min_wake_delay_seconds = 0
    session.flush()
    session.expire(cycle)
    assert cycle.min_wake_delay_seconds == 0
    values = {c.name: getattr(cycle, c.name) for c in cycle.__table__.columns}
    values.update(cycle_id=new_id(), status="completed")
    for required in (
        "max_turns",
        "max_attempts_per_turn",
        "max_wakes",
        "deadline_at",
        "min_wake_delay_seconds",
    ):
        missing = {key: value for key, value in values.items() if key != required}
        with pytest.raises(IntegrityError) as error, session.begin_nested():
            session.execute(cycle.__table__.insert().values(**missing))
        assert error.value.orig.diag.column_name == required
