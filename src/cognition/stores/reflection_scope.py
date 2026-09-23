"""Immutable managed reflection scope, independent of current scheduler pointers."""

import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from cognition.db.models.attention import Wake
from cognition.db.models.development import Interest, Preference, SelfState
from cognition.db.models.evidence import Event
from cognition.db.models.reflection import ManagedReflectionBatch, ReflectionState
from cognition.protocols.common import Ref, normalize_utc

REFLECTION_MARKER_TYPE = "attention.reflection_scheduled"
REFLECTION_MARKER_SOURCE = "attention.reflection"
_TARGET_MODELS = {
    "interest": Interest,
    "preference": Preference,
    "self_state": SelfState,
}


def _identity(value: object) -> UUID:
    if not isinstance(value, (UUID, str)):
        raise ValueError("Reflection identity must be a UUID")
    try:
        return value if isinstance(value, UUID) else UUID(value)
    except ValueError as error:
        raise ValueError("Reflection identity must be a UUID") from error


def _instant(value: object) -> str:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError("Reflection timestamp is invalid") from error
    if not isinstance(value, datetime):
        raise ValueError("Reflection timestamp must be an aware datetime")
    try:
        return normalize_utc(value).isoformat()
    except (ValueError, OverflowError) as error:
        raise ValueError(
            "Reflection timestamp must have a representable UTC instant"
        ) from error


def _canonical_batch(
    *,
    individual_id: UUID,
    wake_id: UUID,
    policy_version: int,
    selected_at: datetime,
    target_metadata: object,
) -> tuple[dict[str, Any], tuple[Ref, ...]]:
    if type(policy_version) is not int or policy_version != 1:
        raise ValueError("Reflection policy must be integer 1")
    if not isinstance(target_metadata, list) or not 1 <= len(target_metadata) <= 8:
        raise ValueError("Reflection batch must contain one to eight targets")
    normalized = []
    refs = []
    seen: set[tuple[str, UUID]] = set()
    for item in target_metadata:
        if not isinstance(item, dict) or set(item) != {
            "kind",
            "id",
            "revision",
            "eligible_at",
        }:
            raise ValueError("Reflection target metadata has an invalid shape")
        kind = item["kind"]
        if not isinstance(kind, str) or kind not in _TARGET_MODELS:
            raise ValueError("Reflection target kind is unsupported")
        identity = _identity(item["id"])
        revision = item["revision"]
        if type(revision) is not int or revision < 1:
            raise ValueError("Reflection target revision must be a positive integer")
        if (kind, identity) in seen:
            raise ValueError("Reflection targets must be distinct")
        seen.add((kind, identity))
        refs.append(Ref(kind=kind, id=identity))
        normalized.append(
            dict(
                kind=kind,
                id=str(identity),
                revision=revision,
                eligible_at=_instant(item["eligible_at"]),
            )
        )
    return dict(
        individual_id=str(_identity(individual_id)),
        wake_id=str(_identity(wake_id)),
        policy_version=policy_version,
        selected_at=_instant(selected_at),
        target_metadata=normalized,
    ), tuple(refs)


def reflection_batch_hash(
    *,
    individual_id: UUID,
    wake_id: UUID,
    policy_version: int,
    selected_at: datetime,
    target_metadata: object,
) -> str:
    """Bind the exact ordered target scope and its batch identity to policy 1."""
    canonical, _ = _canonical_batch(
        individual_id=individual_id,
        wake_id=wake_id,
        policy_version=policy_version,
        selected_at=selected_at,
        target_metadata=target_metadata,
    )
    rendered = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def validate_reflection_batch_snapshot(
    *,
    individual_id: UUID,
    wake_id: UUID,
    policy_version: int,
    selected_at: datetime,
    target_metadata: object,
    content_hash: str,
) -> tuple[Ref, ...]:
    """Validate detached immutable metadata, without consulting current revisions."""
    canonical, refs = _canonical_batch(
        individual_id=individual_id,
        wake_id=wake_id,
        policy_version=policy_version,
        selected_at=selected_at,
        target_metadata=target_metadata,
    )
    rendered = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    if hashlib.sha256(rendered.encode("utf-8")).hexdigest() != content_hash:
        raise ValueError("Reflection batch content hash mismatch")
    return refs


def managed_reflection_scope(
    session: Session, individual_id: UUID, wake_id: UUID
) -> tuple[Ref, ...] | None:
    """Return managed scope, None for ordinary wakes, and fail closed on corruption.

    Retained event envelopes and current pointers independently identify managed
    wakes even if a historical batch was removed. Redacted event payloads are never
    consulted. Core snapshots avoid flushing or refreshing the caller's ORM state.
    """
    with session.no_autoflush:
        wake = (
            session.execute(
                select(
                    Wake.wake_id,
                    Wake.individual_id,
                    Wake.kind,
                    Wake.coalesce_key,
                    Wake.cause_event_id,
                    Wake.context_refs,
                ).where(Wake.wake_id == wake_id)
            )
            .mappings()
            .one_or_none()
        )
        if wake is None or wake.individual_id != individual_id:
            raise ValueError("Reflection wake is missing or foreign")
        batch = (
            session.execute(
                select(ManagedReflectionBatch.__table__).where(
                    ManagedReflectionBatch.wake_id == wake_id
                )
            )
            .mappings()
            .one_or_none()
        )
        pointers = session.scalars(
            select(ReflectionState.individual_id).where(
                ReflectionState.managed_wake_id == wake_id
            )
        ).all()
        markers = (
            session.execute(
                select(
                    Event.event_id,
                    Event.individual_id,
                    Event.source_kind,
                    Event.source_id,
                    Event.subject_kind,
                    Event.subject_id,
                )
                .where(
                    Event.event_type == REFLECTION_MARKER_TYPE,
                    or_(
                        and_(Event.subject_kind == "wake", Event.subject_id == wake_id),
                        Event.event_id == wake.cause_event_id,
                    ),
                )
                .limit(2)
            )
            .mappings()
            .all()
        )
        if batch is None and not markers and not pointers:
            return None
        if (
            batch is None
            or batch.individual_id != individual_id
            or wake.kind != "reflection"
            or wake.coalesce_key is not None
            or any(owner != individual_id for owner in pointers)
            or len(markers) != 1
        ):
            raise ValueError("Managed reflection metadata is missing or inconsistent")
        marker = markers[0]
        if (
            marker.individual_id != individual_id
            or marker.source_kind != "runtime"
            or marker.source_id != REFLECTION_MARKER_SOURCE
            or marker.subject_kind != "wake"
            or marker.subject_id != wake_id
            or wake.cause_event_id != marker.event_id
        ):
            raise ValueError("Managed reflection creation marker is inconsistent")
        refs = validate_reflection_batch_snapshot(
            individual_id=batch.individual_id,
            wake_id=batch.wake_id,
            policy_version=batch.policy_version,
            selected_at=batch.selected_at,
            target_metadata=batch.target_metadata,
            content_hash=batch.content_hash,
        )
        if not isinstance(wake.context_refs, list):
            raise ValueError("Managed reflection wake scope is invalid")
        if tuple(Ref.model_validate(value) for value in wake.context_refs) != refs:
            raise ValueError("Managed reflection wake scope differs from its batch")
        for ref in refs:
            model = _TARGET_MODELS[ref.kind]
            table = model.__table__
            columns = [table.c.individual_id]
            if ref.kind == "self_state":
                columns.append(table.c.layer)
            row = (
                session.execute(
                    select(*columns).where(
                        table.c[f"{ref.kind}_id"] == ref.id,
                        table.c.individual_id == individual_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None or (
                ref.kind == "self_state"
                and row.layer not in {"self_belief", "current_value"}
            ):
                raise ValueError(
                    "Managed reflection target is missing, foreign or unsupported"
                )
        return refs
