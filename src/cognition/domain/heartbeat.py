"""Pure policy-1 heartbeat timing; no persistence, inference or authority."""

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cognition.protocols.common import normalize_utc

HEARTBEAT_POLICY_VERSION = 1
_MAX_UTC = datetime.max.replace(tzinfo=UTC)


def _positive_finite(value: float, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite positive number")
    try:
        result = float(value)
    except OverflowError:
        raise ValueError(f"{field} must be a finite positive number") from None
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{field} must be a finite positive number")
    return result


def _utc(value: datetime) -> datetime:
    try:
        return normalize_utc(value)
    except OverflowError:
        raise ValueError("datetime has no representable UTC instant") from None


def _add_seconds(anchor: datetime, seconds: float) -> datetime:
    """Saturate before constructing timedelta; retain representable microseconds."""
    available = _MAX_UTC - anchor
    available_seconds = available.days * 86400 + available.seconds
    whole_seconds = int(seconds)
    if whole_seconds > available_seconds:
        return _MAX_UTC
    microseconds = round((seconds - whole_seconds) * 1_000_000)
    if whole_seconds == available_seconds and microseconds >= available.microseconds:
        return _MAX_UTC
    return anchor + timedelta(seconds=whole_seconds, microseconds=microseconds)


@dataclass(frozen=True)
class HeartbeatSchedule:
    due_at: datetime
    commitment_shortened: bool


@dataclass(frozen=True)
class HeartbeatPolicy:
    minimum_seconds: float
    maximum_seconds: float
    backoff_factor: float

    def __post_init__(self) -> None:
        for field in ("minimum_seconds", "maximum_seconds", "backoff_factor"):
            object.__setattr__(
                self, field, _positive_finite(getattr(self, field), field)
            )
        if self.maximum_seconds < self.minimum_seconds:
            raise ValueError("maximum_seconds must be at least minimum_seconds")
        if self.backoff_factor < 1:
            raise ValueError("backoff_factor must be at least one")

    @property
    def effective_minimum_seconds(self) -> float:
        return max(1.0, self.minimum_seconds)

    @property
    def effective_maximum_seconds(self) -> float:
        return max(self.effective_minimum_seconds, self.maximum_seconds)

    def clamp(self, interval: float) -> float:
        """Clamp valid retained state; reject corruption instead of repairing it."""
        checked = _positive_finite(interval, "interval")
        return min(
            self.effective_maximum_seconds,
            max(self.effective_minimum_seconds, checked),
        )

    def next_interval(
        self, interval: float, *, autonomous: bool, activity: bool
    ) -> float:
        """Activity or a nonautonomous wake resets; autonomous inactivity backs off.

        The caller classifies all applied turns and wake kinds. A later failure
        cannot erase recorded activity, and failures need no separate policy flag.
        """
        retained = self.clamp(interval)
        if activity or not autonomous:
            return self.effective_minimum_seconds
        maximum = self.effective_maximum_seconds
        if retained >= maximum / self.backoff_factor:
            return maximum
        return min(maximum, retained * self.backoff_factor)

    def schedule(
        self,
        anchor: datetime,
        interval: float,
        *,
        commitment_due: datetime | None = None,
    ) -> HeartbeatSchedule:
        """Anchor one future opportunity, optionally shortened by a future deadline.

        Overdue deadlines do not defeat backoff. Time passing does not change the
        anchor or shift a pending opportunity forward; both are caller-owned state.
        """
        anchor = _utc(anchor)
        due_at = _add_seconds(anchor, self.clamp(interval))
        if commitment_due is not None:
            commitment_due = _utc(commitment_due)
            if commitment_due > anchor:
                earliest = _add_seconds(anchor, self.effective_minimum_seconds)
                shortened = max(earliest, commitment_due)
                if shortened < due_at:
                    return HeartbeatSchedule(shortened, True)
        return HeartbeatSchedule(due_at, False)
