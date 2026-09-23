"""Inbound evidence keeps immutable grouping through claims and recovery."""

import importlib
from datetime import timedelta

import pytest
from sqlalchemy import delete, select
from test_autonomy import NOW
from test_autonomy import person as person

from cognition.db.models import Event, Wake
from cognition.db.models.cognition import CycleWake
from cognition.db.models.evidence import EventContent
from cognition.db.models.perception import (
    ConnectorBinding,
    InboundWake,
    IngestionReceipt,
    Observation,
)
from cognition.protocols.common import new_id
from cognition.stores.cognition import CycleLimits, claim_or_resume, context_sources


def scope():
    return importlib.import_module("cognition.stores.inbound_scope")


def event(session, person, kind, now=NOW, **changes):
    row = Event(
        event_id=new_id(),
        individual_id=person,
        event_type=kind,
        occurred_at=now,
        observed_at=now,
        recorded_at=now,
        source_kind="runtime",
        source_id="perception",
        provenance={},
        runtime_version="test",
    )
    for key, value in changes.items():
        setattr(row, key, value)
    session.add(row)
    session.flush()
    session.add(
        EventContent(
            event_id=row.event_id,
            content_type="application/json",
            payload={},
            sensitivity="internal",
            retention_class="history",
        )
    )
    session.flush()
    return row


def inbound(session, person, count=1, now=NOW):
    binding = ConnectorBinding(
        individual_id=person,
        adapter_id="local_json_v1",
        source_id=str(new_id()),
        enabled=True,
        created_at=now,
        updated_at=now,
    )
    session.add(binding)
    session.flush()
    wake_id = new_id()
    marker = event(
        session,
        person,
        "perception.inbound_wake_created",
        now,
        subject_kind="wake",
        subject_id=wake_id,
        source_binding_id=binding.connector_binding_id,
        occurred_at=None,
    )
    wake = Wake(
        wake_id=wake_id,
        individual_id=person,
        kind="external_event",
        status="pending",
        due_at=now,
        purpose="Attend to incoming observations.",
        cause_event_id=marker.event_id,
        context_refs=[],
    )
    session.add(wake)
    session.flush()
    metadata = InboundWake(
        wake_id=wake_id,
        individual_id=person,
        connector_binding_id=binding.connector_binding_id,
        creation_event_id=marker.event_id,
        created_at=now,
        sealed_at=now if count == 8 else None,
    )
    session.add(metadata)
    session.flush()
    receipt_event = event(session, person, "perception.page_ingested", now)
    receipt = IngestionReceipt(
        event_id=receipt_event.event_id,
        individual_id=person,
        connector_binding_id=binding.connector_binding_id,
        before_cursor=None,
        after_cursor="next",
        before_cursor_revision=0,
        after_cursor_revision=1,
        authorizing_binding_revision=1,
        observed_at=now,
        recorded_at=now,
        item_count=count,
        new_count=count,
        duplicate_count=0,
        source_page_hash="a" * 64,
        receipt_hash="b" * 64,
    )
    session.add(receipt)
    session.flush()
    ids = []
    for index in range(count):
        item_event = event(
            session,
            person,
            "observation.received",
            now,
            source_kind="connector",
            source_id="local_json_v1",
            source_binding_id=binding.connector_binding_id,
        )
        ids.append(item_event.event_id)
        session.add(
            Observation(
                event_id=item_event.event_id,
                individual_id=person,
                connector_binding_id=binding.connector_binding_id,
                external_event_id=str(index),
                dedup_key=f"{index:064x}",
                content_fingerprint="c" * 64,
                external_content_type="application/json",
                authentication={},
                inbound_wake_id=wake_id,
                receipt_event_id=receipt.event_id,
            )
        )
    wake.context_refs = [{"kind": "event", "id": str(identity)} for identity in ids]
    binding.pending_wake_id = wake_id if count < 8 else None
    session.flush()
    return binding, wake, metadata, tuple(ids)


def remove_bootstrap(session, person):
    wake = session.scalar(
        select(Wake).where(Wake.individual_id == person, Wake.kind == "bootstrap")
    )
    wake.status = "cancelled"
    session.flush()


def test_scope_keeps_exact_order_and_core_reads_ignore_dirty_cached_rows(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        binding, wake, metadata, ids = inbound(session, person, 3)
        wake_id = wake.wake_id
    with db_session_factory() as session:
        dirty = session.get(Wake, wake_id)
        dirty.context_refs = []
        found = scope().validate_inbound_wake(session, person, wake_id)
        assert found.member_event_ids == ids
        assert found.sealed_at is None and found.status == "pending"
        assert dirty.context_refs == [] and dirty in session.dirty


@pytest.mark.parametrize(
    "corrupt",
    [
        "kind",
        "purpose",
        "refs",
        "due",
        "pointer",
        "marker",
        "event_source",
        "receipt_owner",
        "full_unsealed",
    ],
)
def test_corruption_is_never_an_ordinary_wake(db_session_factory, person, corrupt):
    from test_integrity_checks import create_healthy

    other = create_healthy(db_session_factory).individual_id
    with db_session_factory.begin() as session:
        binding, wake, metadata, ids = inbound(
            session, person, 8 if corrupt == "full_unsealed" else 1
        )
        wake_id = wake.wake_id
        if corrupt == "kind":
            wake.kind = "routine"
        elif corrupt == "purpose":
            wake.purpose = "untrusted purpose"
        elif corrupt == "refs":
            wake.context_refs = []
        elif corrupt == "due":
            wake.due_at += timedelta(seconds=1)
        elif corrupt == "pointer":
            binding.pending_wake_id = None
        elif corrupt == "marker":
            session.get(Event, metadata.creation_event_id).source_kind = "connector"
        elif corrupt == "event_source":
            session.get(Event, ids[0]).actor_entity_id = new_id()
        elif corrupt == "receipt_owner":
            session.get(
                IngestionReceipt, session.get(Observation, ids[0]).receipt_event_id
            ).individual_id = other
        else:
            metadata.sealed_at = None
            binding.pending_wake_id = wake_id
    with db_session_factory() as session:
        with pytest.raises(ValueError):
            scope().validate_inbound_wake(session, person, wake_id)


def test_marker_alone_preserves_managed_classification(db_session_factory, person):
    with db_session_factory.begin() as session:
        binding, wake, _, ids = inbound(session, person)
        wake_id = wake.wake_id
        binding.pending_wake_id = None
        session.flush()
        session.execute(delete(Observation).where(Observation.event_id.in_(ids)))
        session.execute(delete(InboundWake).where(InboundWake.wake_id == wake_id))
    with db_session_factory() as session:
        assert (
            session.scalar(
                select(Wake.wake_id).where(
                    Wake.wake_id == wake_id, scope().inbound_wake_predicate()
                )
            )
            == wake_id
        )
        with pytest.raises(ValueError):
            scope().validate_inbound_wake(session, person, wake_id)


def test_seal_is_idempotent_and_rollback_preserves_group(db_session_factory, person):
    with db_session_factory.begin() as session:
        binding, wake, _, _ = inbound(session, person)
        binding_id, wake_id = binding.connector_binding_id, wake.wake_id
    with db_session_factory() as session:
        scope().seal_inbound_wake(session, person, wake_id, NOW + timedelta(seconds=1))
        session.rollback()
    with db_session_factory.begin() as session:
        assert scope().validate_inbound_wake(session, person, wake_id).sealed_at is None
        scope().seal_inbound_wake(session, person, wake_id, NOW + timedelta(seconds=1))
        first_revision = session.get(ConnectorBinding, binding_id).revision
        result = scope().seal_inbound_wake(
            session, person, wake_id, NOW + timedelta(seconds=2)
        )
        assert result.sealed_at == NOW + timedelta(seconds=1)
        assert session.get(ConnectorBinding, binding_id).revision == first_revision
        assert session.get(ConnectorBinding, binding_id).pending_wake_id is None


def test_earliest_inbound_claims_alone_with_original_limits(db_session_factory, person):
    with db_session_factory.begin() as session:
        remove_bootstrap(session, person)
        _, first, _, _ = inbound(session, person)
        _, later, _, _ = inbound(session, person, now=NOW + timedelta(seconds=1))
        cycle = claim_or_resume(
            session, person, NOW + timedelta(seconds=2), CycleLimits()
        )
        assert session.scalars(
            select(CycleWake.wake_id).where(CycleWake.cycle_id == cycle.cycle_id)
        ).all() == [first.wake_id]
        assert cycle.max_turns == 3 and cycle.max_wakes == 16
        assert session.get(Wake, later.wake_id).status == "pending"
        assert (
            scope().get_cycle_inbound(session, cycle.cycle_id).wake_id == first.wake_id
        )


def test_ordinary_batch_excludes_later_inbound(db_session_factory, person):
    with db_session_factory.begin() as session:
        _, incoming, _, _ = inbound(session, person, now=NOW + timedelta(seconds=1))
        cycle = claim_or_resume(
            session, person, NOW + timedelta(seconds=2), CycleLimits()
        )
        ids = session.scalars(
            select(CycleWake.wake_id).where(CycleWake.cycle_id == cycle.cycle_id)
        ).all()
        assert incoming.wake_id not in ids and len(ids) == 1
        assert scope().get_cycle_inbound(session, cycle.cycle_id) is None


def test_claimed_inbound_orphan_fails_before_generic_replacement(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        _, wake, metadata, _ = inbound(session, person)
        wake_id = wake.wake_id
        scope().seal_inbound_wake(session, person, wake_id, NOW)
        wake.status, wake.claimed_at = "claimed", NOW
    with db_session_factory.begin() as session:
        with pytest.raises(ValueError):
            claim_or_resume(session, person, NOW, CycleLimits())
        assert session.get(Wake, wake_id).status == "claimed"


def test_recovered_or_loaded_cycle_rejects_extra_wake_and_foreign_owner(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        remove_bootstrap(session, person)
        _, incoming, _, _ = inbound(session, person)
        cycle = claim_or_resume(session, person, NOW, CycleLimits())
        ordinary = session.scalar(
            select(Wake).where(Wake.individual_id == person, Wake.kind == "bootstrap")
        )
        session.add(CycleWake(cycle_id=cycle.cycle_id, wake_id=ordinary.wake_id))
        session.flush()
        with pytest.raises(ValueError):
            context_sources(session, cycle)
        with pytest.raises(ValueError):
            claim_or_resume(session, person, NOW, CycleLimits())


@pytest.mark.parametrize("inbound_first", [False, True])
def test_locked_earliest_wake_prevents_cross_class_overtaking(
    db_session_factory, person, inbound_first
):
    with db_session_factory.begin() as session:
        bootstrap = session.scalar(
            select(Wake).where(Wake.individual_id == person, Wake.kind == "bootstrap")
        )
        _, incoming, _, _ = inbound(session, person, now=NOW + timedelta(seconds=1))
        if inbound_first:
            bootstrap.due_at = NOW + timedelta(seconds=2)
        first_id = incoming.wake_id if inbound_first else bootstrap.wake_id
    with db_session_factory() as locker, db_session_factory() as claimant:
        locker.execute(
            select(Wake.wake_id).where(Wake.wake_id == first_id).with_for_update()
        ).one()
        assert (
            claim_or_resume(claimant, person, NOW + timedelta(seconds=3), CycleLimits())
            is None
        )
        assert claimant.scalar(select(CycleWake.wake_id).limit(1)) is None
        locker.rollback()
        cycle = claim_or_resume(
            claimant, person, NOW + timedelta(seconds=3), CycleLimits()
        )
        assert cycle is not None
        assert (
            claimant.scalar(
                select(CycleWake.wake_id).where(CycleWake.cycle_id == cycle.cycle_id)
            )
            == first_id
        )


def test_foreign_wake_owner_cannot_hide_owned_inbound_claim(db_session_factory, person):
    from test_integrity_checks import create_healthy

    other = create_healthy(db_session_factory).individual_id
    with db_session_factory.begin() as session:
        _, wake, _, _ = inbound(session, person)
        wake.individual_id = other
    with db_session_factory() as session:
        with pytest.raises(ValueError):
            claim_or_resume(session, person, NOW, CycleLimits())


def test_ninth_member_is_rejected(db_session_factory, person):
    with db_session_factory.begin() as session:
        _, wake, _, _ = inbound(session, person, count=9)
        with pytest.raises(ValueError):
            scope().validate_inbound_wake(session, person, wake.wake_id)


def test_foreign_pointer_cannot_claim_an_owned_group(db_session_factory, person):
    from test_integrity_checks import create_healthy

    other = create_healthy(db_session_factory).individual_id
    with db_session_factory.begin() as session:
        _, wake, _, _ = inbound(session, person)
        session.add(
            ConnectorBinding(
                individual_id=other,
                adapter_id="local_json_v1",
                source_id="foreign",
                created_at=NOW,
                updated_at=NOW,
                pending_wake_id=wake.wake_id,
            )
        )
        session.flush()
        with pytest.raises(ValueError):
            scope().validate_inbound_wake(session, person, wake.wake_id)


def test_sealing_rejects_unflushed_mutation_without_erasing_it(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        _, wake, _, _ = inbound(session, person)
        wake_id = wake.wake_id
    with db_session_factory() as session:
        dirty = session.get(Wake, wake_id)
        dirty.context_refs = []
        with pytest.raises(ValueError, match="unflushed_inbound_state"):
            scope().seal_inbound_wake(session, person, wake_id, NOW)
        assert dirty in session.dirty and dirty.context_refs == []


def test_sealing_rejects_unrelated_unflushed_content(db_session_factory, person):
    with db_session_factory.begin() as session:
        _, wake, _, ids = inbound(session, person)
        wake_id = wake.wake_id
    with db_session_factory() as session:
        dirty = session.get(EventContent, ids[0])
        dirty.payload = {"unrelated": "pending edit"}
        with pytest.raises(ValueError, match="unflushed_inbound_state"):
            scope().seal_inbound_wake(session, person, wake_id, NOW)
        assert dirty in session.dirty and dirty.payload == {"unrelated": "pending edit"}


def test_consumed_group_retains_valid_scope_after_content_redaction(
    db_session_factory, person
):
    from cognition.stores.cognition import finish_cycle

    with db_session_factory.begin() as session:
        remove_bootstrap(session, person)
        _, wake, metadata, ids = inbound(session, person, count=8)
        cycle = claim_or_resume(session, person, NOW, CycleLimits())
        finish_cycle(session, cycle.cycle_id, NOW + timedelta(seconds=1), "sleep")
        for identity in (metadata.creation_event_id, *ids):
            session.get(EventContent, identity).payload = None
        session.flush()
        retained = scope().get_cycle_inbound(session, cycle.cycle_id)
        assert retained.status == "consumed" and retained.member_event_ids == ids


def test_full_sealed_group_cannot_be_claimed_before_sealing_time(
    db_session_factory, person
):
    with db_session_factory.begin() as session:
        _, wake, metadata, _ = inbound(session, person, count=8)
        metadata.sealed_at = NOW + timedelta(seconds=10)
        session.flush()
        with pytest.raises(ValueError, match="timing"):
            scope().seal_inbound_wake(session, person, wake.wake_id, NOW)
