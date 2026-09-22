"""The Phase 1 checker diagnoses isolated corruption without repairing it."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text

from cognition.config.loader import load_config
from cognition.db.models.audit import AdminAudit
from cognition.db.models.evidence import Event, EventContent
from cognition.db.models.governance import GovernanceState
from cognition.db.models.identity import Individual
from cognition.db.models.runtime import RuntimeConfigRevision
from cognition.protocols.common import new_id
from cognition.runtime.birth import BirthInput, birth
from cognition.testing.clock import FakeClock

NOW = datetime(2026, 9, 22, 14, tzinfo=UTC)


def create_healthy(factory):
    config = load_config(
        Path(__file__).parents[1] / "fixtures" / "config" / "valid.toml"
    )
    config.runtime.individual_id = new_id()
    return birth(
        factory,
        BirthInput(
            individual_id=config.runtime.individual_id,
            birth_name="Integrity test",
            founding_orientation="Observe carefully.",
            creator_provenance={"origin": "integration-test"},
            admin_authn_provider="local",
            admin_subject="operator",
            config=config,
            runtime_version="0.1.0-test",
        ),
        FakeClock(NOW),
    )


@pytest.fixture
def healthy(db_session_factory):
    return create_healthy(db_session_factory)


def check(session):
    from cognition.db.checks import check_database

    return check_database(session)


def invariant(report, name):
    return [finding for finding in report.findings if finding.invariant_id == name]


def test_healthy_database_is_clean_and_phase_two_is_explicitly_not_applicable(
    db_session_factory, healthy
):
    with db_session_factory.begin() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        report = check(session)
        assert report.healthy and report.findings == ()
        assert report.not_applicable == ("claimed_wake_cycle",)


def test_empty_database_has_no_born_individual_obligations(db_session_factory):
    with db_session_factory() as session:
        assert check(session).healthy


def test_missing_governance_is_reported_without_repair(db_session_factory, healthy):
    with db_session_factory.begin() as session:
        session.delete(session.get(GovernanceState, healthy.individual_id))
    with db_session_factory.begin() as session:
        report = check(session)
        findings = invariant(report, "individual_governance")
        assert len(findings) == 1
        assert findings[0].severity == "error"
        assert findings[0].subject.kind == "individual"
        assert findings[0].subject.id == healthy.individual_id
        assert not report.healthy
        assert session.get(GovernanceState, healthy.individual_id) is None


@pytest.mark.parametrize("count", [0, 2])
def test_exactly_one_genesis_is_required(db_session_factory, healthy, count):
    with db_session_factory.begin() as session:
        original = session.get(Event, healthy.genesis_event_id)
        if count == 0:
            original.event_type = "test.corrupted"
        else:
            values = {
                column.name: getattr(original, column.name)
                for column in Event.__table__.columns
                if column.name not in {"event_id", "event_sequence"}
            }
            session.add(Event(**values))
    with db_session_factory() as session:
        assert len(invariant(check(session), "individual_genesis")) == 1


@pytest.mark.parametrize("count", [0, 2])
def test_exactly_one_active_config_is_required(db_session_factory, healthy, count):
    with db_session_factory.begin() as session:
        original = session.get(RuntimeConfigRevision, healthy.config_revision_id)
        if count == 0:
            original.superseded_at = NOW
        else:
            session.execute(text("DROP INDEX uq_runtime_config_revisions_active"))
            values = {
                column.name: getattr(original, column.name)
                for column in RuntimeConfigRevision.__table__.columns
                if column.name != "config_revision_id"
            }
            session.add(RuntimeConfigRevision(**values))
    with db_session_factory() as session:
        assert len(invariant(check(session), "active_config_revision")) == 1


@pytest.mark.parametrize(
    "kind", ["unpaired", "missing_parent", "missing_event", "self_parent"]
)
def test_lineage_corruption_is_reported(db_session_factory, healthy, kind):
    other = create_healthy(db_session_factory)
    with db_session_factory.begin() as session:
        session.execute(text("SET LOCAL session_replication_role = replica"))
        person = session.get(Individual, healthy.individual_id)
        person.parent_individual_id = other.individual_id
        person.fork_event_id = healthy.genesis_event_id
        if kind == "unpaired":
            person.fork_event_id = None
        elif kind == "missing_parent":
            person.parent_individual_id = new_id()
        elif kind == "missing_event":
            person.fork_event_id = new_id()
        elif kind == "self_parent":
            person.parent_individual_id = healthy.individual_id
    with db_session_factory() as session:
        assert len(invariant(check(session), "individual_lineage")) == 1


def test_lineage_cycle_is_reported(db_session_factory, healthy):
    other = create_healthy(db_session_factory)
    with db_session_factory.begin() as session:
        first = session.get(Individual, healthy.individual_id)
        second = session.get(Individual, other.individual_id)
        first.parent_individual_id, first.fork_event_id = (
            other.individual_id,
            other.genesis_event_id,
        )
        second.parent_individual_id, second.fork_event_id = (
            healthy.individual_id,
            healthy.genesis_event_id,
        )
    with db_session_factory() as session:
        subjects = {
            finding.subject.id
            for finding in invariant(check(session), "individual_lineage")
        }
        assert subjects == {healthy.individual_id, other.individual_id}


@pytest.mark.parametrize("missing", [True, False])
def test_audit_event_must_exist_and_belong_to_individual(
    db_session_factory, healthy, missing
):
    other = create_healthy(db_session_factory)
    with db_session_factory.begin() as session:
        session.execute(text("SET LOCAL session_replication_role = replica"))
        row = AdminAudit(
            individual_id=healthy.individual_id,
            admin_principal_id=healthy.admin_principal_id,
            operation="test_corruption",
            target_kind="individual",
            target_id=healthy.individual_id,
            reason="Integrity fixture",
            before_state={},
            after_state={},
            created_at=NOW,
            event_id=new_id() if missing else other.genesis_event_id,
        )
        session.add(row)
        session.flush()
        audit_id = row.audit_id
    with db_session_factory() as session:
        findings = invariant(check(session), "audit_event_link")
        assert len(findings) == 1 and findings[0].subject.id == audit_id
        assert session.get(AdminAudit, audit_id) is not None


def test_orphan_content_is_reported_and_preserved(db_session_factory, healthy):
    orphan_id = new_id()
    with db_session_factory.begin() as session:
        session.execute(text("SET LOCAL session_replication_role = replica"))
        session.add(
            EventContent(
                event_id=orphan_id,
                content_type="text/plain",
                text="preserve evidence",
                sensitivity="internal",
                retention_class="history",
            )
        )
    with db_session_factory() as session:
        findings = invariant(check(session), "event_content_link")
        assert len(findings) == 1 and findings[0].subject.id == orphan_id
        assert session.get(EventContent, orphan_id).text == "preserve evidence"


def test_retired_runnable_helper_disagreement_is_reported(
    db_session_factory, healthy, monkeypatch
):
    from cognition.runtime import lifecycle

    with db_session_factory.begin() as session:
        session.get(Individual, healthy.individual_id).operational_status = "retired"
    with db_session_factory() as session:
        assert check(session).healthy
    monkeypatch.setattr(lifecycle, "is_runnable", lambda status: True)
    with db_session_factory() as session:
        assert len(invariant(check(session), "retired_not_runnable")) == 1


def test_checker_does_not_flush_pending_changes(db_session_factory, healthy):
    with db_session_factory() as session:
        person = session.get(Individual, healthy.individual_id)
        person.birth_name = "Uncommitted caller change"
        assert check(session).healthy
        assert person in session.dirty
        with db_session_factory() as observer:
            assert (
                observer.get(Individual, healthy.individual_id).birth_name
                == "Integrity test"
            )


@pytest.mark.parametrize("corrupt,expected", [(False, 0), (True, 1)])
def test_cli_reports_json_and_exit_status(
    db_session_factory, db_engine, healthy, monkeypatch, capsys, corrupt, expected
):
    from cognition.cli.commands import check as cli

    if corrupt:
        with db_session_factory.begin() as session:
            session.delete(session.get(GovernanceState, healthy.individual_id))
    monkeypatch.setenv(
        "COGNITION_DATABASE_URL", "postgresql+psycopg://secret-for-connector"
    )
    seen = []

    def engine(url):
        seen.append(url)
        return db_engine

    monkeypatch.setattr(cli, "create_db_engine", engine)
    parser = argparse.ArgumentParser()
    cli.add_check_parser(parser.add_subparsers())
    args = parser.parse_args(["check"])
    assert args.handler(args) == expected
    assert seen == ["postgresql+psycopg://secret-for-connector"]
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report["healthy"] is not corrupt
    assert report["not_applicable"] == ["claimed_wake_cycle"]
    assert "secret-for-connector" not in output.out + output.err


def test_cli_missing_url_fails_without_credentials(monkeypatch, capsys):
    from cognition.cli.commands.check import handle_check

    monkeypatch.delenv("COGNITION_DATABASE_URL", raising=False)
    assert handle_check(argparse.Namespace()) == 2
    assert "COGNITION_DATABASE_URL" in capsys.readouterr().err


def test_cli_invalid_url_does_not_echo_credentials(monkeypatch, capsys):
    from cognition.cli.commands.check import handle_check

    monkeypatch.setenv(
        "COGNITION_DATABASE_URL", "postgresql://user:private-password@host/database"
    )
    assert handle_check(argparse.Namespace()) == 2
    captured = capsys.readouterr()
    assert "private-password" not in captured.out + captured.err
    assert "could not complete" in captured.err
