"""PostgreSQL session locks own authority; runtime rows only record observations."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from cognition.db.locks import (
    OwnershipLostError,
    OwnershipUnavailableError,
    acquire_individual_lock,
    advisory_lock_key,
)
from cognition.protocols.common import new_id
from cognition.runtime.ownership import acquire_runtime_ownership
from cognition.stores.identity import create_individual
from cognition.stores.runtime import list_runtime_instances
from cognition.testing.clock import FakeClock

NOW = datetime(2026, 9, 22, 12, tzinfo=UTC)


@pytest.fixture
def clock():
    return FakeClock(NOW)


@pytest.fixture
def individual_id(db_session_factory):
    individual_id = new_id()
    with db_session_factory.begin() as session:
        create_individual(
            session,
            individual_id=individual_id,
            birth_at=NOW,
            birth_name="Test",
            founding_orientation="Explore",
            creator_provenance={},
        )
    return individual_id


def acquire(engine, individual_id, clock):
    return acquire_runtime_ownership(
        engine,
        individual_id,
        clock=clock,
        host_id="test-host",
        process_id=123,
        runtime_version="0.1.0",
    )


def observations(factory, individual_id):
    with factory() as session:
        return list_runtime_instances(session, individual_id)


def test_deterministic_signed_bigint_uses_entire_uuid():
    assert (
        advisory_lock_key(UUID("00000000-0000-0000-0000-000000000001"))
        == -5285991911909367266
    )
    assert (
        advisory_lock_key(UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"))
        == 1629119801545528667
    )
    assert advisory_lock_key(UUID(int=1)) != advisory_lock_key(UUID(int=2))


def test_same_individual_fails_fast_without_changing_observations(
    db_engine, db_session_factory, individual_id, clock
):
    with acquire(db_engine, individual_id, clock) as owner:
        before = observations(db_session_factory, individual_id)
        with pytest.raises(OwnershipUnavailableError):
            acquire(db_engine, individual_id, clock)
        assert observations(db_session_factory, individual_id) == before
        assert before[0].runtime_instance_id == owner.runtime_instance_id
        assert before[0].status == "starting"
        assert before[0].started_at == before[0].last_heartbeat_at == NOW


def test_different_individuals_can_own_concurrently(db_engine):
    with acquire_individual_lock(db_engine, new_id()) as first:
        with acquire_individual_lock(db_engine, new_id()) as second:
            assert first.backend_pid != second.backend_pid


def test_lock_survives_transaction_commit_and_rollback(db_engine, individual_id):
    with acquire_individual_lock(db_engine, individual_id) as owner:
        owner.connection.execute(text("SELECT 1"))
        owner.connection.commit()
        with pytest.raises(OwnershipUnavailableError):
            acquire_individual_lock(db_engine, individual_id)
        owner.connection.execute(text("SELECT 1"))
        owner.connection.rollback()
        with pytest.raises(OwnershipUnavailableError):
            acquire_individual_lock(db_engine, individual_id)


def test_dedicated_connection_is_not_reused_by_regular_pool(
    db_engine, db_schema, individual_id
):
    with acquire_individual_lock(db_engine, individual_id) as owner:
        assert isinstance(owner.connection.engine.pool, NullPool)
        assert (
            owner.connection.execute(text("SELECT current_schema()")).scalar_one()
            == db_schema
        )
        assert owner.connection.execute(text("SHOW TimeZone")).scalar_one() == "UTC"
        owner.connection.commit()
        with db_engine.connect() as normal:
            assert (
                normal.execute(text("SELECT pg_backend_pid()")).scalar_one()
                != owner.backend_pid
            )


def test_explicit_close_releases_lock_and_marks_own_row_stopped(
    db_engine, db_session_factory, individual_id, clock
):
    owner = acquire(db_engine, individual_id, clock)
    clock.advance(timedelta(seconds=10))
    owner.close()
    owner.close()
    row = observations(db_session_factory, individual_id)[0]
    assert row.status == "stopped"
    assert row.stopped_at == clock.now()
    with acquire(db_engine, individual_id, clock):
        rows = observations(db_session_factory, individual_id)
        assert {row.status for row in rows} == {"stopped", "starting"}


def test_forced_connection_close_releases_lock_and_stale_row_does_not_block(
    db_engine, db_session_factory, individual_id, clock
):
    first = acquire(db_engine, individual_id, clock)
    first.connection.close()
    clock.advance(timedelta(seconds=20))
    try:
        with acquire(db_engine, individual_id, clock) as second:
            rows = {
                row.runtime_instance_id: row
                for row in observations(db_session_factory, individual_id)
            }
            assert rows[first.runtime_instance_id].status == "crashed"
            assert rows[first.runtime_instance_id].stopped_at == clock.now()
            assert rows[second.runtime_instance_id].status == "starting"
            first.close()
            assert observations(db_session_factory, individual_id) == list(
                rows.values()
            )
    finally:
        first.close()


def test_terminated_backend_cannot_reconnect_and_claim_ownership(
    db_engine, db_session_factory, individual_id, clock
):
    first = acquire(db_engine, individual_id, clock)
    with db_engine.begin() as killer:
        assert killer.execute(
            text("SELECT pg_terminate_backend(:pid)"), {"pid": first.backend_pid}
        ).scalar_one()
    first.close()
    first.close()
    with acquire(db_engine, individual_id, clock):
        rows = observations(db_session_factory, individual_id)
        assert (
            next(
                row
                for row in rows
                if row.runtime_instance_id == first.runtime_instance_id
            ).status
            == "crashed"
        )


def test_observation_failure_rolls_back_and_releases_lock(db_engine, clock):
    unknown_id = new_id()
    with pytest.raises(IntegrityError):
        acquire(db_engine, unknown_id, clock)
    with acquire_individual_lock(db_engine, unknown_id):
        pass


def test_context_exception_always_releases_authority(db_engine, individual_id, clock):
    with pytest.raises(RuntimeError, match="caller failed"):
        with acquire(db_engine, individual_id, clock):
            raise RuntimeError("caller failed")
    with acquire_individual_lock(db_engine, individual_id):
        pass


def test_invalidated_connection_cannot_reopen_without_authority(
    db_engine, individual_id
):
    owner = acquire_individual_lock(db_engine, individual_id)
    connection = owner.connection
    connection.invalidate()
    try:
        with pytest.raises(OwnershipLostError):
            connection.execute(text("SELECT 1"))
        with acquire_individual_lock(db_engine, individual_id):
            pass
    finally:
        owner.close()


def test_close_records_stop_even_after_read_transaction(
    db_engine, db_session_factory, individual_id, clock
):
    owner = acquire(db_engine, individual_id, clock)
    owner.connection.execute(text("SELECT 1"))
    owner.close()
    assert observations(db_session_factory, individual_id)[0].status == "stopped"
