"""Deterministic administrative authorization and lifecycle policy."""

from dataclasses import dataclass

from cognition.stores.governance import AdminPrincipalRecord
from cognition.stores.identity import OperationalStatus


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """Identity established by a trusted local transport, never event content."""

    authn_provider: str
    subject: str


TRANSITIONS: dict[tuple[str, str], OperationalStatus] = {
    ("active", "pause"): "paused",
    ("paused", "resume"): "active",
    ("quiescent", "resume"): "active",
    ("active", "begin_quiesce"): "quiescing",
    ("paused", "begin_quiesce"): "quiescing",
    ("quiescing", "complete_quiesce"): "quiescent",
    ("quiescing", "abort_quiesce"): "paused",
    ("paused", "retire"): "retired",
    ("quiescent", "retire"): "retired",
}
ADMIN_OPERATIONS = (
    "pause",
    "resume",
    "begin_quiesce",
    "complete_quiesce",
    "abort_quiesce",
    "retire",
    "emergency_block",
)


def require_admin(principal: AdminPrincipalRecord | None) -> AdminPrincipalRecord:
    if (
        principal is None
        or principal.revoked_at is not None
        or principal.role != "admin"
    ):
        raise PermissionError("An active administrator for this individual is required")
    return principal


def next_operational_status(status: str, operation: str) -> OperationalStatus:
    try:
        return TRANSITIONS[status, operation]
    except KeyError:
        raise ValueError(
            f"Invalid lifecycle transition: {status} -> {operation}"
        ) from None
