"""
timeofday.py — the market's clock in ET (the server runs on UTC).
"""

from datetime import datetime, timezone, time as dtime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def now_et() -> datetime:
    return datetime.now(ET)


def market_date(ts: str | None = None) -> str:
    """
    Trading-day date (ET) as YYYY-MM-DD, for "today" boundaries. The server runs on
    UTC, so date.today() rolls over at 8 PM ET. ts: an ISO timestamp (stored ones are
    UTC; naive ones are treated as UTC); defaults to now. Empty/bad ts gives "".
    """
    if ts is None:
        return now_et().date().isoformat()
    try:
        d = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return ""
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(ET).date().isoformat()


def is_market_open() -> bool:
    """True on weekdays 9:30–16:00 ET (exchange holidays are not known here; Alpaca's clock is)."""
    now = now_et()
    if now.weekday() >= 5:
        return False
    return dtime(9, 30) <= now.time() < dtime(16, 0)
