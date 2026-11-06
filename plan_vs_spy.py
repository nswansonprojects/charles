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


def over(curve: list, spy_closes: list, first: str, last: str) -> dict | None:
    """Charles (and SPY, when its closes are given) over the market days `first`..`last` (YYYY-MM-DD), at the same closes:
    from the last close before `first` (else the window's own first close, for a book that began inside it) to the last
    close on or before `last`. Returns {"start_value", "end_value", "charles_pct", "spy_pct" (None without SPY),
    "base_close", "as_of_close", "values": Charles's close each market day from the base to the end}, or None."""
    closes = close_points(curve)
    spy = {d[:10]: float(c) for d, c in spy_closes or [] if c == c}
    days = sorted(d for d in closes if d <= last and (not spy or d in spy))
    before, inside = [d for d in days if d < first], [d for d in days if d >= first]
    if not inside:
        return None
    b, e = (before[-1] if before else inside[0]), inside[-1]
    if b == e or not closes[b]["value"]:
        return None
    return {"start_value": closes[b]["value"], "end_value": closes[e]["value"],
            "charles_pct": round((closes[e]["value"] / closes[b]["value"] - 1) * 100, 2),
            "spy_pct": round((spy[e] / spy[b] - 1) * 100, 2) if spy else None,
            "base_close": b, "as_of_close": e, "values": [closes[d]["value"] for d in days if b <= d <= e]}


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
