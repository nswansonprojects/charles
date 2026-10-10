"""
timeofday.py — Time-of-day trading rules.
Avoids the noisy open (9:30–10:00 ET) and pre-close (3:45–4:00 ET).
Defines optimal trading windows and session metadata.
"""

import logging
from datetime import datetime, timezone, time as dtime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# Windows where we suppress NEW entries (existing positions still managed)
AVOID_WINDOWS = [
    (dtime(9, 30), dtime(10, 0),  "Opening noise window"),
    (dtime(15, 45), dtime(16, 0), "Pre-close window"),
]

# Best trading windows — highest signal quality
OPTIMAL_WINDOWS = [
    (dtime(10, 0),  dtime(11, 30), "Morning trend window"),
    (dtime(13, 30), dtime(15, 30), "Afternoon trend window"),
]


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
    """True if US stock market is currently open."""
    now = now_et()
    if now.weekday() >= 5:   # Saturday=5, Sunday=6
        return False
    t = now.time()
    return dtime(9, 30) <= t < dtime(16, 0)


def in_avoid_window() -> tuple[bool, str]:
    """
    Returns (should_avoid, reason).
    True during opening noise and pre-close windows.
    """
    t = now_et().time()
    for start, end, name in AVOID_WINDOWS:
        if start <= t <= end:
            log.info(f"  ⏰ Time-of-day filter: {name} — skipping new entries")
            return True, name
    return False, ""


def in_optimal_window() -> bool:
    """Returns True if we're in a high-quality trading window."""
    t = now_et().time()
    return any(s <= t <= e for s, e, _ in OPTIMAL_WINDOWS)


def session_info() -> dict:
    """Returns current session metadata for the LLM prompt."""
    now   = now_et()
    t     = now.time()
    open_ = is_market_open()

    # Time remaining in session
    if open_:
        close_dt = now.replace(hour=16, minute=0, second=0, microsecond=0)
        mins_left = (close_dt - now).seconds // 60
    else:
        mins_left = 0

    # Session phase
    if not open_:
        phase = "after_hours"
    elif t < dtime(10, 0):
        phase = "open_noise"
    elif t < dtime(11, 30):
        phase = "morning_trend"
    elif t < dtime(13, 30):
        phase = "midday_lull"
    elif t < dtime(15, 30):
        phase = "afternoon_trend"
    else:
        phase = "pre_close"

    avoid, avoid_reason = in_avoid_window()

    return {
        "market_open":    open_,
        "phase":          phase,
        "mins_left":      mins_left,
        "avoid_trading":  avoid,
        "avoid_reason":   avoid_reason,
        "optimal_window": in_optimal_window(),
        "time_et":        now.strftime("%H:%M ET"),
    }
