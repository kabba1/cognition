"""Reconcile sanitized behavior revisions and their evidence atomically."""

from dataclasses import dataclass
from importlib.metadata import version
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from cognition.config.revisions import behavior_config
from cognition.config.schema import Configuration
from cognition.protocols.common import Clock, Ref, new_id, normalize_utc
from cognition.protocols.events_v1 import EventContent, EventEnvelopeV1, EventSource
from cognition.stores.configuration import ConfigRevisionRecord, replace_config_revision
from cognition.stores.evidence import append_event


@dataclass(frozen=True)
class ConfigReconciliationResult:
    revision: ConfigRevisionRecord
    changed: bool
    event_id: UUID | None


def reconcile_config(
    factory: sessionmaker[Session],
    individual_id: UUID,
    config: Configuration,
    clock: Clock,
) -> ConfigReconciliationResult:
    """Record a behavior change with one ``config.changed`` event, or do nothing.

    The configuration store locks the individual even when no revision exists,
    serializing concurrent reconciliation. This service owns the transaction:
    supersession, activation, and evidence either all commit or all roll back.
    Deployment settings and environment values are never passed to either store.
    """
    if config.runtime.individual_id != individual_id:
        raise ValueError(
            "config runtime individual does not match the target individual"
        )
    sanitized = behavior_config(config)
    now = normalize_utc(clock.now())
    with factory.begin() as session:
        revision, changed = replace_config_revision(
            session, individual_id, sanitized, now
        )
        if not changed:
            return ConfigReconciliationResult(revision, False, None)
        event_id = new_id()
        envelope = EventEnvelopeV1(
            schema_version=1,
            event_id=event_id,
            individual_id=individual_id,
            event_type="config.changed",
            occurred_at=now,
            observed_at=now,
            recorded_at=now,
            source=EventSource(
                kind="runtime", source_id="config.reconciliation", binding_id=None
            ),
            actor_entity_id=None,
            causation_event_id=None,
            correlation_id=None,
            subject=Ref(kind="config_revision", id=revision.config_revision_id),
            provenance={"operation": "config.reconciliation"},
            content=EventContent(
                content_type="application/json",
                payload={
                    "config_revision_id": str(revision.config_revision_id),
                    "config_schema_version": revision.config_schema_version,
                    "content_hash": revision.content_hash,
                },
                text=None,
                blob_ref=None,
                content_hash=None,
                sensitivity="internal",
                retention_class="history",
                retain_until=None,
            ),
            runtime_version=version("cognition"),
        )
        append_event(session, envelope)
        return ConfigReconciliationResult(revision, True, event_id)
