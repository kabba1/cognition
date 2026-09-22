"""Deterministic clock tests with no sleeping or wall-clock progression."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from cognition.protocols.common import Clock
from cognition.testing.clock import FakeClock


def test_fake_clock_returns_supplied_instant_repeatedly() -> None:
    start = datetime(2026, 9, 22, 12, tzinfo=UTC)
    clock: Clock = FakeClock(start)
    assert clock.now() == clock.now() == start
    assert clock.now().tzinfo is UTC


def test_fake_clock_normalizes_initial_time() -> None:
    clock = FakeClock(datetime(2026, 9, 22, 12, tzinfo=timezone(timedelta(hours=2))))
    assert clock.now() == datetime(2026, 9, 22, 10, tzinfo=UTC)
    assert clock.now().tzinfo is UTC


def test_fake_clock_rejects_naive_initial_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FakeClock(datetime(2026, 9, 22))


def test_advance_changes_only_selected_clock() -> None:
    start = datetime(2026, 9, 22, 12, tzinfo=UTC)
    clock = FakeClock(start)
    independent = FakeClock(start)
    clock.advance(timedelta(days=1, seconds=2, microseconds=3))
    assert clock.now() == datetime(2026, 9, 23, 12, 0, 2, 3, tzinfo=UTC)
    assert clock.now().tzinfo is UTC
    assert independent.now() == start
    assert start == datetime(2026, 9, 22, 12, tzinfo=UTC)


def test_set_normalizes_and_replaces_time() -> None:
    clock = FakeClock(datetime(2026, 9, 22, tzinfo=UTC))
    clock.set(datetime(2026, 9, 21, 10, tzinfo=timezone(timedelta(hours=-4))))
    assert clock.now() == datetime(2026, 9, 21, 14, tzinfo=UTC)
    assert clock.now().tzinfo is UTC


def test_rejected_set_preserves_time() -> None:
    start = datetime(2026, 9, 22, tzinfo=UTC)
    clock = FakeClock(start)
    with pytest.raises(ValueError, match="timezone-aware"):
        clock.set(datetime(2026, 9, 23))
    assert clock.now() == start
