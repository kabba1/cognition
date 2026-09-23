"""Shared identity, reference, and UTC clock primitives."""

from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol
from uuid import UUID, uuid4

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
)

type CognitionId = UUID
type JsonObject = dict[str, JsonValue]
NonEmptyString = Annotated[str, Field(min_length=1)]


def _integer_version(value: object) -> object:
    if type(value) is not int:
        raise ValueError("schema version must be the integer 1")
    return value


VersionOne = Annotated[Literal[1], BeforeValidator(_integer_version)]


def _integer_version_two(value: object) -> object:
    if type(value) is not int:
        raise ValueError("schema version must be the integer 2")
    return value


VersionTwo = Annotated[Literal[2], BeforeValidator(_integer_version_two)]


class ProtocolModel(BaseModel):
    """Shared strict object boundary; flexible payloads remain JSON-only."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def new_id() -> CognitionId:
    """Generate an application-owned UUIDv4."""
    return uuid4()


def parse_id(value: UUID | str) -> CognitionId:
    """Parse a supplied UUID without generating or substituting an identity."""
    return value if isinstance(value, UUID) else UUID(value)


class Ref(BaseModel):
    """Reference an identity by an open-ended semantic kind."""

    model_config = ConfigDict(extra="forbid")

    kind: NonEmptyString
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
