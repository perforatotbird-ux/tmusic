"""
T-Music shared time helpers.

datetime.utcnow() is deprecated since Python 3.12 and will be removed.
All DB columns are naive DateTime, so every helper here returns NAIVE UTC —
never mix these with timezone-aware datetimes (comparison raises TypeError).
"""
from datetime import datetime, timezone


def utcnow() -> datetime:
    """Current UTC time as a naive datetime (drop-in for datetime.utcnow)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utcfromtimestamp(ts: float) -> datetime:
    """Naive-UTC counterpart of datetime.utcfromtimestamp."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
