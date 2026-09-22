"""Runtime observations; these rows never grant deployment ownership."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from cognition.db.models.runtime import RuntimeInstance
from cognition.protocols.common import normalize_utc

ACTIVE_RUNTIME_STATUSES = ("starting", "running", "stopping")


@dataclass(frozen=True)
class RuntimeInstanceRecord:
    runtime_instance_id: UUID
    individual_id: UUID
    status: str
    host_id: str
    process_id: int
    started_at: datetime
    last_heartbeat_at: datetime
    stopped_at: datetime | None
    runtime_version: str


def _snapshot(row: RuntimeInstance) -> RuntimeInstanceRecord:
    return RuntimeInstanceRecord(
        row.runtime_instance_id,
        row.individual_id,
        row.status,
        row.host_id,
        row.process_id,
        row.started_at,
        row.last_heartbeat_at,
        row.stopped_at,
        row.runtime_version,
    )


def record_runtime_start(
    session: Session,
    individual_id: UUID,
    *,
    now: datetime,
    host_id: str,
    process_id: int,
    runtime_version: str,
) -> RuntimeInstanceRecord:
    """After acquiring authority, crash stale rows and record the new startup.

    Caller owns the transaction and must already hold the session advisory lock.
    No existing observation is used to decide whether startup is allowed.
    """
    instant = normalize_utc(now)
    session.execute(
        update(RuntimeInstance)
        .where(
            RuntimeInstance.individual_id == individual_id,
            RuntimeInstance.status.in_(ACTIVE_RUNTIME_STATUSES),
            RuntimeInstance.stopped_at.is_(None),
        )
        .values(status="crashed", stopped_at=instant)
    )
    row = RuntimeInstance(
        individual_id=individual_id,
        status="starting",
        host_id=host_id,
        process_id=process_id,
        started_at=instant,
        last_heartbeat_at=instant,
        stopped_at=None,
        runtime_version=runtime_version,
    )
    session.add(row)
    session.flush()
    return _snapshot(row)


def record_runtime_stop(
    session: Session, runtime_instance_id: UUID, *, now: datetime
) -> None:
    """Record graceful termination only while the caller still owns its session."""
    instant = normalize_utc(now)
    session.execute(
        update(RuntimeInstance)
        .where(
            RuntimeInstance.runtime_instance_id == runtime_instance_id,
            RuntimeInstance.status.in_(ACTIVE_RUNTIME_STATUSES),
            RuntimeInstance.stopped_at.is_(None),
        )
        .values(status="stopped", stopped_at=instant, last_heartbeat_at=instant)
    )
    session.flush()


def list_runtime_instances(
    session: Session, individual_id: UUID
) -> list[RuntimeInstanceRecord]:
    """Return detached observations in stable chronological order."""
    rows = session.scalars(
        select(RuntimeInstance)
        .where(RuntimeInstance.individual_id == individual_id)
        .order_by(RuntimeInstance.started_at, RuntimeInstance.runtime_instance_id)
    )
    return [_snapshot(row) for row in rows]
