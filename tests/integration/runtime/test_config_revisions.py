"""Sanitized behavior revisions and their evidence commit as one change."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import select

from cognition.config.loader import load_config
from cognition.config.revisions import behavior_config, behavior_hash
from cognition.config.schema import ConfigV1
from cognition.db.models.evidence import Event, EventContent
from cognition.db.models.runtime import RuntimeConfigRevision
from cognition.protocols.common import new_id
from cognition.stores.identity import create_individual
from cognition.testing.clock import FakeClock

NOW = datetime(2026, 9, 22, 12, tzinfo=UTC)
CONFIG = Path(__file__).parents[2] / "fixtures" / "config" / "valid.toml"


@pytest.fixture
def configuration(db_session_factory):
    config = load_config(CONFIG)
    with db_session_factory.begin() as session:
        create_individual(
            session,
            individual_id=config.runtime.individual_id,
            birth_at=NOW,
            birth_name="Config test individual",
            founding_orientation="Explore carefully.",
            creator_provenance={"origin": "integration-test"},
        )
    return config


def reconcile(factory, config, clock):
    from cognition.config.recording import reconcile_config

    return reconcile_config(factory, config.runtime.individual_id, config, clock)


def durable_state(factory):
    with factory() as session:
        revisions = session.scalars(select(RuntimeConfigRevision)).all()
        events = session.scalars(select(Event).order_by(Event.event_sequence)).all()
        contents = session.scalars(select(EventContent)).all()
        return revisions, events, contents


def changed(config, **changes):
    data = config.model_dump()
    for section, values in changes.items():
        data[section].update(values)
    return ConfigV1.model_validate(data)


def test_initial_revision_and_event_use_one_clock_read(
    db_session_factory, configuration
):
    class AdvancingClock:
        calls = 0

        def now(self):
            value = NOW + timedelta(seconds=self.calls)
            self.calls += 1
            return value

    clock = AdvancingClock()
    result = reconcile(db_session_factory, configuration, clock)
    assert clock.calls == 1
    assert result.changed and result.event_id is not None
    assert result.revision.content_hash == behavior_hash(configuration)
    assert result.revision.sanitized_config == behavior_config(configuration)
    revisions, events, contents = durable_state(db_session_factory)
    assert len(revisions) == len(events) == len(contents) == 1
    assert revisions[0].created_at == revisions[0].activated_at == NOW
    assert revisions[0].superseded_at is None
    event = events[0]
    assert event.event_id == result.event_id
    assert event.event_type == "config.changed"
    assert event.subject_kind == "config_revision"
    assert event.subject_id == revisions[0].config_revision_id
    assert event.source_kind == "runtime"
    assert event.occurred_at == event.observed_at == event.recorded_at == NOW
    assert contents[0].payload["content_hash"] == behavior_hash(configuration)


def test_same_hash_is_noop_and_preserves_original_activation(
    db_session_factory, configuration
):
    clock = FakeClock(NOW)
    first = reconcile(db_session_factory, configuration, clock)
    clock.advance(timedelta(hours=1))
    second = reconcile(db_session_factory, configuration, clock)
    assert second.changed is False and second.event_id is None
    assert second.revision == first.revision
    assert [len(rows) for rows in durable_state(db_session_factory)] == [1, 1, 1]


def test_deployment_changes_do_not_create_behavior_revision(
    db_session_factory, configuration
):
    first = reconcile(db_session_factory, configuration, FakeClock(NOW))
    deployed = changed(
        configuration,
        installation={"installation_id": new_id(), "environment": "another-host"},
        database={"url_env": "OTHER_DATABASE_SECRET"},
        runtime={"poll_interval_seconds": 10.0},
        workspace={"root": "private/local/deployment/path"},
        logging={"level": "DEBUG"},
    )
    second = reconcile(
        db_session_factory, deployed, FakeClock(NOW + timedelta(hours=1))
    )
    assert second.changed is False
    assert second.revision.config_revision_id == first.revision.config_revision_id
    assert [len(rows) for rows in durable_state(db_session_factory)] == [1, 1, 1]


def test_behavior_change_supersedes_revision_and_records_event(
    db_session_factory, configuration
):
    first = reconcile(db_session_factory, configuration, FakeClock(NOW))
    updated = changed(configuration, model={"max_output_tokens": 2048})
    next_time = NOW + timedelta(hours=1)
    second = reconcile(db_session_factory, updated, FakeClock(next_time))
    assert second.changed
    assert second.revision.config_revision_id != first.revision.config_revision_id
    assert second.revision.content_hash != first.revision.content_hash
    revisions, events, _ = durable_state(db_session_factory)
    assert len(revisions) == len(events) == 2
    prior = next(
        row
        for row in revisions
        if row.config_revision_id == first.revision.config_revision_id
    )
    active = [
        row
        for row in revisions
        if row.activated_at is not None and row.superseded_at is None
    ]
    assert prior.superseded_at == next_time
    assert (
        len(active) == 1
        and active[0].config_revision_id == second.revision.config_revision_id
    )
    assert active[0].activated_at == next_time


def test_deployment_secrets_and_paths_never_enter_durable_rows(
    db_session_factory, configuration, monkeypatch
):
    secret = "do-not-persist-resolved-database-password"
    monkeypatch.setenv("PRIVATE_DATABASE_REFERENCE", secret)
    deployed = changed(
        configuration,
        database={"url_env": "PRIVATE_DATABASE_REFERENCE"},
        workspace={"root": "PRIVATE_WORKSPACE_PATH"},
        installation={"environment": "PRIVATE_DEPLOYMENT_NAME"},
    )
    reconcile(db_session_factory, deployed, FakeClock(NOW))
    revisions, events, contents = durable_state(db_session_factory)
    serialized = json.dumps(
        [
            {column.name: getattr(row, column.name) for column in row.__table__.columns}
            for row in [*revisions, *events, *contents]
        ],
        default=str,
    )
    for forbidden in (
        secret,
        "PRIVATE_DATABASE_REFERENCE",
        "PRIVATE_WORKSPACE_PATH",
        "PRIVATE_DEPLOYMENT_NAME",
    ):
        assert forbidden not in serialized
    assert set(revisions[0].sanitized_config) == {
        "config_schema_version",
        "model",
        "attention",
        "retention",
        "sandbox",
    }


@pytest.mark.parametrize("existing", [False, True])
def test_revision_and_event_roll_back_together(
    db_session_factory, configuration, monkeypatch, existing
):
    from cognition.config import recording

    if existing:
        reconcile(db_session_factory, configuration, FakeClock(NOW))
    real_append = recording.append_event

    def fail_after_event(session, envelope):
        real_append(session, envelope)
        raise RuntimeError("injected after event flush")

    monkeypatch.setattr(recording, "append_event", fail_after_event)
    updated = changed(configuration, model={"max_output_tokens": 2048})
    with pytest.raises(RuntimeError, match="injected"):
        reconcile(db_session_factory, updated, FakeClock(NOW + timedelta(hours=1)))
    revisions, events, contents = durable_state(db_session_factory)
    assert len(revisions) == len(events) == len(contents) == int(existing)
    if existing:
        assert revisions[0].superseded_at is None
        assert revisions[0].content_hash == behavior_hash(configuration)


@pytest.mark.parametrize("existing", [False, True])
def test_concurrent_equivalent_reconciliation_serializes(
    db_session_factory, configuration, existing
):
    if existing:
        reconcile(db_session_factory, configuration, FakeClock(NOW))
    updated = changed(configuration, model={"max_output_tokens": 2048})
    barrier = Barrier(2)

    def run():
        barrier.wait(timeout=10)
        return reconcile(
            db_session_factory, updated, FakeClock(NOW + timedelta(hours=1))
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run), pool.submit(run)]
        results = [future.result(timeout=10) for future in futures]
    assert sorted(result.changed for result in results) == [False, True]
    assert (
        results[0].revision.config_revision_id == results[1].revision.config_revision_id
    )
    revisions, events, contents = durable_state(db_session_factory)
    assert len(revisions) == len(events) == len(contents) == 1 + int(existing)
    assert (
        sum(
            row.activated_at is not None and row.superseded_at is None
            for row in revisions
        )
        == 1
    )


def test_runtime_individual_mismatch_rejected_before_writes(
    db_session_factory, configuration
):
    from cognition.config.recording import reconcile_config

    with pytest.raises(ValueError, match="individual"):
        reconcile_config(db_session_factory, new_id(), configuration, FakeClock(NOW))
    assert [len(rows) for rows in durable_state(db_session_factory)] == [0, 0, 0]
