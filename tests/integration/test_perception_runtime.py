"""Fetch outside SQL and fence stale or lost ingestion authority."""

import importlib

import pytest
from sqlalchemy import func, select
from test_autonomy import NOW
from test_autonomy import person as person
from test_perception import binding as binding

from cognition.connectors.base import ConnectorBatch, ConnectorItem
from cognition.db.locks import OwnershipLostError
from cognition.db.models.identity import Individual
from cognition.db.models.perception import ConnectorBinding, Observation
from cognition.runtime.ownership import acquire_runtime_ownership
from cognition.testing.clock import FakeClock


class Source:
    adapter_id = "local_json_v1"
    source_id = "fixture-stream"

    def __init__(self, callback=lambda: None):
        self.callback = callback
        self.calls = []

    def poll(self, cursor):
        self.calls.append(cursor)
        self.callback()
        return ConnectorBatch((ConnectorItem("id", {"text": "incoming"}),), "next")

    def acknowledge(self, cursor):
        pytest.fail("ingestion must never acknowledge source")


@pytest.fixture
def owner(db_engine, person):
    with acquire_runtime_ownership(
        db_engine,
        person,
        clock=FakeClock(NOW),
        host_id="test",
        process_id=999,
        runtime_version="test",
    ) as value:
        yield value


def ingest(owner, binding, source):
    return importlib.import_module("cognition.runtime.perception").ingest_once(
        owner, binding, source, FakeClock(NOW)
    )


def test_fetch_has_no_open_transaction_and_replay_is_noop(
    owner, binding, db_session_factory
):
    def outside_sql():
        assert not owner.connection.in_transaction()

    source = Source(outside_sql)
    first = ingest(owner, binding, source)
    second = ingest(owner, binding, source)
    assert len(first.created_event_ids) == 1 and second.created_event_ids == ()
    assert source.calls == [None, "next"]
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Observation)) == 1


@pytest.mark.parametrize("change", ["disable", "toggle", "pause", "pause_resume"])
def test_changed_authority_during_fetch_rejects_atomically(
    owner, person, binding, db_session_factory, change
):
    def mutate():
        with db_session_factory.begin() as session:
            if change in ("disable", "toggle"):
                row = session.get(ConnectorBinding, binding)
                row.enabled = change == "toggle"
                row.revision += 2 if change == "toggle" else 1
            else:
                row = session.get(Individual, person)
                row.operational_status = (
                    "active" if change == "pause_resume" else "paused"
                )
                row.revision += 2 if change == "pause_resume" else 1

    with pytest.raises(ValueError):
        ingest(owner, binding, Source(mutate))
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Observation)) == 0
        assert session.get(ConnectorBinding, binding).cursor_revision == 0


@pytest.mark.parametrize(
    "blocked", ["disabled", "paused", "wrong_adapter", "wrong_source"]
)
def test_blocked_source_never_fetches(
    owner, person, binding, db_session_factory, blocked
):
    source = Source()
    with db_session_factory.begin() as session:
        if blocked == "disabled":
            session.get(ConnectorBinding, binding).enabled = False
        elif blocked == "paused":
            session.get(Individual, person).operational_status = "paused"
        elif blocked == "wrong_adapter":
            source.adapter_id = "other"
        else:
            source.source_id = "other"
    with pytest.raises(ValueError):
        ingest(owner, binding, source)
    assert not source.calls


def test_lost_connection_during_fetch_never_reconnects(
    owner, binding, db_session_factory
):
    with pytest.raises(OwnershipLostError):
        ingest(owner, binding, Source(lambda: owner.connection.invalidate()))
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Observation)) == 0


def test_caller_transaction_is_not_silently_committed(owner, binding):
    source = Source()
    with owner.connection.begin():
        with pytest.raises(ValueError):
            ingest(owner, binding, source)
    assert not source.calls
