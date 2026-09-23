"""Independent ingress review regressions for retained checkpoint invariants."""

import pytest
from sqlalchemy import select
from test_autonomy import NOW
from test_perception import binding as binding
from test_perception import ingest, modules, page
from test_perception import person as person

from cognition.db.models.evidence import Event
from cognition.db.models.governance import AdminPrincipal


@pytest.mark.parametrize("lost_field", ["event_type", "source_binding_id"])
def test_newer_receipt_cannot_be_hidden_by_marker_reclassification_and_pointer_rewind(
    db_session_factory, person, binding, lost_field
):
    models, _, _, _, store = modules()
    with db_session_factory.begin() as session:
        older = ingest(session, person, binding, [], "first")
        newer = ingest(session, person, binding, [], "second")
    with db_session_factory.begin() as session:
        marker = session.get(Event, newer.receipt_event_id)
        setattr(
            marker, lost_field, "other.event" if lost_field == "event_type" else None
        )
        state = session.get(models.ConnectorBinding, binding)
        state.latest_receipt_event_id = older.receipt_event_id
        state.cursor, state.cursor_revision = "first", 1
    with db_session_factory() as session, pytest.raises(ValueError):
        store.validate_checkpoint(session, person, binding)


def test_lost_pending_pointer_cannot_hide_retained_unsealed_membership(
    db_session_factory, person, binding
):
    models, _, _, _, _ = modules()
    values = [("one", {"text": "retained observation"})]
    with db_session_factory.begin() as session:
        ingest(session, person, binding, values, "first")
    with db_session_factory.begin() as session:
        state = session.get(models.ConnectorBinding, binding)
        state.pending_wake_id = None
    with db_session_factory.begin() as session, pytest.raises(ValueError):
        ingest(session, person, binding, values, "second")


@pytest.mark.parametrize("change", ["dirty", "new", "deleted"])
def test_ingress_does_not_flush_unrelated_pending_administrator_mutations(
    db_session_factory, person, binding, change
):
    _, _, _, _, store = modules()
    with db_session_factory() as session:
        snapshot, normalized = page(session, person, binding, [("one", {})])
        administrator = session.scalar(
            select(AdminPrincipal).where(AdminPrincipal.individual_id == person)
        )
        if change == "dirty":
            administrator.role = "reader"
        elif change == "deleted":
            session.delete(administrator)
        else:
            administrator = AdminPrincipal(
                individual_id=person,
                authn_provider="local_os",
                subject="unreviewed-admin",
                role="admin",
                revoked_at=None,
                principal_metadata={},
            )
            session.add(administrator)
        with pytest.raises(ValueError):
            store.persist_page(session, snapshot, normalized, recorded_at=NOW)
        assert administrator in session.new | session.dirty | session.deleted
