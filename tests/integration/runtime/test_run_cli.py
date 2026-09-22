"""One local fixture run uses real PostgreSQL, OS identity, and runtime ownership."""

import argparse
import importlib
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from cognition.cli.commands.admin import local_principal
from cognition.config.loader import load_config
from cognition.protocols.common import SystemClock, new_id
from cognition.runtime.birth import BirthInput, birth


@pytest.fixture
def born(db_session_factory):
    config = load_config(Path(__file__).parents[2] / "fixtures/config/valid.toml")
    config.runtime.individual_id = new_id()
    config.model.adapter = config.model.requested_model = "script-file"
    principal = local_principal()
    return birth(
        db_session_factory,
        BirthInput(
            individual_id=config.runtime.individual_id,
            birth_name="CLI fixture",
            founding_orientation="Inspect local fixture decisions",
            creator_provenance={},
            admin_authn_provider=principal.authn_provider,
            admin_subject=principal.subject,
            config=config,
            runtime_version="2.0",
        ),
        SystemClock(),
    )


@pytest.fixture
def script(tmp_path):
    payload = json.loads(
        (Path(__file__).parents[2] / "golden/model_result_v1.json").read_text("utf-8")
    )["decision"]
    for key in ("cycle_id", "turn_id", "decision_id"):
        payload.pop(key)
    payload["current_focus"] = {"summary": "Fixture smoke verified", "refs": []}
    path = tmp_path / "script.json"
    path.write_text(json.dumps([payload]), encoding="utf-8")
    return path


def run_module():
    assert importlib.util.find_spec("cognition.cli.commands.run") is not None
    return importlib.import_module("cognition.cli.commands.run")


def invoke(module, born, script):
    parser = argparse.ArgumentParser()
    module.add_run_parser(parser.add_subparsers())
    args = parser.parse_args(
        [
            "run-once",
            "--individual-id",
            str(born.individual_id),
            "--script",
            str(script),
        ]
    )
    return args.handler(args)


def test_cli_applies_fixture_and_releases_ownership(
    db_engine,
    db_session_factory,
    born,
    script,
    monkeypatch,
    capsys,
):
    from cognition.db.models.cognition import (
        AttentionState,
        ContextSnapshot,
        ModelInvocation,
    )
    from cognition.db.models.runtime import RuntimeInstance

    run = run_module()
    monkeypatch.setattr(run, "configured_engine", lambda: db_engine)
    assert invoke(run, born, script) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "completed"
    assert output["cycle_id"]
    with db_session_factory() as session:
        assert session.get(AttentionState, born.individual_id).current_focus == {
            "summary": "Fixture smoke verified",
            "refs": [],
        }
        snapshot = session.scalar(select(ContextSnapshot))
        invocation = session.scalar(select(ModelInvocation))
        assert snapshot.model_adapter == "script-file"
        assert invocation.result_json["provider"] == "script-file"
        assert session.scalar(select(RuntimeInstance)).stopped_at is not None
    assert invoke(run, born, script) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "idle"


def test_cli_denies_revoked_os_admin_before_runtime_mutation(
    db_engine,
    db_session_factory,
    born,
    script,
    monkeypatch,
    capsys,
):
    from cognition.db.models.governance import AdminPrincipal
    from cognition.db.models.runtime import RuntimeInstance

    with db_session_factory.begin() as session:
        session.get(
            AdminPrincipal, born.admin_principal_id
        ).revoked_at = SystemClock().now()
    run = run_module()
    monkeypatch.setattr(run, "configured_engine", lambda: db_engine)
    assert invoke(run, born, script) == 2
    assert "failed" in capsys.readouterr().err.lower()
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RuntimeInstance)) == 0


def test_cli_does_not_migrate_or_expose_database_errors(
    born, script, monkeypatch, capsys
):
    from sqlalchemy.exc import OperationalError

    run = run_module()

    def broken_engine():
        raise OperationalError(
            "secret-SQL", {"credential": "secret-password"}, Exception()
        )

    monkeypatch.setattr(run, "configured_engine", broken_engine)
    assert invoke(run, born, script) == 2
    output = capsys.readouterr()
    assert not output.out
    assert "secret" not in output.err


def test_cli_rejects_nonfixture_active_config(
    db_engine,
    db_session_factory,
    born,
    script,
    monkeypatch,
    capsys,
):
    from cognition.db.models.runtime import RuntimeInstance
    from cognition.stores.configuration import (
        get_active_config,
        replace_config_revision,
    )

    with db_session_factory.begin() as session:
        config = get_active_config(session, born.individual_id).sanitized_config
        config.model.adapter = "live-provider"
        replace_config_revision(
            session, born.individual_id, config, SystemClock().now()
        )
    run = run_module()
    monkeypatch.setattr(run, "configured_engine", lambda: db_engine)
    assert invoke(run, born, script) == 2
    assert "failed" in capsys.readouterr().err.lower()
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RuntimeInstance)) == 0


def test_cli_old_schema_is_not_implicitly_migrated(
    db_engine,
    alembic_config,
    born,
    script,
    monkeypatch,
    capsys,
):
    from alembic import command
    from alembic.runtime.migration import MigrationContext

    from cognition.cli.database import require_supported_schema

    command.downgrade(alembic_config, "0003_evidence")
    run = run_module()

    def checked_engine():
        require_supported_schema(db_engine)
        return db_engine

    monkeypatch.setattr(run, "configured_engine", checked_engine)
    assert invoke(run, born, script) == 2
    assert "failed" in capsys.readouterr().err.lower()
    with db_engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={
                "version_table_schema": db_engine.get_execution_options()[
                    "cognition_schema"
                ],
            },
        )
        assert context.get_current_heads() == ("0003_evidence",)


@pytest.mark.parametrize(
    "committed", [False, True], ids=["needs_inference", "has_decision"]
)
def test_cli_frozen_provider_guard_preserves_committed_decision_recovery(
    db_engine,
    db_session_factory,
    born,
    script,
    monkeypatch,
    capsys,
    committed,
):
    from cognition.db.models.cognition import (
        CognitionTurn,
        ContextSnapshot,
        ModelInvocation,
    )
    from cognition.models.script_file import load_script_file
    from cognition.protocols.model_v1 import ModelRequestV1
    from cognition.runtime.cognition import CognitionRuntime
    from cognition.runtime.ownership import acquire_runtime_ownership
    from cognition.stores.cognition import latest_turn, load_cycle, record_result

    class Interrupted(BaseException):
        pass

    class InterruptedModel:
        def decide(self, request):
            raise Interrupted()

    with acquire_runtime_ownership(
        db_engine,
        born.individual_id,
        clock=SystemClock(),
        host_id="test",
        process_id=1,
        runtime_version="2.0",
    ) as owner:
        with pytest.raises(Interrupted):
            CognitionRuntime(
                owner,
                born.individual_id,
                InterruptedModel(),
                SystemClock(),
            ).run_once()
    with db_session_factory.begin() as session:
        snapshot = session.scalar(select(ContextSnapshot))
        snapshot.model_adapter = snapshot.requested_model = "other-provider"
        if committed:
            request = ModelRequestV1.model_validate(snapshot.request_json)
            result = load_script_file(script).decide(request)
            result.provider = result.requested_model = "other-provider"
            cycle_id = session.get(CognitionTurn, snapshot.turn_id).cycle_id
            record_result(
                session,
                load_cycle(session, cycle_id),
                latest_turn(session, cycle_id),
                session.scalar(select(ModelInvocation)).invocation_id,
                request,
                result,
                SystemClock().now(),
            )
    run = run_module()
    monkeypatch.setattr(run, "configured_engine", lambda: db_engine)
    assert invoke(run, born, script) == (0 if committed else 2)
    output = capsys.readouterr()
    if committed:
        assert json.loads(output.out)["status"] == "completed"
    else:
        assert "failed" in output.err.lower()
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ModelInvocation)) == 1
