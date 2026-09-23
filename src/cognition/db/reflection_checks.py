"""Read-only reflection diagnostics, including scope beyond the current pointer."""

from collections import Counter
from collections.abc import Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from cognition.db.models.attention import Wake
from cognition.db.models.cognition import CognitionCycle, CycleWake
from cognition.db.models.evidence import Event
from cognition.db.models.identity import Individual
from cognition.db.models.reflection import ManagedReflectionBatch, ReflectionState
from cognition.stores.reflection import validate_reflection_state
from cognition.stores.reflection_scope import (
    REFLECTION_MARKER_SOURCE,
    REFLECTION_MARKER_TYPE,
    managed_reflection_scope,
)


def check_reflection_state(
    session: Session, error: Callable[[str, str, UUID, str], None]
) -> None:
    """Check retained envelopes and Core snapshots without flushing or refreshing."""
    with session.no_autoflush:
        owners: set[UUID] = set()
        scopes: set[tuple[UUID, UUID]] = set()
        for identity, pointer in session.execute(
            select(ReflectionState.individual_id, ReflectionState.managed_wake_id)
        ):
            owners.add(identity)
            if pointer is not None:
                scopes.add((identity, pointer))
        for identity, wake_id in session.execute(
            select(ManagedReflectionBatch.individual_id, ManagedReflectionBatch.wake_id)
        ):
            owners.add(identity)
            scopes.add((identity, wake_id))

        # Creation envelopes survive payload redaction and scope-record deletion.
        # Do not filter source/subject integrity before observing malformed markers.
        for marker in session.execute(
            select(
                Event.event_id,
                Event.individual_id,
                Event.subject_kind,
                Event.subject_id,
                Event.source_kind,
                Event.source_id,
            ).where(Event.event_type == REFLECTION_MARKER_TYPE)
        ):
            owners.add(marker.individual_id)
            if (
                marker.subject_kind != "wake"
                or marker.subject_id is None
                or marker.source_kind != "runtime"
                or marker.source_id != REFLECTION_MARKER_SOURCE
            ):
                error(
                    "reflection_marker",
                    "event",
                    marker.event_id,
                    "Managed reflection creation marker is malformed.",
                )
            if marker.subject_kind == "wake" and marker.subject_id is not None:
                scopes.add((marker.individual_id, marker.subject_id))
        # A changed marker subject must not hide the wake retaining its cause ID.
        for identity, wake_id in session.execute(
            select(Wake.individual_id, Wake.wake_id)
            .join(Event, Event.event_id == Wake.cause_event_id)
            .where(Event.event_type == REFLECTION_MARKER_TYPE)
        ):
            owners.add(identity)
            scopes.add((identity, wake_id))

        individuals = set(session.scalars(select(Individual.individual_id)))
        wakes = {
            row.wake_id: row
            for row in session.execute(
                select(Wake.wake_id, Wake.individual_id, Wake.status, Wake.consumed_at)
            )
        }
        cycles = {
            row.cycle_id: row
            for row in session.execute(
                select(
                    CognitionCycle.cycle_id,
                    CognitionCycle.individual_id,
                    CognitionCycle.status,
                    CognitionCycle.completed_at,
                )
            )
        }
        claims = dict(
            session.execute(select(CycleWake.wake_id, CycleWake.cycle_id))
            .tuples()
            .all()
        )
        completions = Counter(
            session.execute(
                select(
                    Event.individual_id, Event.subject_id, Event.correlation_id
                ).where(
                    Event.event_type == "attention.reflection_completed",
                    Event.source_kind == "runtime",
                    Event.source_id == REFLECTION_MARKER_SOURCE,
                    Event.subject_kind == "wake",
                )
            )
            .tuples()
            .all()
        )
        for identity in sorted(owners):
            if identity not in individuals:
                error(
                    "reflection_owner",
                    "individual",
                    identity,
                    "Reflection state, batch or marker owner is missing.",
                )
            try:
                validate_reflection_state(session, identity)
            except (ValueError, LookupError):
                error(
                    "reflection_state",
                    "individual",
                    identity,
                    "Reflection state, cadence or live-batch linkage is invalid.",
                )
        for identity, wake_id in sorted(scopes):
            try:
                if managed_reflection_scope(session, identity, wake_id) is None:
                    raise ValueError("Missing retained managed scope")
            except (ValueError, LookupError):
                error(
                    "reflection_scope",
                    "wake",
                    wake_id,
                    "Managed reflection scope, owner or creation linkage is invalid.",
                )
            wake = wakes.get(wake_id)
            if wake is None or wake.status not in {"claimed", "consumed"}:
                continue
            cycle = cycles.get(claims.get(wake_id))
            invalid = (
                cycle is None
                or wake.individual_id != identity
                or cycle.individual_id != identity
            )
            if not invalid and cycle is not None:
                if wake.status == "claimed":
                    invalid = cycle.status != "active"
                else:
                    invalid = (
                        cycle.status not in {"completed", "failed"}
                        or cycle.completed_at is None
                        or wake.consumed_at != cycle.completed_at
                        or completions[(identity, wake_id, cycle.cycle_id)] != 1
                    )
            if invalid:
                error(
                    "reflection_cycle",
                    "wake",
                    wake_id,
                    "Managed reflection claim, consumption or outcome link is invalid.",
                )
