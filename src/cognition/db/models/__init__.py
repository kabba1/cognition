"""Register durable SQLAlchemy models on the shared metadata."""

from cognition.db.models.attention import Wake
from cognition.db.models.audit import AdminAudit
from cognition.db.models.cognition import (
    AppliedOperation,
    AttentionState,
    CognitionCycle,
    CognitionTurn,
    ContextSnapshot,
    CycleWake,
    ModelInvocation,
)
from cognition.db.models.evidence import Event, EventContent
from cognition.db.models.governance import AdminPrincipal, GovernanceState
from cognition.db.models.identity import Individual
from cognition.db.models.runtime import RuntimeConfigRevision, RuntimeInstance

__all__ = [
    "AppliedOperation",
    "AttentionState",
    "CognitionCycle",
    "CognitionTurn",
    "ContextSnapshot",
    "CycleWake",
    "ModelInvocation",
    "AdminAudit",
    "AdminPrincipal",
    "Event",
    "EventContent",
    "GovernanceState",
    "Individual",
    "RuntimeConfigRevision",
    "RuntimeInstance",
    "Wake",
]
