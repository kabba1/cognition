"""Birth identity is insert-only; lifecycle updates preserve genesis fields."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from cognition.db.models.identity import Individual
from cognition.protocols.common import JsonObject, normalize_utc
from cognition.stores.errors import RevisionConflict

type OperationalStatus = Literal[
    "active", "paused", "quiescing", "quiescent", "retired"
]


@dataclass(frozen=True)
class IndividualRecord:
    individual_id: UUID
    birth_at: datetime
    birth_name: str
    founding_orientation: str
    creator_provenance: JsonObject
    temperament_seed: JsonObject | None
    founding_value_seed: JsonObject | None
    parent_individual_id: UUID | None
    fork_event_id: UUID | None
    operational_status: str
    revision: int


def _snapshot(row: Individual) -> IndividualRecord:
    return IndividualRecord(
        row.individual_id,
        row.birth_at,
        row.birth_name,
        row.founding_orientation,
        deepcopy(row.creator_provenance),
        deepcopy(row.temperament_seed),
        deepcopy(row.founding_value_seed),
        row.parent_individual_id,
        row.fork_event_id,
        row.operational_status,
        row.revision,
    )


def create_individual(
    session: Session,
    *,
    individual_id: UUID,
    birth_at: datetime,
    birth_name: str,
    founding_orientation: str,
    creator_provenance: JsonObject,
    temperament_seed: JsonObject | None = None,
    founding_value_seed: JsonObject | None = None,
    parent_individual_id: UUID | None = None,
    fork_event_id: UUID | None = None,
) -> IndividualRecord:
    row = Individual(
        individual_id=individual_id,
        birth_at=normalize_utc(birth_at),
        birth_name=birth_name,
        founding_orientation=founding_orientation,
        creator_provenance=deepcopy(creator_provenance),
        temperament_seed=deepcopy(temperament_seed),
        founding_value_seed=deepcopy(founding_value_seed),
        parent_individual_id=parent_individual_id,
        fork_event_id=fork_event_id,
        operational_status="active",
        revision=1,
    )
    session.add(row)
    session.flush()
    return _snapshot(row)


def load_individual(session: Session, individual_id: UUID) -> IndividualRecord:
    row = session.get(Individual, individual_id, populate_existing=True)
    if row is None:
        raise LookupError("Individual does not exist")
    return _snapshot(row)


def set_operational_status(
    session: Session,
    individual_id: UUID,
    status: OperationalStatus,
    *,
    expected_revision: int | None = None,
) -> IndividualRecord:
    row = session.scalar(
        select(Individual)
        .where(
            Individual.individual_id == individual_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise LookupError("Individual does not exist")
    if expected_revision is not None and row.revision != expected_revision:
        raise RevisionConflict("Individual revision changed")
    row.operational_status = status
    row.revision += 1
    session.flush()
    return _snapshot(row)
