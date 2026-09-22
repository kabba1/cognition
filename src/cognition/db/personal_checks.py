"""Read-only personal-state ownership and revision diagnostics."""

import json
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from cognition.db.models.attention import Wake
from cognition.db.models.cognition import (
    AppliedOperation,
    CognitionCycle,
    CognitionTurn,
)
from cognition.db.models.evidence import Event
from cognition.db.models.governance import GovernanceState
from cognition.db.models.identity import Individual
from cognition.db.models.personal import (
    Belief,
    Commitment,
    Entity,
    Episode,
    Goal,
    PersonalStateRevision,
    Project,
)
from cognition.protocols.common import JsonObject, Ref, normalize_utc


def _json_default(value: object) -> str:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return normalize_utc(value).isoformat()
    raise TypeError("Unsupported stored value")


def _snapshot(row: RowMapping) -> JsonObject:
    return cast(JsonObject, json.loads(json.dumps(dict(row), default=_json_default)))


def check_personal_state(
    session: Session, error: Callable[[str, str, UUID, str], None]
) -> None:
    """Use column snapshots so diagnostics never flush or overwrite ORM state."""
    with session.no_autoflush:
        _check_personal_state(session, error)


def _check_personal_state(
    session: Session, error: Callable[[str, str, UUID, str], None]
) -> None:
    tables = {
        "entity": (Entity.__table__, "entity_id"),
        "project": (Project.__table__, "project_id"),
        "goal": (Goal.__table__, "goal_id"),
        "commitment": (Commitment.__table__, "commitment_id"),
        "belief": (Belief.__table__, "belief_id"),
        "episode": (Episode.__table__, "episode_id"),
    }
    objects: dict[tuple[str, UUID], RowMapping] = {}
    for kind, (table, identity_column) in tables.items():
        for row in session.execute(select(table)).mappings():
            objects[(kind, row[identity_column])] = row
    owners: dict[tuple[str, UUID], UUID] = {
        key: row.individual_id for key, row in objects.items()
    }
    for kind, model, column in (
        ("event", Event, Event.event_id),
        ("wake", Wake, Wake.wake_id),
        ("cycle", CognitionCycle, CognitionCycle.cycle_id),
        ("individual", Individual, Individual.individual_id),
        ("governance", GovernanceState, GovernanceState.individual_id),
    ):
        for identity, individual_id in session.execute(
            select(column, model.individual_id)
        ).tuples():
            owners[(kind, identity)] = individual_id

    for (kind, identity), row in objects.items():
        links = {
            "goal": (("project", "project_id"),),
            "commitment": (("entity", "counterparty_entity_id"),),
            "belief": (
                ("entity", "subject_entity_id"),
                ("belief", "supersedes_belief_id"),
            ),
        }.get(kind, ())
        for target_kind, field in links:
            target_id = row[field]
            if (
                target_id is not None
                and owners.get((target_kind, target_id)) != row.individual_id
            ):
                error(
                    "personal_ownership",
                    kind,
                    identity,
                    "Personal link is missing or belongs to another individual.",
                )
        evidence_fields = (
            ("supporting_evidence", "contradicting_evidence")
            if kind == "belief"
            else ("evidence_refs",)
        )
        for field in evidence_fields:
            if field not in row:
                continue
            try:
                if not isinstance(row[field], list):
                    raise ValueError("Expected references")
                refs = [Ref.model_validate(value) for value in row[field]]
                if any(
                    owners.get((ref.kind, ref.id)) != row.individual_id for ref in refs
                ):
                    raise ValueError("Unscoped reference")
            except (ValueError, TypeError):
                error(
                    "personal_evidence",
                    kind,
                    identity,
                    "Personal evidence references are invalid, missing, or unscoped.",
                )
        if kind == "episode":
            for target_kind, field in (
                ("entity", "entity_refs"),
                ("project", "project_refs"),
            ):
                try:
                    if not isinstance(row[field], list):
                        raise ValueError("Expected links")
                    if any(
                        owners.get((target_kind, UUID(value))) != row.individual_id
                        for value in row[field]
                    ):
                        raise ValueError("Unscoped link")
                except (ValueError, TypeError, AttributeError):
                    error(
                        "personal_ownership",
                        kind,
                        identity,
                        "Episode links are invalid, missing, or unscoped.",
                    )
        if kind == "belief" and row.supersedes_belief_id is not None:
            previous = objects.get(("belief", row.supersedes_belief_id))
            if (
                previous is None
                or previous.status != "superseded"
                or row.supersedes_belief_id == identity
            ):
                error(
                    "belief_supersession",
                    kind,
                    identity,
                    "Belief supersession lacks a distinct superseded predecessor.",
                )

    histories: dict[tuple[str, UUID], list[RowMapping]] = defaultdict(list)
    operations = {
        row.operation_id: row
        for row in session.execute(select(AppliedOperation.__table__)).mappings()
    }
    turns = {
        row.turn_id: row.cycle_id
        for row in session.execute(select(CognitionTurn.__table__)).mappings()
    }
    for history in session.execute(select(PersonalStateRevision.__table__)).mappings():
        key = (history.object_kind, history.object_id)
        histories[key].append(history)
        invalid = (
            owners.get(key) != history.individual_id
            or owners.get(("event", history.event_id)) != history.individual_id
        )
        if history.turn_id is not None:
            cycle_id = turns.get(history.turn_id)
            invalid |= (
                cycle_id is None
                or owners.get(("cycle", cycle_id)) != history.individual_id
            )
        if history.operation_id is not None:
            operation = operations.get(history.operation_id)
            invalid |= (
                operation is None
                or operation.individual_id != history.individual_id
                or operation.turn_id != history.turn_id
            )
        if invalid:
            error(
                "personal_revision_provenance",
                "personal_revision",
                history.revision_id,
                "Revision lacks consistent object, evidence, or operation ownership.",
            )
    for (kind, identity), row in objects.items():
        chain = sorted(histories[(kind, identity)], key=lambda item: item.revision)
        prior: JsonObject | None = None
        invalid_chain = len(chain) != row.revision
        for ordinal, history in enumerate(chain, 1):
            invalid_chain |= history.revision != ordinal or history.before_json != prior
            if not isinstance(history.after_json, dict):
                invalid_chain = True
            prior = history.after_json
        if invalid_chain:
            error(
                "personal_revision_chain",
                kind,
                identity,
                "Personal history does not form a complete ordered revision chain.",
            )
        if chain and chain[-1].after_json != _snapshot(row):
            error(
                "personal_projection",
                kind,
                identity,
                "Current personal projection differs from its latest revision.",
            )
