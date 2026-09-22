"""Serialized active configuration replacement inside caller transactions."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from cognition.config.revisions import behavior_hash
from cognition.config.schema import BehaviorConfig
from cognition.db.models.identity import Individual
from cognition.db.models.runtime import RuntimeConfigRevision
from cognition.protocols.common import new_id, normalize_utc


@dataclass(frozen=True)
class ConfigRevisionRecord:
    config_revision_id: UUID
    individual_id: UUID
    config_schema_version: int
    sanitized_config: BehaviorConfig
    content_hash: str
    created_at: datetime
    activated_at: datetime | None
    superseded_at: datetime | None


def _snapshot(row: RuntimeConfigRevision) -> ConfigRevisionRecord:
    return ConfigRevisionRecord(
        row.config_revision_id,
        row.individual_id,
        row.config_schema_version,
        BehaviorConfig.model_validate(row.sanitized_config),
        row.content_hash,
        row.created_at,
        row.activated_at,
        row.superseded_at,
    )


def _active(session: Session, individual_id: UUID) -> RuntimeConfigRevision | None:
    return session.scalar(
        select(RuntimeConfigRevision)
        .where(
            RuntimeConfigRevision.individual_id == individual_id,
            RuntimeConfigRevision.activated_at.is_not(None),
            RuntimeConfigRevision.superseded_at.is_(None),
        )
        .execution_options(populate_existing=True)
    )


def get_active_config(
    session: Session,
    individual_id: UUID,
) -> ConfigRevisionRecord | None:
    row = _active(session, individual_id)
    return None if row is None else _snapshot(row)


def replace_config_revision(
    session: Session,
    individual_id: UUID,
    config: BehaviorConfig,
    now: datetime,
    *,
    revision_id: UUID | None = None,
) -> tuple[ConfigRevisionRecord, bool]:
    # Typed models remain mutable. Validate a detached snapshot before any SQL:
    # callers may catch validation errors and still commit their transaction.
    config = BehaviorConfig.model_validate(config.model_dump(warnings=False))
    now = normalize_utc(now)
    # Lock the parent even before the first revision exists.
    parent = session.scalar(
        select(Individual.individual_id)
        .where(
            Individual.individual_id == individual_id,
        )
        .with_for_update()
    )
    if parent is None:
        raise LookupError("Individual does not exist")
    previous = _active(session, individual_id)
    content_hash = behavior_hash(config)
    if previous is not None and previous.content_hash == content_hash:
        return _snapshot(previous), False
    if previous is not None:
        previous.superseded_at = now
        session.flush()
    row = RuntimeConfigRevision(
        config_revision_id=revision_id or new_id(),
        individual_id=individual_id,
        config_schema_version=config.config_schema_version,
        sanitized_config=config.model_dump(mode="json"),
        content_hash=content_hash,
        created_at=now,
        activated_at=now,
        superseded_at=None,
    )
    session.add(row)
    session.flush()
    return _snapshot(row), True
