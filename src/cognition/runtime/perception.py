"""One bounded ingress fetch, outside transactions, on retained ownership."""

from uuid import UUID

from sqlalchemy.orm import Session

from cognition.connectors.base import InboundConnector
from cognition.domain.perception import normalize_page
from cognition.protocols.common import Clock
from cognition.runtime.ownership import RuntimeOwnership
from cognition.stores.connectors import load_connector_binding_snapshot
from cognition.stores.perception import (
    IngestionResult,
    persist_page,
    validate_checkpoint,
)


def ingest_once(
    owner: RuntimeOwnership,
    binding_id: UUID,
    connector: InboundConnector,
    clock: Clock,
) -> IngestionResult:
    """Never acknowledge a source or reconnect a lost advisory-lock session."""
    connection = owner.connection
    if connection.in_transaction():
        raise ValueError("Ingress requires a transaction-free ownership connection")
    with Session(bind=connection) as session, session.begin():
        snapshot = load_connector_binding_snapshot(
            session, owner.individual_id, binding_id
        )
        if not snapshot.enabled or snapshot.operational_status != "active":
            raise ValueError("Ingress source or lifecycle is blocked")
        if (connector.adapter_id, connector.source_id) != (
            snapshot.adapter_id,
            snapshot.source_id,
        ):
            raise ValueError("Connector identity does not match its binding")
        validate_checkpoint(session, owner.individual_id, binding_id)
    batch = connector.poll(snapshot.cursor)
    page = normalize_page(snapshot, batch, observed_at=clock.now())
    # The property fences locally closed/invalidated connections; the SQL layer
    # fences remote loss. Neither path obtains a replacement ownership session.
    if owner.connection is not connection or connection.in_transaction():
        raise ValueError("Ingress ownership connection changed during fetch")
    with Session(bind=connection) as session, session.begin():
        return persist_page(session, snapshot, page, recorded_at=clock.now())
