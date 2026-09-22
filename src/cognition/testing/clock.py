"""Explicitly controlled time for deterministic tests."""

from datetime import datetime, timedelta

from cognition.protocols.common import normalize_utc


class FakeClock:
    """Keep one UTC instant, changed only by advance() or set()."""

    def __init__(self, start: datetime) -> None:
        self._now = normalize_utc(start)

    def now(self) -> datetime:
        """Return the stored instant without advancing time."""
        return self._now

    def advance(self, duration: timedelta) -> None:
        """Add a duration to this clock without sleeping or changing other clocks."""
        self._now += duration

    def set(self, value: datetime) -> None:
        """Replace the stored instant, rejecting naive values before mutation."""
        self._now = normalize_utc(value)
