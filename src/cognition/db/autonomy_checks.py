"""Read-only scheduler diagnostics from committed column snapshots."""

import math
from collections.abc import Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from cognition.config.revisions import behavior_hash
from cognition.config.schema import parse_behavior_config
from cognition.db.models.attention import Wake
from cognition.db.models.autonomy import AutonomyState
from cognition.db.models.cognition import CognitionCycle, CycleWake
from cognition.db.models.identity import Individual
from cognition.db.models.runtime import RuntimeConfigRevision
from cognition.domain.heartbeat import HeartbeatPolicy


def check_autonomy_state(
    session: Session, error: Callable[[str, str, UUID, str], None]
) -> None:
    """Observe ownership and deferred materialization without ORM refresh or writes."""
    with session.no_autoflush:
        individuals = set(session.scalars(select(Individual.individual_id)))
        configurations = {
            row.config_revision_id: row
            for row in session.execute(
                select(RuntimeConfigRevision.__table__)
            ).mappings()
        }
        wakes = {
            row.wake_id: row
            for row in session.execute(select(Wake.__table__)).mappings()
        }
        cycles = {
            row.cycle_id: row
            for row in session.execute(select(CognitionCycle.__table__)).mappings()
        }
        claims = dict(
            session.execute(select(CycleWake.wake_id, CycleWake.cycle_id))
            .tuples()
            .all()
        )
        for state in session.execute(select(AutonomyState.__table__)).mappings():
            identity = state.individual_id
            if identity not in individuals:
                error(
                    "autonomy_owner",
                    "individual",
                    identity,
                    "Scheduler owner is missing.",
                )
            if (
                state.policy_version != 1
                or state.revision < 1
                or not math.isfinite(state.interval_seconds)
                or state.interval_seconds < 1
            ):
                error(
                    "autonomy_policy",
                    "individual",
                    identity,
                    "Scheduler policy or interval is invalid.",
                )
            configuration = configurations.get(state.config_revision_id)
            try:
                if (
                    configuration is None
                    or configuration.individual_id != identity
                    or configuration.activated_at is None
                ):
                    raise ValueError("Missing owned activated configuration")
                parsed = parse_behavior_config(configuration.sanitized_config)
                if (
                    parsed.config_schema_version != configuration.config_schema_version
                    or behavior_hash(parsed) != configuration.content_hash
                ):
                    raise ValueError("Configuration integrity mismatch")
                policy = HeartbeatPolicy(
                    parsed.attention.heartbeat_min_seconds,
                    parsed.attention.heartbeat_max_seconds,
                    parsed.attention.heartbeat_backoff_factor,
                )
                if policy.clamp(state.interval_seconds) != state.interval_seconds:
                    raise ValueError("Interval is outside retained policy")
            except (ValueError, TypeError):
                error(
                    "autonomy_configuration",
                    "individual",
                    identity,
                    "Scheduler retained configuration or interval linkage is invalid.",
                )
            if state.last_completed_cycle_id is not None:
                cycle = cycles.get(state.last_completed_cycle_id)
                if (
                    cycle is None
                    or cycle.individual_id != identity
                    or cycle.status not in {"completed", "failed"}
                    or cycle.completed_at != state.anchor_at
                ):
                    error(
                        "autonomy_completed_cycle",
                        "individual",
                        identity,
                        "Scheduler completed cycle or anchor is inconsistent.",
                    )
            if state.managed_wake_id is None:
                if not state.materialization_pending:
                    error(
                        "autonomy_managed_wake",
                        "individual",
                        identity,
                        "Scheduler has no managed wake or deferred materialization.",
                    )
                continue
            wake = wakes.get(state.managed_wake_id)
            if (
                wake is None
                or wake.individual_id != identity
                or wake.kind != "heartbeat"
                or wake.coalesce_key is not None
            ):
                error(
                    "autonomy_managed_wake",
                    "individual",
                    identity,
                    "Managed heartbeat is missing, foreign or inconsistent.",
                )
                continue
            if wake.status == "consumed" and not state.materialization_pending:
                error(
                    "autonomy_managed_wake",
                    "individual",
                    identity,
                    "Consumed heartbeat has no deferred materialization marker.",
                )
            if wake.status == "claimed":
                cycle = cycles.get(claims.get(wake.wake_id))
                if (
                    cycle is None
                    or cycle.individual_id != identity
                    or cycle.status != "active"
                ):
                    error(
                        "autonomy_managed_wake",
                        "individual",
                        identity,
                        "Managed heartbeat claim has no owned active cycle.",
                    )
