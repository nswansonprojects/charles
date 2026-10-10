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

import config
import state_io
START_VALUE = config.VIRTUAL_CASH


def record_snapshot(total_value: float):
    """Append a timestamped portfolio value to the equity curve."""
    os.makedirs(DATA_DIR, exist_ok=True)
    prev_peak = peak_value()   # before this point joins the curve
    curve = _load()
    curve.append({
        "ts":    datetime.now(timezone.utc).isoformat(),
        "value": round(total_value, 2),
        "pnl":   round(total_value - START_VALUE, 2),
        "pct":   round((total_value - START_VALUE) / START_VALUE * 100, 4),
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
        peak = max((p["value"] for p in _load()), default=START_VALUE)
    return max(peak, START_VALUE)


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
