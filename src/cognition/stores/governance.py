"""Focused authority stores; all writes join the caller's transaction."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from cognition.db.models.governance import AdminPrincipal, GovernanceState
from cognition.protocols.common import JsonObject, new_id
from cognition.stores.errors import RevisionConflict


@dataclass(frozen=True)
class GovernanceRecord:
    individual_id: UUID
    external_actions_blocked: bool
    inference_blocked: bool
    reconciliation_required: bool
    hard_boundaries: JsonObject
    budget_policy: JsonObject
    revision: int


def _snapshot(row: GovernanceState) -> GovernanceRecord:
    return GovernanceRecord(
        row.individual_id,
        row.external_actions_blocked,
        row.inference_blocked,
        row.reconciliation_required,
        deepcopy(row.hard_boundaries),
        deepcopy(row.budget_policy),
        row.revision,
    )


def create_governance(
    session: Session,
    individual_id: UUID,
    *,
    hard_boundaries: JsonObject | None = None,
    budget_policy: JsonObject | None = None,
) -> GovernanceRecord:
    row = GovernanceState(
        individual_id=individual_id,
        external_actions_blocked=True,
        inference_blocked=False,
        reconciliation_required=False,
        hard_boundaries=deepcopy(hard_boundaries or {}),
        budget_policy=deepcopy(budget_policy or {}),
        revision=1,
    )
    session.add(row)
    session.flush()
    return _snapshot(row)


def load_governance(session: Session, individual_id: UUID) -> GovernanceRecord:
    row = session.get(GovernanceState, individual_id, populate_existing=True)
    if row is None:
        raise LookupError("Governance state does not exist")
    return _snapshot(row)


def update_governance(
    session: Session,
    individual_id: UUID,
    *,
    expected_revision: int | None = None,
    external_actions_blocked: bool | None = None,
    inference_blocked: bool | None = None,
    reconciliation_required: bool | None = None,
) -> GovernanceRecord:
    row = session.scalar(
        select(GovernanceState)
        .where(
            GovernanceState.individual_id == individual_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise LookupError("Governance state does not exist")
    if expected_revision is not None and expected_revision != row.revision:
        raise RevisionConflict("Governance revision changed")
    if external_actions_blocked is not None:
        row.external_actions_blocked = external_actions_blocked
    if inference_blocked is not None:
        row.inference_blocked = inference_blocked
    if reconciliation_required is not None:
        row.reconciliation_required = reconciliation_required
    row.revision += 1
    session.flush()
    return _snapshot(row)


@dataclass(frozen=True)
class AdminPrincipalRecord:
    admin_principal_id: UUID
    individual_id: UUID
    authn_provider: str
    subject: str
    role: str
    revoked_at: datetime | None


def _principal(row: AdminPrincipal) -> AdminPrincipalRecord:
    return AdminPrincipalRecord(
        row.admin_principal_id,
        row.individual_id,
        row.authn_provider,
        row.subject,
        row.role,
        row.revoked_at,
    )


def create_admin_principal(
    session: Session,
    individual_id: UUID,
    *,
    authn_provider: str,
    subject: str,
    role: str = "admin",
    admin_principal_id: UUID | None = None,
) -> AdminPrincipalRecord:
    row = AdminPrincipal(
        admin_principal_id=admin_principal_id or new_id(),
        individual_id=individual_id,
        authn_provider=authn_provider,
        subject=subject,
        role=role,
        revoked_at=None,
        principal_metadata={},
    )
    session.add(row)
    session.flush()
    return _principal(row)


def find_admin_principal(
    session: Session,
    individual_id: UUID,
    *,
    authn_provider: str,
    subject: str,
) -> AdminPrincipalRecord | None:
    row = session.scalar(
        select(AdminPrincipal)
        .where(
            AdminPrincipal.individual_id == individual_id,
            AdminPrincipal.authn_provider == authn_provider,
            AdminPrincipal.subject == subject,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return None if row is None else _principal(row)
