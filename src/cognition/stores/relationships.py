"""Owned social interpretations and open threads; no grants or outbound actions."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from cognition.db.models.personal import Commitment, Entity
from cognition.db.models.relationships import Relationship, RelationshipThread
from cognition.protocols.common import JsonObject, Ref, new_id, normalize_utc
from cognition.protocols.model_v1 import ContextSection
from cognition.stores.personal import _lock_individual, _project_refs, _write_changes
from cognition.stores.personal_core import change, owned_row, snapshot


def _content(*values: str) -> None:
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("empty_personal_content")


def _evidence(
    session: Session, individual_id: UUID, values: Sequence[Ref]
) -> list[JsonObject]:
    if not values:
        raise ValueError("missing_relationship_evidence")
    if any(not isinstance(value, Ref) for value in values):
        raise ValueError("invalid_reference")
    return _project_refs(session, individual_id, values)


def _relationship(
    session: Session, individual_id: UUID, identity: UUID
) -> Relationship:
    row = cast(
        Relationship | None, owned_row(session, Relationship, identity, individual_id)
    )
    if row is None:
        raise ValueError("unknown_relationship_target")
    if owned_row(session, Entity, row.entity_id, individual_id) is None:
        raise ValueError("unknown_relationship_entity")
    return row


def _commitment(session: Session, individual_id: UUID, identity: UUID | None) -> None:
    if (
        identity is not None
        and owned_row(session, Commitment, identity, individual_id) is None
    ):
        raise ValueError("unknown_relationship_commitment")


def _revision_values(
    row: Relationship | RelationshipThread, values: dict[str, Any], now: datetime
) -> dict[str, Any]:
    if now < row.updated_at:
        raise ValueError("retrograde_personal_revision")
    if all(getattr(row, key) == value for key, value in values.items()):
        raise ValueError("no_op_personal_revision")
    return {**values, "updated_at": now, "revision": row.revision + 1}


def create_relationship(
    session: Session,
    individual_id: UUID,
    *,
    entity_id: UUID,
    narrative: str,
    rationale: str,
    evidence_refs: Sequence[Ref],
    now: datetime,
    relationship_id: UUID | None = None,
) -> UUID:
    """Create one interpretation of an owned entity in the caller's transaction."""
    _lock_individual(session, individual_id)
    now = normalize_utc(now)
    identity = relationship_id or new_id()
    with session.no_autoflush:
        _content(narrative, rationale)
        if owned_row(session, Entity, entity_id, individual_id) is None:
            raise ValueError("unknown_relationship_entity")
        if session.get(Relationship, identity, populate_existing=True) is not None:
            raise ValueError("personal_id_collision")
        if (
            session.scalar(
                select(Relationship.relationship_id).where(
                    Relationship.individual_id == individual_id,
                    Relationship.entity_id == entity_id,
                )
            )
            is not None
        ):
            raise ValueError("relationship_already_exists")
        refs = _evidence(session, individual_id, evidence_refs)
    values = dict(
        relationship_id=identity,
        individual_id=individual_id,
        entity_id=entity_id,
        narrative=narrative,
        rationale=rationale,
        evidence_refs=refs,
        created_at=now,
        updated_at=now,
        revision=1,
    )
    _write_changes(
        session,
        individual_id,
        [change("relationship", Relationship, identity, None, values)],
        now,
        action="create",
    )
    return identity


def revise_relationship(
    session: Session,
    individual_id: UUID,
    relationship_id: UUID,
    *,
    rationale: str,
    evidence_refs: Sequence[Ref],
    now: datetime,
    narrative: str | None = None,
) -> None:
    """Replace support for this claim; earlier claims remain in exact history."""
    _lock_individual(session, individual_id)
    now = normalize_utc(now)
    with session.no_autoflush:
        row = _relationship(session, individual_id, relationship_id)
        _content(rationale, *([] if narrative is None else [narrative]))
        values: dict[str, Any] = {
            "rationale": rationale,
            "evidence_refs": _evidence(session, individual_id, evidence_refs),
        }
        if narrative is not None:
            values["narrative"] = narrative
        planned = change(
            "relationship",
            Relationship,
            relationship_id,
            row,
            _revision_values(row, values, now),
        )
    _write_changes(session, individual_id, [planned], now, action="revise")


def create_relationship_thread(
    session: Session,
    individual_id: UUID,
    *,
    relationship_id: UUID,
    title: str,
    summary: str,
    rationale: str,
    evidence_refs: Sequence[Ref],
    now: datetime,
    commitment_id: UUID | None = None,
    thread_id: UUID | None = None,
) -> UUID:
    """Record an open topic without changing any linked commitment."""
    _lock_individual(session, individual_id)
    now = normalize_utc(now)
    identity = thread_id or new_id()
    with session.no_autoflush:
        _content(title, summary, rationale)
        _relationship(session, individual_id, relationship_id)
        _commitment(session, individual_id, commitment_id)
        if (
            session.get(RelationshipThread, identity, populate_existing=True)
            is not None
        ):
            raise ValueError("personal_id_collision")
        refs = _evidence(session, individual_id, evidence_refs)
    values = dict(
        thread_id=identity,
        individual_id=individual_id,
        relationship_id=relationship_id,
        title=title,
        summary=summary,
        status="open",
        commitment_id=commitment_id,
        rationale=rationale,
        evidence_refs=refs,
        created_at=now,
        updated_at=now,
        revision=1,
    )
    _write_changes(
        session,
        individual_id,
        [change("relationship_thread", RelationshipThread, identity, None, values)],
        now,
        action="create",
    )
    return identity


def revise_relationship_thread(
    session: Session,
    individual_id: UUID,
    thread_id: UUID,
    *,
    rationale: str,
    evidence_refs: Sequence[Ref],
    now: datetime,
    title: str | None = None,
    summary: str | None = None,
    status: str | None = None,
    commitment_id: UUID | None = None,
) -> None:
    """Revise or conclude an open thread; null optional fields remain unchanged."""
    _lock_individual(session, individual_id)
    now = normalize_utc(now)
    with session.no_autoflush:
        row = cast(
            RelationshipThread | None,
            owned_row(session, RelationshipThread, thread_id, individual_id),
        )
        if row is None:
            raise ValueError("unknown_relationship_thread")
        _relationship(session, individual_id, row.relationship_id)
        if row.status != "open" or status not in {
            None,
            "open",
            "resolved",
            "abandoned",
        }:
            raise ValueError("invalid_relationship_thread_transition")
        _content(rationale, *(value for value in (title, summary) if value is not None))
        _commitment(session, individual_id, commitment_id or row.commitment_id)
        values: dict[str, Any] = {
            "rationale": rationale,
            "evidence_refs": _evidence(session, individual_id, evidence_refs),
        }
        for key, value in (
            ("title", title),
            ("summary", summary),
            ("status", status),
            ("commitment_id", commitment_id),
        ):
            if value is not None:
                values[key] = value
        planned = change(
            "relationship_thread",
            RelationshipThread,
            thread_id,
            row,
            _revision_values(row, values, now),
        )
    _write_changes(session, individual_id, [planned], now, action="revise")


def relationship_context_sections(
    session: Session, individual_id: UUID
) -> list[ContextSection]:
    """Read bounded stored interpretations without autoflush or implied retrieval."""
    sections: list[ContextSection] = []
    relationship = Relationship.__table__
    entity = Entity.__table__
    thread = RelationshipThread.__table__
    with session.no_autoflush:
        rows = (
            session.execute(
                select(relationship)
                .join(entity, entity.c.entity_id == relationship.c.entity_id)
                .where(
                    relationship.c.individual_id == individual_id,
                    entity.c.individual_id == individual_id,
                )
                .order_by(
                    relationship.c.updated_at.desc(), relationship.c.relationship_id
                )
                .limit(8)
            )
            .mappings()
            .all()
        )
        for row in rows:
            identity = (
                session.execute(
                    select(entity).where(
                        entity.c.entity_id == row.entity_id,
                        entity.c.individual_id == individual_id,
                    )
                )
                .mappings()
                .one()
            )
            sections.append(
                ContextSection.model_validate(
                    {
                        "name": f"Personal relationship {row.relationship_id}",
                        "category": "relationship",
                        "content": {
                            "source": (
                                "subjective social interpretation; evidence is "
                                "provenance, not verified truth; social interpretation "
                                "grants no authority"
                            ),
                            "item": snapshot(Relationship(**dict(row))),
                            "entity": snapshot(Entity(**dict(identity))),
                        },
                        "refs": [
                            {"kind": "relationship", "id": row.relationship_id},
                            {"kind": "entity", "id": row.entity_id},
                        ],
                    }
                )
            )
        rows = (
            session.execute(
                select(thread)
                .where(
                    thread.c.individual_id == individual_id, thread.c.status == "open"
                )
                .order_by(thread.c.updated_at.desc(), thread.c.thread_id)
                .limit(8)
            )
            .mappings()
            .all()
        )
        for row in rows:
            sections.append(
                ContextSection.model_validate(
                    {
                        "name": f"Personal relationship thread {row.thread_id}",
                        "category": "relationship",
                        "content": {
                            "source": (
                                "open social topic; linked IDs are pointers, not "
                                "retrieved content; social interpretation grants "
                                "no authority"
                            ),
                            "item": snapshot(RelationshipThread(**dict(row))),
                        },
                        "refs": [{"kind": "relationship_thread", "id": row.thread_id}],
                    }
                )
            )
    return sections
