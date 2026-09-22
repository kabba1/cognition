"""Shared identity, reference, and UTC clock primitives."""

from datetime import UTC, datetime
from typing import Annotated, Protocol
from uuid import UUID, uuid4

from pydantic import AfterValidator, BaseModel, ConfigDict

type CognitionId = UUID


def new_id() -> CognitionId:
    """Generate an application-owned UUIDv4."""
    return uuid4()


def parse_id(value: UUID | str) -> CognitionId:
    """Parse a supplied UUID without generating or substituting an identity."""
    return value if isinstance(value, UUID) else UUID(value)


class Ref(BaseModel):
    """Reference an identity by an open-ended semantic kind."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    id: CognitionId


def normalize_utc(value: datetime) -> datetime:
    """Reject naive datetimes and preserve an aware instant in UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


UTCDateTime = Annotated[datetime, AfterValidator(normalize_utc)]


class Clock(Protocol):
    """Synchronous source of timezone-aware UTC instants."""

    def now(self) -> datetime:
        """Return the current instant as an aware UTC datetime."""
        ...


class SystemClock:
    """Read the system clock directly in UTC."""

    def now(self) -> datetime:
        """Return the current system instant in UTC."""
        return datetime.now(UTC)
