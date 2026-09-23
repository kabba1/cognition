"""Connector bindings require narrow authenticated administrative grants."""

import json
from datetime import timedelta

import pytest
from sqlalchemy import event, func, select
from test_admin_lifecycle import NOW, seed

from cognition.db.models import AdminAudit, Event, GovernanceState, Individual
from cognition.policy.governance import AuthenticatedPrincipal
from cognition.runtime.connector_admin import register_connector, set_connector_enabled
from cognition.stores.connectors import (
    load_connector_binding,
    load_connector_binding_snapshot,
)
from cognition.testing.clock import FakeClock


def register(factory, identity, principal, **changes):
    return register_connector(
        factory,
        identity,
        principal,
        adapter_id="local_json_v1",
        source_id=changes.pop("source_id", "stream-A"),
        reason="explicit source allowance",
        clock=FakeClock(NOW),
        **changes,
    )


@pytest.mark.parametrize("enabled", [False, True])
def test_register_default_and_explicit_enable_are_audited(db_session_factory, enabled):
    identity, principal = seed(db_session_factory, "paused")
    result = register(
        db_session_factory,
        identity,
        principal,
        **({"enabled": True} if enabled else {}),
    )
    with db_session_factory() as session:
        binding = load_connector_binding(session, identity, result.connector_binding_id)
        assert binding.enabled is enabled and binding.cursor is None
        assert binding.cursor_revision == 0 and binding.revision == 1
        assert binding.adapter_id == "local_json_v1" and binding.source_id == "stream-A"
        assert binding.created_at == binding.updated_at == NOW
        assert session.get(Individual, identity).operational_status == "paused"
        audit = session.get(AdminAudit, result.audit_id)
        assert (
            audit.event_id == result.event_id
            and audit.operation == "connector.register"
        )
        assert audit.before_state == {}
        assert audit.after_state["enabled"] is enabled
        assert audit.after_state["connector_binding_id"] == str(
            binding.connector_binding_id
        )


def test_source_identity_is_immutable_and_duplicate_registration_rolls_back(
    db_session_factory,
):
    identity, principal = seed(db_session_factory)
    result = register(db_session_factory, identity, principal)
    with pytest.raises(ValueError, match="registered"):
        register(db_session_factory, identity, principal)
    second = register(db_session_factory, identity, principal, source_id="stream-a")
    assert second.connector_binding_id != result.connector_binding_id
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AdminAudit)) == 2


@pytest.mark.parametrize(
    "source_id", ["", "x" * 513, "é" * 257, "bad\x00source", "bad\ud800source"]
)
def test_invalid_source_ids_do_not_write(db_session_factory, source_id):
    identity, principal = seed(db_session_factory)
    with pytest.raises(ValueError):
        register(db_session_factory, identity, principal, source_id=source_id)
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AdminAudit)) == 0


@pytest.mark.parametrize("source_id", ["é" * 256, " ", " Stream-A "])
def test_exact_source_id_is_preserved(db_session_factory, source_id):
    identity, principal = seed(db_session_factory)
    result = register(db_session_factory, identity, principal, source_id=source_id)
    with db_session_factory() as session:
        assert (
            load_connector_binding(
                session, identity, result.connector_binding_id
            ).source_id
            == source_id
        )


@pytest.mark.parametrize(
    "case",
    ["reader", "revoked", "social", "unknown", "empty_reason", "integer_enabled"],
)
def test_registration_requires_real_local_admin_and_strict_inputs(
    db_session_factory, case
):
    identity, principal = seed(
        db_session_factory,
        role="reader" if case == "reader" else "admin",
        revoked=case == "revoked",
    )
    if case in {"social", "unknown"}:
        principal = AuthenticatedPrincipal(
            "social" if case == "social" else "local_os", "unknown"
        )
    with pytest.raises((PermissionError, ValueError)):
        register_connector(
            db_session_factory,
            identity,
            principal,
            adapter_id="local_json_v1",
            source_id="stream",
            enabled=1 if case == "integer_enabled" else False,
            reason=" " if case == "empty_reason" else "allow",
            clock=FakeClock(NOW),
        )
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AdminAudit)) == 0
        assert session.scalar(select(func.count()).select_from(Event)) == 0


def test_toggle_advances_only_binding_epoch_and_preserves_cursor(db_session_factory):
    from cognition.db.models.perception import ConnectorBinding

    identity, principal = seed(db_session_factory)
    result = register(db_session_factory, identity, principal)
    with db_session_factory.begin() as session:
        binding = session.get(ConnectorBinding, result.connector_binding_id)
        binding.cursor, binding.cursor_revision = "private-cursor", 7
    for enabled in (True, False):
        result = set_connector_enabled(
            db_session_factory,
            identity,
            result.connector_binding_id,
            principal,
            enabled=enabled,
            reason="operator choice",
            clock=FakeClock(NOW + timedelta(seconds=1)),
        )
    with db_session_factory() as session:
        binding = load_connector_binding(session, identity, result.connector_binding_id)
        assert binding.revision == 3 and binding.cursor_revision == 7
        assert binding.cursor == "private-cursor" and binding.enabled is False
        audit = session.get(AdminAudit, result.audit_id)
        assert (
            audit.before_state["enabled"] is True
            and audit.after_state["enabled"] is False
        )
        assert "private-cursor" not in str(audit.after_state)


def test_foreign_binding_cannot_be_loaded_or_toggled(db_session_factory):
    identity, principal = seed(db_session_factory)
    other, other_principal = seed(db_session_factory)
    binding = register(db_session_factory, other, other_principal)
    with db_session_factory() as session, pytest.raises(LookupError):
        load_connector_binding(session, identity, binding.connector_binding_id)
    with pytest.raises(LookupError):
        set_connector_enabled(
            db_session_factory,
            identity,
            binding.connector_binding_id,
            principal,
            enabled=True,
            reason="attempt",
            clock=FakeClock(NOW),
        )


def test_registration_failure_after_audit_rolls_back_binding_and_evidence(
    db_engine, db_session_factory
):
    from cognition.db.models.perception import ConnectorBinding

    identity, principal = seed(db_session_factory)

    def interrupt(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO admin_audit"):
            raise RuntimeError("simulated interruption")

    event.listen(db_engine, "after_cursor_execute", interrupt)
    try:
        with pytest.raises(RuntimeError, match="interruption"):
            register(db_session_factory, identity, principal)
    finally:
        event.remove(db_engine, "after_cursor_execute", interrupt)
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ConnectorBinding)) == 0
        assert session.scalar(select(func.count()).select_from(Event)) == 0
        assert session.scalar(select(func.count()).select_from(AdminAudit)) == 0


def test_snapshot_uses_committed_epochs_without_flushing_or_refreshing_dirty_rows(
    db_session_factory,
):
    from cognition.db.models.perception import ConnectorBinding

    identity, principal = seed(db_session_factory)
    result = register(db_session_factory, identity, principal)
    with db_session_factory() as session:
        binding = session.get(ConnectorBinding, result.connector_binding_id)
        individual = session.get(Individual, identity)
        governance = session.get(GovernanceState, identity)
        binding.enabled, binding.revision = True, 99
        individual.operational_status, individual.revision = "paused", 99
        governance.revision = 99
        snapshot = load_connector_binding_snapshot(
            session, identity, binding.connector_binding_id
        )
        assert snapshot.enabled is False and snapshot.binding_revision == 1
        assert snapshot.individual_revision == snapshot.governance_revision == 1
        assert snapshot.operational_status == "active"
        assert binding.revision == individual.revision == governance.revision == 99
        assert {binding, individual, governance} <= set(session.dirty)


def test_toggle_locks_individual_admin_governance_then_binding(
    db_engine, db_session_factory
):
    identity, principal = seed(db_session_factory)
    binding = register(db_session_factory, identity, principal)
    locks = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" in statement:
            locks.append(statement.split("FROM ")[1].split()[0])

    event.listen(db_engine, "before_cursor_execute", observe)
    try:
        set_connector_enabled(
            db_session_factory,
            identity,
            binding.connector_binding_id,
            principal,
            enabled=True,
            reason="allow",
            clock=FakeClock(NOW),
        )
    finally:
        event.remove(db_engine, "before_cursor_execute", observe)
    assert locks[:4] == [
        "individuals",
        "admin_principals",
        "governance_state",
        "connector_bindings",
    ]


@pytest.mark.parametrize("case", ["clock", "revision", "dirty"])
def test_store_rejects_clock_revision_and_unflushed_binding_changes(
    db_session_factory, case
):
    from cognition.db.models.perception import ConnectorBinding
    from cognition.stores.connectors import set_connector_enabled as store_toggle
    from cognition.stores.errors import RevisionConflict

    identity, principal = seed(db_session_factory)
    binding = register(db_session_factory, identity, principal)
    with db_session_factory() as session:
        if case == "dirty":
            tracked = session.get(ConnectorBinding, binding.connector_binding_id)
            tracked.enabled = True
        with pytest.raises((ValueError, RevisionConflict)):
            store_toggle(
                session,
                identity,
                binding.connector_binding_id,
                True,
                now=NOW - timedelta(seconds=1) if case == "clock" else NOW,
                expected_revision=99 if case == "revision" else 1,
            )
        committed = load_connector_binding(
            session, identity, binding.connector_binding_id
        )
        assert committed.enabled is False and committed.revision == 1
        if case == "dirty":
            assert tracked.enabled is True and tracked in session.dirty


def test_toggle_audit_failure_rolls_back_enablement(db_engine, db_session_factory):
    identity, principal = seed(db_session_factory)
    binding = register(db_session_factory, identity, principal)

    def interrupt(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO admin_audit"):
            raise RuntimeError("simulated interruption")

    event.listen(db_engine, "after_cursor_execute", interrupt)
    try:
        with pytest.raises(RuntimeError, match="interruption"):
            set_connector_enabled(
                db_session_factory,
                identity,
                binding.connector_binding_id,
                principal,
                enabled=True,
                reason="allow",
                clock=FakeClock(NOW),
            )
    finally:
        event.remove(db_engine, "after_cursor_execute", interrupt)
    with db_session_factory() as session:
        committed = load_connector_binding(
            session, identity, binding.connector_binding_id
        )
        assert committed.enabled is False and committed.revision == 1
        assert session.scalar(select(func.count()).select_from(AdminAudit)) == 1
        assert session.scalar(select(func.count()).select_from(Event)) == 1


def test_cli_register_and_toggle_use_os_principal_without_cursor_or_identity_overrides(
    db_engine, db_session_factory, monkeypatch, capsys
):
    from cognition.cli.commands import connectors
    from cognition.cli.commands.admin import local_principal
    from cognition.cli.main import main
    from cognition.stores.governance import create_admin_principal

    identity, _ = seed(db_session_factory)
    principal = local_principal()
    with db_session_factory.begin() as session:
        create_admin_principal(
            session,
            identity,
            authn_provider=principal.authn_provider,
            subject=principal.subject,
        )
    monkeypatch.setattr(connectors, "configured_engine", lambda: db_engine)
    common = ["--individual-id", str(identity), "--reason", "local operator"]
    assert (
        main(
            [
                "connector",
                "register",
                *common,
                "--adapter",
                "local_json_v1",
                "--source-id",
                "stream",
            ]
        )
        == 0
    )
    registered = json.loads(capsys.readouterr().out)
    assert registered["enabled"] is False and "cursor" not in registered
    for operation, enabled in (("enable", True), ("disable", False)):
        assert (
            main(
                [
                    "connector",
                    operation,
                    *common,
                    "--binding-id",
                    registered["connector_binding_id"],
                ]
            )
            == 0
        )
        assert json.loads(capsys.readouterr().out)["enabled"] is enabled
    with pytest.raises(SystemExit) as stopped:
        main(["connector", "register", "--help"])
    assert stopped.value.code == 0
    help_text = capsys.readouterr().out
    assert "--cursor" not in help_text and "--principal" not in help_text
