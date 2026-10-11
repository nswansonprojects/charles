"""
attribution.py — where the bot beats or trails SPY (roadmap task 4a). Read-only analysis.

For each closed position (data/position_history.jsonl) the "excess" is its return minus SPY's
return over the same open → close dates (daily closes, so a same-day trade is compared with 0).
Excess is grouped by regime at entry, SPY up/down over the hold, holding time, exit reason, AI
confidence at entry and sector, for the last 4 weeks and for all time, so the check-up can see
which kinds of trades cost us against the index. Never changes a setting or a trade.
"""
import bisect
import json
import os
from datetime import datetime, timezone, timedelta

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
MIN_TRADES = 10            # groups with fewer trades are flagged "too few"


def _ts(s) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _close_on(spy: list, day: str):
    """SPY close on `day` or the last trading day before it. spy: [[YYYY-MM-DD, close], ...] sorted."""
    i = bisect.bisect_right([d for d, _ in spy], day) - 1
    return spy[i][1] if i >= 0 else None


def hold_bucket(days: float) -> str:
    if days <= 1:
        return "≤1d"
    if days <= 3:
        return "2–3d"
    if days <= 7:
        return "4–7d"
    return "8d+"


def conf_bucket(c) -> str:
    if c is None:
        return "unknown"
    return "<65%" if c < 0.65 else "65–75%" if c < 0.75 else "75%+"


def _sector(symbol: str) -> str:
    try:
        import sector_rotation as sr
        etf = sr.STOCK_SECTOR_MAP.get(symbol)
        return sr.SECTOR_ETFS.get(etf, etf) if etf else "unknown"
    except Exception:
        return "unknown"


def _before(rows: list, symbol: str, opened: datetime, key: str):
    """Field `key` of the latest row for `symbol` at or before `opened` (rows have ts/timestamp)."""
    best = None
    for r in rows:
        if r.get("symbol") != symbol:
            continue
        t = _ts(r.get("ts") or r.get("timestamp"))
        if t and t <= opened + timedelta(minutes=5) and (best is None or t > best[0]):
            best = (t, r)
    return best[1].get(key) if best else None


def excess_rows(positions: list, spy: list, trades: list = (), decisions: list = ()) -> list:
    """One row per closed position with its return, SPY's return and the excess (all in %)."""
    out = []
    buys = [t for t in trades if str(t.get("action")).upper() == "BUY"]
    for p in positions:
        opened, closed = _ts(p.get("opened")), _ts(p.get("closed"))
        if not opened or not closed or p.get("exit_pnl") is None:
            continue
        cost = _before(buys, p["symbol"], opened, "value")
        if not cost:
            continue                                   # can't size the trade → can't compare
        s0, s1 = _close_on(spy, opened.date().isoformat()), _close_on(spy, closed.date().isoformat())
        if not s0 or not s1:
            continue
        ret = 100 * p["exit_pnl"] / cost
        spy_ret = 100 * (s1 / s0 - 1)
        out.append({"symbol": p["symbol"], "opened": opened.isoformat(), "closed": closed.isoformat(),
                    "return_pct": round(ret, 3), "spy_pct": round(spy_ret, 3),
                    "excess_pct": round(ret - spy_ret, 3),
                    "excess_usd": round(p["exit_pnl"] - cost * spy_ret / 100, 2),
                    "regime": _before(decisions, p["symbol"], opened, "regime") or "unknown",
                    "spy_move": "SPY up" if spy_ret > 0 else "SPY down or flat",
                    "hold": hold_bucket((closed - opened).total_seconds() / 86400),
                    "exit": p.get("exit_reason") or p.get("exit_source") or "unknown",
                    "confidence": conf_bucket(_before(buys, p["symbol"], opened, "confidence")),
                    "sector": _sector(p["symbol"])})
    return out


GROUPS = ("regime", "spy_move", "hold", "exit", "confidence", "sector")


def group(rows: list) -> dict:
    out = {}
    for g in GROUPS:
        acc = {}
        for r in rows:
            acc.setdefault(r[g], []).append(r)
        out[g] = {k: {"trades": len(v),
                      "avg_excess_pct": round(sum(x["excess_pct"] for x in v) / len(v), 2),
                      "total_excess_usd": round(sum(x["excess_usd"] for x in v), 2),
                      "beat_spy_pct": round(100 * sum(x["excess_pct"] > 0 for x in v) / len(v)),
                      **({"too_few": True} if len(v) < MIN_TRADES else {})}
                  for k, v in sorted(acc.items(), key=lambda kv: sum(x["excess_usd"] for x in kv[1]))}
    return out


def _curve_return(curve: list, since: str) -> float | None:
    pts = [v for t, v in curve if t >= since]
    before = [v for t, v in curve if t < since]
    pts = before[-1:] + pts
    return round(100 * (pts[-1] / pts[0] - 1), 2) if len(pts) >= 2 else None


def vs_spy(positions: list, spy: list, trades: list = (), decisions: list = (), equity: list = (),
           now: datetime | None = None, weeks: int = 4) -> dict:
    """Excess vs SPY grouped, for the last `weeks` weeks and all time.
    equity: [[iso ts, account value], ...] for the bot's overall return in each window."""
    now = now or datetime.now(timezone.utc)
    rows = excess_rows(positions, spy, trades, decisions)
    cut = (now - timedelta(weeks=weeks)).date().isoformat()
    out = {}
    for name, since in ((f"last_{weeks}_weeks", cut), ("all_time", "")):
        rs = [r for r in rows if r["closed"][:10] >= since]
        first = since or max(spy[0][0] if spy else "", str(equity[0][0])[:10] if equity else "")
        out[name] = {"trades": len(rs), "since": first,
                     "beat_spy_pct": round(100 * sum(r["excess_pct"] > 0 for r in rs) / len(rs)) if rs else None,
                     "total_excess_usd": round(sum(r["excess_usd"] for r in rs), 2),
                     "bot_return_pct": _curve_return(list(equity), first),
                     "spy_return_pct": _curve_return(spy, first),
                     "groups": group(rs)}
        b, s = out[name]["bot_return_pct"], out[name]["spy_return_pct"]
        out[name]["bot_minus_spy_pct"] = round(b - s, 2) if b is not None and s is not None else None
    return out


def from_files(spy: list, now: datetime | None = None) -> dict:
    """vs_spy() on the bot's own data files (read-only)."""
    def jl(name):
        try:
            with open(os.path.join(DATA, name)) as f:
                return [json.loads(l) for l in f if l.strip()]
        except FileNotFoundError:
            return []

    def js(name, default):
        try:
            with open(os.path.join(DATA, name)) as f:
                return json.load(f)
        except (FileNotFoundError, ValueError):
            return default
    equity = [[e["ts"], e["value"]] for e in js("equity_curve.json", []) if "ts" in e and "value" in e]
    return vs_spy(jl("position_history.jsonl"), spy, js("trades.json", []), jl("decisions.jsonl"),
                  equity, now=now)
