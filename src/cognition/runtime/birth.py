"""Create a thin individual and its initial attention in one transaction."""

from dataclasses import dataclass
from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from cognition.config.revisions import behavior_config
from cognition.config.schema import Configuration
from cognition.protocols.common import (
    Clock,
    JsonObject,
    NonEmptyString,
    ProtocolModel,
    Ref,
    new_id,
    normalize_utc,
)
from cognition.protocols.events_v1 import EventContent, EventEnvelopeV1, EventSource
from cognition.protocols.wakes_v1 import WakeV1
from cognition.stores.attention import create_or_merge_pending_wake
from cognition.stores.configuration import replace_config_revision
from cognition.stores.evidence import append_event
from cognition.stores.governance import create_admin_principal, create_governance
from cognition.stores.identity import create_individual


class BirthInput(ProtocolModel):
    """Explicit genesis, administrative identity, and configuration inputs.

    The optional seeds are supplied by the creator; they do not assert memories,
    interests, or a prior personal history. Deployment configuration is validated
    here but only its allowlisted behavioral subset is persisted.
    """

    individual_id: UUID
    birth_name: NonEmptyString
    founding_orientation: NonEmptyString
    creator_provenance: JsonObject
    admin_authn_provider: NonEmptyString
    admin_subject: NonEmptyString
    config: Configuration
    runtime_version: NonEmptyString
    temperament_seed: JsonObject | None = None
    founding_value_seed: JsonObject | None = None
    parent_individual_id: UUID | None = None
    fork_event_id: UUID | None = None
    hard_boundaries: JsonObject = Field(default_factory=dict)
    budget_policy: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def coherent_identity(self) -> Self:
        if self.config.runtime.individual_id != self.individual_id:
            raise ValueError(
                "config runtime individual_id must match birth individual_id"
            )
        if (self.parent_individual_id is None) != (self.fork_event_id is None):
            raise ValueError(
                "parent_individual_id and fork_event_id must be supplied together"
            )
        if self.parent_individual_id == self.individual_id:
            raise ValueError("an individual cannot be its own parent")
        return self


@dataclass(frozen=True)
class BirthResult:
    individual_id: UUID
    birth_at: datetime
    admin_principal_id: UUID
    config_revision_id: UUID
    genesis_event_id: UUID
    bootstrap_wake_id: UUID


class BirthAlreadyExists(ValueError):
    """The requested identity is already durable; birth never replaces it."""


def birth(
    factory: sessionmaker[Session], request: BirthInput, clock: Clock
) -> BirthResult:
    """Commit all birth rows together, without inference or external effects."""
    # Revalidate a detached snapshot: callers can mutate Pydantic input objects
    # after construction, including nested deployment identity and seed mappings.
    data = BirthInput.model_validate(request.model_dump())
    now = normalize_utc(clock.now())
    result = BirthResult(
        individual_id=data.individual_id,
        birth_at=now,
        admin_principal_id=new_id(),
        config_revision_id=new_id(),
        genesis_event_id=new_id(),
        bootstrap_wake_id=new_id(),
    )
    payload: JsonObject = {
        "individual_id": str(data.individual_id),
        "birth_at": now.isoformat(),
        "birth_name": data.birth_name,
        "founding_orientation": data.founding_orientation,
        "creator_provenance": data.creator_provenance,
        "temperament_seed": data.temperament_seed,
        "founding_value_seed": data.founding_value_seed,
        "parent_individual_id": (
            str(data.parent_individual_id) if data.parent_individual_id else None
        ),
        "fork_event_id": str(data.fork_event_id) if data.fork_event_id else None,
        "admin_principal_id": str(result.admin_principal_id),
        "config_revision_id": str(result.config_revision_id),
    }
    try:
        with factory.begin() as session:
            create_individual(
                session,
                individual_id=data.individual_id,
                birth_at=now,
                birth_name=data.birth_name,
                founding_orientation=data.founding_orientation,
                creator_provenance=data.creator_provenance,
                temperament_seed=data.temperament_seed,
                founding_value_seed=data.founding_value_seed,
                parent_individual_id=data.parent_individual_id,
                fork_event_id=data.fork_event_id,
            )
            create_governance(
                session,
                data.individual_id,
                hard_boundaries=data.hard_boundaries,
                budget_policy=data.budget_policy,
            )
            create_admin_principal(
                session,
                data.individual_id,
                authn_provider=data.admin_authn_provider,
                subject=data.admin_subject,
                role="admin",
                admin_principal_id=result.admin_principal_id,
            )
            replace_config_revision(
                session,
                data.individual_id,
                behavior_config(data.config),
                now,
                revision_id=result.config_revision_id,
            )
            append_event(
                session,
                EventEnvelopeV1(
                    schema_version=1,
                    event_id=result.genesis_event_id,
                    individual_id=data.individual_id,
                    event_type="individual.born",
                    occurred_at=now,
                    observed_at=now,
                    recorded_at=now,
                    source=EventSource(
                        kind="runtime", source_id="birth", binding_id=None
                    ),
                    actor_entity_id=None,
                    causation_event_id=None,
                    correlation_id=None,
                    subject=Ref(kind="individual", id=data.individual_id),
                    provenance={"creator": data.creator_provenance},
                    content=EventContent(
                        content_type="application/json",
                        payload=payload,
                        text=None,
                        blob_ref=None,
                        content_hash=None,
                        sensitivity="internal",
                        retention_class="history",
                        retain_until=None,
                    ),
                    runtime_version=data.runtime_version,
                ),
            )
            create_or_merge_pending_wake(
                session,
                WakeV1(
                    schema_version=1,
                    wake_id=result.bootstrap_wake_id,
                    individual_id=data.individual_id,
                    kind="bootstrap",
                    due_at=now,
                    purpose="First executive attention after birth.",
                    cause_event_id=result.genesis_event_id,
                    context_refs=[Ref(kind="event", id=result.genesis_event_id)],
                    coalesce_key="bootstrap",
                ),
            )
    except IntegrityError as error:
        constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
        if constraint == "pk_individuals":
            raise BirthAlreadyExists("Individual already exists") from error
        raise
    return result
