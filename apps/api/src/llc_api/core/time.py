from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def utc_now() -> datetime:
    """Return the single timestamp convention used by LLC."""
    return datetime.now(UTC)


def local_date(now: datetime, timezone_name: str):
    """Convert an aware instant to the configured local calendar date."""
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    return now.astimezone(ZoneInfo(timezone_name)).date()
