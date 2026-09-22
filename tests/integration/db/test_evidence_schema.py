"""PostgreSQL preserves event identity and provenance independently of content."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from cognition.protocols.common import new_id

NOW = datetime(2026, 9, 22, 12, tzinfo=UTC)


def individual(session):
    from cognition.db.models.identity import Individual

    row = Individual(
        birth_at=NOW,
        birth_name="Evidence test individual",
        founding_orientation="Observe carefully.",
        creator_provenance={"kind": "integration-test"},
        operational_status="active",
    )
    session.add(row)
    session.flush()
    return row


def event(session, individual_id, **overrides):
    from cognition.db.models.evidence import Event

    values = dict(
        individual_id=individual_id,
        event_type="observation.received",
        occurred_at=None,
        observed_at=NOW,
        recorded_at=NOW,
        source_kind="connector",
        source_id="inbox",
        source_binding_id=new_id(),
        provenance={"origin": "authenticated-source", "claim": "not established"},
        runtime_version="0.1.0",
    )
    values.update(overrides)
    row = Event(**values)
    session.add(row)
    session.flush()
    return row


def audit(session, person, event_id):
    from cognition.db.models.audit import AdminAudit
    from cognition.db.models.governance import AdminPrincipal

    principal = AdminPrincipal(
        individual_id=person.individual_id,
        authn_provider="local",
        subject=str(new_id()),
        role="admin",
        principal_metadata={},
    )
    session.add(principal)
    session.flush()
    row = AdminAudit(
        individual_id=person.individual_id,
        admin_principal_id=principal.admin_principal_id,
        operation="redact_content",
        target_kind="event",
        target_id=event_id,
        reason="Privacy request",
        before_state={"content_present": True},
        after_state={"content_present": False},
        created_at=NOW,
        event_id=event_id,
    )
    session.add(row)
    session.flush()
    return row


def test_event_sequence_is_unique_ordered_and_not_gapless(db_session_factory):
    from cognition.db.models.evidence import Event

    with db_session_factory() as session:
        person = individual(session)
        first = event(session, person.individual_id)
        with session.begin_nested() as savepoint:
            abandoned = event(session, person.individual_id)
            abandoned_sequence = abandoned.event_sequence
            savepoint.rollback()
        second = event(session, person.individual_id)
        assert first.event_sequence < abandoned_sequence < second.event_sequence
        assert isinstance(first.event_id, UUID)
        assert first.event_id.version == 4
        session.commit()
        stored = session.scalars(select(Event).order_by(Event.event_sequence)).all()
        assert [row.event_id for row in stored] == [first.event_id, second.event_id]
        assert len({row.event_sequence for row in stored}) == 2
        assert all(row.observed_at.utcoffset() == timedelta(0) for row in stored)
        assert all(row.recorded_at.utcoffset() == timedelta(0) for row in stored)
        with pytest.raises(IntegrityError) as caught, session.begin_nested():
            session.execute(
                text(
                    "INSERT INTO events (event_id,event_sequence,individual_id,"
                    "event_type,observed_at,recorded_at,source_kind,provenance,"
                    "runtime_version) OVERRIDING SYSTEM VALUE VALUES "
                    "(:id,:sequence,:individual_id,'test',:now,:now,'runtime',"
                    "'{}'::jsonb,'0.1.0')"
                ),
                {
                    "id": new_id(),
                    "sequence": first.event_sequence,
                    "individual_id": person.individual_id,
                    "now": NOW,
                },
            )
        assert caught.value.orig.diag.constraint_name == "uq_events_event_sequence"


def test_redaction_preserves_event_metadata_and_audit(db_session_factory):
    from cognition.db.models.audit import AdminAudit
    from cognition.db.models.evidence import Event, EventContent

    with db_session_factory() as session:
        person = individual(session)
        observed = event(session, person.individual_id)
        content = EventContent(
            event_id=observed.event_id,
            content_type="application/json",
            payload={"message": "private"},
            text="private",
            blob_ref="artifact:private",
            content_hash="content-digest",
            sensitivity="sensitive",
            retention_class="history",
        )
        session.add(content)
        session.flush()
        original = {
            column.name: getattr(observed, column.name)
            for column in Event.__table__.columns
        }
        redaction_event = event(session, person.individual_id, source_kind="admin")
        record = audit(session, person, redaction_event.event_id)
        record.target_id = observed.event_id
        content.payload = None
        content.text = None
        content.blob_ref = None
        content.redacted_at = NOW
        content.redaction_audit_id = record.audit_id
        session.commit()
        session.expire_all()
        preserved = session.get(Event, observed.event_id)
        assert preserved is not None
        assert {
            column.name: getattr(preserved, column.name)
            for column in Event.__table__.columns
        } == original
        redacted = session.get(EventContent, observed.event_id)
        assert (
            redacted.payload is None
            and redacted.text is None
            and redacted.blob_ref is None
        )
        assert redacted.content_hash == "content-digest"
        assert redacted.redacted_at == NOW
        assert (
            session.get(AdminAudit, redacted.redaction_audit_id).event_id
            == redaction_event.event_id
        )


def test_causation_and_deferred_event_foreign_keys(db_session_factory):
    from cognition.db.models.evidence import Event

    with db_session_factory() as session:
        person = individual(session)
        cause = event(session, person.individual_id)
        effect = event(session, person.individual_id, causation_event_id=cause.event_id)
        assert session.get(Event, effect.causation_event_id) is cause
        with pytest.raises(IntegrityError), session.begin_nested():
            event(session, person.individual_id, causation_event_id=new_id())
        for sql, constraint in (
            (
                "UPDATE individuals SET fork_event_id = :missing "
                "WHERE individual_id = :id",
                "fk_individuals_fork_event_id_events",
            ),
            (
                "INSERT INTO wakes "
                "(wake_id,individual_id,kind,status,due_at,purpose,cause_event_id,"
                "context_refs) VALUES "
                "(:missing,:id,'bootstrap','pending',:now,'Check missing cause',"
                ":missing,'[]'::jsonb)",
                "fk_wakes_cause_event_id_events",
            ),
        ):
            with pytest.raises(IntegrityError) as caught, session.begin_nested():
                session.execute(
                    text(sql),
                    {"missing": new_id(), "id": person.individual_id, "now": NOW},
                )
            assert caught.value.orig.diag.constraint_name == constraint


def test_audit_event_link_is_required_unique_and_referenced(db_session_factory):
    with db_session_factory() as session:
        person = individual(session)
        first = event(session, person.individual_id, source_kind="admin")
        audit(session, person, first.event_id)
        with pytest.raises(IntegrityError), session.begin_nested():
            audit(session, person, first.event_id)
        with pytest.raises(IntegrityError), session.begin_nested():
            audit(session, person, new_id())


@pytest.mark.parametrize(
    "values",
    [
        {"schema_version": 2},
        {"source_kind": "trusted"},
        {"subject_kind": "goal", "subject_id": None},
        {"subject_kind": None, "subject_id": new_id()},
        {"provenance": []},
        {"event_type": ""},
    ],
)
def test_event_constraints_reject_schema_drift(db_session_factory, values):
    with db_session_factory() as session:
        person = individual(session)
        with pytest.raises(IntegrityError), session.begin_nested():
            event(session, person.individual_id, **values)


@pytest.mark.parametrize(
    "values",
    [
        {"sensitivity": "secret"},
        {"retention_class": "forever"},
        {"content_schema_version": 2},
        {"payload": []},
    ],
)
def test_content_constraints(db_session_factory, values):
    from cognition.db.models.evidence import EventContent

    with db_session_factory() as session:
        person = individual(session)
        observed = event(session, person.individual_id)
        data = dict(
            event_id=observed.event_id,
            content_type="text/plain",
            sensitivity="internal",
            retention_class="history",
        )
        data.update(values)
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(EventContent(**data))
            session.flush()


def test_evidence_migration_upgrades_from_identity_and_downgrades(
    alembic_config, db_engine
):
    with db_engine.begin() as connection:
        alembic_config.attributes["connection"] = connection
        command.downgrade(alembic_config, "0002_identity")
        assert "events" not in inspect(connection).get_table_names()
        command.upgrade(alembic_config, "0003_evidence")
        tables = set(inspect(connection).get_table_names())
        assert {"events", "event_contents", "admin_audit"} <= tables
        assert inspect(connection).get_unique_constraints("events")
        assert any(
            fk["referred_table"] == "events"
            for fk in inspect(connection).get_foreign_keys("individuals")
        )
        assert any(
            fk["referred_table"] == "events"
            for fk in inspect(connection).get_foreign_keys("wakes")
        )
