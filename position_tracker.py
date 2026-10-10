"""
position_tracker.py — the path of every live stock position, not just its buy and sell.

Every stop check (15 min in market hours) snapshots each position's unrealized P&L and keeps
its best (peak) and worst (trough) point. When a position disappears (sold by a stop, the AI,
a time exit...), its path is closed into data/position_history.jsonl with what it finally
made and how much it gave back from its peak. Owner's idea (2026-10-09): AMD peaked at
+8.6% (+$4.15) and was sold at +$1.69; since August, trades held over an hour peaked at
+$63 together but closed at −$0.82. This is the data that lets the bot learn exits.

Read-only towards trading: it never places or changes orders. State: data/position_paths.json.
"""
import json
import os
from datetime import datetime, timezone

import state_io

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PATHS_FILE = os.path.join(DATA, "position_paths.json")
HISTORY_FILE = os.path.join(DATA, "position_history.jsonl")
TRADES_FILE = os.path.join(DATA, "trades.json")
MAX_MARKS = 600          # 15-min marks: ~23 trading days


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def snapshot(paths: dict, symbol: str, entry: float, price: float, qty: float, now: datetime,
             opened: str | None = None) -> dict:
    """Add one mark to a symbol's path (pure: works on the dict passed in)."""
    pnl = round((price - entry) * qty, 2)
    pct = round((price / entry - 1) * 100, 2) if entry else 0.0
    ts = now.isoformat(timespec="minutes")
    rec = paths.get(symbol)
    if rec is None:          # one path per continuous holding (adding to it keeps the path)
        rec = {"opened": opened or ts, "tracked_from": ts, "entry": entry, "qty": qty,
               "peak_pnl": pnl, "peak_pct": pct, "peak_ts": ts, "peak_price": price,
               "trough_pnl": pnl, "trough_pct": pct, "trough_ts": ts, "marks": []}
        paths[symbol] = rec
    rec.update(entry=entry, qty=qty, last_pnl=pnl, last_pct=pct, last_ts=ts, last_price=price)
    if pnl > rec["peak_pnl"]:
        rec.update(peak_pnl=pnl, peak_pct=pct, peak_ts=ts, peak_price=price)
    if pnl < rec["trough_pnl"]:
        rec.update(trough_pnl=pnl, trough_pct=pct, trough_ts=ts)
    rec["marks"] = (rec["marks"] + [[ts, pnl]])[-MAX_MARKS:]
    return rec


def finish(rec: dict, symbol: str, sell: dict | None, now: datetime) -> dict:
    """Close a path. `sell` is the trades.json SELL that ended it (None if not found)."""
    exit_pnl = sell.get("pnl") if sell else rec.get("last_pnl")
    return {"symbol": symbol, "opened": rec["opened"], "tracked_from": rec["tracked_from"],
            "closed": (sell or {}).get("timestamp") or now.isoformat(timespec="minutes"),
            "entry": rec["entry"], "exit_pnl": exit_pnl,
            "exit_source": (sell or {}).get("source"), "exit_reason": (sell or {}).get("reason"),
            "peak_pnl": rec["peak_pnl"], "peak_pct": rec["peak_pct"], "peak_ts": rec["peak_ts"],
            "trough_pnl": rec["trough_pnl"], "trough_pct": rec["trough_pct"],
            "gave_back": round(rec["peak_pnl"] - exit_pnl, 2) if exit_pnl is not None else None,
            "marks": len(rec["marks"])}


def _last_sell(trades: list, symbol: str, since: str) -> dict | None:
    for t in reversed(trades):
        if t.get("symbol") == symbol and t.get("action") == "SELL" and t.get("timestamp", "") >= since[:16]:
            return t
    return None


def _first_buy_ts(trades: list, symbol: str) -> str | None:
    for t in reversed(trades):
        if t.get("symbol") == symbol and t.get("action") == "BUY":
            return t["timestamp"][:16]
    return None


def _hourly_range(symbol: str, since: str):
    """(high, high_ts, low, low_ts) of hourly bars since `since` (yfinance), or None."""
    try:
        import yfinance as yf
        start = datetime.fromisoformat(since).replace(tzinfo=timezone.utc) if "+" not in since else datetime.fromisoformat(since)
        h = yf.Ticker(symbol).history(start=start.date(), interval="1h")
        h = h[h.index >= start]
        if h.empty:
            return None
        return (float(h["High"].max()), h["High"].idxmax().isoformat(timespec="minutes"),
                float(h["Low"].min()), h["Low"].idxmin().isoformat(timespec="minutes"))
    except Exception:
        return None


def seed(rec: dict, rng) -> None:
    """Fill a new path's peak/trough from price history since the buy (pure given `rng`)."""
    if not rng:
        return
    hi, hi_ts, lo, lo_ts = rng
    e, q = rec["entry"], rec["qty"]
    if (hi - e) * q > rec["peak_pnl"]:
        rec.update(peak_pnl=round((hi - e) * q, 2), peak_pct=round((hi / e - 1) * 100, 2),
                   peak_ts=hi_ts, peak_price=hi)
    if (lo - e) * q < rec["trough_pnl"]:
        rec.update(trough_pnl=round((lo - e) * q, 2), trough_pct=round((lo / e - 1) * 100, 2), trough_ts=lo_ts)
    rec["seeded"] = True


def update(positions, now: datetime | None = None, price_range=_hourly_range):
    """positions: Alpaca stock positions. Snapshot the held ones, close paths of sold ones."""
    now = now or datetime.now(timezone.utc)
    paths = _load(PATHS_FILE, {})
    trades = _load(TRADES_FILE, [])
    held = set()
    for p in positions:
        sym = p.symbol
        held.add(sym)
        new = sym not in paths
        rec = snapshot(paths, sym, float(p.avg_entry_price), float(p.current_price), float(p.qty), now,
                       opened=_first_buy_ts(trades, sym) if new else None)
        if new and rec["opened"] < rec["tracked_from"]:      # bought before tracking began
            seed(rec, price_range(sym, rec["opened"]))
    done = []
    for sym in [s for s in paths if s not in held]:
        rec = paths.pop(sym)
        done.append(finish(rec, sym, _last_sell(trades, sym, rec["opened"]), now))
    if done:
        with open(HISTORY_FILE, "a") as f:
            for d in done:
                f.write(json.dumps(d) + "\n")
    state_io.write_json(PATHS_FILE, paths, indent=1)
    return done


def history(limit: int = 200) -> list:
    try:
        with open(HISTORY_FILE) as f:
            rows = [json.loads(l) for l in f if l.strip()]
    except FileNotFoundError:
        return []
    return rows[-limit:]


def current() -> dict:
    return _load(PATHS_FILE, {})
