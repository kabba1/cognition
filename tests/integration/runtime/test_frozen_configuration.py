"""A frozen request's version and linked configuration cannot be reinterpreted."""

from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm.attributes import flag_modified
from test_cognition import NOW, response, use_v2_config
from test_cognition import born as born
from test_cognition import owner as owner

from cognition.config.loader import load_config
from cognition.config.recording import reconcile_config
from cognition.db.models.cognition import (
    AppliedOperation,
    AttentionState,
    CognitionTurn,
    ContextSnapshot,
    ModelInvocation,
)
from cognition.protocols.common import new_id
from cognition.runtime.cognition import CognitionRuntime
from cognition.stores.cognition import canonical_json, content_hash
from cognition.testing.clock import FakeClock
from cognition.testing.scripted_model import ScriptedModelAdapter


def runtime(owner, born, model=None):
    return CognitionRuntime(owner, born.individual_id, model, FakeClock(NOW))


def replace_request(snapshot, value):
    snapshot.request_json = value
    # SQLAlchemy's Python equality treats True == 1; force the intended JSON type.
    flag_modified(snapshot, "request_json")
    snapshot.rendered_context = canonical_json(value)
    snapshot.content_hash = content_hash(value)


def freeze(owner, born):
    outcome = runtime(owner, born).run_once()
    assert outcome.status == "blocked" and outcome.reason == "model_unavailable"


def relink_config(factory, born):
    config = load_config(Path(__file__).parents[2] / "fixtures/config/valid.toml")
    config.runtime.individual_id = born.individual_id
    config.model.max_output_tokens += 1
    revision = reconcile_config(
        factory, born.individual_id, config, FakeClock(NOW)
    ).revision
    with factory.begin() as session:
        snapshot = session.scalar(select(ContextSnapshot))
        assert snapshot.config_revision_id != revision.config_revision_id
        snapshot.config_revision_id = revision.config_revision_id


def assert_blocked_without_attempt(owner, born, factory):
    model = ScriptedModelAdapter([response])
    outcome = runtime(owner, born, model).run_once()
    assert outcome.status == "blocked"
    assert outcome.reason == "incompatible_executive_contract"
    assert model.requests == ()
    with factory() as session:
        assert session.scalar(select(CognitionTurn)).status == "prepared"
        assert session.scalar(select(func.count()).select_from(ModelInvocation)) == 0
        assert session.get(AttentionState, born.individual_id) is None


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("fault", ["other_protocol", "bool_protocol"])
def test_nested_frozen_protocol_mismatch_blocks_without_coercion_or_attempt(
    owner, born, db_session_factory, version, fault
):
    if version == 2:
        use_v2_config(db_session_factory, born.individual_id)
    freeze(owner, born)
    with db_session_factory.begin() as session:
        snapshot = session.scalar(select(ContextSnapshot))
        value = deepcopy(snapshot.request_json)
        value["cognition_protocol_version"] = (
            3 - version if fault == "other_protocol" else True
        )
        replace_request(snapshot, value)
    assert_blocked_without_attempt(owner, born, db_session_factory)


@pytest.mark.parametrize("contract", ["2.0", "3.0", "3.1"])
def test_frozen_request_cannot_be_relinked_to_different_same_protocol_configuration(
    owner, born, db_session_factory, monkeypatch, contract
):
    from cognition.runtime import context

    monkeypatch.setattr(context, "RUNTIME_CONTRACT_VERSION", contract)
    freeze(owner, born)
    relink_config(db_session_factory, born)
    assert_blocked_without_attempt(owner, born, db_session_factory)


@pytest.mark.parametrize(
    "fault", ["missing", "duplicate", "category", "content", "id", "hash"]
)
def test_embedded_runtime_configuration_requires_one_exact_control_record(
    owner, born, db_session_factory, fault
):
    freeze(owner, born)
    with db_session_factory.begin() as session:
        snapshot = session.scalar(select(ContextSnapshot))
        value = deepcopy(snapshot.request_json)
        sections = value["context_sections"]
        control = next(
            section for section in sections if section["name"] == "runtime_control"
        )
        if fault == "missing":
            sections.remove(control)
        elif fault == "duplicate":
            sections.append(deepcopy(control))
        elif fault == "category":
            control["category"] = "evidence"
        elif fault == "content":
            control["content"] = "No configuration linkage"
        elif fault == "id":
            control["content"]["config_revision_id"] = str(new_id())
        else:
            control["content"]["config_content_hash"] = "0" * 64
        replace_request(snapshot, value)
    assert_blocked_without_attempt(owner, born, db_session_factory)


@pytest.mark.parametrize("fault", ["nested_version", "config_link"])
def test_committed_decision_stays_unapplied_when_its_frozen_contract_is_corrupt(
    owner, born, db_session_factory, fault
):
    original_model = ScriptedModelAdapter([response])

    def interrupt_apply(*args):
        raise RuntimeError("process lost after decision commit")

    event.listen(AttentionState, "before_insert", interrupt_apply)
    try:
        with pytest.raises(RuntimeError, match="process lost"):
            runtime(owner, born, original_model).run_once()
    finally:
        event.remove(AttentionState, "before_insert", interrupt_apply)
    with db_session_factory.begin() as session:
        turn = session.scalar(select(CognitionTurn))
        assert turn.status == "decided"
        retained = deepcopy(turn.decision_json)
        digest = turn.decision_hash
        if fault == "nested_version":
            snapshot = session.scalar(select(ContextSnapshot))
            value = deepcopy(snapshot.request_json)
            value["cognition_protocol_version"] = 2
            replace_request(snapshot, value)
    if fault == "config_link":
        relink_config(db_session_factory, born)
    model = ScriptedModelAdapter([])
    outcome = runtime(owner, born, model).run_once()
    assert outcome.status == "blocked"
    assert outcome.reason == "incompatible_executive_contract"
    assert model.requests == () and len(original_model.requests) == 1
    with db_session_factory() as session:
        turn = session.scalar(select(CognitionTurn))
        assert turn.status == "decided"
        assert turn.decision_json == retained and turn.decision_hash == digest
        assert session.get(AttentionState, born.individual_id) is None
        assert session.scalar(select(func.count()).select_from(ModelInvocation)) == 1
        assert session.scalar(select(func.count()).select_from(AppliedOperation)) == 0


def test_rendered_boolean_cannot_equal_stored_integer_version(
    owner, born, db_session_factory
):
    freeze(owner, born)
    with db_session_factory.begin() as session:
        snapshot = session.scalar(select(ContextSnapshot))
        rendered = deepcopy(snapshot.request_json)
        rendered["schema_version"] = True
        snapshot.rendered_context = canonical_json(rendered)
        snapshot.content_hash = content_hash(rendered)
    model = ScriptedModelAdapter([response])
    with pytest.raises(ValueError, match="Persisted request does not match"):
        runtime(owner, born, model).run_once()
    assert model.requests == ()
    with db_session_factory() as session:
        assert session.scalar(select(CognitionTurn)).status == "prepared"
        assert session.scalar(select(func.count()).select_from(ModelInvocation)) == 0
