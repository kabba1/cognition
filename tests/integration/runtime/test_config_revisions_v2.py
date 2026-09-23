"""Protocol selection changes use the existing durable configuration pipeline."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from cognition.config.loader import load_config
from cognition.config.recording import reconcile_config
from cognition.config.revisions import behavior_config, behavior_hash
from cognition.config.schema import BehaviorConfigV2, parse_config
from cognition.db.models.evidence import Event, EventContent
from cognition.db.models.runtime import RuntimeConfigRevision
from cognition.stores.configuration import get_active_config, replace_config_revision
from cognition.stores.identity import create_individual
from cognition.testing.clock import FakeClock

NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)
CONFIG = Path(__file__).parents[2] / "fixtures/config/valid.toml"


@pytest.fixture
def configs(db_session_factory):
    v1 = load_config(CONFIG)
    data = v1.model_dump()
    data.update(config_schema_version=2, execution={"cognition_protocol_version": 2})
    v2 = parse_config(data)
    with db_session_factory.begin() as session:
        create_individual(
            session,
            individual_id=v1.runtime.individual_id,
            birth_at=NOW,
            birth_name="Explicit executive",
            founding_orientation="Keep contracts stable",
            creator_provenance={},
        )
    return v1, v2


def test_switch_to_v2_records_selection_evidence_and_preserves_v1_json(
    db_session_factory, configs
):
    v1, v2 = configs
    first = reconcile_config(
        db_session_factory, v1.runtime.individual_id, v1, FakeClock(NOW)
    )
    old_json = first.revision.sanitized_config.model_dump(mode="json")
    second = reconcile_config(
        db_session_factory,
        v2.runtime.individual_id,
        v2,
        FakeClock(NOW + timedelta(seconds=1)),
    )
    assert second.changed and second.revision.config_schema_version == 2
    assert isinstance(second.revision.sanitized_config, BehaviorConfigV2)
    assert (
        second.revision.content_hash == behavior_hash(v2) != first.revision.content_hash
    )
    with db_session_factory() as session:
        old = session.get(RuntimeConfigRevision, first.revision.config_revision_id)
        assert old.sanitized_config == old_json
        assert old.content_hash == first.revision.content_hash
        assert old.superseded_at == NOW + timedelta(seconds=1)
        active = get_active_config(session, v2.runtime.individual_id)
        assert active == second.revision
        event = session.get(Event, second.event_id)
        payload = session.get(EventContent, second.event_id).payload
        assert event.event_type == "config.changed"
        assert event.subject_id == second.revision.config_revision_id
        assert payload["config_schema_version"] == 2
        assert payload["content_hash"] == second.revision.content_hash
    again = reconcile_config(
        db_session_factory,
        v2.runtime.individual_id,
        v2,
        FakeClock(NOW + timedelta(seconds=2)),
    )
    assert not again.changed and again.event_id is None
    assert again.revision == second.revision


def test_v2_evidence_failure_rolls_back_selection_and_keeps_v1_active(
    db_session_factory, configs, monkeypatch
):
    from cognition.config import recording

    v1, v2 = configs
    first = reconcile_config(
        db_session_factory, v1.runtime.individual_id, v1, FakeClock(NOW)
    )
    real_append = recording.append_event

    def fail_after_event(session, envelope):
        real_append(session, envelope)
        raise RuntimeError("injected after evidence")

    monkeypatch.setattr(recording, "append_event", fail_after_event)
    with pytest.raises(RuntimeError, match="injected"):
        reconcile_config(
            db_session_factory,
            v2.runtime.individual_id,
            v2,
            FakeClock(NOW + timedelta(seconds=1)),
        )
    with db_session_factory() as session:
        assert get_active_config(session, v1.runtime.individual_id) == first.revision
        assert len(session.scalars(select(RuntimeConfigRevision)).all()) == 1
        assert len(session.scalars(select(Event)).all()) == 1


def test_mutated_v2_selection_is_rejected_before_any_supersession(
    db_session_factory, configs
):
    v1, v2 = configs
    first = reconcile_config(
        db_session_factory, v1.runtime.individual_id, v1, FakeClock(NOW)
    )
    invalid = behavior_config(v2)
    invalid.execution.cognition_protocol_version = 1
    with db_session_factory.begin() as session:
        with pytest.raises(ValueError):
            replace_config_revision(
                session, v1.runtime.individual_id, invalid, NOW + timedelta(seconds=1)
            )
    with db_session_factory() as session:
        assert get_active_config(session, v1.runtime.individual_id) == first.revision
        assert len(session.scalars(select(RuntimeConfigRevision)).all()) == 1
