"""Real PostgreSQL constraints for identity, governance, runtime, and wakes."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from alembic import command
from sqlalchemy import delete, inspect, select, text
from sqlalchemy.exc import IntegrityError

from cognition.db.models import (
    AdminPrincipal,
    GovernanceState,
    Individual,
    RuntimeConfigRevision,
    RuntimeInstance,
    Wake,
)
from cognition.protocols.common import new_id

NOW = datetime(2026, 9, 22, 12, tzinfo=UTC)


@pytest.fixture
def session(db_session_factory):
    with db_session_factory() as session, session.begin():
        yield session


def individual(session, **changes):
    values = dict(
        birth_at=NOW,
        birth_name="Test individual",
        founding_orientation="Explore",
        creator_provenance={"source": "integration-test"},
        operational_status="active",
    )
    values.update(changes)
    row = Individual(**values)
    session.add(row)
    session.flush()
    return row


def dependent(model, individual_id, **changes):
    values = {
        GovernanceState: dict(hard_boundaries={}, budget_policy={}),
        AdminPrincipal: dict(
            authn_provider="local",
            subject="operator",
            role="admin",
            principal_metadata={},
        ),
        RuntimeConfigRevision: dict(
            config_schema_version=1,
            sanitized_config={},
            content_hash="a" * 64,
            created_at=NOW,
        ),
        RuntimeInstance: dict(
            status="starting",
            host_id="test-host",
            process_id=123,
            started_at=NOW,
            last_heartbeat_at=NOW,
            runtime_version="0.1.0",
        ),
        Wake: dict(
            kind="bootstrap",
            status="pending",
            due_at=NOW,
            purpose="Begin",
            context_refs=[],
        ),
    }[model]
    values.update(individual_id=individual_id, **changes)
    return model(**values)


def rejected(session, row, constraint):
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.add(row)
        session.flush()
    assert error.value.orig.diag.constraint_name == constraint


def test_identity_migration_upgrades_from_foundation_and_downgrades_explicitly(
    db_engine, alembic_config
):
    command.downgrade(alembic_config, "0001_foundation")
    assert "individuals" not in inspect(db_engine).get_table_names()
    command.upgrade(alembic_config, "0002_identity")
    tables = set(inspect(db_engine).get_table_names())
    assert {
        "individuals",
        "governance_state",
        "admin_principals",
        "runtime_config_revisions",
        "runtime_instances",
        "wakes",
    } <= tables
    with db_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == "0002_identity"
        )
    command.downgrade(alembic_config, "0001_foundation")
    assert "wakes" not in inspect(db_engine).get_table_names()


def test_migration_creates_all_tables_types_constraints_and_indexes(db_engine):
    schema = inspect(db_engine)
    tables = {
        "individuals",
        "governance_state",
        "admin_principals",
        "runtime_config_revisions",
        "runtime_instances",
        "wakes",
    }
    assert tables <= set(schema.get_table_names())
    for table in tables:
        assert schema.get_pk_constraint(table)["name"] == f"pk_{table}"
        for constraint in schema.get_check_constraints(table):
            assert constraint["name"].startswith(f"ck_{table}_")
        for foreign_key in schema.get_foreign_keys(table):
            assert foreign_key["name"].startswith(f"fk_{table}_")
            assert foreign_key["options"].get("ondelete") in (None, "NO ACTION")
        for column in schema.get_columns(table):
            if column["name"].endswith("_at"):
                assert column["type"].timezone is True
            if column["name"] == "revision":
                assert str(column["type"]) == "BIGINT"
            if column["name"] in ("status", "operational_status", "kind", "role"):
                assert str(column["type"]) == "TEXT"
    indexes = {index["name"]: index for index in schema.get_indexes("wakes")}
    due = indexes["ix_wakes_pending_due"]
    assert due["column_names"] == ["individual_id", "due_at"]
    assert "pending" in due["dialect_options"]["postgresql_where"]
    coalesce = indexes["uq_wakes_pending_coalesce"]
    assert coalesce["unique"] is True
    assert coalesce["column_names"] == ["individual_id", "coalesce_key"]
    assert "IS NOT NULL" in coalesce["dialect_options"]["postgresql_where"]


def test_application_owned_uuid_defaults_utc_instants_and_safe_governance_defaults(
    session,
):
    birth_at = datetime(2026, 9, 22, 7, tzinfo=timezone(timedelta(hours=-5)))
    person = individual(session, birth_at=birth_at)
    governance = dependent(GovernanceState, person.individual_id)
    session.add(governance)
    session.flush()
    session.expire_all()
    assert person.individual_id.version == 4
    assert person.birth_at == NOW
    assert person.birth_at.utcoffset() == timedelta(0)
    assert person.revision == governance.revision == 1
    assert governance.external_actions_blocked is True
    assert governance.inference_blocked is False
    assert governance.reconciliation_required is False


@pytest.mark.parametrize(
    "status", ["active", "paused", "quiescing", "quiescent", "retired"]
)
def test_individual_lifecycle_states(session, status):
    assert individual(session, operational_status=status).operational_status == status


@pytest.mark.parametrize(
    "status", ["starting", "running", "stopping", "stopped", "crashed"]
)
def test_runtime_states(session, status):
    person = individual(session)
    row = dependent(RuntimeInstance, person.individual_id, status=status)
    session.add(row)
    session.flush()
    assert row.runtime_instance_id.version == 4


@pytest.mark.parametrize(
    "kind",
    [
        "bootstrap",
        "external_event",
        "action_result",
        "commitment_due",
        "self_scheduled",
        "goal_review",
        "routine",
        "reflection",
        "maintenance",
        "heartbeat",
        "recovery",
    ],
)
def test_exact_wake_kinds(session, kind):
    row = dependent(Wake, individual(session).individual_id, kind=kind)
    session.add(row)
    session.flush()
    assert row.kind == kind


@pytest.mark.parametrize(
    "status", ["pending", "claimed", "consumed", "cancelled", "superseded"]
)
def test_exact_wake_states(session, status):
    row = dependent(Wake, individual(session).individual_id, status=status)
    session.add(row)
    session.flush()
    assert row.status == status


@pytest.mark.parametrize(
    ("model", "field", "value", "constraint"),
    [
        (
            Individual,
            "operational_status",
            "running",
            "ck_individuals_operational_status",
        ),
        (Individual, "revision", 0, "ck_individuals_revision_positive"),
        (GovernanceState, "revision", -1, "ck_governance_state_revision_positive"),
        (RuntimeInstance, "status", "active", "ck_runtime_instances_status"),
        (
            RuntimeConfigRevision,
            "config_schema_version",
            2,
            "ck_runtime_config_revisions_schema_version",
        ),
        (AdminPrincipal, "role", "", "ck_admin_principals_role_nonempty"),
        (Wake, "status", "completed", "ck_wakes_status"),
        (Wake, "kind", "user_request", "ck_wakes_kind"),
        (Wake, "revision", 0, "ck_wakes_revision_positive"),
    ],
)
def test_named_check_constraints(session, model, field, value, constraint):
    if model is Individual:
        with pytest.raises(IntegrityError) as error, session.begin_nested():
            individual(session, **{field: value})
        assert error.value.orig.diag.constraint_name == constraint
    else:
        row = dependent(model, individual(session).individual_id, **{field: value})
        rejected(session, row, constraint)


@pytest.mark.parametrize(
    "model",
    [
        GovernanceState,
        AdminPrincipal,
        RuntimeConfigRevision,
        RuntimeInstance,
        Wake,
    ],
)
def test_individual_foreign_keys_reject_orphans_and_prevent_cascade_deletion(
    session, model
):
    table = model.__tablename__
    rejected(
        session, dependent(model, new_id()), f"fk_{table}_individual_id_individuals"
    )
    person = individual(session)
    session.add(dependent(model, person.individual_id))
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.execute(
            delete(Individual).where(Individual.individual_id == person.individual_id)
        )
    assert session.get(Individual, person.individual_id) is person


def test_parent_lineage_foreign_key_and_optional_lineage(session):
    parent = individual(session)
    child = individual(session, parent_individual_id=parent.individual_id)
    assert child.parent_individual_id == parent.individual_id
    assert parent.parent_individual_id is None
    assert parent.fork_event_id is None
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        individual(session, parent_individual_id=new_id())
    assert (
        error.value.orig.diag.constraint_name
        == "fk_individuals_parent_individual_id_individuals"
    )


def test_one_governance_row_per_individual(session):
    person = individual(session)
    session.add(dependent(GovernanceState, person.individual_id))
    session.flush()
    rejected(
        session, dependent(GovernanceState, person.individual_id), "pk_governance_state"
    )


@pytest.mark.parametrize(
    "model",
    [
        Individual,
        AdminPrincipal,
        RuntimeConfigRevision,
        RuntimeInstance,
        Wake,
    ],
)
def test_application_id_primary_keys_reject_duplicate_identity(session, model):
    person = individual(session)
    row = person if model is Individual else dependent(model, person.individual_id)
    session.add(row)
    session.flush()
    values = {
        attr.columns[0].name: getattr(row, attr.key)
        for attr in inspect(model).column_attrs
    }
    if model is AdminPrincipal:
        values["subject"] = "different subject, same principal ID"
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        session.execute(model.__table__.insert().values(**values))
    assert error.value.orig.diag.constraint_name == f"pk_{model.__tablename__}"


def test_admin_identity_unique_within_individual_provider_subject(session):
    person = individual(session)
    row = dependent(AdminPrincipal, person.individual_id)
    session.add(row)
    session.flush()
    assert row.admin_principal_id.version == 4
    rejected(
        session,
        dependent(AdminPrincipal, person.individual_id),
        "uq_admin_principals_identity",
    )
    session.add(dependent(AdminPrincipal, individual(session).individual_id))
    session.add(dependent(AdminPrincipal, person.individual_id, authn_provider="oidc"))
    session.add(dependent(AdminPrincipal, person.individual_id, subject="another"))
    session.flush()
    stored = session.execute(
        text("SELECT metadata FROM admin_principals WHERE admin_principal_id=:id"),
        {"id": row.admin_principal_id},
    ).scalar_one()
    assert stored == {}


def test_one_active_config_revision_and_history_preserved(session):
    person = individual(session)
    active = dependent(RuntimeConfigRevision, person.individual_id, activated_at=NOW)
    session.add(active)
    session.flush()
    rejected(
        session,
        dependent(RuntimeConfigRevision, person.individual_id, activated_at=NOW),
        "uq_runtime_config_revisions_active",
    )
    session.add(dependent(RuntimeConfigRevision, person.individual_id))
    session.add(dependent(RuntimeConfigRevision, person.individual_id))
    session.add(
        dependent(
            RuntimeConfigRevision, individual(session).individual_id, activated_at=NOW
        )
    )
    active.superseded_at = NOW + timedelta(seconds=1)
    session.flush()
    replacement = dependent(
        RuntimeConfigRevision,
        person.individual_id,
        activated_at=NOW + timedelta(seconds=1),
    )
    session.add(replacement)
    session.flush()
    assert active.config_revision_id != replacement.config_revision_id
    assert (
        len(
            session.scalars(
                select(RuntimeConfigRevision).where(
                    RuntimeConfigRevision.individual_id == person.individual_id
                )
            ).all()
        )
        == 4
    )


def test_pending_coalesce_uniqueness_is_per_individual_and_excludes_null_and_history(
    session,
):
    person = individual(session)
    pending = dependent(Wake, person.individual_id, coalesce_key="heartbeat")
    session.add(pending)
    session.flush()
    rejected(
        session,
        dependent(Wake, person.individual_id, coalesce_key="heartbeat"),
        "uq_wakes_pending_coalesce",
    )
    for status in ("claimed", "consumed", "cancelled", "superseded"):
        session.add(
            dependent(
                Wake, person.individual_id, coalesce_key="heartbeat", status=status
            )
        )
    session.add(
        dependent(Wake, individual(session).individual_id, coalesce_key="heartbeat")
    )
    session.add(dependent(Wake, person.individual_id))
    session.add(dependent(Wake, person.individual_id))
    session.flush()
    pending.status = "consumed"
    session.flush()
    session.add(dependent(Wake, person.individual_id, coalesce_key="heartbeat"))
    session.flush()
