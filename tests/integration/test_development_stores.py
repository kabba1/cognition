"""Development requires elapsed time, deliberate attention, and original anchors."""

import importlib
import json
from datetime import timedelta, timezone

import pytest
from sqlalchemy import func, select
from test_personal_stores import GOLDEN, NOW, decision, operation, persist, store
from test_personal_stores import state as state

from cognition.db.models.attention import Wake
from cognition.db.models.cognition import AppliedOperation, CycleWake
from cognition.db.models.evidence import Event
from cognition.db.models.identity import Individual
from cognition.db.models.personal import PersonalStateRevision
from cognition.protocols.common import new_id
from cognition.protocols.events_v1 import EventEnvelopeV1
from cognition.stores.evidence import append_event

DAY = timedelta(days=1)


def models():
    return importlib.import_module("cognition.db.models.development")


def proposal(kind, **updates):
    value = json.loads(GOLDEN.read_text())[f"{kind}_operations"][0]
    value["operation_id"] = str(new_id())
    value.update(updates)
    return value


def apply(factory, state, value, now=NOW):
    persist(factory, state, value)
    with factory.begin() as session:
        store().apply_personal_operations(session, state[0].individual_id, value, now)


def errors(factory, state, value, now=NOW):
    with factory.begin() as session:
        return store().validate_personal_operations(
            session, state[0].individual_id, value, now
        )


def observation(
    factory, state, when, source="connector", event_type="observation.received"
):
    identity = new_id()
    with factory.begin() as session:
        append_event(
            session,
            EventEnvelopeV1.model_validate(
                {
                    "schema_version": 1,
                    "event_id": identity,
                    "individual_id": state[0].individual_id,
                    "event_type": event_type,
                    "occurred_at": when,
                    "observed_at": when,
                    "recorded_at": when,
                    "source": {
                        "kind": source,
                        "source_id": "fixture",
                        "binding_id": None,
                    },
                    "actor_entity_id": None,
                    "causation_event_id": None,
                    "correlation_id": None,
                    "subject": None,
                    "provenance": {},
                    "runtime_version": "test",
                    "content": {
                        "content_type": "text/plain",
                        "payload": None,
                        "text": "Observed",
                        "blob_ref": None,
                        "content_hash": None,
                        "sensitivity": "internal",
                        "retention_class": "history",
                        "retain_until": None,
                    },
                }
            ),
        )
    return {"kind": "event", "id": str(identity)}


def attention(factory, state, kind="reflection", refs=None):
    with factory.begin() as session:
        wake_id = session.scalar(
            select(CycleWake.wake_id).where(CycleWake.cycle_id == state[1].cycle_id)
        )
        row = session.get(Wake, wake_id)
        row.kind = kind
        row.context_refs = refs or []


def create(factory, state, kind, refs=None, now=NOW):
    value = proposal(kind, evidence_refs=refs or [])
    apply(factory, state, decision(state, **{f"{kind}_operations": [value]}), now)
    return value["operation_id"]


def transition(state, kind, identity, op, refs=None, **updates):
    fields = (
        {"topic": None, "summary": None}
        if kind == "interest"
        else {"context": None, "statement": None}
    )
    fields.update(updates)
    return decision(
        state,
        **{
            f"{kind}_operations": [
                proposal(
                    kind,
                    op=op,
                    **{f"{kind}_id": identity},
                    evidence_refs=refs or [],
                    **fields,
                )
            ]
        },
    )


@pytest.mark.parametrize(
    "kind,initial", [("interest", "candidate"), ("preference", "tentative")]
)
def test_first_utterance_stays_tentative_and_freezes_eligibility(
    db_session_factory, state, kind, initial
):
    identity = create(db_session_factory, state, kind)
    with db_session_factory() as session:
        row = session.get(getattr(models(), kind.title()), identity)
        assert row.status == initial
        assert row.promotion_not_before == NOW + DAY
        assert row.retirement_not_before is None
        history = session.scalar(
            select(PersonalStateRevision).where(
                PersonalStateRevision.object_kind == kind
            )
        )
        assert history.before_json is None and history.after_json["status"] == initial
        assert (
            session.get(Event, history.event_id).provenance[
                "development_policy_version"
            ]
            == 1
        )


@pytest.mark.parametrize("kind", ["interest", "preference"])
@pytest.mark.parametrize(
    "case",
    ["too_young", "no_reflection", "duplicate", "too_close", "future", "wrong_wake"],
)
def test_promotion_requires_time_reflection_and_two_distinct_separated_anchors(
    db_session_factory, state, kind, case
):
    first = observation(db_session_factory, state, NOW)
    second_time = NOW + DAY if case != "too_close" else NOW + timedelta(hours=23)
    if case == "future":
        second_time = NOW + 3 * DAY
    second = observation(db_session_factory, state, second_time, "capability")
    identity = create(db_session_factory, state, kind, [first])
    attention(
        db_session_factory,
        state,
        "external_event"
        if case == "wrong_wake"
        else "bootstrap"
        if case == "no_reflection"
        else "reflection",
    )
    value = transition(
        state, kind, identity, "establish", [first if case == "duplicate" else second]
    )
    now = NOW + timedelta(hours=23) if case == "too_young" else NOW + DAY
    assert errors(db_session_factory, state, value, now)
    with pytest.raises(ValueError):
        apply(db_session_factory, state, value, now)
    with db_session_factory() as session:
        assert session.get(getattr(models(), kind.title()), identity).revision == 1


@pytest.mark.parametrize("kind", ["interest", "preference"])
@pytest.mark.parametrize("wake", ["reflection", "self_scheduled"])
def test_reflective_establishment_unions_anchors(db_session_factory, state, kind, wake):
    first = observation(db_session_factory, state, NOW)
    second = observation(db_session_factory, state, NOW + DAY)
    identity = create(db_session_factory, state, kind, [first])
    attention(db_session_factory, state, wake, [{"kind": kind, "id": identity}])
    apply(
        db_session_factory,
        state,
        transition(state, kind, identity, "establish", [second]),
        NOW + DAY,
    )
    with db_session_factory() as session:
        row = session.get(getattr(models(), kind.title()), identity)
        assert row.status == "established" and row.evidence_refs == [first, second]
        assert row.revision == 2
        if kind == "preference":
            assert row.retirement_not_before == NOW + 8 * DAY


@pytest.mark.parametrize(
    "source,event_type",
    [
        ("admin", "observation"),
        ("import", "observation"),
        ("runtime", "observation"),
        ("model", "personal.interest.establish"),
        ("model", "personal.preference.establish"),
        ("model", "personal.self_state.propose_revision"),
        ("model", "personal.belief.create"),
        ("model", "cognition.decision_recorded"),
        ("model", "personal.goal.create"),
    ],
)
def test_grounding_allowlist_excludes_feedback_and_forged_choice_events(
    db_session_factory, state, source, event_type
):
    refs = [
        observation(db_session_factory, state, NOW, source, event_type),
        observation(db_session_factory, state, NOW + DAY, source, event_type),
    ]
    identity = create(db_session_factory, state, "interest")
    attention(db_session_factory, state)
    assert errors(
        db_session_factory,
        state,
        transition(state, "interest", identity, "establish", refs),
        NOW + DAY,
    )


def test_actual_goal_choices_can_ground_reflection(db_session_factory, state):
    anchors = []
    for when in (NOW, NOW + DAY):
        goal = operation("goal", state)
        apply(db_session_factory, state, decision(state, goal_operations=[goal]), when)
        with db_session_factory() as session:
            revision = session.scalar(
                select(PersonalStateRevision).where(
                    PersonalStateRevision.operation_id == goal["operation_id"]
                )
            )
            anchors.append({"kind": "event", "id": str(revision.event_id)})
    identity = create(db_session_factory, state, "interest", now=NOW)
    attention(db_session_factory, state)
    apply(
        db_session_factory,
        state,
        transition(state, "interest", identity, "establish", anchors),
        NOW + DAY,
    )


def test_episode_expansion_does_not_turn_old_evidence_into_new_anchor(
    db_session_factory, state
):
    first = observation(db_session_factory, state, NOW)
    second = observation(db_session_factory, state, NOW + DAY)
    identity = create(db_session_factory, state, "interest", [first])
    attention(db_session_factory, state)
    apply(
        db_session_factory,
        state,
        transition(state, "interest", identity, "establish", [second]),
        NOW + DAY,
    )
    apply(
        db_session_factory,
        state,
        transition(state, "interest", identity, "set_dormant", [first]),
        NOW + DAY,
    )
    episode = operation("episode", state, evidence_refs=[first, second])
    apply(
        db_session_factory,
        state,
        decision(state, episode_operations=[episode]),
        NOW + 2 * DAY,
    )
    wrapper = {"kind": "episode", "id": episode["operation_id"]}
    assert errors(
        db_session_factory,
        state,
        transition(state, "interest", identity, "establish", [wrapper]),
        NOW + 2 * DAY,
    )
    third = observation(db_session_factory, state, NOW + 2 * DAY)
    apply(
        db_session_factory,
        state,
        transition(state, "interest", identity, "establish", [third]),
        NOW + 2 * DAY,
    )


@pytest.mark.parametrize("kind", ["interest", "preference"])
def test_retirement_requires_frozen_week_reflection_and_new_anchor(
    db_session_factory, state, kind
):
    anchors = [
        observation(db_session_factory, state, NOW),
        observation(db_session_factory, state, NOW + DAY),
    ]
    identity = create(db_session_factory, state, kind, [anchors[0]])
    attention(db_session_factory, state)
    apply(
        db_session_factory,
        state,
        transition(state, kind, identity, "establish", anchors),
        NOW + DAY,
    )
    if kind == "interest":
        assert errors(
            db_session_factory,
            state,
            transition(state, kind, identity, "retire", anchors),
            NOW + 10 * DAY,
        )
        apply(
            db_session_factory,
            state,
            transition(state, kind, identity, "set_dormant", [anchors[0]]),
            NOW + DAY,
        )
    third = observation(db_session_factory, state, NOW + 2 * DAY)
    assert errors(
        db_session_factory,
        state,
        transition(state, kind, identity, "retire", [third]),
        NOW + 7 * DAY,
    )
    assert errors(
        db_session_factory,
        state,
        transition(state, kind, identity, "retire", anchors),
        NOW + 8 * DAY,
    )
    attention(db_session_factory, state, "bootstrap")
    assert errors(
        db_session_factory,
        state,
        transition(state, kind, identity, "retire", [third]),
        NOW + 8 * DAY,
    )
    attention(db_session_factory, state)
    apply(
        db_session_factory,
        state,
        transition(state, kind, identity, "retire", [third]),
        NOW + 8 * DAY,
    )
    assert errors(
        db_session_factory,
        state,
        transition(state, kind, identity, "establish", [third]),
        NOW + 9 * DAY,
    )


@pytest.mark.parametrize("layer", ["self_belief", "current_value"])
def test_inferred_self_proposal_is_pending_until_deliberate_separated_evidence(
    db_session_factory, state, layer
):
    first = observation(db_session_factory, state, NOW)
    second = observation(db_session_factory, state, NOW + DAY)
    proposed = proposal(
        "self_model",
        layer=layer,
        proposed_content="An inference",
        evidence_refs=[first],
    )
    apply(db_session_factory, state, decision(state, self_model_operations=[proposed]))
    with db_session_factory() as session:
        row = session.scalar(select(models().SelfState))
        assert row.content is None and row.pending_content == {"value": "An inference"}
        assert row.pending_not_before == NOW + DAY
    repeat = proposal(
        "self_model",
        layer=layer,
        proposed_content="An inference",
        evidence_refs=[second],
    )
    attention(db_session_factory, state)
    apply(
        db_session_factory,
        state,
        decision(state, self_model_operations=[repeat]),
        NOW + DAY,
    )
    with db_session_factory() as session:
        row = session.scalar(select(models().SelfState))
        assert row.content == {"value": "An inference"}
        assert row.pending_content is None and row.pending_not_before is None
        assert row.evidence_refs == [first, second]


def test_pending_repeat_preserves_deadline_and_replacement_resets_only_pending_claim(
    db_session_factory, state
):
    first = observation(db_session_factory, state, NOW)
    second = observation(db_session_factory, state, NOW + timedelta(hours=1))
    proposed = proposal(
        "self_model",
        layer="self_belief",
        proposed_content="First",
        evidence_refs=[first],
    )
    apply(db_session_factory, state, decision(state, self_model_operations=[proposed]))
    repeat = proposal(
        "self_model",
        layer="self_belief",
        proposed_content="First",
        evidence_refs=[second],
    )
    apply(
        db_session_factory,
        state,
        decision(state, self_model_operations=[repeat]),
        NOW + timedelta(hours=1),
    )
    with db_session_factory() as session:
        row = session.scalar(select(models().SelfState))
        assert row.pending_not_before == NOW + DAY
        assert row.pending_evidence_refs == [first, second]
    replacement = proposal(
        "self_model",
        layer="self_belief",
        proposed_content="Different",
        evidence_refs=[second],
    )
    apply(
        db_session_factory,
        state,
        decision(state, self_model_operations=[replacement]),
        NOW + timedelta(hours=2),
    )
    with db_session_factory() as session:
        row = session.scalar(select(models().SelfState))
        assert row.pending_not_before == NOW + DAY + timedelta(hours=2)
        assert row.pending_evidence_refs == [second]
        assert (
            session.scalar(select(func.count()).select_from(PersonalStateRevision)) == 3
        )


@pytest.mark.parametrize("layer", ["current_identity", "narrative"])
def test_immediate_subjective_self_layers_do_not_modify_genesis(
    db_session_factory, state, layer
):
    ref = {"kind": "event", "id": str(state[0].genesis_event_id)}
    value = proposal(
        "self_model",
        layer=layer,
        proposed_content="Subjective presentation",
        evidence_refs=[ref],
    )
    with db_session_factory() as session:
        birth_name = session.get(Individual, state[0].individual_id).birth_name
    apply(db_session_factory, state, decision(state, self_model_operations=[value]))
    with db_session_factory() as session:
        row = session.scalar(select(models().SelfState))
        assert (
            row.content == {"value": "Subjective presentation"}
            and row.pending_content is None
        )
        assert session.get(Individual, state[0].individual_id).birth_name == birth_name


@pytest.mark.parametrize(
    "case",
    [
        "blank_rationale",
        "blank_content",
        "empty_object",
        "duplicate_layer",
        "ungrounded_narrative",
    ],
)
def test_invalid_self_proposals_reject_whole_mixed_batch(
    db_session_factory, state, case
):
    value = proposal("self_model", layer="current_identity", proposed_content="Valid")
    if case == "blank_rationale":
        value["rationale"] = "  "
    elif case == "blank_content":
        value["proposed_content"] = " "
    elif case == "empty_object":
        value["proposed_content"] = {}
    elif case == "ungrounded_narrative":
        value.update(layer="narrative", evidence_refs=[])
    proposals = [value]
    if case == "duplicate_layer":
        proposals.append(dict(value, operation_id=str(new_id())))
    mixed = decision(
        state,
        goal_operations=[operation("goal", state)],
        self_model_operations=proposals,
    )
    assert errors(db_session_factory, state, mixed)
    with pytest.raises(ValueError):
        apply(db_session_factory, state, mixed)
    with db_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AppliedOperation)) == 0


def test_context_and_passive_retrieval_never_promote_and_preserve_deadlines(
    db_session_factory, state
):
    create(db_session_factory, state, "interest")
    pending = proposal("self_model", layer="current_value", proposed_content="Maybe")
    apply(db_session_factory, state, decision(state, self_model_operations=[pending]))
    with db_session_factory() as session:
        first = store().personal_context_sections(session, state[0].individual_id)
        second = store().personal_context_sections(session, state[0].individual_id)
        assert first == second and not session.dirty
        text = json.dumps([section.model_dump(mode="json") for section in first])
        assert "candidate" in text and "pending_not_before" in text
        assert session.scalar(select(models().Interest)).revision == 1
        assert session.scalar(select(models().SelfState)).content is None


def test_aware_non_utc_now_normalizes_before_temporal_gates(db_session_factory, state):
    first = observation(db_session_factory, state, NOW)
    second = observation(db_session_factory, state, NOW + DAY)
    identity = create(db_session_factory, state, "interest", [first])
    attention(db_session_factory, state)
    apply(
        db_session_factory,
        state,
        transition(state, "interest", identity, "establish", [second]),
        (NOW + DAY).astimezone(timezone(timedelta(hours=-5))),
    )


@pytest.mark.parametrize(
    "kind,status,op,allowed",
    [
        (kind, status, op, (status, op) in valid)
        for kind, states, ops, valid in (
            (
                "interest",
                ("candidate", "established", "dormant", "retired"),
                ("establish", "set_dormant", "retire"),
                {
                    ("candidate", "establish"),
                    ("candidate", "retire"),
                    ("established", "set_dormant"),
                    ("dormant", "establish"),
                    ("dormant", "retire"),
                },
            ),
            (
                "preference",
                ("tentative", "established", "retired"),
                ("establish", "retire"),
                {
                    ("tentative", "establish"),
                    ("tentative", "retire"),
                    ("established", "retire"),
                },
            ),
        )
        for status in states
        for op in ops
    ],
)
def test_development_transition_matrix(
    db_session_factory, state, kind, status, op, allowed
):
    old = observation(db_session_factory, state, NOW)
    new = observation(db_session_factory, state, NOW + DAY)
    identity = create(db_session_factory, state, kind, [old])
    with db_session_factory.begin() as session:
        row = session.get(getattr(models(), kind.title()), identity)
        row.status = status
        row.retirement_not_before = NOW + 7 * DAY
    attention(db_session_factory, state)
    value = transition(state, kind, identity, op, [new])
    assert (errors(db_session_factory, state, value, NOW + 8 * DAY) == ()) is allowed


@pytest.mark.parametrize(
    "kind,field",
    [
        ("interest", "topic"),
        ("interest", "summary"),
        ("preference", "context"),
        ("preference", "statement"),
    ],
)
def test_development_noncreate_fields_cannot_be_silently_ignored(
    db_session_factory, state, kind, field
):
    identity = create(db_session_factory, state, kind)
    value = transition(state, kind, identity, "retire", **{field: "incompatible"})
    assert "incompatible_operation_fields" in errors(db_session_factory, state, value)


@pytest.mark.parametrize("kind", ["interest", "preference"])
def test_candidate_can_retire_without_claiming_establishment(
    db_session_factory, state, kind
):
    identity = create(db_session_factory, state, kind)
    apply(db_session_factory, state, transition(state, kind, identity, "retire"))
    with db_session_factory() as session:
        assert (
            session.get(getattr(models(), kind.title()), identity).status == "retired"
        )


def test_episode_expansion_is_exactly_one_level(db_session_factory, state):
    anchors = [
        observation(db_session_factory, state, NOW),
        observation(db_session_factory, state, NOW + DAY),
    ]
    first = operation("episode", state, evidence_refs=anchors)
    apply(
        db_session_factory,
        state,
        decision(state, episode_operations=[first]),
        NOW + DAY,
    )
    first_ref = {"kind": "episode", "id": first["operation_id"]}
    second = operation("episode", state, evidence_refs=[first_ref])
    apply(
        db_session_factory,
        state,
        decision(state, episode_operations=[second]),
        NOW + DAY,
    )
    identity = create(db_session_factory, state, "preference")
    attention(db_session_factory, state)
    nested = transition(
        state,
        "preference",
        identity,
        "establish",
        [{"kind": "episode", "id": second["operation_id"]}],
    )
    assert "development_grounding_required" in errors(
        db_session_factory, state, nested, NOW + DAY
    )
    apply(
        db_session_factory,
        state,
        transition(state, "preference", identity, "establish", [first_ref]),
        NOW + DAY,
    )


def test_self_scheduled_reflection_requires_the_correct_target(
    db_session_factory, state
):
    first = observation(db_session_factory, state, NOW)
    second = observation(db_session_factory, state, NOW + DAY)
    identity = create(db_session_factory, state, "interest", [first])
    attention(
        db_session_factory,
        state,
        "self_scheduled",
        [{"kind": "interest", "id": str(new_id())}],
    )
    value = transition(state, "interest", identity, "establish", [second])
    assert "development_reflection_required" in errors(
        db_session_factory, state, value, NOW + DAY
    )


@pytest.mark.parametrize(
    "layer", ["current_identity", "narrative", "self_belief", "current_value"]
)
def test_replacing_self_content_never_relabels_old_claim_evidence(
    db_session_factory, state, layer
):
    old_refs = [
        observation(db_session_factory, state, NOW),
        observation(db_session_factory, state, NOW + DAY),
    ]
    new_refs = [
        observation(db_session_factory, state, NOW + 2 * DAY),
        observation(db_session_factory, state, NOW + 3 * DAY),
    ]
    attention(db_session_factory, state)
    inferred = layer in {"self_belief", "current_value"}
    first = proposal(
        "self_model", layer=layer, proposed_content="Old claim", evidence_refs=old_refs
    )
    apply(db_session_factory, state, decision(state, self_model_operations=[first]))
    if inferred:
        first["operation_id"] = str(new_id())
        apply(
            db_session_factory,
            state,
            decision(state, self_model_operations=[first]),
            NOW + DAY,
        )
    changed = proposal(
        "self_model", layer=layer, proposed_content="New claim", evidence_refs=new_refs
    )
    apply(
        db_session_factory,
        state,
        decision(state, self_model_operations=[changed]),
        NOW + 2 * DAY,
    )
    if inferred:
        with db_session_factory() as session:
            row = session.scalar(select(models().SelfState))
            assert (
                row.content == {"value": "Old claim"} and row.evidence_refs == old_refs
            )
            assert row.pending_content == {"value": "New claim"}
        changed["operation_id"] = str(new_id())
        apply(
            db_session_factory,
            state,
            decision(state, self_model_operations=[changed]),
            NOW + 3 * DAY,
        )
    with db_session_factory() as session:
        row = session.scalar(select(models().SelfState))
        assert row.content == {"value": "New claim"}
        assert row.evidence_refs == new_refs
        latest = session.scalar(
            select(PersonalStateRevision)
            .where(PersonalStateRevision.object_id == row.self_state_id)
            .order_by(PersonalStateRevision.revision.desc())
        )
        assert latest.before_json["evidence_refs"] == old_refs
        assert latest.after_json["evidence_refs"] == new_refs


@pytest.mark.parametrize(
    "case", ["early_unchanged", "late_no_reflection", "late_no_grounding"]
)
def test_repeated_pending_self_does_not_evade_reflection_or_grounding(
    db_session_factory, state, case
):
    first = observation(db_session_factory, state, NOW)
    second = observation(db_session_factory, state, NOW + DAY)
    initial = proposal(
        "self_model",
        layer="self_belief",
        proposed_content="Tentative",
        evidence_refs=[first],
    )
    apply(db_session_factory, state, decision(state, self_model_operations=[initial]))
    if case == "late_no_grounding":
        attention(db_session_factory, state)
    repeat = proposal(
        "self_model",
        layer="self_belief",
        proposed_content="Tentative",
        evidence_refs=[second] if case == "late_no_reflection" else [first],
    )
    now = NOW if case == "early_unchanged" else NOW + DAY
    assert errors(
        db_session_factory, state, decision(state, self_model_operations=[repeat]), now
    )


@pytest.mark.parametrize(
    "layer,delay",
    [
        ("current_identity", 0),
        ("narrative", 0),
        ("self_belief", 24),
        ("current_value", 24),
    ],
)
def test_context_exposes_layer_specific_gates_and_exact_reflection_target(
    db_session_factory, state, layer, delay
):
    value = proposal(
        "self_model",
        layer=layer,
        proposed_content="Interpretation",
        evidence_refs=[{"kind": "event", "id": str(state[0].genesis_event_id)}],
    )
    apply(db_session_factory, state, decision(state, self_model_operations=[value]))
    with db_session_factory() as session:
        section = next(
            item
            for item in store().personal_context_sections(
                session, state[0].individual_id
            )
            if item.refs[0].kind == "self_state"
        )
        policy = section.content["policy"]
        assert policy["new_proposal_delay_hours"] == delay
        assert section.content["self_scheduled_context_refs"] == [
            section.refs[0].model_dump(mode="json")
        ]
        grounding = section.content["grounding_policy"]
        assert grounding["episode_expansion_levels"] == 1
        assert grounding["eligible_sources"] == [
            "connector",
            "capability",
            "history-linked model goal/commitment choices",
        ]
