"""The last whole local day, across the clock changes and around gaps."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from custom_components.bright_meter.coordinator import DayTotal, last_full_day

LONDON = ZoneInfo("Europe/London")


def _hours(start: datetime, end: datetime, value: float = 0.5) -> dict:
    out = {}
    moment = start
    while moment < end:
        out[moment] = value
        moment += timedelta(hours=1)
    return out


def test_the_day_the_clocks_go_back_has_25_hours() -> None:
    """Sun 25 Oct 2026: BST ends, and the day is 25 hours long."""
    start = datetime(2026, 10, 24, 23, tzinfo=UTC)  # 00:00 BST on the 25th
    end = datetime(2026, 10, 26, 0, tzinfo=UTC)  # 00:00 GMT on the 26th
    hourly = _hours(start, end)

    total = last_full_day(hourly, end, LONDON, datetime(2026, 10, 26, 9, tzinfo=UTC))

    assert total == DayTotal(day=datetime(2026, 10, 25).date(), value=12.5)


def test_the_day_the_clocks_go_forward_has_23_hours() -> None:
    """Sun 28 Mar 2027: BST starts, and the day is 23 hours long."""
    start = datetime(2027, 3, 28, 0, tzinfo=UTC)
    end = datetime(2027, 3, 28, 23, tzinfo=UTC)  # 00:00 BST on the 29th
    hourly = _hours(start, end)

    total = last_full_day(hourly, end, LONDON, datetime(2027, 3, 29, 9, tzinfo=UTC))

    assert total == DayTotal(day=datetime(2027, 3, 28).date(), value=11.5)


def test_a_day_with_a_gap_is_skipped_not_reported_short() -> None:
    """Yesterday has a missing hour, so the day before is the last full one."""
    start = datetime(2026, 11, 1, 0, tzinfo=UTC)
    end = datetime(2026, 11, 3, 0, tzinfo=UTC)
    hourly = _hours(start, end)
    hourly[datetime(2026, 11, 2, 13, tzinfo=UTC)] = None

    total = last_full_day(hourly, end, LONDON, datetime(2026, 11, 3, 9, tzinfo=UTC))

    assert total == DayTotal(day=datetime(2026, 11, 1).date(), value=12.0)


def test_a_day_past_the_data_is_not_whole_yet() -> None:
    """Data reaching 20:00 yesterday makes the day before the last full one."""
    start = datetime(2026, 11, 1, 0, tzinfo=UTC)
    data_until = datetime(2026, 11, 2, 20, tzinfo=UTC)
    hourly = _hours(start, data_until)

    total = last_full_day(
        hourly, data_until, LONDON, datetime(2026, 11, 3, 9, tzinfo=UTC)
    )

    assert total == DayTotal(day=datetime(2026, 11, 1).date(), value=12.0)


def test_no_data_is_no_day() -> None:
    """Nothing collected yet: unknown, not zero."""
    now = datetime(2026, 11, 3, 9, tzinfo=UTC)
    assert last_full_day({}, None, LONDON, now) is None
    assert last_full_day({now: 1.0}, None, LONDON, now) is None
