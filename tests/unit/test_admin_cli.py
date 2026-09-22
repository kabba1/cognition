"""Local administrator identity comes from the OS, not caller input."""

import pytest


def test_local_principal_ignores_username_environment(monkeypatch):
    from cognition.cli.commands.admin import local_principal

    original = local_principal()
    monkeypatch.setenv("USERNAME", "pretend-administrator")
    monkeypatch.setenv("USER", "pretend-administrator")
    assert local_principal() == original
    assert original.authn_provider == "local_os"
    assert original.subject


def test_admin_cli_has_no_identity_override_or_personal_edit(capsys):
    from cognition.cli.main import main

    with pytest.raises(SystemExit) as stopped:
        main(["admin", "--help"])
    assert stopped.value.code == 0
    help_text = capsys.readouterr().out
    assert "emergency_block" in help_text
    assert "--subject" not in help_text
    assert "--principal" not in help_text
    assert "memory" not in help_text
