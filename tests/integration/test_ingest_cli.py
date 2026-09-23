"""Explicit local ingress requires administrator identity before opening files."""

import json

from sqlalchemy import func, select
from test_autonomy import NOW
from test_autonomy import person as person
from test_perception import binding as binding

from cognition.db.models.perception import Observation
from cognition.testing.clock import FakeClock


def test_local_ingest_cli_authenticates_then_persists_and_replays(
    db_engine,
    db_session_factory,
    person,
    binding,
    tmp_path,
    monkeypatch,
    capsys,
):
    from cognition.cli.commands import ingest
    from cognition.cli.commands.admin import local_principal
    from cognition.cli.main import main
    from cognition.stores.governance import create_admin_principal

    source = tmp_path / "source.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "stream_id": "fixture-stream",
                "items": [
                    {"external_id": "hello", "payload": {"text": "Untrusted evidence"}}
                ],
            }
        ),
        encoding="utf-8",
    )
    principal = local_principal()
    with db_session_factory.begin() as session:
        create_admin_principal(
            session,
            person,
            authn_provider=principal.authn_provider,
            subject=principal.subject,
        )
    monkeypatch.setattr(ingest, "configured_engine", lambda: db_engine)
    monkeypatch.setattr(ingest, "SystemClock", lambda: FakeClock(NOW))
    args = [
        "ingest-once",
        "--individual-id",
        str(person),
        "--binding-id",
        str(binding),
        "--source",
        str(source),
    ]
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["created_count"] == 1 and first["cursor_revision"] == 1
    assert "cursor" not in first and "source" not in first
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["created_count"] == 0
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Observation)) == 1


def test_unauthorized_ingest_never_constructs_adapter(
    db_engine,
    person,
    binding,
    monkeypatch,
    capsys,
):
    from cognition.cli.commands import ingest
    from cognition.cli.main import main

    monkeypatch.setattr(ingest, "configured_engine", lambda: db_engine)

    def forbidden(*args, **kwargs):
        raise AssertionError("unauthorized adapter construction")

    monkeypatch.setattr(ingest, "LocalJsonConnector", forbidden)
    assert (
        main(
            [
                "ingest-once",
                "--individual-id",
                str(person),
                "--binding-id",
                str(binding),
                "--source",
                "secret-path",
            ]
        )
        == 2
    )
    assert "secret-path" not in capsys.readouterr().err
