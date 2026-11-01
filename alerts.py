"""
alerts.py — the bot's notices: data/notifications.json, shown on the dashboard's Home page.
An "error" notice shows as "Needs a look"; every notice is listed under Recent activity.
"""

import json
import logging
import os
import state_io

log = logging.getLogger(__name__)

_NOTIF_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "data", "notifications.json")


def _notify(title: str, message: str, level: str = "info"):
    """Add a notice. Never raises: a full disk mustn't stop the trading."""
    try:
        from datetime import datetime, timezone
        os.makedirs(os.path.dirname(_NOTIF_FILE), exist_ok=True)
        notifs = []
        if os.path.exists(_NOTIF_FILE):
            try:
                with open(_NOTIF_FILE) as f:
                    notifs = json.load(f)
            except Exception:
                notifs = []
        notifs.append({
            "ts":      datetime.now(timezone.utc).isoformat(),
            "title":   title,
            "message": message,
            "level":   level,
        })
        notifs = notifs[-50:]   # keep last 50
        state_io.write_json(_NOTIF_FILE, notifs)
    except Exception as e:
        log.debug(f"Notification log error: {e}")


MODES = {"invest": "Invest mode", "safety": "Safety mode", "aggressive": "Aggressive mode"}


def _usd(v: float) -> str:
    return ("−$" if v < 0 else "$") + f"{abs(v):,.2f}"


def bot_started():
    _notify("Charles started", "It checks the plan at 10:00 AM ET on weekdays.")


def rebalanced(summary: str, lines: list):
    """After the daily check placed orders or switched modes."""
    _notify("Rebalanced", "; ".join(lines)[:300] + (f" {summary}" if summary else ""))


def daily_loss_halted(loss: float, limit: float):
    _notify("Buys paused for today",
            f"The account is down {_usd(abs(loss))}, past the {_usd(limit)} daily limit. "
            "Sales still go through; buys resume tomorrow.", "error")


def control(text: str):
    """Pause, resume, updates and settings changes, from the dashboard or SSH."""
    first, _, rest = text.partition("\n")
    _notify(first[:100], rest[:300])


def error_alert(msg: str):
    _notify("Charles hit an error", msg[:300], "error")


def daily_summary(summary: dict, plan: dict | None = None):
    day = summary.get("day_change", 0) or 0
    ret = summary.get("total_return_pct", 0)
    mode = (plan or {}).get("mode")
    _notify("Daily summary",
            f"{_usd(summary.get('total_value', 0))}, {'+' if day >= 0 else ''}{_usd(day)} today, "
            f"{'+' if ret >= 0 else ''}{ret:.2f}% since the start"
            + (f". {MODES[mode]}" if mode in MODES else "")
            + (f", next re-pick {plan['next_rebalance']}" if plan and plan.get("next_rebalance") else ""))


def report(title: str, text: str):
    """A weekly, monthly or yearly report (reports.py)."""
    _notify(title, text)


def notice(text: str):
    """A note from an ops script (ops/notify.py). The first line is the title; "needs you" or "failed" in it asks for a look."""
    first, _, rest = text.strip().partition("\n")
    first = first.lstrip("✅🔧🎛️⚠🛠📈️ \t")
    urgent = any(w in first.lower() for w in ("needs you", "failed", "needs a look"))
    _notify(first[:120] or "Note", rest.strip()[:1500], "error" if urgent else "info")
