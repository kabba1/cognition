"""Focused connector stores with immutable source identity and no cursor reset."""

from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import Table, select
from sqlalchemy.orm import Session

from cognition.db.models.governance import GovernanceState
from cognition.db.models.identity import Individual
from cognition.db.models.perception import ConnectorBinding
from cognition.domain.perception import ConnectorBindingSnapshot, validate_opaque_id
from cognition.protocols.common import new_id, normalize_utc
from cognition.stores.errors import RevisionConflict


@dataclass(frozen=True)
class ConnectorBindingRecord:
    connector_binding_id: UUID
    individual_id: UUID
    adapter_id: str
    source_id: str
    enabled: bool
    cursor: str | None
    cursor_revision: int
    revision: int
    pending_wake_id: UUID | None
    latest_receipt_event_id: UUID | None
    created_at: datetime
    updated_at: datetime


def load_connector_binding(
    session: Session, individual_id: UUID, connector_binding_id: UUID
) -> ConnectorBindingRecord:
    """Read detached committed columns without refreshing or flushing ORM state."""
    with session.no_autoflush:
        row = (
            session.execute(
                select(ConnectorBinding.__table__).where(
                    ConnectorBinding.connector_binding_id == connector_binding_id,
                    ConnectorBinding.individual_id == individual_id,
                )
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise LookupError(
            "Connector binding is missing or belongs to another individual"
        )
    return ConnectorBindingRecord(**dict(row))


def load_connector_binding_snapshot(
    session: Session, individual_id: UUID, connector_binding_id: UUID
) -> ConnectorBindingSnapshot:
    """One Core join captures source, cursor and lifecycle/governance epochs."""
    with session.no_autoflush:
        row = (
            session.execute(
                select(
                    ConnectorBinding.__table__,
                    Individual.revision.label("individual_revision"),
                    Individual.operational_status,
                    GovernanceState.revision.label("governance_revision"),
                )
                .join(
                    Individual,
                    Individual.individual_id == ConnectorBinding.individual_id,
                )
                .join(
                    GovernanceState,
                    GovernanceState.individual_id == ConnectorBinding.individual_id,
                )
                .where(
                    ConnectorBinding.connector_binding_id == connector_binding_id,
                    ConnectorBinding.individual_id == individual_id,
                )
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise LookupError(
            "Connector binding, individual or governance state is missing"
        )
    return ConnectorBindingSnapshot(
        connector_binding_id=row.connector_binding_id,
        individual_id=row.individual_id,
        adapter_id=row.adapter_id,
        source_id=row.source_id,
        enabled=row.enabled,
        cursor=row.cursor,
        cursor_revision=row.cursor_revision,
        binding_revision=row.revision,
        individual_revision=row.individual_revision,
        governance_revision=row.governance_revision,
        operational_status=row.operational_status,
    )


def _clean(session: Session) -> None:
    if any(
        isinstance(row, ConnectorBinding)
        for row in session.new | session.dirty | session.deleted
    ):
        raise ValueError("Unflushed connector binding state")


def _enabled(enabled: bool) -> None:
    if type(enabled) is not bool:
        raise ValueError("Connector enabled must be a boolean")


def create_connector_binding(
    session: Session,
    individual_id: UUID,
    *,
    adapter_id: str,
    source_id: str,
    now: datetime,
    enabled: bool = False,
    connector_binding_id: UUID | None = None,
) -> ConnectorBindingRecord:
    """Trusted registration; caller locks individual/admin/governance before binding."""
    adapter_id, source_id = (
        validate_opaque_id(adapter_id),
        validate_opaque_id(source_id),
    )
    _enabled(enabled)
    now = normalize_utc(now)
    _clean(session)
    with session.no_autoflush:
        existing = session.scalar(
            select(ConnectorBinding.connector_binding_id).where(
                ConnectorBinding.individual_id == individual_id,
                ConnectorBinding.adapter_id == adapter_id,
                ConnectorBinding.source_id == source_id,
            )
        )
        if existing is not None:
            raise ValueError("Connector source is already registered")
        identity = connector_binding_id or new_id()
        if (
            session.scalar(
                select(ConnectorBinding.connector_binding_id).where(
                    ConnectorBinding.connector_binding_id == identity
                )
            )
            is not None
        ):
            raise ValueError("Connector binding identity is already registered")
        session.add(
            ConnectorBinding(
                connector_binding_id=identity,
                individual_id=individual_id,
                adapter_id=adapter_id,
                source_id=source_id,
                enabled=enabled,
                cursor=None,
                cursor_revision=0,
                revision=1,
                pending_wake_id=None,
                latest_receipt_event_id=None,
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        return load_connector_binding(session, individual_id, identity)


def set_connector_enabled(
    session: Session,
    individual_id: UUID,
    connector_binding_id: UUID,
    enabled: bool,
    *,
    now: datetime,
    expected_revision: int | None = None,
) -> tuple[ConnectorBindingRecord, ConnectorBindingRecord]:
    """Change only enablement and its operational epoch; retain all inbound work."""
    _enabled(enabled)
    now = normalize_utc(now)
    _clean(session)
    with session.no_autoflush:
        table = cast(Table, ConnectorBinding.__table__)
        row = (
            session.execute(
                select(table)
                .where(
                    table.c.connector_binding_id == connector_binding_id,
                    table.c.individual_id == individual_id,
                )
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise LookupError(
                "Connector binding is missing or belongs to another individual"
            )
        if expected_revision is not None and row.revision != expected_revision:
            raise RevisionConflict("Connector binding revision changed")
        if now < row.created_at or now < row.updated_at:
            raise ValueError("Connector clock precedes recorded binding state")
        before = ConnectorBindingRecord(**dict(row))
        changed = (
            session.execute(
                table.update()
                .where(table.c.connector_binding_id == connector_binding_id)
                .values(enabled=enabled, revision=row.revision + 1, updated_at=now)
                .returning(table)
            )
            .mappings()
            .one()
        )
        return before, ConnectorBindingRecord(**dict(changed))
