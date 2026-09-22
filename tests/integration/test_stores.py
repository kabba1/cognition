"""Caller-owned transactions protect durable identity and evidence."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from cognition.protocols.wakes_v1 import WakeV1

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def event_for(individual_id):
    from cognition.protocols.events_v1 import EventContent, EventEnvelopeV1, EventSource

    return EventEnvelopeV1(
        schema_version=1,
        event_id=uuid4(),
        individual_id=individual_id,
        event_type="test",
        occurred_at=None,
        observed_at=NOW,
        recorded_at=NOW,
        source=EventSource(kind="runtime", source_id=None, binding_id=None),
        actor_entity_id=None,
        causation_event_id=None,
        correlation_id=None,
        subject=None,
        provenance={"original": True},
        runtime_version="test",
        content=EventContent(
            content_type="text/plain",
            payload={"sensitive": True},
            text="secret",
            blob_ref="blob:test",
            content_hash="hash:original",
            sensitivity="sensitive",
            retention_class="history",
            retain_until=None,
        ),
    )


def create_identity(session):
    from cognition.stores.identity import create_individual

    return create_individual(
        session,
        individual_id=uuid4(),
        birth_at=NOW,
        birth_name="Ada",
        founding_orientation="Learn carefully",
        creator_provenance={"source": "test"},
    )


def test_store_operations_roll_back_together(db_session_factory):
    from cognition.db.models.identity import Individual
    from cognition.stores.governance import create_governance

    with pytest.raises(RuntimeError, match="abort"):
        with db_session_factory.begin() as session:
            individual = create_identity(session)
            create_governance(session, individual.individual_id)
            raise RuntimeError("abort")
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Individual)) == 0


def test_identity_snapshot_and_revision_conflict(db_session_factory):
    from dataclasses import FrozenInstanceError

    from cognition.stores.errors import RevisionConflict
    from cognition.stores.identity import load_individual, set_operational_status

    with db_session_factory.begin() as session:
        original = create_identity(session)
        with pytest.raises(FrozenInstanceError):
            original.birth_name = "Changed"
        changed = set_operational_status(
            session,
            original.individual_id,
            "paused",
            expected_revision=1,
        )
        assert changed.revision == 2
        with pytest.raises(RevisionConflict):
            set_operational_status(
                session,
                original.individual_id,
                "active",
                expected_revision=1,
            )
        assert load_individual(session, original.individual_id).birth_name == "Ada"


def test_pending_wake_merges_earliest_due_and_context(db_session_factory):
    from cognition.stores.attention import create_or_merge_pending_wake, load_wake

    with db_session_factory.begin() as session:
        individual = create_identity(session)
        first = WakeV1(
            schema_version=1,
            wake_id=uuid4(),
            individual_id=individual.individual_id,
            kind="bootstrap",
            due_at=NOW,
            purpose="First",
            cause_event_id=None,
            context_refs=[],
            coalesce_key="bootstrap",
        )
        wake_id = create_or_merge_pending_wake(session, first)
        second = first.model_copy(
            update={
                "wake_id": uuid4(),
                "due_at": NOW - timedelta(seconds=1),
                "purpose": "Second",
            }
        )
        assert create_or_merge_pending_wake(session, second) == wake_id
        stored = load_wake(session, wake_id)
        assert stored.wake.due_at == second.due_at
        assert stored.wake.purpose == "First\nSecond"
        assert stored.revision == 2


def test_config_revision_same_hash_noop_and_change_history(db_session_factory):
    from pathlib import Path

    from cognition.config.loader import load_config
    from cognition.config.revisions import behavior_config
    from cognition.stores.configuration import (
        get_active_config,
        replace_config_revision,
    )

    config = behavior_config(
        load_config(
            Path(__file__).parents[1] / "fixtures/config/valid.toml",
        )
    )
    with db_session_factory.begin() as session:
        individual = create_identity(session)
        first, changed = replace_config_revision(
            session,
            individual.individual_id,
            config,
            NOW,
        )
        assert changed
        same, changed = replace_config_revision(
            session,
            individual.individual_id,
            config,
            NOW,
        )
        assert not changed
        assert same.config_revision_id == first.config_revision_id
        config.model.max_output_tokens += 1
        second, changed = replace_config_revision(
            session,
            individual.individual_id,
            config,
            NOW + timedelta(seconds=1),
        )
        assert changed
        assert second.content_hash != first.content_hash
        assert get_active_config(session, individual.individual_id) == second


def test_event_append_roundtrip_and_rollback(db_session_factory):
    from cognition.protocols.events_v1 import EventEnvelopeV1
    from cognition.stores.evidence import append_event, load_event

    with db_session_factory.begin() as session:
        individual = create_identity(session)
        envelope = EventEnvelopeV1.model_validate(
            {
                "schema_version": 1,
                "event_id": str(uuid4()),
                "individual_id": str(individual.individual_id),
                "event_type": "test",
                "occurred_at": None,
                "observed_at": NOW,
                "recorded_at": NOW,
                "source": {"kind": "runtime", "source_id": None, "binding_id": uuid4()},
                "actor_entity_id": None,
                "causation_event_id": None,
                "correlation_id": None,
                "subject": None,
                "provenance": {},
                "runtime_version": "test",
                "content": {
                    "content_type": "application/json",
                    "payload": {"hello": "world"},
                    "text": None,
                    "blob_ref": None,
                    "content_hash": None,
                    "sensitivity": "internal",
                    "retention_class": "history",
                    "retain_until": None,
                },
            }
        )
        first = append_event(session, envelope)
        assert first.event_sequence > 0
        assert load_event(session, envelope.event_id).envelope == envelope
        with pytest.raises(RuntimeError), session.begin_nested():
            second = append_event(
                session, envelope.model_copy(update={"event_id": uuid4()})
            )
            raise RuntimeError("abort")
        with pytest.raises(LookupError):
            load_event(session, second.envelope.event_id)


def test_concurrent_wakes_preserve_one_pending_record(db_session_factory):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from cognition.db.models.attention import Wake
    from cognition.protocols.common import Ref
    from cognition.stores.attention import create_or_merge_pending_wake, load_wake

    with db_session_factory.begin() as session:
        individual = create_identity(session)
    barrier = Barrier(2)
    refs = [Ref(kind="goal", id=uuid4()), Ref(kind="goal", id=uuid4())]

    def insert_wake(index):
        with db_session_factory.begin() as session:
            barrier.wait(timeout=10)
            return create_or_merge_pending_wake(
                session,
                WakeV1(
                    schema_version=1,
                    wake_id=uuid4(),
                    individual_id=individual.individual_id,
                    kind="goal_review",
                    due_at=NOW + timedelta(seconds=index),
                    purpose=f"Review {index}",
                    cause_event_id=None,
                    context_refs=[refs[index]],
                    coalesce_key="review",
                ),
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(insert_wake, range(2)))
    assert results[0] == results[1]
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Wake)) == 1
        stored = load_wake(session, results[0])
        assert stored.wake.due_at == NOW
        assert set(ref.id for ref in stored.wake.context_refs) == set(
            ref.id for ref in refs
        )


def test_governance_rejects_stale_revision_across_sessions(db_session_factory):
    from cognition.stores.errors import RevisionConflict
    from cognition.stores.governance import (
        create_governance,
        load_governance,
        update_governance,
    )

    with db_session_factory.begin() as session:
        individual = create_identity(session)
        create_governance(session, individual.individual_id)
    with db_session_factory.begin() as stale_session:
        snapshot = load_governance(stale_session, individual.individual_id)
        with db_session_factory.begin() as current_session:
            update_governance(
                current_session,
                individual.individual_id,
                inference_blocked=True,
                expected_revision=1,
            )
        with pytest.raises(RevisionConflict):
            update_governance(
                stale_session,
                individual.individual_id,
                inference_blocked=False,
                expected_revision=snapshot.revision,
            )
    with db_session_factory() as session:
        assert load_governance(session, individual.individual_id).inference_blocked


def test_audited_redaction_keeps_original_metadata(db_session_factory):
    from cognition.protocols.common import Ref
    from cognition.stores.evidence import (
        append_event,
        load_event,
        record_admin_audit,
        redact_event_content,
    )
    from cognition.stores.governance import create_admin_principal

    with db_session_factory.begin() as session:
        individual = create_identity(session)
        principal = create_admin_principal(
            session, individual.individual_id, authn_provider="test", subject="admin"
        )
        original = append_event(session, event_for(individual.individual_id))
        audit_event = append_event(session, event_for(individual.individual_id))
        audit_id = record_admin_audit(
            session,
            individual_id=individual.individual_id,
            admin_principal_id=principal.admin_principal_id,
            operation="redact",
            target=Ref(kind="event", id=original.envelope.event_id),
            reason="test",
            before_state={"redacted": False},
            after_state={"redacted": True},
            created_at=NOW,
            event_id=audit_event.envelope.event_id,
        )
        with pytest.raises(ValueError, match="targeting"):
            redact_event_content(
                session,
                audit_event.envelope.event_id,
                audit_id=audit_id,
                redacted_at=NOW,
            )
        redact_event_content(
            session, original.envelope.event_id, audit_id=audit_id, redacted_at=NOW
        )
        redacted = load_event(session, original.envelope.event_id)
        assert redacted.envelope.model_dump(exclude={"content"}) == (
            original.envelope.model_dump(exclude={"content"})
        )
        assert redacted.event_sequence == original.event_sequence
        assert redacted.envelope.content.payload is None
        assert redacted.envelope.content.text is None
        assert redacted.envelope.content.blob_ref is None
        assert redacted.envelope.content.content_hash == "hash:original"
        assert redacted.redaction_audit_id == audit_id


def test_returned_json_cannot_mutate_durable_genesis(db_session_factory):
    from cognition.stores.identity import load_individual

    with db_session_factory.begin() as session:
        individual = create_identity(session)
        individual.creator_provenance["source"] = "modified snapshot"
    with db_session_factory() as session:
        stored = load_individual(session, individual.individual_id)
        assert stored.creator_provenance == {"source": "test"}
