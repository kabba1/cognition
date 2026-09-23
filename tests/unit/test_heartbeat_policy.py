"""Adaptive heartbeat timing is bounded arithmetic, not inferred inner activity."""

import importlib
import math
import sys
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest

ANCHOR = datetime(2026, 9, 23, 12, 0, 0, 123456, tzinfo=UTC)
MAX_UTC = datetime.max.replace(tzinfo=UTC)


def policy(minimum=60.0, maximum=3600.0, factor=2.0):
    return importlib.import_module("cognition.domain.heartbeat").HeartbeatPolicy(
        minimum_seconds=minimum, maximum_seconds=maximum, backoff_factor=factor
    )


@pytest.mark.parametrize(
    "minimum,maximum,factor",
    [
        (0, 1, 2),
        (-1, 1, 2),
        (2, 1, 2),
        (1, 0, 2),
        (1, -1, 2),
        (1, 2, 0),
        (1, 2, 0.999),
        (1, 2, -2),
        (math.inf, math.inf, 2),
        (1, math.inf, 2),
        (1, 2, math.inf),
        (math.nan, 2, 2),
        (1, math.nan, 2),
        (1, 2, math.nan),
        (True, 2, 2),
        (1, True, 2),
        (1, 2, True),
        ("1", 2, 2),
    ],
)
def test_invalid_configuration_rejected(minimum, maximum, factor):
    with pytest.raises(ValueError):
        policy(minimum, maximum, factor)


def test_configuration_and_schedule_are_frozen():
    value = policy()
    with pytest.raises(FrozenInstanceError):
        value.minimum_seconds = 5.0
    schedule = value.schedule(ANCHOR, 60)
    with pytest.raises(FrozenInstanceError):
        schedule.due_at = ANCHOR


def test_repeated_autonomous_noop_backs_off_deterministically_and_caps():
    value = policy(10, 100, 2)
    interval = 10.0
    intervals = []
    for _ in range(6):
        interval = value.next_interval(interval, autonomous=True, activity=False)
        intervals.append(interval)
    assert intervals == [20, 40, 80, 100, 100, 100]


@pytest.mark.parametrize(
    "autonomous,activity", [(True, True), (False, True), (False, False)]
)
def test_recorded_activity_or_external_cycle_resets_to_minimum(autonomous, activity):
    value = policy(10, 100, 2)
    assert value.next_interval(80, autonomous=autonomous, activity=activity) == 10


def test_factor_one_does_not_increase_interval():
    value = policy(1, 100, 1)
    assert value.next_interval(40, autonomous=True, activity=False) == 40


def test_effective_one_second_floor_and_fractional_backoff():
    tiny = policy(0.2, 0.8, 1.5)
    assert tiny.effective_minimum_seconds == tiny.effective_maximum_seconds == 1
    assert tiny.clamp(0.1) == 1
    assert tiny.schedule(ANCHOR, 0.3).due_at == ANCHOR + timedelta(seconds=1)
    fractional = policy(0.2, 2.75, 1.25)
    assert fractional.next_interval(1.5, autonomous=True, activity=False) == 1.875
    assert fractional.next_interval(2.5, autonomous=True, activity=False) == 2.75
    assert fractional.schedule(ANCHOR, 1.125).due_at == ANCHOR + timedelta(
        seconds=1.125
    )


def test_retained_interval_is_clamped_under_new_configuration():
    value = policy(10, 100)
    assert value.clamp(0.001) == 10
    assert value.clamp(50) == 50
    assert value.clamp(1e300) == 100
    assert value.next_interval(1e300, autonomous=True, activity=False) == 100


@pytest.mark.parametrize("interval", [0, -1, math.nan, math.inf, -math.inf, True, "10"])
def test_invalid_retained_intervals_are_not_silently_repaired(interval):
    value = policy()
    with pytest.raises(ValueError):
        value.clamp(interval)
    with pytest.raises(ValueError):
        value.next_interval(interval, autonomous=False, activity=True)
    with pytest.raises(ValueError):
        value.schedule(ANCHOR, interval)


@pytest.mark.parametrize(
    "interval,factor,expected",
    [
        (1e300, 1e300, sys.float_info.max),
        (sys.float_info.max, 2.0, sys.float_info.max),
        (sys.float_info.max / 4, 2.0, sys.float_info.max / 2),
    ],
)
def test_huge_finite_backoff_saturates_without_infinity(interval, factor, expected):
    result = policy(1, sys.float_info.max, factor).next_interval(
        interval, autonomous=True, activity=False
    )
    assert math.isfinite(result) and result == expected


@pytest.mark.parametrize(
    "anchor,seconds",
    [(ANCHOR, 1e300), (MAX_UTC, 1), (MAX_UTC - timedelta(seconds=1), 2)],
)
def test_calendar_addition_saturates_at_greatest_utc_timestamp(anchor, seconds):
    result = policy(1, sys.float_info.max).schedule(anchor, seconds)
    assert result.due_at == MAX_UTC and result.commitment_shortened is False


def test_calendar_near_maximum_preserves_representable_microseconds():
    anchor = MAX_UTC - timedelta(seconds=2, microseconds=5)
    result = policy(1, 10).schedule(anchor, 2)
    assert result.due_at == MAX_UTC - timedelta(microseconds=5)


@pytest.mark.parametrize("offset", [-1, 0, 5, 60, 120, 180])
def test_deadline_shortening_is_anchored_floored_and_only_when_earlier(offset):
    result = policy(60, 3600).schedule(
        ANCHOR, 120, commitment_due=ANCHOR + timedelta(seconds=offset)
    )
    if 0 < offset < 120:
        assert result.due_at == ANCHOR + timedelta(seconds=max(60, offset))
        assert result.commitment_shortened is True
    else:
        assert result.due_at == ANCHOR + timedelta(seconds=120)
        assert result.commitment_shortened is False


def test_minimum_interval_cannot_be_shortened_further():
    result = policy(60, 3600).schedule(
        ANCHOR, 60, commitment_due=ANCHOR + timedelta(seconds=5)
    )
    assert result.due_at == ANCHOR + timedelta(seconds=60)
    assert result.commitment_shortened is False


def test_utc_normalization_preserves_instants_and_ignores_local_clock_order():
    east = timezone(timedelta(hours=9, minutes=30))
    west = timezone(timedelta(hours=-7))
    anchor = ANCHOR.astimezone(east)
    due = (ANCHOR + timedelta(seconds=90)).astimezone(west)
    result = policy(60, 3600).schedule(anchor, 120, commitment_due=due)
    assert result.due_at == ANCHOR + timedelta(seconds=90)
    assert result.due_at.tzinfo is UTC and result.commitment_shortened


@pytest.mark.parametrize("which", ["anchor", "commitment"])
def test_naive_datetimes_are_rejected(which):
    with pytest.raises(ValueError):
        policy().schedule(
            ANCHOR.replace(tzinfo=None) if which == "anchor" else ANCHOR,
            120,
            commitment_due=ANCHOR.replace(tzinfo=None)
            if which == "commitment"
            else None,
        )


def test_unrepresentable_utc_anchor_is_rejected_without_wrapping_calendar():
    anchor = datetime.max.replace(tzinfo=timezone(timedelta(hours=-1)))
    with pytest.raises(ValueError):
        policy().schedule(anchor, 60)


def test_saturated_default_can_still_be_shortened_by_a_future_commitment():
    result = policy(1, sys.float_info.max).schedule(
        ANCHOR, sys.float_info.max, commitment_due=ANCHOR + timedelta(days=1)
    )
    assert result.due_at == ANCHOR + timedelta(days=1)
    assert result.commitment_shortened is True
