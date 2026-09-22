"""Insert or coalesce pending attention atomically without consuming evidence."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from cognition.db.models.attention import Wake
from cognition.db.models.evidence import Event
from cognition.protocols.common import Ref
from cognition.protocols.wakes_v1 import WakeV1


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
