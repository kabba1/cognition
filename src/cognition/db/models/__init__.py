"""Register durable SQLAlchemy models on the shared metadata."""

from cognition.db.models.attention import Wake
from cognition.db.models.governance import AdminPrincipal, GovernanceState
from cognition.db.models.identity import Individual
from cognition.db.models.runtime import RuntimeConfigRevision, RuntimeInstance

__all__ = [
    "AdminPrincipal",
    "GovernanceState",
    "Individual",
    "RuntimeConfigRevision",
    "RuntimeInstance",
    "Wake",
]
