"""Phase 1 exit scenarios over real PostgreSQL, without Phase 2 execution."""

import json
import os
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import Queue
from threading import Thread
from uuid import UUID

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.engine import make_url

from cognition.config.loader import load_config
from cognition.config.recording import reconcile_config
from cognition.config.revisions import behavior_hash
from cognition.config.schema import ConfigV1
from cognition.db.base import Base
from cognition.db.locks import OwnershipUnavailableError, advisory_lock_key
from cognition.db.models import (
    AdminAudit,
    AdminPrincipal,
    Event,
    EventContent,
    GovernanceState,
    Individual,
    RuntimeConfigRevision,
    RuntimeInstance,
    Wake,
)
from cognition.db.session import create_db_engine, create_session_factory
from cognition.protocols.common import Ref, new_id
from cognition.protocols.events_v1 import EventEnvelopeV1
from cognition.runtime.birth import BirthInput, BirthResult, birth
from cognition.runtime.ownership import acquire_runtime_ownership
from cognition.stores.evidence import (
    append_event,
    load_event,
    record_admin_audit,
    redact_event_content,
)
from cognition.stores.identity import load_individual
from cognition.stores.runtime import list_runtime_instances
from cognition.testing.clock import FakeClock

NOW = datetime(2026, 9, 22, 14, tzinfo=UTC)
SECRET_MARKER = "phase1-secret-value-must-never-be-persisted"
BIRTH_MODELS = (
    Individual,
    GovernanceState,
    AdminPrincipal,
    RuntimeConfigRevision,
    Event,
    EventContent,
    Wake,
)


@dataclass
class Scenario:
    request: BirthInput
    born: BirthResult
    clock: FakeClock


@pytest.fixture
def birth_request(monkeypatch):
    configuration = load_config(
        Path(__file__).parents[2] / "fixtures" / "config" / "valid.toml"
    )
    individual_id = new_id()
    configuration.runtime.individual_id = individual_id
    monkeypatch.setenv(configuration.database.url_env, SECRET_MARKER)
    return BirthInput(
        individual_id=individual_id,
        birth_name="Acceptance individual",
        founding_orientation="Observe carefully.",
        creator_provenance={"source": "phase1-acceptance"},
        admin_authn_provider="local_os",
        admin_subject="acceptance-admin",
        config=configuration,
        runtime_version="phase1-acceptance",
    )


@pytest.fixture
def scenario(db_session_factory, birth_request):
    clock = FakeClock(NOW)
    return Scenario(
        birth_request, birth(db_session_factory, birth_request, clock), clock
    )


def acquire(engine, scenario):
    return acquire_runtime_ownership(
        engine,
        scenario.born.individual_id,
        clock=scenario.clock,
        host_id="acceptance-parent",
        process_id=os.getpid(),
        runtime_version="phase1-acceptance",
    )


@contextmanager
def restarted(db_url, db_schema):
    """Open independent deployment connections after the former owner closes."""
    engine = create_db_engine(db_url, schema=db_schema)
    try:
        yield engine, create_session_factory(engine)
    finally:
        engine.dispose()


def assert_recovered(factory, db_url):
    from cognition.db.checks import check_database

    with factory() as session:
        report = check_database(session)
        assert report.healthy, [finding.invariant_id for finding in report.findings]
        secrets = [SECRET_MARKER, db_url, make_url(db_url).password]
        for table in Base.metadata.tables.values():
            for row in session.execute(select(table)).mappings():
                rendered = str(dict(row))
                if any(secret and secret in rendered for secret in secrets):
                    pytest.fail("A secret value was persisted in durable state")
        assert "actions" not in Base.metadata.tables


def genesis(factory, scenario):
    with factory() as session:
        return load_event(session, scenario.born.genesis_event_id).envelope.model_dump(
            mode="json"
        )


def observations(factory, scenario):
    with factory() as session:
        return list_runtime_instances(session, scenario.born.individual_id)


def admin(factory, scenario, operation):
    from cognition.policy.governance import AuthenticatedPrincipal
    from cognition.runtime.lifecycle import apply_admin_operation

    result = apply_admin_operation(
        factory,
        scenario.born.individual_id,
        AuthenticatedPrincipal("local_os", "acceptance-admin"),
        operation,
        "Phase 1 acceptance transition",
        scenario.clock,
    )
    with factory() as session:
        audit = session.get(AdminAudit, result.audit_id)
        assert audit.admin_principal_id == scenario.born.admin_principal_id
        assert audit.event_id == result.event_id
        assert audit.operation == operation
        evidence = session.get(Event, audit.event_id)
        assert evidence.individual_id == scenario.born.individual_id
        assert evidence.source_kind == "admin"
    return result


def external_event(scenario, event_type, *, occurred_at=NOW, payload=None):
    return EventEnvelopeV1.model_validate(
        {
            "schema_version": 1,
            "event_id": new_id(),
            "individual_id": scenario.born.individual_id,
            "event_type": event_type,
            "occurred_at": occurred_at,
            "observed_at": scenario.clock.now(),
            "recorded_at": scenario.clock.now(),
            "source": {
                "kind": "connector",
                "source_id": "inbound-test",
                "binding_id": None,
            },
            "actor_entity_id": None,
            "causation_event_id": None,
            "correlation_id": None,
            "subject": {"kind": "individual", "id": scenario.born.individual_id},
            "provenance": {"external_id": event_type, "delivery": "source observation"},
            "content": {
                "content_type": "application/json",
                "payload": payload or {"message": event_type},
                "text": "external content",
                "blob_ref": None,
                "content_hash": "a" * 64,
                "sensitivity": "internal",
                "retention_class": "history",
                "retain_until": None,
            },
            "runtime_version": "phase1-acceptance",
        }
    )


@pytest.mark.parametrize("model", BIRTH_MODELS)
def test_birth_crash_after_each_write_recovers_without_partial_identity(
    db_engine, db_session_factory, db_url, birth_request, model
):
    def fail_after_insert(
        connection, cursor, statement, parameters, context, executemany
    ):
        table = getattr(getattr(context.compiled, "statement", None), "table", None)
        if context.isinsert and getattr(table, "name", None) == model.__tablename__:
            raise RuntimeError("deterministic birth crash")

    event.listen(db_engine, "after_cursor_execute", fail_after_insert)
    try:
        with pytest.raises(RuntimeError, match="birth crash"):
            birth(db_session_factory, birth_request, FakeClock(NOW))
    finally:
        event.remove(db_engine, "after_cursor_execute", fail_after_insert)
    with db_session_factory() as session:
        for table in BIRTH_MODELS:
            assert session.scalar(select(func.count()).select_from(table)) == 0
    assert_recovered(db_session_factory, db_url)
    birth(db_session_factory, birth_request, FakeClock(NOW))
    with db_session_factory() as session:
        for table in BIRTH_MODELS:
            assert session.scalar(select(func.count()).select_from(table)) == 1
    assert_recovered(db_session_factory, db_url)


def test_duplicate_runtime_startup_cannot_create_second_owner(
    db_engine, db_session_factory, db_url, scenario
):
    original = genesis(db_session_factory, scenario)
    with acquire(db_engine, scenario) as first:
        with pytest.raises(OwnershipUnavailableError):
            acquire(db_engine, scenario)
        rows = observations(db_session_factory, scenario)
        assert len(rows) == 1
        assert rows[0].runtime_instance_id == first.runtime_instance_id
        assert rows[0].status == "starting"
        assert_recovered(db_session_factory, db_url)
    assert genesis(db_session_factory, scenario) == original
    assert_recovered(db_session_factory, db_url)


def test_ownership_connection_death_releases_authority_and_records_crash(
    db_engine, db_session_factory, db_url, scenario
):
    first = acquire(db_engine, scenario)
    try:
        with db_engine.begin() as connection:
            # This backend PID belongs to the ownership object just created here.
            assert connection.execute(
                text("SELECT pg_terminate_backend(:pid, 5000)"),
                {"pid": first.backend_pid},
            ).scalar_one()
        first.close()
        scenario.clock.advance(timedelta(seconds=1))
        with acquire(db_engine, scenario) as replacement:
            rows = {
                row.runtime_instance_id: row
                for row in observations(db_session_factory, scenario)
            }
            assert rows[first.runtime_instance_id].status == "crashed"
            assert rows[replacement.runtime_instance_id].status == "starting"
            assert_recovered(db_session_factory, db_url)
    finally:
        first.close()
    assert_recovered(db_session_factory, db_url)


def test_actual_owner_process_death_releases_lock_without_graceful_shutdown(
    db_engine, db_session_factory, db_schema, db_url, scenario
):
    environment = os.environ.copy()
    environment.update(
        {
            "COGNITION_ACCEPTANCE_DB_URL": db_url,
            "COGNITION_ACCEPTANCE_SCHEMA": db_schema,
            "COGNITION_ACCEPTANCE_INDIVIDUAL_ID": str(scenario.born.individual_id),
            "COGNITION_ACCEPTANCE_TIME": NOW.isoformat(),
        }
    )
    child = subprocess.Popen(
        [sys.executable, "-u", str(Path(__file__).with_name("owner_process.py"))],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    ready = Queue()
    reader = Thread(target=lambda: ready.put(child.stdout.readline()), daemon=True)
    reader.start()
    try:
        handshake = json.loads(ready.get(timeout=15))
        assert handshake["status"] == "ready", "Owned child could not acquire authority"
        instance_id = UUID(handshake["runtime_instance_id"])
        with pytest.raises(OwnershipUnavailableError):
            acquire(db_engine, scenario)
        # Terminate only the process created above, never a discovered host process.
        child.terminate()
        child.wait(timeout=10)
        assert child.returncode != 0
        # PostgreSQL itself supplies the bounded synchronization barrier: the
        # blocking lock wakes when the dead client's session releases authority.
        with db_engine.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            key = advisory_lock_key(scenario.born.individual_id)
            connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
            assert connection.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": key}
            ).scalar_one()
        with acquire(db_engine, scenario) as replacement:
            rows = {
                row.runtime_instance_id: row
                for row in observations(db_session_factory, scenario)
            }
            assert rows[instance_id].status == "crashed"
            assert rows[replacement.runtime_instance_id].status == "starting"
            assert_recovered(db_session_factory, db_url)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
        child.stdin.close()
        child.stdout.close()
        reader.join(timeout=5)
    assert_recovered(db_session_factory, db_url)


def test_pause_survives_restart_and_resume_is_audited(
    db_engine, db_session_factory, db_url, db_schema, scenario
):
    from cognition.runtime.lifecycle import is_runnable

    original = genesis(db_session_factory, scenario)
    with acquire(db_engine, scenario):
        assert (
            admin(db_session_factory, scenario, "pause").operational_status == "paused"
        )
    with restarted(db_url, db_schema) as (engine, factory), acquire(engine, scenario):
        with factory() as session:
            identity = load_individual(session, scenario.born.individual_id)
            assert identity.operational_status == "paused"
            assert not is_runnable(identity.operational_status)
        assert_recovered(factory, db_url)
        assert admin(factory, scenario, "resume").operational_status == "active"
        assert_recovered(factory, db_url)
    assert genesis(db_session_factory, scenario) == original


def test_quiesce_and_quiescent_state_survive_independent_restarts(
    db_engine, db_session_factory, db_url, db_schema, scenario
):
    from cognition.runtime.lifecycle import is_runnable

    with acquire(db_engine, scenario):
        assert (
            admin(db_session_factory, scenario, "begin_quiesce").operational_status
            == "quiescing"
        )
    with restarted(db_url, db_schema) as (engine, factory), acquire(engine, scenario):
        with factory() as session:
            assert (
                load_individual(session, scenario.born.individual_id).operational_status
                == "quiescing"
            )
        assert_recovered(factory, db_url)
        assert (
            admin(factory, scenario, "complete_quiesce").operational_status
            == "quiescent"
        )
    with restarted(db_url, db_schema) as (engine, factory), acquire(engine, scenario):
        with factory() as session:
            identity = load_individual(session, scenario.born.individual_id)
            assert identity.operational_status == "quiescent"
            assert not is_runnable(identity.operational_status)
        assert_recovered(factory, db_url)


def test_config_revision_change_is_durable_sanitized_and_restart_idempotent(
    db_session_factory, db_url, db_schema, scenario
):
    original = genesis(db_session_factory, scenario)
    updated = scenario.request.config.model_dump()
    updated["model"]["max_output_tokens"] += 1
    configuration = ConfigV1.model_validate(updated)
    scenario.clock.advance(timedelta(seconds=1))
    changed = reconcile_config(
        db_session_factory, scenario.born.individual_id, configuration, scenario.clock
    )
    assert changed.changed
    with restarted(db_url, db_schema) as (_, factory):
        again = reconcile_config(
            factory, scenario.born.individual_id, configuration, scenario.clock
        )
        assert not again.changed and again.event_id is None
        assert again.revision.content_hash == behavior_hash(configuration)
        with factory() as session:
            revisions = session.scalars(select(RuntimeConfigRevision)).all()
            assert len(revisions) == 2
            assert sum(revision.superseded_at is None for revision in revisions) == 1
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(Event)
                    .where(Event.event_type == "config.changed")
                )
                == 1
            )
        assert_recovered(factory, db_url)
    assert genesis(db_session_factory, scenario) == original


def test_event_append_commit_and_rollback_survive_restart_with_provenance(
    db_session_factory, db_url, db_schema, scenario
):
    later = external_event(
        scenario, "arrival.later", occurred_at=NOW + timedelta(hours=1)
    )
    earlier = external_event(
        scenario, "arrival.earlier", occurred_at=NOW - timedelta(hours=1)
    )
    with db_session_factory.begin() as session:
        first = append_event(session, later)
        second = append_event(session, earlier)
    abandoned = external_event(scenario, "arrival.uncommitted")
    with (
        pytest.raises(RuntimeError, match="append crash"),
        db_session_factory.begin() as session,
    ):
        append_event(session, abandoned)
        raise RuntimeError("deterministic append crash")
    with restarted(db_url, db_schema) as (_, factory):
        with factory() as session:
            read_first = load_event(session, first.envelope.event_id)
            read_second = load_event(session, second.envelope.event_id)
            assert read_first.envelope == later and read_second.envelope == earlier
            assert read_first.event_sequence < read_second.event_sequence
            assert read_first.envelope.occurred_at > read_second.envelope.occurred_at
            with pytest.raises(LookupError):
                load_event(session, abandoned.event_id)
        assert_recovered(factory, db_url)


def test_content_redaction_preserves_metadata_order_provenance_and_audit_after_restart(
    db_session_factory, db_url, db_schema, scenario
):
    source = external_event(
        scenario, "source.private", payload={"private": "redactable"}
    )
    with db_session_factory.begin() as session:
        original = append_event(session, source)
        audit_envelope = external_event(scenario, "admin.content_redacted")
        audit_envelope.source.kind = "admin"
        audit_envelope.source.source_id = str(scenario.born.admin_principal_id)
        audit_envelope.subject = Ref(kind="event", id=source.event_id)
        append_event(session, audit_envelope)
        audit_id = record_admin_audit(
            session,
            individual_id=scenario.born.individual_id,
            admin_principal_id=scenario.born.admin_principal_id,
            operation="redact_event_content",
            target=Ref(kind="event", id=source.event_id),
            reason="Acceptance retention test",
            before_state={"redacted": False},
            after_state={"redacted": True},
            created_at=scenario.clock.now(),
            event_id=audit_envelope.event_id,
        )
        redact_event_content(
            session,
            source.event_id,
            audit_id=audit_id,
            redacted_at=scenario.clock.now(),
        )
    with restarted(db_url, db_schema) as (_, factory):
        with factory() as session:
            redacted = load_event(session, source.event_id)
            assert redacted.event_sequence == original.event_sequence
            assert redacted.envelope.model_dump(
                exclude={"content"}
            ) == original.envelope.model_dump(exclude={"content"})
            assert redacted.envelope.content.payload is None
            assert redacted.envelope.content.text is None
            assert redacted.envelope.content.blob_ref is None
            assert (
                redacted.envelope.content.content_hash
                == original.envelope.content.content_hash
            )
            assert redacted.redaction_audit_id == audit_id
            assert session.get(AdminAudit, audit_id).event_id == audit_envelope.event_id
        assert_recovered(factory, db_url)


def test_stale_runtime_rows_are_observations_and_new_owner_cleans_them(
    db_engine, db_session_factory, db_url, scenario
):
    stale_ids = []
    terminal_ids = []
    with db_session_factory.begin() as session:
        for status in ("starting", "running", "stopping", "stopped", "crashed"):
            row = RuntimeInstance(
                individual_id=scenario.born.individual_id,
                status=status,
                host_id="former-deployment",
                process_id=123,
                started_at=NOW,
                last_heartbeat_at=NOW,
                runtime_version="phase1-acceptance",
                stopped_at=NOW if status in ("stopped", "crashed") else None,
            )
            session.add(row)
            session.flush()
            (terminal_ids if row.stopped_at else stale_ids).append(
                row.runtime_instance_id
            )
    scenario.clock.advance(timedelta(minutes=1))
    with acquire(db_engine, scenario) as current:
        rows = {
            row.runtime_instance_id: row
            for row in observations(db_session_factory, scenario)
        }
        for runtime_id in stale_ids:
            assert rows[runtime_id].status == "crashed"
            assert rows[runtime_id].stopped_at == scenario.clock.now()
        assert [rows[runtime_id].status for runtime_id in terminal_ids] == [
            "stopped",
            "crashed",
        ]
        assert all(rows[runtime_id].stopped_at == NOW for runtime_id in terminal_ids)
        assert rows[current.runtime_instance_id].status == "starting"
        assert (
            sum(
                row.status in ("starting", "running", "stopping")
                for row in rows.values()
            )
            == 1
        )
        assert_recovered(db_session_factory, db_url)
    assert_recovered(db_session_factory, db_url)
