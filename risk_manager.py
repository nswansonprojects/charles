"""
risk_manager.py — the daily loss limit (config.MAX_DAILY_LOSS, the owner's setting).

When the account is down the limit today (open and closed), the rebalancer places no buys until
the next trading day; sales still go through. The trailing stops, ATR stops and drawdown sizing of
the old rules were retired with the redesign (2026-10-16).
"""

import json
import logging
import os

import config
import state_io
import timeofday

log = logging.getLogger(__name__)

DATA_DIR        = os.path.join(os.path.dirname(__file__), "data")
DAILY_LOSS_FILE = os.path.join(DATA_DIR, "daily_loss.json")


# ── Daily loss limit ──────────────────────────────────────────────────────────

def _load_daily_loss() -> dict:
    if os.path.exists(DAILY_LOSS_FILE):
        try:
            with open(DAILY_LOSS_FILE) as f:
                data = json.load(f)
            # Reset on a new trading day (ET, not the server's UTC date)
            if data.get("date") != timeofday.market_date():
                return {"date": timeofday.market_date(), "loss": 0.0, "halted": False}
            return data
        except Exception:
            pass
    return {"date": timeofday.market_date(), "loss": 0.0, "halted": False}


def _save_daily_loss(data: dict):
    state_io.write_json(DAILY_LOSS_FILE, data, indent=2)


def record_daily_pnl(pnl: float):
    """Add a closed sale's P&L to today's total."""
    data = _load_daily_loss()
    data["loss"] = round(data["loss"] + pnl, 2)

    if not data["halted"] and data["loss"] <= -abs(config.MAX_DAILY_LOSS):
        data["halted"] = True
        log.warning(f"🚨 DAILY LOSS LIMIT HIT: ${data['loss']:.2f} — "
                    f"trading halted for today")
        try:
            import alerts
            alerts.daily_loss_halted(data["loss"], config.MAX_DAILY_LOSS)
        except Exception:
            pass

    _save_daily_loss(data)


def check_day_change(day_change: float):
    """Halt when the whole account is down the limit today, open positions included."""
    data = _load_daily_loss()
    data["day_change"] = round(day_change, 2)
    if not data["halted"] and day_change <= -abs(config.MAX_DAILY_LOSS):
        data["halted"] = True
        log.warning(f"🚨 DAILY LOSS LIMIT HIT (open + closed): ${day_change:.2f} — "
                    f"trading halted for today")
        try:
            import alerts
            alerts.daily_loss_halted(day_change, config.MAX_DAILY_LOSS)
        except Exception:
            pass
    _save_daily_loss(data)


def is_trading_halted() -> bool:
    """Returns True if the daily loss kill switch has fired."""
    return _load_daily_loss().get("halted", False)


def get_daily_stats() -> dict:
    return _load_daily_loss()
