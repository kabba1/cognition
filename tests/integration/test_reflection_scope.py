"""Managed deliberation retains exact scope without changing generic reflection."""

import importlib
from copy import deepcopy
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import delete, select
from test_development_stores import (
    DAY,
    NOW,
    attention,
    create,
    errors,
    observation,
    transition,
)
from test_integrity_checks import create_healthy
from test_personal_stores import state as state

from cognition.db.models.attention import Wake
from cognition.db.models.cognition import CycleWake
from cognition.db.models.development import Interest
from cognition.db.models.evidence import Event, EventContent
from cognition.protocols.common import Ref, new_id
from cognition.protocols.events_v1 import EventEnvelopeV1
from cognition.stores.development import _reflection
from cognition.stores.evidence import append_event


def scope_store():
    return importlib.import_module("cognition.stores.reflection_scope")


def reflection_models():
    return importlib.import_module("cognition.db.models.reflection")


def metadata(identity, kind="interest"):
    return dict(
        kind=kind, id=str(identity), revision=1, eligible_at=(NOW + DAY).isoformat()
    )


def batch_fields():
    return dict(
        individual_id=UUID(int=1),
        wake_id=UUID(int=2),
        policy_version=1,
        selected_at=NOW,
        target_metadata=[metadata(UUID(int=3))],
    )


def test_hash_binds_ordered_scope_and_each_batch_identity():
    store = scope_store()
    original = batch_fields()
    digest = store.reflection_batch_hash(**original)
    assert store.validate_reflection_batch_snapshot(
        **original, content_hash=digest
    ) == (Ref(kind="interest", id=UUID(int=3)),)
    for field, value in (
        ("individual_id", UUID(int=4)),
        ("wake_id", UUID(int=4)),
        ("selected_at", NOW + timedelta(seconds=1)),
        ("target_metadata", [metadata(UUID(int=4))]),
    ):
        with pytest.raises(ValueError):
            store.validate_reflection_batch_snapshot(
                **{**original, field: value}, content_hash=digest
            )
    pair = [metadata(UUID(int=3)), metadata(UUID(int=4), "preference")]
    assert store.reflection_batch_hash(**{**original, "target_metadata": pair}) != (
        store.reflection_batch_hash(**{**original, "target_metadata": pair[::-1]})
    )


@pytest.mark.parametrize(
    "change",
    [
        {"revision": True},
        {"revision": 1.0},
        {"revision": 0},
        {"kind": "goal"},
        {"eligible_at": "2026-09-22T10:00:00"},
        {"eligible_at": 1},
        {"extra": "forbidden"},
    ],
)
def test_metadata_rejects_coercions_unknown_fields_and_nonreflected_families(change):
    fields = batch_fields()
    fields["target_metadata"][0].update(change)
    with pytest.raises(ValueError):
        scope_store().reflection_batch_hash(**fields)


@pytest.mark.parametrize("case", ["empty", "duplicate", "nine", "boolean_policy"])
def test_batch_scope_has_strict_version_and_one_to_eight_distinct_targets(case):
    fields = batch_fields()
    if case == "empty":
        fields["target_metadata"] = []
    elif case == "duplicate":
        fields["target_metadata"] *= 2
    elif case == "nine":
        fields["target_metadata"] = [
            metadata(UUID(int=index + 10)) for index in range(9)
        ]
    else:
        fields["policy_version"] = True
    with pytest.raises(ValueError):
        scope_store().reflection_batch_hash(**fields)


def managed_batch(factory, state, target_ids):
    with factory.begin() as session:
        wake_id = session.scalar(
            select(CycleWake.wake_id).where(CycleWake.cycle_id == state[1].cycle_id)
        )
        wake = session.get(Wake, wake_id)
        wake.kind, wake.coalesce_key = "reflection", None
        wake.context_refs = [
            Ref(kind="interest", id=identity).model_dump(mode="json")
            for identity in target_ids
        ]
        marker_id = new_id()
        append_event(
            session,
            EventEnvelopeV1.model_validate(
                dict(
                    schema_version=1,
                    event_id=marker_id,
                    individual_id=state[0].individual_id,
                    event_type="attention.reflection_scheduled",
                    occurred_at=NOW,
                    observed_at=NOW,
                    recorded_at=NOW,
                    source=dict(
                        kind="runtime",
                        source_id="attention.reflection",
                        binding_id=None,
                    ),
                    actor_entity_id=None,
                    causation_event_id=None,
                    correlation_id=None,
                    subject=dict(kind="wake", id=wake_id),
                    provenance={},
                    content=dict(
                        content_type="application/json",
                        payload={},
                        text=None,
                        blob_ref=None,
                        content_hash=None,
                        sensitivity="internal",
                        retention_class="history",
                        retain_until=None,
                    ),
                    runtime_version="test",
                )
            ),
        )
        wake.cause_event_id = marker_id
        fields = dict(
            individual_id=state[0].individual_id,
            wake_id=wake_id,
            policy_version=1,
            selected_at=NOW,
            target_metadata=[metadata(identity) for identity in target_ids],
        )
        session.add(
            reflection_models().ManagedReflectionBatch(
                **fields, content_hash=scope_store().reflection_batch_hash(**fields)
            )
        )
        return wake_id, marker_id


def read_scope(factory, state, wake_id):
    with factory() as session:
        return scope_store().managed_reflection_scope(
            session, state[0].individual_id, wake_id
        )


def test_generic_reflection_and_exact_self_scheduling_keep_existing_scope(
    db_session_factory, state
):
    target = UUID(create(db_session_factory, state, "interest"))
    attention(db_session_factory, state)
    with db_session_factory() as session:
        wake_id = session.scalar(select(CycleWake.wake_id))
        assert (
            scope_store().managed_reflection_scope(
                session, state[0].individual_id, wake_id
            )
            is None
        )
        assert _reflection(
            session, state[0].individual_id, state[1].cycle_id, "interest", target
        )
    attention(
        db_session_factory,
        state,
        "self_scheduled",
        [dict(kind="interest", id=str(target))],
    )
    with db_session_factory() as session:
        assert _reflection(
            session, state[0].individual_id, state[1].cycle_id, "interest", target
        )
        assert not _reflection(
            session, state[0].individual_id, state[1].cycle_id, "interest", new_id()
        )


def test_managed_scope_permits_only_its_targets_and_does_not_supply_grounding(
    db_session_factory, state
):
    first = observation(db_session_factory, state, NOW)
    second = observation(db_session_factory, state, NOW + DAY)
    target = UUID(create(db_session_factory, state, "interest", [first]))
    unrelated = UUID(create(db_session_factory, state, "interest", [first]))
    wake_id, _ = managed_batch(db_session_factory, state, [target])
    assert read_scope(db_session_factory, state, wake_id) == (
        Ref(kind="interest", id=target),
    )
    grounded = transition(state, "interest", str(target), "establish", [second])
    assert errors(db_session_factory, state, grounded, NOW + DAY) == ()
    ungrounded = transition(state, "interest", str(target), "establish")
    assert "development_grounding_required" in errors(
        db_session_factory, state, ungrounded, NOW + DAY
    )
    other = transition(state, "interest", str(unrelated), "establish", [second])
    assert "development_reflection_required" in errors(
        db_session_factory, state, other, NOW + DAY
    )


@pytest.mark.parametrize(
    "corruption",
    [
        "missing_batch",
        "hash",
        "wake_refs",
        "wrong_marker_binding",
        "wrong_marker_source",
    ],
)
def test_managed_metadata_corruption_never_becomes_generic_reflection(
    db_session_factory, state, corruption
):
    target = UUID(create(db_session_factory, state, "interest"))
    wake_id, marker_id = managed_batch(db_session_factory, state, [target])
    with db_session_factory.begin() as session:
        batch = session.get(reflection_models().ManagedReflectionBatch, wake_id)
        if corruption == "missing_batch":
            session.delete(batch)
        elif corruption == "hash":
            batch.content_hash = "0" * 64
        elif corruption == "wake_refs":
            session.get(Wake, wake_id).context_refs = []
        elif corruption == "wrong_marker_binding":
            session.get(Event, marker_id).subject_id = new_id()
        else:
            # Existing owned marker must agree with the wake's requested owner.
            session.get(Event, marker_id).source_kind = "connector"
    with pytest.raises(ValueError):
        read_scope(db_session_factory, state, wake_id)


def test_historical_scope_survives_payload_redaction_and_changed_target_revision(
    db_session_factory, state
):
    target = UUID(create(db_session_factory, state, "interest"))
    wake_id, marker_id = managed_batch(db_session_factory, state, [target])
    with db_session_factory.begin() as session:
        session.get(EventContent, marker_id).payload = None
        session.get(EventContent, marker_id).redacted_at = NOW + DAY
        session.get(Interest, target).revision = 9
        session.get(Interest, target).status = "established"
        session.get(Wake, wake_id).status = "consumed"
    assert read_scope(db_session_factory, state, wake_id) == (
        Ref(kind="interest", id=target),
    )
    with db_session_factory.begin() as session:
        session.execute(
            delete(reflection_models().ManagedReflectionBatch).where(
                reflection_models().ManagedReflectionBatch.wake_id == wake_id
            )
        )
    with pytest.raises(ValueError):
        read_scope(db_session_factory, state, wake_id)


def test_dirty_orm_values_are_neither_used_nor_refreshed_by_scope_lookup(
    db_session_factory, state
):
    target = UUID(create(db_session_factory, state, "interest"))
    wake_id, _ = managed_batch(db_session_factory, state, [target])
    with db_session_factory() as session:
        wake = session.get(Wake, wake_id)
        batch = session.get(reflection_models().ManagedReflectionBatch, wake_id)
        wake.context_refs = []
        batch.target_metadata = []
        before = deepcopy((wake.context_refs, batch.target_metadata))
        assert scope_store().managed_reflection_scope(
            session, state[0].individual_id, wake_id
        ) == (Ref(kind="interest", id=target),)
        assert (wake.context_refs, batch.target_metadata) == before
        assert wake in session.dirty and batch in session.dirty


@pytest.mark.parametrize("corrupt", [False, True])
def test_mixed_generic_wake_keeps_broad_scope_but_cannot_hide_invalid_managed_scope(
    db_session_factory, state, corrupt
):
    target = UUID(create(db_session_factory, state, "interest"))
    wake_id, _ = managed_batch(db_session_factory, state, [target])
    with db_session_factory.begin() as session:
        if corrupt:
            session.get(
                reflection_models().ManagedReflectionBatch, wake_id
            ).content_hash = "0" * 64
        generic_id = UUID(int=1)
        session.add(
            Wake(
                wake_id=generic_id,
                individual_id=state[0].individual_id,
                kind="reflection",
                status="claimed",
                due_at=NOW,
                purpose="Ordinary reflection",
                cause_event_id=None,
                context_refs=[],
                coalesce_key=None,
                claimed_at=NOW,
                revision=1,
            )
        )
        session.flush()
        session.add(CycleWake(cycle_id=state[1].cycle_id, wake_id=generic_id))
    with db_session_factory() as session:
        if corrupt:
            with pytest.raises(ValueError):
                _reflection(
                    session,
                    state[0].individual_id,
                    state[1].cycle_id,
                    "interest",
                    new_id(),
                )
        else:
            assert _reflection(
                session, state[0].individual_id, state[1].cycle_id, "interest", new_id()
            )


@pytest.mark.parametrize("location", ["batch", "marker", "target"])
def test_foreign_ownership_is_rejected_at_each_scope_link(
    db_session_factory, state, location
):
    target = UUID(create(db_session_factory, state, "interest"))
    wake_id, marker_id = managed_batch(db_session_factory, state, [target])
    foreign = create_healthy(db_session_factory)
    with db_session_factory.begin() as session:
        model, identity = {
            "batch": (reflection_models().ManagedReflectionBatch, wake_id),
            "marker": (Event, marker_id),
            "target": (Interest, target),
        }[location]
        session.get(model, identity).individual_id = foreign.individual_id
    with pytest.raises(ValueError):
        read_scope(db_session_factory, state, wake_id)


def test_current_pointer_prevents_generic_fallback_when_scope_and_marker_are_missing(
    db_session_factory, state
):
    attention(db_session_factory, state)
    with db_session_factory.begin() as session:
        wake_id = session.scalar(select(CycleWake.wake_id))
        session.add(
            reflection_models().ReflectionState(
                individual_id=state[0].individual_id,
                policy_version=1,
                next_review_at=NOW,
                managed_wake_id=wake_id,
                last_completed_cycle_id=None,
                interest_cursor=None,
                preference_cursor=None,
                self_state_cursor=None,
                materialization_pending=False,
                revision=1,
            )
        )
    with pytest.raises(ValueError):
        read_scope(db_session_factory, state, wake_id)


@pytest.mark.parametrize("kind", ["self_scheduled", "heartbeat"])
def test_changing_managed_wake_kind_cannot_bypass_development_scope_validation(
    db_session_factory, state, kind
):
    target = UUID(create(db_session_factory, state, "interest"))
    wake_id, _ = managed_batch(db_session_factory, state, [target])
    with db_session_factory.begin() as session:
        session.get(Wake, wake_id).kind = kind
    with db_session_factory() as session, pytest.raises(ValueError):
        _reflection(
            session, state[0].individual_id, state[1].cycle_id, "interest", target
        )
