"""Exploration allowance changes are authenticated, narrow and transactional."""

import json

import pytest
from sqlalchemy import event, func, select
from test_admin_lifecycle import NOW, seed
from test_birth import birth_input as birth_input
from test_birth import row_counts

from cognition.db.models import AdminAudit, Event, GovernanceState, Individual
from cognition.policy.governance import AuthenticatedPrincipal
from cognition.runtime.birth import birth
from cognition.runtime.exploration_admin import set_internal_exploration
from cognition.stores.evidence import load_event
from cognition.testing.clock import FakeClock


@pytest.mark.parametrize("status", ["active", "paused"])
def test_toggle_preserves_other_policy_and_lifecycle_with_exact_audit(
    db_session_factory, status
):
    identity, principal = seed(db_session_factory, status)
    with db_session_factory.begin() as session:
        session.get(GovernanceState, identity).budget_policy = {"other": {"limit": 17}}
    for enabled in (True, False):
        result = set_internal_exploration(
            db_session_factory,
            identity,
            principal,
            enabled,
            "operator choice",
            FakeClock(NOW),
        )
        with db_session_factory() as session:
            governance = session.get(GovernanceState, identity)
            assert governance.budget_policy == {
                "other": {"limit": 17},
                "internal_exploration": {"schema_version": 1, "enabled": enabled},
            }
            assert governance.external_actions_blocked
            assert session.get(Individual, identity).operational_status == status
            assert result.enabled is enabled and result.operational_status == status
            assert result.governance_revision == governance.revision
            audit = session.get(AdminAudit, result.audit_id)
            assert (
                audit.event_id == result.event_id and audit.reason == "operator choice"
            )
            assert audit.before_state["governance_revision"] + 1 == governance.revision
            assert audit.after_state["governance_revision"] == governance.revision
            assert audit.after_state["budget_policy"] == governance.budget_policy
            recorded = load_event(session, result.event_id)
            assert recorded.envelope.source.kind == "admin"
            assert recorded.envelope.content.payload["before"] == audit.before_state
            assert recorded.envelope.content.payload["after"] == audit.after_state


@pytest.mark.parametrize(
    "case",
    ["reader", "revoked", "unknown", "social", "empty_reason", "integer_enabled"],
)
def test_toggle_rejection_does_not_write(db_session_factory, case):
    identity, principal = seed(
        db_session_factory,
        role="reader" if case == "reader" else "admin",
        revoked=case == "revoked",
    )
    if case in {"unknown", "social"}:
        principal = AuthenticatedPrincipal(
            "social" if case == "social" else "local_os", "unknown"
        )
    with pytest.raises((PermissionError, ValueError)):
        set_internal_exploration(
            db_session_factory,
            identity,
            principal,
            1 if case == "integer_enabled" else True,
            " " if case == "empty_reason" else "request",
            FakeClock(NOW),
        )
    with db_session_factory() as session:
        assert session.get(GovernanceState, identity).revision == 1
        assert session.get(GovernanceState, identity).budget_policy == {}
        assert session.scalar(select(func.count()).select_from(AdminAudit)) == 0
        assert session.scalar(select(func.count()).select_from(Event)) == 0


def test_audit_failure_rolls_back_policy_revision_and_event(
    db_engine, db_session_factory
):
    identity, principal = seed(db_session_factory)

    def interrupt(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO admin_audit"):
            raise RuntimeError("simulated interruption")

    event.listen(db_engine, "after_cursor_execute", interrupt)
    try:
        with pytest.raises(RuntimeError, match="interruption"):
            set_internal_exploration(
                db_session_factory, identity, principal, True, "enable", FakeClock(NOW)
            )
    finally:
        event.remove(db_engine, "after_cursor_execute", interrupt)
    with db_session_factory() as session:
        assert session.get(GovernanceState, identity).budget_policy == {}
        assert session.get(GovernanceState, identity).revision == 1
        assert session.scalar(select(func.count()).select_from(Event)) == 0
        assert session.scalar(select(func.count()).select_from(AdminAudit)) == 0


@pytest.mark.parametrize(
    "operation,enabled", [("enable_exploration", True), ("disable_exploration", False)]
)
def test_cli_uses_os_principal_and_narrow_policy_api(
    db_engine, db_session_factory, monkeypatch, capsys, operation, enabled
):
    from cognition.cli.commands import admin
    from cognition.cli.main import main
    from cognition.policy.governance import ADMIN_OPERATIONS
    from cognition.stores.governance import create_admin_principal

    identity, _ = seed(db_session_factory, "paused")
    authenticated = admin.local_principal()
    with db_session_factory.begin() as session:
        create_admin_principal(
            session,
            identity,
            authn_provider=authenticated.authn_provider,
            subject=authenticated.subject,
        )
    monkeypatch.setattr(admin, "configured_engine", lambda: db_engine)
    assert operation not in ADMIN_OPERATIONS
    assert (
        main(
            [
                "admin",
                operation,
                "--individual-id",
                str(identity),
                "--reason",
                "local choice",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["internal_exploration_enabled"] is enabled
    with db_session_factory() as session:
        assert (
            session.get(GovernanceState, identity).budget_policy[
                "internal_exploration"
            ]["enabled"]
            is enabled
        )
        assert session.get(AdminAudit, payload["audit_id"]) is not None


@pytest.mark.parametrize(
    "policy",
    [
        {"schema_version": True, "enabled": True},
        {"schema_version": 1, "enabled": "yes"},
    ],
)
def test_birth_revalidates_reserved_policy_before_any_writes(
    db_session_factory, birth_input, policy
):
    birth_input.budget_policy["internal_exploration"] = policy
    with pytest.raises(ValueError):
        birth(db_session_factory, birth_input, FakeClock(NOW))
    assert not any(row_counts(db_session_factory).values())


def test_birth_can_explicitly_enable_without_creating_exploration_work(
    db_session_factory, birth_input
):
    from cognition.db.models.attention import Wake

    birth_input.budget_policy["internal_exploration"] = {
        "schema_version": 1,
        "enabled": True,
    }
    result = birth(db_session_factory, birth_input, FakeClock(NOW))
    with db_session_factory() as session:
        assert (
            session.get(GovernanceState, result.individual_id).budget_policy[
                "internal_exploration"
            ]["enabled"]
            is True
        )
        assert list(session.scalars(select(Wake.kind))) == ["bootstrap"]


def test_toggle_locks_individual_then_admin_then_governance(
    db_engine, db_session_factory
):
    identity, principal = seed(db_session_factory)
    locks = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" in statement:
            for table in ("individuals", "admin_principals", "governance_state"):
                if f"FROM {table}" in statement:
                    locks.append(table)

    event.listen(db_engine, "before_cursor_execute", observe)
    try:
        set_internal_exploration(
            db_session_factory, identity, principal, True, "enable", FakeClock(NOW)
        )
    finally:
        event.remove(db_engine, "before_cursor_execute", observe)
    assert locks[:3] == ["individuals", "admin_principals", "governance_state"]


def test_authenticated_toggle_can_repair_invalid_reserved_policy(db_session_factory):
    identity, principal = seed(db_session_factory)
    invalid = {"internal_exploration": {"enabled": "unsafe coercion"}, "other": 17}
    with db_session_factory.begin() as session:
        session.get(GovernanceState, identity).budget_policy = invalid
    result = set_internal_exploration(
        db_session_factory, identity, principal, False, "repair policy", FakeClock(NOW)
    )
    with db_session_factory() as session:
        audit = session.get(AdminAudit, result.audit_id)
        assert audit.before_state["budget_policy"] == invalid
        assert audit.after_state["budget_policy"] == {
            "internal_exploration": {"schema_version": 1, "enabled": False},
            "other": 17,
        }


def test_trusted_setter_rejects_dirty_governance_without_refresh_or_flush(
    db_session_factory,
):
    from cognition.stores.governance import set_internal_exploration_policy

    identity, _ = seed(db_session_factory)
    with db_session_factory() as session:
        row = session.get(GovernanceState, identity)
        row.budget_policy = {"dirty": True}
        with pytest.raises(ValueError, match="Unflushed"):
            set_internal_exploration_policy(session, identity, True)
        assert row in session.dirty and row.budget_policy == {"dirty": True}
    with db_session_factory() as session:
        assert session.get(GovernanceState, identity).budget_policy == {}


def test_toggle_rejects_nonobject_budget_without_overwriting_it(db_session_factory):
    identity, principal = seed(db_session_factory)
    with db_session_factory.begin() as session:
        session.get(GovernanceState, identity).budget_policy = []
    with pytest.raises(ValueError, match="Budget policy"):
        set_internal_exploration(
            db_session_factory, identity, principal, False, "disable", FakeClock(NOW)
        )
    with db_session_factory() as session:
        assert session.get(GovernanceState, identity).budget_policy == []
        assert session.scalar(select(func.count()).select_from(AdminAudit)) == 0


def test_toggle_preserves_started_invocation_and_claimed_cycle(
    db_session_factory, birth_input
):
    from cognition.db.models.attention import Wake
    from cognition.db.models.cognition import (
        CognitionCycle,
        CognitionTurn,
        CycleWake,
        ModelInvocation,
    )
    from cognition.stores.cognition import (
        CycleLimits,
        claim_or_resume,
        latest_turn,
        start_invocation,
    )

    birth_input.admin_authn_provider = "local_os"
    birth_input.admin_subject = "test-admin"
    birth_input.budget_policy["internal_exploration"] = {
        "schema_version": 1,
        "enabled": True,
    }
    born = birth(db_session_factory, birth_input, FakeClock(NOW))
    tables = (Wake, CognitionCycle, CognitionTurn, CycleWake, ModelInvocation)
    with db_session_factory.begin() as session:
        cycle = claim_or_resume(session, born.individual_id, NOW, CycleLimits())
        turn = latest_turn(session, cycle.cycle_id)
        assert start_invocation(session, cycle, turn, NOW) is not None
        before = [list(session.execute(select(model.__table__))) for model in tables]
    set_internal_exploration(
        db_session_factory,
        born.individual_id,
        AuthenticatedPrincipal("local_os", "test-admin"),
        False,
        "stop new exploration",
        FakeClock(NOW),
    )
    with db_session_factory() as session:
        assert [
            list(session.execute(select(model.__table__))) for model in tables
        ] == before
