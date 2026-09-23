"""Read-only diagnostics for explicit internal exploration allowances."""

from collections import Counter
from collections.abc import Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from cognition.db.models.attention import Wake
from cognition.db.models.cognition import (
    CognitionCycle,
    CognitionTurn,
    ContextSnapshot,
    CycleWake,
    ModelInvocation,
)
from cognition.db.models.evidence import Event
from cognition.db.models.exploration import ExplorationGrant, ExplorationState
from cognition.db.models.governance import GovernanceState
from cognition.domain.exploration import parse_internal_exploration
from cognition.protocols.executive import parse_request
from cognition.stores.exploration import validate_exploration_state
from cognition.stores.exploration_scope import (
    EXPLORATION_MARKER_SOURCE,
    EXPLORATION_MARKER_TYPE,
    get_cycle_exploration,
    get_exploration_grant,
    validate_exploration_control,
)


def check_exploration_state(
    session: Session, error: Callable[[str, str, UUID, str], None]
) -> None:
    """Read persisted columns without flushing or refreshing a caller's dirty state."""
    with session.no_autoflush:
        for governance in session.execute(
            select(GovernanceState.individual_id, GovernanceState.budget_policy)
        ):
            try:
                parse_internal_exploration(governance.budget_policy)
            except ValueError:
                error(
                    "exploration_policy",
                    "governance",
                    governance.individual_id,
                    "Reserved internal exploration policy is malformed.",
                )
        owners: set[UUID] = set()
        grants: set[tuple[UUID, UUID]] = set()
        for identity, pointer in session.execute(
            select(ExplorationState.individual_id, ExplorationState.managed_wake_id)
        ):
            owners.add(identity)
            if pointer is not None:
                grants.add((identity, pointer))
        for identity, wake_id in session.execute(
            select(ExplorationGrant.individual_id, ExplorationGrant.wake_id)
        ):
            owners.add(identity)
            grants.add((identity, wake_id))
        for marker in session.execute(
            select(
                Event.event_id,
                Event.individual_id,
                Event.subject_kind,
                Event.subject_id,
                Event.source_kind,
                Event.source_id,
            ).where(Event.event_type == EXPLORATION_MARKER_TYPE)
        ):
            owners.add(marker.individual_id)
            if (
                marker.subject_kind != "wake"
                or marker.subject_id is None
                or marker.source_kind != "runtime"
                or marker.source_id != EXPLORATION_MARKER_SOURCE
            ):
                error(
                    "exploration_marker",
                    "event",
                    marker.event_id,
                    "Managed exploration creation marker is malformed.",
                )
            if marker.subject_kind == "wake" and marker.subject_id is not None:
                grants.add((marker.individual_id, marker.subject_id))
        for identity, wake_id in session.execute(
            select(Wake.individual_id, Wake.wake_id)
            .join(Event, Event.event_id == Wake.cause_event_id)
            .where(Event.event_type == EXPLORATION_MARKER_TYPE)
        ):
            owners.add(identity)
            grants.add((identity, wake_id))
        for identity in sorted(owners):
            try:
                validate_exploration_state(session, identity)
            except (ValueError, LookupError):
                error(
                    "exploration_state",
                    "individual",
                    identity,
                    "Exploration state, cadence or live-grant linkage is invalid.",
                )
        for identity, wake_id in sorted(grants):
            try:
                if get_exploration_grant(session, identity, wake_id) is None:
                    raise ValueError("Missing retained exploration grant")
            except (ValueError, LookupError):
                error(
                    "exploration_grant",
                    "wake",
                    wake_id,
                    "Managed exploration grant, owner or creation linkage is invalid.",
                )
        _check_lifecycle(session, grants, error)
        _check_controls(session, error)


def _check_lifecycle(
    session: Session,
    grants: set[tuple[UUID, UUID]],
    error: Callable[[str, str, UUID, str], None],
) -> None:
    wakes = {
        row.wake_id: row for row in session.execute(select(Wake.__table__)).mappings()
    }
    cycles = {
        row.cycle_id: row
        for row in session.execute(select(CognitionCycle.__table__)).mappings()
    }
    claims = dict(
        session.execute(select(CycleWake.wake_id, CycleWake.cycle_id)).tuples().all()
    )
    outcomes = list(
        session.execute(
            select(Event.__table__).where(
                Event.event_type.in_(
                    (
                        "attention.exploration_completed",
                        "attention.exploration_cancelled",
                    )
                )
            )
        ).mappings()
    )
    for marker in outcomes:
        if (
            marker.source_kind != "runtime"
            or marker.source_id != EXPLORATION_MARKER_SOURCE
            or marker.subject_kind != "wake"
            or marker.subject_id is None
        ):
            error(
                "exploration_outcome",
                "event",
                marker.event_id,
                "Managed exploration outcome marker is malformed.",
            )
        if marker.subject_kind == "wake" and marker.subject_id is not None:
            grants.add((marker.individual_id, marker.subject_id))
    for identity, wake_id in sorted(grants):
        wake = wakes.get(wake_id)
        cycle = cycles.get(claims.get(wake_id))
        try:
            grant = get_exploration_grant(session, identity, wake_id)
            if grant is None:
                raise ValueError("Missing exploration grant")
        except (ValueError, LookupError):
            # Grant checks above report structural errors; outcome-only identities
            # still need a finding after grant and creation history disappeared.
            error(
                "exploration_grant",
                "wake",
                wake_id,
                "Managed exploration outcome or lifecycle lacks its valid grant.",
            )
            continue
        assert wake is not None
        if wake.status in {"claimed", "consumed"} or cycle is not None:
            try:
                if (
                    cycle is None
                    or cycle.individual_id != identity
                    or get_cycle_exploration(session, cycle.cycle_id) is None
                    or wake.status not in {"claimed", "consumed"}
                    or wake.status == "claimed"
                    and cycle.status != "active"
                    or wake.status == "consumed"
                    and (
                        cycle.status not in {"completed", "failed"}
                        or cycle.completed_at is None
                        or cycle.completed_at != wake.consumed_at
                    )
                ):
                    raise ValueError("Invalid exploration cycle")
            except (ValueError, LookupError):
                error(
                    "exploration_cycle",
                    "wake",
                    wake_id,
                    "Managed exploration membership, limits or consumption is invalid.",
                )
            else:
                assert cycle is not None
                _check_attempts(session, cycle, error)
        linked = [marker for marker in outcomes if marker.subject_id == wake_id]
        if wake.status not in {"consumed", "cancelled"}:
            valid = not linked
        else:
            expected_type = (
                "attention.exploration_completed"
                if wake.status == "consumed"
                else "attention.exploration_cancelled"
            )
            valid = len(linked) == 1
            if valid:
                marker = linked[0]
                valid = (
                    marker.individual_id == identity
                    and marker.source_kind == "runtime"
                    and marker.source_id == EXPLORATION_MARKER_SOURCE
                    and marker.subject_kind == "wake"
                    and marker.event_type == expected_type
                    and marker.occurred_at is not None
                    and marker.occurred_at >= grant.created_at
                )
                if wake.status == "consumed":
                    valid = (
                        valid
                        and cycle is not None
                        and marker.correlation_id == cycle.cycle_id
                        and marker.occurred_at == cycle.completed_at
                    )
                else:
                    valid = valid and marker.correlation_id is None and cycle is None
        if not valid:
            error(
                "exploration_outcome",
                "wake",
                wake_id,
                "Managed exploration terminal state requires one retained outcome.",
            )


def _check_attempts(
    session: Session,
    cycle: RowMapping,
    error: Callable[[str, str, UUID, str], None],
) -> None:
    turns = list(
        session.execute(
            select(CognitionTurn.turn_id, CognitionTurn.ordinal).where(
                CognitionTurn.cycle_id == cycle.cycle_id
            )
        )
    )
    attempts = list(
        session.execute(
            select(ModelInvocation.__table__)
            .join(CognitionTurn, CognitionTurn.turn_id == ModelInvocation.turn_id)
            .where(CognitionTurn.cycle_id == cycle.cycle_id)
        ).mappings()
    )
    counts = Counter(attempt.turn_id for attempt in attempts)
    if (
        len(turns) > cycle.max_turns
        or any(turn.ordinal > cycle.max_turns for turn in turns)
        or any(count > cycle.max_attempts_per_turn for count in counts.values())
        or any(
            attempt.attempt_number > cycle.max_attempts_per_turn
            or not cycle.started_at <= attempt.started_at < cycle.deadline_at
            for attempt in attempts
        )
    ):
        error(
            "exploration_attempts",
            "cycle",
            cycle.cycle_id,
            "Recorded exploration turns or invocation starts exceed frozen limits.",
        )


def _check_controls(
    session: Session, error: Callable[[str, str, UUID, str], None]
) -> None:
    for snapshot in session.execute(
        select(
            ContextSnapshot.snapshot_id,
            ContextSnapshot.request_json,
            CognitionTurn.cycle_id,
        ).join(CognitionTurn, CognitionTurn.turn_id == ContextSnapshot.turn_id)
    ):
        try:
            cycle = get_cycle_exploration(session, snapshot.cycle_id)
            value = snapshot.request_json
            raw_sections = (
                value.get("context_sections", []) if isinstance(value, dict) else []
            )
            has_control = isinstance(raw_sections, list) and any(
                isinstance(section, dict)
                and section.get("name") == "internal_exploration"
                for section in raw_sections
            )
            if cycle is None and not has_control:
                continue
            request = parse_request(value)
            validate_exploration_control(cycle, request.context_sections)
        except (ValueError, TypeError, LookupError):
            error(
                "exploration_control",
                "context_snapshot",
                snapshot.snapshot_id,
                "Frozen exploration control differs from durable grant or membership.",
            )
