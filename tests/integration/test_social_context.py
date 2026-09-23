"""Owned social and project state remains inspectable without mutation."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from cognition.config.loader import load_config
from cognition.db.models.personal import Entity, PersonalStateRevision, Project
from cognition.protocols.common import new_id
from cognition.runtime.birth import BirthInput, birth
from cognition.stores.personal import (
    create_entity,
    create_project,
    personal_context_sections,
    revise_project,
)
from cognition.testing.clock import FakeClock

NOW = datetime(2026, 9, 22, tzinfo=UTC)


@pytest.fixture
def social_born(db_session_factory):
    config = load_config(Path(__file__).parents[1] / "fixtures/config/valid.toml")
    config.runtime.individual_id = new_id()
    return birth(
        db_session_factory,
        BirthInput(
            individual_id=config.runtime.individual_id,
            birth_name="Social context",
            founding_orientation="Remember people and chosen projects",
            creator_provenance={},
            admin_authn_provider="local_os",
            admin_subject="context-test",
            config=config,
            runtime_version="test",
        ),
        FakeClock(NOW),
    )


@pytest.mark.parametrize("kind", ["entity", "project"])
def test_directory_context_is_owned_bounded_and_deterministic(
    db_session_factory, social_born, kind
):
    identifiers = []
    with db_session_factory.begin() as session:
        for ordinal in range(10):
            args = dict(now=NOW + timedelta(seconds=ordinal))
            if kind == "entity":
                identity = create_entity(
                    session,
                    social_born.individual_id,
                    kind="person",
                    display_name=f"Person {ordinal}",
                    **args,
                )
            else:
                identity = create_project(
                    session,
                    social_born.individual_id,
                    title=f"Project {ordinal}",
                    desired_state="Understand",
                    rationale="Chosen",
                    evidence_refs=[],
                    **args,
                )
            identifiers.append(identity)
    with db_session_factory.begin() as session:
        before = session.scalar(select(func.count()).select_from(PersonalStateRevision))
        sections = personal_context_sections(session, social_born.individual_id)
        selected = [s for s in sections if s.name.startswith(f"Personal {kind} ")]
        assert [s.refs[0].id for s in selected] == list(reversed(identifiers[-8:]))
        assert all(s.category != "control" for s in selected)
        assert personal_context_sections(session, new_id()) == []
        assert personal_context_sections(session, social_born.individual_id) == sections
        assert (
            session.scalar(select(func.count()).select_from(PersonalStateRevision))
            == before
        )


def test_directory_reads_stored_projection_without_flushing_dirty_session(
    db_session_factory, social_born
):
    with db_session_factory.begin() as session:
        entity_id = create_entity(
            session,
            social_born.individual_id,
            kind="person",
            display_name="Stored person",
            now=NOW,
        )
        project_id = create_project(
            session,
            social_born.individual_id,
            title="Stored project",
            desired_state="Understand",
            rationale="Chosen",
            evidence_refs=[],
            now=NOW,
        )
    with db_session_factory.begin() as session:
        entity = session.get(Entity, entity_id)
        project = session.get(Project, project_id)
        entity.display_name = "Uncommitted person"
        project.title = "Uncommitted project"
        sections = personal_context_sections(session, social_born.individual_id)
        rendered = " ".join(s.model_dump_json() for s in sections)
        assert "Stored person" in rendered and "Stored project" in rendered
        assert "Uncommitted" not in rendered
        assert entity in session.dirty and project in session.dirty
        session.rollback()


def test_active_project_precedes_newer_paused_and_terminal_project_is_omitted(
    db_session_factory, social_born
):
    with db_session_factory.begin() as session:
        ids = []
        for status in ("active", "paused", "active"):
            ids.append(
                create_project(
                    session,
                    social_born.individual_id,
                    title=status,
                    desired_state="Understand",
                    rationale="Chosen",
                    evidence_refs=[],
                    now=NOW + timedelta(seconds=len(ids)),
                    status=status,
                )
            )
        revise_project(
            session,
            social_born.individual_id,
            ids[2],
            rationale="Finished",
            evidence_refs=[],
            now=NOW + timedelta(seconds=3),
            status="completed",
        )
    with db_session_factory() as session:
        sections = personal_context_sections(session, social_born.individual_id)
        selected = [s for s in sections if s.name.startswith("Personal project ")]
        assert [s.refs[0].id for s in selected] == ids[:2]
