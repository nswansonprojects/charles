"""
equity_tracker.py — Records portfolio value snapshots over time.
Called after every trade cycle to build the equity curve shown in the dashboard.
Data saved to data/equity_curve.json — survives restarts.
"""

import json
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)

DATA_DIR    = os.path.join(os.path.dirname(__file__), "data")
EQUITY_FILE = os.path.join(DATA_DIR, "equity_curve.json")
PEAK_FILE   = os.path.join(DATA_DIR, "peak_equity.json")   # survives curve truncation
START_FILE  = os.path.join(DATA_DIR, "start.json")         # what returns are measured from (ensure_start)

import config
import state_io
import timeofday


def start_cash() -> float:
    """The balance returns are measured from: data/start.json, else config.VIRTUAL_CASH.
    Read at every call, so the dashboard and the bot always agree on it."""
    try:
        with open(START_FILE) as f:
            value = float(json.load(f)["start_cash"])
        if value > 0:
            return value
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return config.VIRTUAL_CASH


def ensure_start(account_value: float | None, balance: float | None = None) -> float:
    """Fix the starting balance once, at the bot's first start: the install's own `balance` if it has
    one (the old setup wizard's launch flag), else the account's value then. A new Alpaca paper
    account holds $100,000, so measuring from config.VIRTUAL_CASH showed "+9,900% since the $1,000
    start" on fresh installs. Returns the starting balance in use."""
    if os.path.exists(START_FILE):
        return start_cash()
    value = balance or account_value
    if not value or value <= 0:
        return start_cash()
    state_io.write_json(START_FILE, {"start_cash": round(float(value), 2), "since": timeofday.market_date(),
                                     "from": "launch flag balance" if balance else "account value at the first start"},
                        indent=1)
    return round(float(value), 2)


def record_snapshot(total_value: float):
    """Append a timestamped portfolio value to the equity curve."""
    os.makedirs(DATA_DIR, exist_ok=True)
    prev_peak = peak_value()   # before this point joins the curve
    start = start_cash()
    curve = _load()
    curve.append({
        "ts":    datetime.now(timezone.utc).isoformat(),
        "value": round(total_value, 2),
        "pnl":   round(total_value - start, 2),
        "pct":   round((total_value - start) / start * 100, 4),
    })
    # Keep last 10,000 points (~3 months at 30-min intervals)
    curve = curve[-10_000:]
    state_io.write_json(EQUITY_FILE, curve)
    if total_value > prev_peak or not os.path.exists(PEAK_FILE):
        state_io.write_json(PEAK_FILE, {"peak": round(max(total_value, prev_peak), 2),
                                        "ts": datetime.now(timezone.utc).isoformat()})


def peak_value() -> float:
    """Highest recorded portfolio value, never below the starting balance."""
    try:
        with open(PEAK_FILE) as f:
            peak = json.load(f)["peak"]
    except Exception:
        peak = max((p["value"] for p in _load()), default=start_cash())
    return max(peak, start_cash())


def load_curve() -> list[dict]:
    return _load()


def _load() -> list:
    if os.path.exists(EQUITY_FILE):
        try:
            with open(EQUITY_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []
