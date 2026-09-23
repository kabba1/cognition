"""Shared detached plans and exact snapshots; no family application or commits."""

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy.orm import Session

from cognition.db.base import Base
from cognition.protocols.common import JsonObject, Ref, normalize_utc


@dataclass
class Change:
    kind: str
    identity: UUID
    model: type[Base]
    row: Base | None
    values: dict[str, Any]
    before: JsonObject | None


@dataclass
class PlannedOperation:
    operation_id: UUID
    kind: str
    action: str
    changes: list[Change]


def snapshot(row: Base) -> JsonObject:
    values: JsonObject = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        if isinstance(value, UUID):
            value = str(value)
        elif isinstance(value, datetime):
            value = normalize_utc(value).isoformat()
        values[column.name] = deepcopy(value)
    return values


def refs(values: Sequence[Ref]) -> list[JsonObject]:
    return [ref.model_dump(mode="json") for ref in values]


def union_refs(old: list[JsonObject], new: Sequence[Ref]) -> list[JsonObject]:
    result = deepcopy(old)
    for ref in refs(new):
        if ref not in result:
            result.append(ref)
    return result


def owned_row(
    session: Session, model: type[Base], identity: UUID | None, individual_id: UUID
) -> Base | None:
    if identity is None:
        return None
    row = session.get(model, identity, populate_existing=True)
    return (
        row
        if row is not None and cast(Any, row).individual_id == individual_id
        else None
    )


def change(
    kind: str,
    model: type[Base],
    identity: UUID,
    row: Base | None,
    values: dict[str, Any],
) -> Change:
    return Change(
        kind, identity, model, row, values, None if row is None else snapshot(row)
    )
