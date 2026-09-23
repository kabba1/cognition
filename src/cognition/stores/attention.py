"""Durable wake persistence and read-only bounded personal attention selection."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from cognition.db.base import Base
from cognition.db.models.attention import Wake
from cognition.db.models.development import Interest, Preference, SelfState
from cognition.db.models.evidence import Event
from cognition.db.models.personal import (
    Belief,
    Commitment,
    Entity,
    Episode,
    Goal,
    Project,
)
from cognition.db.models.relationships import Relationship, RelationshipThread
from cognition.domain.attention import (
    DIRECT_REF_LIMIT,
    LINKED_REF_LIMIT,
    URGENT_DETAIL_LIMIT,
    URGENT_HORIZON_HOURS,
    AttentionCandidate,
    PersonalAttention,
)
from cognition.protocols.cognition_v1 import CurrentFocus
from cognition.protocols.common import Ref, normalize_utc
from cognition.protocols.model_v1 import ContextSection
from cognition.protocols.wakes_v1 import WakeV1
from cognition.stores.development import development_context_section
from cognition.stores.personal import (
    personal_context_section,
    personal_context_sections,
)
from cognition.stores.relationships import (
    relationship_context_section,
    relationship_thread_context_section,
)


@dataclass(frozen=True)
class StoredWake:
    wake: WakeV1
    status: str
    revision: int


def load_wake(session: Session, wake_id: UUID) -> StoredWake:
    row = session.get(Wake, wake_id, populate_existing=True)
    if row is None:
        raise LookupError("Wake does not exist")
    return StoredWake(
        WakeV1.model_validate(
            {
                "schema_version": 1,
                "wake_id": row.wake_id,
                "individual_id": row.individual_id,
                "kind": row.kind,
                "due_at": row.due_at,
                "purpose": row.purpose,
                "cause_event_id": row.cause_event_id,
                "context_refs": row.context_refs,
                "coalesce_key": row.coalesce_key,
            }
        ),
        row.status,
        row.revision,
    )


def create_or_merge_pending_wake(session: Session, wake: WakeV1) -> UUID:
    if wake.cause_event_id is not None:
        # A discarded ON CONFLICT insert does not run its foreign-key checks.
        # Validate before either path, retaining the causal row until commit.
        cause_individual = session.scalar(
            select(Event.individual_id)
            .where(Event.event_id == wake.cause_event_id)
            .with_for_update(read=True, key_share=True)
        )
        if cause_individual != wake.individual_id:
            raise ValueError("Wake cause must be an existing event for this individual")
    values = wake.model_dump(exclude={"schema_version", "context_refs"})
    values.update(
        context_refs=[ref.model_dump(mode="json") for ref in wake.context_refs],
        status="pending",
        revision=1,
    )
    statement = insert(Wake).values(**values)
    if wake.coalesce_key is None:
        session.execute(statement)
        return wake.wake_id
    returning_insert = statement.on_conflict_do_nothing(
        index_elements=[Wake.individual_id, Wake.coalesce_key],
        index_where=text("status = 'pending' AND coalesce_key IS NOT NULL"),
    ).returning(Wake.wake_id)
    while True:
        created = session.scalar(returning_insert)
        if created is not None:
            return created
        existing = session.scalar(
            select(Wake)
            .where(
                Wake.individual_id == wake.individual_id,
                Wake.coalesce_key == wake.coalesce_key,
                Wake.status == "pending",
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        # A concurrent consumer may have removed the pending index entry.
        if existing is None:
            continue
        refs = [Ref.model_validate(value) for value in existing.context_refs]
        incoming = list(wake.context_refs)
        if wake.cause_event_id and wake.cause_event_id != existing.cause_event_id:
            incoming.append(Ref(kind="event", id=wake.cause_event_id))
        for ref in incoming:
            if ref not in refs:
                refs.append(ref)
        existing.context_refs = [ref.model_dump(mode="json") for ref in refs]
        existing.due_at = min(existing.due_at, wake.due_at)
        if wake.purpose != existing.purpose:
            existing.purpose += "\n" + wake.purpose
        existing.revision += 1
        session.flush()
        return existing.wake_id


_PERSONAL_ATTENTION_MODELS: dict[str, type[Base]] = {
    "entity": Entity,
    "project": Project,
    "goal": Goal,
    "commitment": Commitment,
    "belief": Belief,
    "episode": Episode,
    "interest": Interest,
    "preference": Preference,
    "self_state": SelfState,
    "relationship": Relationship,
    "relationship_thread": RelationshipThread,
}
_PARENT_FIELDS = {
    "goal": (("project", "project_id"),),
    "belief": (("entity", "subject_entity_id"),),
    "commitment": (("entity", "counterparty_entity_id"),),
    "relationship": (("entity", "entity_id"),),
    "relationship_thread": (
        ("relationship", "relationship_id"),
        ("commitment", "commitment_id"),
    ),
}


def build_personal_attention(
    session: Session,
    individual_id: UUID,
    *,
    wakes: Sequence[WakeV1],
    focus: CurrentFocus | None,
    now: datetime,
) -> PersonalAttention:
    """Select stored owned content; retrieval never flushes or updates state.

    Direct and linked caps count unique input lookups. Truncated references are
    not claims that their targets exist or were finally omitted from context.
    The ninth urgent row is only an overflow sentinel; the compiler renders its
    mandatory bounded notice from ``urgent_scan_truncated``.
    """
    now = normalize_utc(now)
    rows: dict[tuple[str, UUID], Mapping[str, Any] | None] = {}
    candidates: dict[tuple[str, UUID], AttentionCandidate] = {}

    def load(ref: Ref) -> Mapping[str, Any] | None:
        key = (ref.kind, ref.id)
        if key not in rows:
            table = _PERSONAL_ATTENTION_MODELS[ref.kind].__table__
            primary = (
                "thread_id" if ref.kind == "relationship_thread" else f"{ref.kind}_id"
            )
            stored = (
                session.execute(
                    select(table).where(
                        table.c.individual_id == individual_id,
                        table.c[primary] == ref.id,
                    )
                )
                .mappings()
                .first()
            )
            rows[key] = None if stored is None else dict(stored)
        return rows[key]

    def render(ref: Ref) -> ContextSection | None:
        row = load(ref)
        if row is None:
            return None
        if ref.kind in {"interest", "preference", "self_state"}:
            return development_context_section(ref.kind, row)
        if ref.kind == "relationship":
            entity = load(Ref(kind="entity", id=row["entity_id"]))
            return None if entity is None else relationship_context_section(row, entity)
        if ref.kind == "relationship_thread":
            return relationship_thread_context_section(row)
        return personal_context_section(ref.kind, row)

    def add(section: ContextSection, reason: str, mandatory: bool = False) -> None:
        primary = section.refs[0]
        key = (primary.kind, primary.id)
        # Call order expresses mandatory > direct > linked > recent precedence.
        if key not in candidates:
            candidates[key] = AttentionCandidate(section, reason, mandatory)

    inputs: dict[tuple[str, UUID], str] = {}
    sources = [
        (wake.context_refs, "wake_reference")
        for wake in sorted(wakes, key=lambda item: (item.due_at, item.wake_id.int))
        if wake.individual_id == individual_id
    ]
    if focus is not None:
        sources.append((focus.refs, "focus_reference"))
    for source_refs, reason in sources:
        for ref in sorted(source_refs, key=lambda item: (item.kind, item.id.int)):
            if ref.kind in _PERSONAL_ATTENTION_MODELS:
                inputs.setdefault((ref.kind, ref.id), reason)
    direct = list(inputs.items())[:DIRECT_REF_LIMIT]
    direct_keys = {key for key, _ in direct}
    unresolved = 0
    linked: dict[tuple[str, UUID], Ref] = {}
    with session.no_autoflush:
        commitment = Commitment.__table__
        urgent = (
            session.execute(
                select(commitment)
                .where(
                    commitment.c.individual_id == individual_id,
                    commitment.c.status.in_(("active", "disputed")),
                    commitment.c.due_at <= now + timedelta(hours=URGENT_HORIZON_HOURS),
                )
                .order_by(commitment.c.due_at, commitment.c.commitment_id)
                .limit(URGENT_DETAIL_LIMIT + 1)
            )
            .mappings()
            .all()
        )
        for urgent_row in urgent[:URGENT_DETAIL_LIMIT]:
            rows[("commitment", urgent_row["commitment_id"])] = dict(urgent_row)
            add(
                personal_context_section("commitment", dict(urgent_row)),
                "urgent_commitment",
                True,
            )
        for key, reason in direct:
            ref = Ref(kind=key[0], id=key[1])
            section = render(ref)
            if section is None:
                unresolved += 1
                continue
            add(section, reason)
            row = rows[key]
            assert row is not None
            for parent_kind, field in _PARENT_FIELDS.get(ref.kind, ()):
                identity = row[field]
                parent_key = (parent_kind, identity)
                if (
                    identity is not None
                    and parent_key not in direct_keys
                    and parent_key not in candidates
                ):
                    linked.setdefault(parent_key, Ref(kind=parent_kind, id=identity))
        for ref in list(linked.values())[:LINKED_REF_LIMIT]:
            section = render(ref)
            if section is not None:
                add(section, "linked_reference")
        for section in personal_context_sections(session, individual_id):
            add(section, "recent_personal")
    return PersonalAttention(
        tuple(candidates.values()),
        direct_refs_truncated=max(0, len(inputs) - DIRECT_REF_LIMIT),
        unresolved_direct_refs=unresolved,
        linked_refs_truncated=max(0, len(linked) - LINKED_REF_LIMIT),
        urgent_scan_truncated=len(urgent) > URGENT_DETAIL_LIMIT,
    )
