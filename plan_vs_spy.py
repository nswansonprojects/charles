"""
plan_vs_spy.py — the plan since it started vs SPY, both measured at the same market close.

Read-only and pure (no network, no files): the caller passes the equity curve and SPY's daily
closes. The curve also has points outside market hours (midnight day rollovers, restarts, weekend
runs), whose account value carries after-hours marks; those are ignored here, so Charles's side is
the last point taken in the session (9:30-16:00 ET) of the same day as SPY's close.
"""

from datetime import datetime, time as dtime, timezone

from timeofday import ET, market_date

OPEN, CLOSE = dtime(9, 30), dtime(16, 0)


def in_session(ts: str | None) -> bool:
    """True when the ISO timestamp (UTC if naive) falls on a weekday between 9:30 and 16:00 ET."""
    try:
        d = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return False
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    d = d.astimezone(ET)
    return d.weekday() < 5 and OPEN <= d.time() <= CLOSE


def close_points(curve: list) -> dict:
    """{ET date: the last in-session curve point that day}."""
    out = {}
    for p in curve:
        if in_session(p.get("ts")):
            out[market_date(p["ts"])] = p
    return out


def close_point(curve: list, day: str) -> dict | None:
    """The last in-session equity-curve point on ET trading day `day` (YYYY-MM-DD), or None."""
    return close_points(curve).get(day)


def since_started(curve: list, started: str, spy_closes: list, until: str | None = None) -> dict | None:
    """Charles and SPY from the last close before `started` to the latest close both have
    (on or before `until` when given).

    curve: [{"ts": ISO UTC, "value": float}, ...] oldest first.
    spy_closes: [(YYYY-MM-DD, close), ...] oldest first.
    Returns {"charles_pct", "spy_pct", "as_of_close", "base_close"} or None when a side lacks a start or an end."""
    if not started or not curve or not spy_closes:
        return None
    started = started[:10]
    closes = close_points(curve)
    spy = {d[:10]: float(c) for d, c in spy_closes if c == c}
    shared = sorted(d for d in closes if d in spy)
    base = [d for d in shared if d < started]
    end = [d for d in shared if d >= started and (until is None or d <= until)]
    if not base or not end:
        return None
    b, e = base[-1], end[-1]
    return {"charles_pct": round((closes[e]["value"] / closes[b]["value"] - 1) * 100, 2),
            "spy_pct": round((spy[e] / spy[b] - 1) * 100, 2),
            "as_of_close": e, "base_close": b}
