"""Work calendar.

Simplified calendar used during execution: Monday-Friday are workdays, each
workday is 24 clock hours for ordering purposes, weekends are non-working.
There are no holidays. The calendar only converts between report timestamps
and elapsed workdays; all schedule offsets are workday counts.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

_SATURDAY = 5
_ONE_DAY = timedelta(days=1)


def to_utc(ts: str | datetime) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware UTC datetime."""
    if isinstance(ts, datetime):
        dt = ts
    else:
        text = ts.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _midnight(dt: datetime) -> datetime:
    return datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)


def elapsed_workdays(a: datetime, b: datetime) -> float:
    """Elapsed working time from a to b along the simplified calendar.

    Weekday boundaries split the interval into day pieces; a piece inside a
    Monday-Friday day contributes its fraction of 24h, weekend pieces zero.
    Negative when b < a.
    """
    if b < a:
        return -elapsed_workdays(b, a)
    total = 0.0
    cur = a
    while cur.date() != b.date():
        if cur.weekday() < _SATURDAY:
            total += (max(0.0, (_midnight(cur) + _ONE_DAY - cur).total_seconds()) / 3600.0) / 24.0
        cur = _midnight(cur) + _ONE_DAY
    if b.weekday() < _SATURDAY:
        total += (max(0.0, (b - cur).total_seconds()) / 3600.0) / 24.0
    return total
