"""
risk_manager.py — the daily loss limit (config.MAX_DAILY_LOSS in dollars, the owner's setting, or
MAX_DAILY_LOSS_PCT of the account when no dollar amount is set).

When the account is down the limit today (its value now vs yesterday's close, so open positions and
sales count alike), the rebalancer places no buys until the next trading day; sales still go through.
A sale's realized P&L is not added on top: a pick sold at a $25 loss lost that money over weeks, and
it was already in earlier days' values (until 2026-10-29 it was counted again as "today's" loss).
The trailing stops, ATR stops and drawdown sizing of the old rules were retired with the redesign (2026-10-16).
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
                return {"date": timeofday.market_date(), "halted": False}
            return data
        except Exception:
            pass
    return {"date": timeofday.market_date(), "halted": False}


def _save_daily_loss(data: dict):
    state_io.write_json(DAILY_LOSS_FILE, data, indent=2)


def limit(yesterday_value: float | None = None) -> float | None:
    """Today's limit in dollars: config.MAX_DAILY_LOSS when it's set (the owner's $20 comes from the
    launch flag), else MAX_DAILY_LOSS_PCT of the account's value at yesterday's close. None when
    neither is known."""
    if config.MAX_DAILY_LOSS:
        return abs(float(config.MAX_DAILY_LOSS))
    if yesterday_value and yesterday_value > 0:
        return round(yesterday_value * config.MAX_DAILY_LOSS_PCT / 100, 2)
    return None


def check_day_change(day_change: float, account_value: float | None = None):
    """Halt when the whole account is down the limit today, open positions included.
    account_value: the account's value now (a % limit is taken of yesterday's close)."""
    data = _load_daily_loss()
    data["day_change"] = round(day_change, 2)
    lim = limit(account_value - day_change if account_value else None)
    data["limit"] = lim
    if lim and not data["halted"] and day_change <= -lim:
        data["halted"] = True
        log.warning(f"🚨 DAILY LOSS LIMIT HIT (open + closed): ${day_change:.2f} (limit ${lim:,.2f}) — "
                    f"trading halted for today")
        try:
            import alerts
            alerts.daily_loss_halted(day_change, lim)
        except Exception:
            pass
    _save_daily_loss(data)


def is_trading_halted() -> bool:
    """Returns True if the daily loss kill switch has fired."""
    return _load_daily_loss().get("halted", False)


def get_daily_stats() -> dict:
    return _load_daily_loss()
