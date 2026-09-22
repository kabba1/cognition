"""Administrative authority and transitions are deterministic and atomic."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select

from cognition.db.models import AdminAudit, Event, Individual
from cognition.stores.governance import create_admin_principal, create_governance
from cognition.stores.identity import create_individual
from cognition.testing.clock import FakeClock

NOW = datetime(2026, 1, 1, tzinfo=UTC)
STATES = ("active", "paused", "quiescing", "quiescent", "retired")
OPERATIONS = (
    "pause",
    "resume",
    "begin_quiesce",
    "complete_quiesce",
    "abort_quiesce",
    "retire",
)
TRANSITIONS = {
    ("active", "pause"): "paused",
    ("paused", "resume"): "active",
    ("quiescent", "resume"): "active",
    ("active", "begin_quiesce"): "quiescing",
    ("paused", "begin_quiesce"): "quiescing",
    ("quiescing", "complete_quiesce"): "quiescent",
    ("quiescing", "abort_quiesce"): "paused",
    ("paused", "retire"): "retired",
    ("quiescent", "retire"): "retired",
}


def seed(factory, status="active", *, role="admin", revoked=False):
    from cognition.db.models import AdminPrincipal
    from cognition.policy.governance import AuthenticatedPrincipal

    individual_id = uuid4()
    with factory.begin() as session:
        create_individual(
            session,
            individual_id=individual_id,
            birth_at=NOW,
            birth_name="Ada",
            founding_orientation="Learn",
            creator_provenance={},
        )
        session.get(Individual, individual_id).operational_status = status
        create_governance(session, individual_id)
        principal = create_admin_principal(
            session,
            individual_id,
            authn_provider="local_os",
            subject="test-admin",
            role=role,
        )
        if revoked:
            session.get(AdminPrincipal, principal.admin_principal_id).revoked_at = NOW
    return individual_id, AuthenticatedPrincipal("local_os", "test-admin")


@pytest.mark.parametrize("status", STATES)
@pytest.mark.parametrize("operation", OPERATIONS)
def test_complete_transition_matrix(db_session_factory, status, operation):
    from cognition.runtime.lifecycle import apply_admin_operation

    individual_id, principal = seed(db_session_factory, status)
    expected = TRANSITIONS.get((status, operation))
    if expected is None:
        with pytest.raises(ValueError, match="transition"):
            apply_admin_operation(
                db_session_factory,
                individual_id,
                principal,
                operation,
                "test",
                FakeClock(NOW),
            )
    else:
        result = apply_admin_operation(
            db_session_factory,
            individual_id,
            principal,
            operation,
            "test",
            FakeClock(NOW),
        )
        assert result.operational_status == expected
    with db_session_factory() as session:
        assert session.get(Individual, individual_id).operational_status == (
            expected or status
        )
        assert session.scalar(select(func.count()).select_from(AdminAudit)) == bool(
            expected
        )
        assert session.scalar(select(func.count()).select_from(Event)) == bool(expected)


@pytest.mark.parametrize("role,revoked", [("reader", False), ("admin", True)])
def test_unauthorized_principal_writes_nothing(db_session_factory, role, revoked):
    from cognition.runtime.lifecycle import apply_admin_operation

    individual_id, principal = seed(db_session_factory, role=role, revoked=revoked)
    with pytest.raises(PermissionError):
        apply_admin_operation(
            db_session_factory,
            individual_id,
            principal,
            "pause",
            "test",
            FakeClock(NOW),
        )
    with db_session_factory() as session:
        assert session.get(Individual, individual_id).operational_status == "active"
        assert session.scalar(select(func.count()).select_from(AdminAudit)) == 0


def test_failure_after_audit_rolls_back_state_and_evidence(
    db_engine, db_session_factory
):
    from cognition.runtime.lifecycle import apply_admin_operation

    individual_id, principal = seed(db_session_factory)

    def fail_after_audit(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO admin_audit"):
            raise RuntimeError("simulated interruption")

    event.listen(db_engine, "after_cursor_execute", fail_after_audit)
    try:
        with pytest.raises(RuntimeError, match="interruption"):
            apply_admin_operation(
                db_session_factory,
                individual_id,
                principal,
                "pause",
                "test",
                FakeClock(NOW),
            )
    finally:
        event.remove(db_engine, "after_cursor_execute", fail_after_audit)
    with db_session_factory() as session:
        assert session.get(Individual, individual_id).operational_status == "active"
        assert session.scalar(select(func.count()).select_from(Event)) == 0
        assert session.scalar(select(func.count()).select_from(AdminAudit)) == 0


def test_emergency_block_works_even_for_retired(db_session_factory):
    from cognition.runtime.lifecycle import apply_admin_operation
    from cognition.stores.governance import load_governance, update_governance

    individual_id, principal = seed(db_session_factory, "retired")
    with db_session_factory.begin() as session:
        update_governance(session, individual_id, external_actions_blocked=False)
    result = apply_admin_operation(
        db_session_factory,
        individual_id,
        principal,
        "emergency_block",
        "contain",
        FakeClock(NOW),
    )
    assert result.operational_status == "retired"
    with db_session_factory() as session:
        assert load_governance(session, individual_id).external_actions_blocked
        assert session.scalar(select(func.count()).select_from(AdminAudit)) == 1


def test_cli_uses_real_os_principal_and_writes_audit(
    db_engine,
    db_session_factory,
    monkeypatch,
    capsys,
):
    import json

    from cognition.cli.commands import admin
    from cognition.cli.database import require_supported_schema
    from cognition.cli.main import main

    individual_id, _ = seed(db_session_factory)
    authenticated = admin.local_principal()
    with db_session_factory.begin() as session:
        create_admin_principal(
            session,
            individual_id,
            authn_provider=authenticated.authn_provider,
            subject=authenticated.subject,
        )
    require_supported_schema(db_engine)
    monkeypatch.setattr(admin, "configured_engine", lambda: db_engine)
    assert (
        main(
            [
                "admin",
                "pause",
                "--individual-id",
                str(individual_id),
                "--reason",
                "CLI smoke",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["operational_status"] == "paused"
    with db_session_factory() as session:
        audit = session.scalar(select(AdminAudit))
        assert str(audit.audit_id) == payload["audit_id"]
        assert str(audit.event_id) == payload["event_id"]


def test_schema_gate_rejects_empty_without_migrating(db_url, db_schema):
    from sqlalchemy import inspect

    from cognition.cli.database import require_supported_schema
    from cognition.db.session import create_db_engine

    engine = create_db_engine(db_url, schema=db_schema)
    try:
        with pytest.raises(ValueError, match="explicit migrations"):
            require_supported_schema(engine)
        assert inspect(engine).get_table_names() == []
    finally:
        engine.dispose()


def test_social_identity_and_other_individual_admin_cannot_mutate(db_session_factory):
    from cognition.policy.governance import AuthenticatedPrincipal
    from cognition.runtime.lifecycle import apply_admin_operation

    individual_id, _ = seed(db_session_factory)
    for principal in (
        AuthenticatedPrincipal("connector", "test-admin"),
        AuthenticatedPrincipal("local_os", "different-admin"),
    ):
        with pytest.raises(PermissionError):
            apply_admin_operation(
                db_session_factory,
                individual_id,
                principal,
                "pause",
                "test",
                FakeClock(NOW),
            )
    with db_session_factory() as session:
        assert session.get(Individual, individual_id).operational_status == "active"
        assert session.scalar(select(func.count()).select_from(AdminAudit)) == 0
