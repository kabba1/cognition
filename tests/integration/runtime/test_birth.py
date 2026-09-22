"""Birth is one PostgreSQL transaction, including all evidence and attention."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import event, func, select

from cognition.config.loader import load_config
from cognition.config.revisions import behavior_config, behavior_hash
from cognition.db.base import Base
from cognition.db.models.attention import Wake
from cognition.db.models.audit import AdminAudit
from cognition.db.models.evidence import Event, EventContent
from cognition.db.models.governance import AdminPrincipal, GovernanceState
from cognition.db.models.identity import Individual
from cognition.db.models.runtime import RuntimeConfigRevision, RuntimeInstance
from cognition.protocols.common import new_id
from cognition.runtime.birth import BirthAlreadyExists, BirthInput, birth
from cognition.stores.evidence import load_event
from cognition.stores.identity import load_individual, set_operational_status
from cognition.testing.clock import FakeClock

NOW = datetime(2026, 9, 22, 14, tzinfo=UTC)
BIRTH_TABLES = (
    Individual,
    GovernanceState,
    AdminPrincipal,
    RuntimeConfigRevision,
    Event,
    EventContent,
    Wake,
)


@pytest.fixture
def birth_input():
    config = load_config(
        Path(__file__).parents[2] / "fixtures" / "config" / "valid.toml"
    )
    return BirthInput(
        individual_id=config.runtime.individual_id,
        birth_name="First name",
        founding_orientation="Explore with care.",
        creator_provenance={"kind": "operator", "reference": "local installation"},
        temperament_seed={"initial_caution": "moderate"},
        founding_value_seed={"care": "Avoid preventable harm."},
        admin_authn_provider="local",
        admin_subject="operator",
        config=config,
        runtime_version="0.1.0-test",
        hard_boundaries={"consequential_actions_require_approval": True},
        budget_policy={"daily_model_calls": 20},
    )


class CountingClock(FakeClock):
    def __init__(self):
        super().__init__(NOW)
        self.calls = 0

    def now(self):
        self.calls += 1
        return super().now()


def row_counts(factory):
    with factory() as session:
        return {
            model.__tablename__: session.scalar(select(func.count()).select_from(model))
            for model in (*BIRTH_TABLES, RuntimeInstance, AdminAudit)
        }


def test_birth_creates_exactly_one_thin_genesis_and_bootstrap_atomically(
    db_engine, db_session_factory, birth_input
):
    clock = CountingClock()
    result = birth(db_session_factory, birth_input, clock)
    assert result.individual_id == birth_input.individual_id
    assert result.birth_at == NOW
    assert clock.calls == 1
    counts = row_counts(db_session_factory)
    assert all(counts[model.__tablename__] == 1 for model in BIRTH_TABLES)
    assert counts["runtime_instances"] == counts["admin_audit"] == 0
    with db_session_factory() as session:
        birth_tables = {model.__tablename__ for model in BIRTH_TABLES}
        for table in Base.metadata.tables.values():
            if table.name not in birth_tables:
                assert session.scalar(select(func.count()).select_from(table)) == 0
        person = session.get(Individual, result.individual_id)
        assert person.birth_at == NOW
        assert person.birth_name == birth_input.birth_name
        assert person.founding_orientation == birth_input.founding_orientation
        assert person.temperament_seed == birth_input.temperament_seed
        assert person.founding_value_seed == birth_input.founding_value_seed
        assert person.parent_individual_id is None
        assert person.fork_event_id is None
        assert person.revision == 1
        assert person.operational_status == "active"
        governance = session.get(GovernanceState, result.individual_id)
        assert governance.external_actions_blocked is True
        assert governance.inference_blocked is False
        assert governance.reconciliation_required is False
        assert governance.hard_boundaries == birth_input.hard_boundaries
        assert governance.budget_policy == birth_input.budget_policy
        principal = session.get(AdminPrincipal, result.admin_principal_id)
        assert principal.authn_provider == "local"
        assert principal.subject == "operator"
        assert principal.role == "admin"
        assert principal.revoked_at is None
        revision = session.get(RuntimeConfigRevision, result.config_revision_id)
        assert revision.created_at == revision.activated_at == NOW
        assert revision.superseded_at is None
        assert revision.sanitized_config == behavior_config(
            birth_input.config
        ).model_dump(mode="json")
        assert revision.content_hash == behavior_hash(birth_input.config)
        assert "database" not in revision.sanitized_config
        assert "workspace" not in revision.sanitized_config
        genesis = session.get(Event, result.genesis_event_id)
        assert genesis.event_type == "individual.born"
        assert genesis.source_kind == "runtime"
        assert genesis.occurred_at == genesis.observed_at == genesis.recorded_at == NOW
        assert genesis.subject_kind == "individual"
        assert genesis.subject_id == result.individual_id
        assert genesis.runtime_version == birth_input.runtime_version
        content = session.get(EventContent, result.genesis_event_id)
        assert content.retention_class == "history"
        assert content.payload["birth_name"] == birth_input.birth_name
        assert set(content.payload) == {
            "individual_id",
            "birth_at",
            "birth_name",
            "founding_orientation",
            "creator_provenance",
            "temperament_seed",
            "founding_value_seed",
            "parent_individual_id",
            "fork_event_id",
            "admin_principal_id",
            "config_revision_id",
        }
        wake = session.get(Wake, result.bootstrap_wake_id)
        assert wake.kind == "bootstrap"
        assert wake.status == "pending"
        assert wake.due_at == NOW
        assert wake.cause_event_id == result.genesis_event_id
        assert "first executive attention" in wake.purpose.lower()
        assert {
            "kind": "event",
            "id": str(result.genesis_event_id),
        } in wake.context_refs


class BirthInterrupted(RuntimeError):
    pass


@pytest.mark.parametrize("model", BIRTH_TABLES)
def test_exception_after_each_insert_rolls_back_every_birth_row(
    db_engine, db_session_factory, birth_input, model
):
    seen = []

    def after_flush(session, flush_context):
        if any(isinstance(row, model) for row in session.new):
            seen.append(model.__tablename__)
            raise BirthInterrupted(model.__tablename__)

    def after_cursor_execute(
        connection, cursor, statement, parameters, context, executemany
    ):
        compiled = context.compiled
        table = getattr(getattr(compiled, "statement", None), "table", None)
        if getattr(table, "name", None) == "wakes" and context.isinsert:
            seen.append("wakes")
            raise BirthInterrupted("wakes")

    # ORM after_flush exposes successful inserts without production test hooks.
    # Wake insertion uses Core SQL, so its boundary is the engine cursor event.
    target = db_engine if model is Wake else db_session_factory.class_
    event_name = "after_cursor_execute" if model is Wake else "after_flush"
    listener = after_cursor_execute if model is Wake else after_flush
    event.listen(target, event_name, listener)
    try:
        with pytest.raises(BirthInterrupted, match=model.__tablename__):
            birth(db_session_factory, birth_input, FakeClock(NOW))
    finally:
        event.remove(target, event_name, listener)
    assert seen == [model.__tablename__]
    assert all(count == 0 for count in row_counts(db_session_factory).values())
    birth(db_session_factory, birth_input, FakeClock(NOW))
    assert all(
        row_counts(db_session_factory)[table.__tablename__] == 1
        for table in BIRTH_TABLES
    )


def test_duplicate_birth_fails_cleanly_and_preserves_original_genesis(
    db_session_factory, birth_input
):
    first = birth(db_session_factory, birth_input, FakeClock(NOW))
    birth_input.birth_name = "Attempted replacement"
    with pytest.raises(BirthAlreadyExists, match="already exists"):
        birth(db_session_factory, birth_input, FakeClock(NOW))
    counts = row_counts(db_session_factory)
    assert all(counts[model.__tablename__] == 1 for model in BIRTH_TABLES)
    with db_session_factory() as session:
        assert session.get(Individual, first.individual_id).birth_name == "First name"
        assert (
            session.get(EventContent, first.genesis_event_id).payload["birth_name"]
            == "First name"
        )


@pytest.mark.parametrize("field", ["parent_individual_id", "fork_event_id"])
def test_incomplete_lineage_is_rejected_before_birth(birth_input, field):
    payload = birth_input.model_dump()
    payload[field] = new_id()
    with pytest.raises(ValidationError, match="together"):
        BirthInput.model_validate(payload)


def test_config_identity_mismatch_is_rejected_before_any_write(
    db_session_factory, birth_input
):
    birth_input.config.runtime.individual_id = new_id()
    clock = CountingClock()
    with pytest.raises(ValidationError, match="individual_id"):
        birth(db_session_factory, birth_input, clock)
    assert clock.calls == 0
    assert all(count == 0 for count in row_counts(db_session_factory).values())


def test_valid_fork_lineage_is_preserved_without_copying_parent_state(
    db_session_factory, birth_input
):
    parent = birth(db_session_factory, birth_input, FakeClock(NOW))
    child_id = new_id()
    payload = birth_input.model_dump()
    payload["individual_id"] = child_id
    payload["config"]["runtime"]["individual_id"] = child_id
    payload["parent_individual_id"] = parent.individual_id
    payload["fork_event_id"] = parent.genesis_event_id
    child = birth(
        db_session_factory, BirthInput.model_validate(payload), FakeClock(NOW)
    )
    with db_session_factory() as session:
        row = session.get(Individual, child.individual_id)
        assert row.parent_individual_id == parent.individual_id
        assert row.fork_event_id == parent.genesis_event_id
        assert child.genesis_event_id != parent.genesis_event_id
        assert (
            session.get(GovernanceState, child.individual_id).external_actions_blocked
            is True
        )


def test_supported_store_updates_do_not_rewrite_genesis(
    db_session_factory, birth_input
):
    created = birth(db_session_factory, birth_input, FakeClock(NOW))
    with db_session_factory.begin() as session:
        identity = load_individual(session, created.individual_id)
        genesis = load_event(session, created.genesis_event_id)
        original = genesis.envelope.model_dump(mode="json")
        identity.creator_provenance["kind"] = "changed detached snapshot"
        genesis.envelope.content.payload["birth_name"] = "changed detached snapshot"
        set_operational_status(
            session, created.individual_id, "paused", expected_revision=1
        )
    with db_session_factory() as session:
        identity = load_individual(session, created.individual_id)
        assert identity.operational_status == "paused"
        assert identity.birth_name == birth_input.birth_name
        assert identity.creator_provenance == birth_input.creator_provenance
        assert (
            load_event(session, created.genesis_event_id).envelope.model_dump(
                mode="json"
            )
            == original
        )


def test_self_parent_lineage_is_rejected(birth_input):
    payload = birth_input.model_dump()
    payload["parent_individual_id"] = birth_input.individual_id
    payload["fork_event_id"] = new_id()
    with pytest.raises(ValidationError, match="own parent"):
        BirthInput.model_validate(payload)


def test_naive_clock_is_rejected_before_any_write(db_session_factory, birth_input):
    class NaiveClock:
        def now(self):
            return datetime(2026, 9, 22)

    with pytest.raises(ValueError, match="timezone-aware"):
        birth(db_session_factory, birth_input, NaiveClock())
    assert all(count == 0 for count in row_counts(db_session_factory).values())
