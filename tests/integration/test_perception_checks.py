"""Perception diagnostics retain checkpoint and membership truth without writes."""

import importlib
from datetime import timedelta

import pytest
from sqlalchemy import event, select
from test_perception import NOW, ingest
from test_perception import binding as binding
from test_perception import person as person

from cognition.db.models.attention import Wake
from cognition.db.models.evidence import Event, EventContent
from cognition.db.models.perception import (
    ConnectorBinding,
    InboundWake,
    IngestionReceipt,
    Observation,
)
from cognition.protocols.common import new_id


def findings(session):
    result = []
    checks = importlib.import_module("cognition.db.perception_checks")
    checks.check_perception_state(session, lambda *args: result.append(args))
    return result


@pytest.fixture
def graph(db_session_factory, person, binding):
    with db_session_factory.begin() as session:
        first = ingest(
            session,
            person,
            binding,
            [("one", {"text": "private source secret"}), ("two", {})],
            "cursor-one",
        )
    with db_session_factory.begin() as session:
        second = ingest(
            session,
            person,
            binding,
            [("one", {"text": "private source secret"}), ("three", {})],
            "cursor-two",
            NOW + timedelta(seconds=1),
        )
    return person, binding, first, second


def test_empty_perception_is_healthy(db_session_factory, person):
    with db_session_factory() as session:
        assert findings(session) == []


def test_healthy_history_remains_valid_after_all_content_is_redacted(
    db_session_factory, graph
):
    with db_session_factory() as session:
        assert findings(session) == []
    with db_session_factory.begin() as session:
        for content in session.scalars(select(EventContent)):
            content.text = content.payload = content.blob_ref = None
            content.redacted_at = NOW
    with db_session_factory() as session:
        assert findings(session) == []


@pytest.mark.parametrize("fault", ["null", "stale", "cursor", "revision"])
def test_binding_checkpoint_matches_latest_retained_receipt(
    db_session_factory, graph, fault
):
    _, identity, first, _ = graph
    with db_session_factory.begin() as session:
        row = session.get(ConnectorBinding, identity)
        if fault == "null":
            row.latest_receipt_event_id = row.cursor = None
            row.cursor_revision = 0
        elif fault == "stale":
            row.latest_receipt_event_id = first.receipt_event_id
            row.cursor, row.cursor_revision = "cursor-one", 1
        elif fault == "cursor":
            row.cursor = "private tampered cursor"
        else:
            row.cursor_revision += 1
    with db_session_factory() as session:
        result = findings(session)
        assert any(item[0] == "perception_checkpoint" for item in result)
        assert "private" not in str(result)


def rehash(receipt):
    fields = {
        column.name: getattr(receipt, column.name)
        for column in receipt.__table__.columns
        if column.name != "receipt_hash"
    }
    receipt.receipt_hash = importlib.import_module(
        "cognition.stores.perception"
    ).receipt_hash(fields)


@pytest.mark.parametrize(
    "fault", ["hash", "marker", "before_cursor", "count", "time", "binding_revision"]
)
def test_full_receipt_history_checks_hash_chain_markers_and_owned_counts(
    db_session_factory, graph, fault
):
    _, _, first, second = graph
    with db_session_factory.begin() as session:
        row = session.get(
            IngestionReceipt,
            second.receipt_event_id
            if fault == "before_cursor"
            else first.receipt_event_id,
        )
        if fault == "hash":
            row.receipt_hash = "0" * 64
        elif fault == "marker":
            session.get(Event, row.event_id).source_kind = "model"
        elif fault == "before_cursor":
            row.before_cursor = "private invented previous cursor"
            rehash(row)
        elif fault == "count":
            row.new_count -= 1
            row.duplicate_count += 1
            rehash(row)
        elif fault == "time":
            session.get(Event, row.event_id).recorded_at += timedelta(seconds=1)
        else:
            row.authorizing_binding_revision = 2**40
            rehash(row)
    with db_session_factory() as session:
        result = findings(session)
        assert any(item[0].startswith("perception_receipt") for item in result)
        assert "private" not in str(result)


@pytest.mark.parametrize(
    "fault",
    [
        "event_type",
        "source",
        "binding",
        "actor",
        "subject",
        "correlation",
        "receipt",
        "fingerprint",
        "unmarked_redaction",
    ],
)
def test_observation_envelope_and_membership_cannot_acquire_authority(
    db_session_factory, graph, fault
):
    _, _, first, second = graph
    with db_session_factory.begin() as session:
        row = session.get(Observation, first.created_event_ids[0])
        envelope = session.get(Event, row.event_id)
        if fault == "event_type":
            envelope.event_type = "admin.resume"
        elif fault == "source":
            envelope.source_kind = "admin"
        elif fault == "binding":
            envelope.source_binding_id = new_id()
        elif fault == "actor":
            envelope.actor_entity_id = new_id()
        elif fault == "subject":
            envelope.subject_kind, envelope.subject_id = "governance", graph[0]
        elif fault == "correlation":
            envelope.correlation_id = new_id()
        elif fault == "receipt":
            row.receipt_event_id = second.receipt_event_id
        elif fault == "fingerprint":
            row.content_fingerprint = "0" * 64
        else:
            content = session.get(EventContent, row.event_id)
            content.text = content.payload = None
    with db_session_factory() as session:
        result = findings(session)
        assert any(item[0].startswith("perception_observation") for item in result)
        assert "private source secret" not in str(result)


@pytest.mark.parametrize("fault", ["refs", "coalesce", "kind", "pointer", "seal"])
def test_inbound_wake_membership_and_pointer_are_durable(
    db_session_factory, graph, fault
):
    _, binding_id, first, _ = graph
    with db_session_factory.begin() as session:
        wake_id = first.wake_ids[0]
        wake = session.get(Wake, wake_id)
        if fault == "refs":
            wake.context_refs = []
        elif fault == "coalesce":
            wake.coalesce_key = "model-selectable-key"
        elif fault == "kind":
            wake.kind = "self_scheduled"
        elif fault == "pointer":
            session.get(ConnectorBinding, binding_id).pending_wake_id = None
        else:
            session.get(InboundWake, wake_id).sealed_at = NOW
    with db_session_factory() as session:
        assert any(item[0] == "perception_inbound" for item in findings(session))


def test_missing_observation_metadata_is_detected_from_retained_envelope(
    db_session_factory, graph
):
    with db_session_factory.begin() as session:
        session.delete(session.get(Observation, graph[2].created_event_ids[0]))
    with db_session_factory() as session:
        assert any(item[0] == "perception_observation" for item in findings(session))


def test_removed_receipt_metadata_cannot_hide_behind_reset_binding(
    db_session_factory, graph
):
    _, binding_id, first, second = graph
    with db_session_factory.begin() as session:
        binding = session.get(ConnectorBinding, binding_id)
        binding.latest_receipt_event_id = first.receipt_event_id
        binding.cursor, binding.cursor_revision = "cursor-one", 1
        for identity in second.created_event_ids:
            session.delete(session.get(Observation, identity))
        session.flush()
        session.delete(session.get(IngestionReceipt, second.receipt_event_id))
    with db_session_factory() as session:
        assert any(
            item[0] in {"perception_checkpoint", "perception_receipt"}
            for item in findings(session)
        )


def test_diagnostics_leave_dirty_orm_cache_and_database_untouched(
    db_engine, db_session_factory, graph
):
    _, identity, first, _ = graph
    writes = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().split(maxsplit=1)[0] in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)

    with db_session_factory() as session:
        binding = session.get(ConnectorBinding, identity)
        receipt = session.get(IngestionReceipt, first.receipt_event_id)
        observation = session.get(Observation, first.created_event_ids[0])
        wake = session.get(Wake, first.wake_ids[0])
        binding.cursor = "dirty private cursor"
        receipt.receipt_hash = "0" * 64
        observation.authentication = {"actor": "dirty private assertion"}
        wake.context_refs = []
        dirty = set(session.dirty)
        event.listen(db_engine, "before_cursor_execute", observe)
        try:
            assert findings(session) == []
        finally:
            event.remove(db_engine, "before_cursor_execute", observe)
        assert set(session.dirty) == dirty
        assert binding.cursor == "dirty private cursor" and wake.context_refs == []
        assert observation.authentication == {"actor": "dirty private assertion"}
        assert not writes


def test_general_integrity_report_includes_perception_diagnostics(
    db_session_factory, graph
):
    from cognition.db.checks import check_database

    with db_session_factory.begin() as session:
        session.get(ConnectorBinding, graph[1]).cursor = "changed"
    with db_session_factory() as session:
        assert any(
            finding.invariant_id == "perception_checkpoint"
            for finding in check_database(session).findings
        )


@pytest.mark.parametrize("model_name", ["binding", "receipt", "observation", "inbound"])
def test_individually_existing_foreign_owners_do_not_make_links_valid(
    db_session_factory, graph, model_name
):
    from test_integrity_checks import create_healthy

    other = create_healthy(db_session_factory)
    _, binding_id, first, _ = graph
    with db_session_factory.begin() as session:
        model, identity = {
            "binding": (ConnectorBinding, binding_id),
            "receipt": (IngestionReceipt, first.receipt_event_id),
            "observation": (Observation, first.created_event_ids[0]),
            "inbound": (InboundWake, first.wake_ids[0]),
        }[model_name]
        row = session.get(model, identity)
        row.individual_id = other.individual_id
        if model_name == "receipt":
            rehash(row)
    with db_session_factory() as session:
        result = findings(session)
        assert result and all(item[0].startswith("perception_") for item in result)


@pytest.mark.parametrize("status", ["claimed", "consumed"])
def test_managed_inbound_terminal_or_active_status_requires_owned_cycle(
    db_session_factory, graph, status
):
    _, binding_id, first, _ = graph
    with db_session_factory.begin() as session:
        wake_id = first.wake_ids[0]
        session.get(ConnectorBinding, binding_id).pending_wake_id = None
        session.get(InboundWake, wake_id).sealed_at = NOW + timedelta(seconds=1)
        wake = session.get(Wake, wake_id)
        wake.status = status
        wake.claimed_at = NOW + timedelta(seconds=1)
        wake.consumed_at = NOW + timedelta(seconds=2) if status == "consumed" else None
    with db_session_factory() as session:
        assert any(item[0] == "perception_inbound" for item in findings(session))
