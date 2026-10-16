"""
alerts.py — Telegram alerts + local notification log for dashboard.
Every function both sends a Telegram message AND writes to
data/notifications.json for the live dashboard feed.
"""

import json
import logging
import os
import requests
import config
import state_io

log = logging.getLogger(__name__)

_NOTIF_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "data", "notifications.json")


# ── Credential helper ─────────────────────────────────────────────────────────

def _get_creds():
    """Get Telegram credentials from credential_manager, fallback to config."""
    try:
        import credential_manager as cm
        return cm.telegram_token(), cm.telegram_chat_id()
    except Exception:
        return (getattr(config, "TELEGRAM_BOT_TOKEN", ""),
                getattr(config, "TELEGRAM_CHAT_ID", ""))


# ── Core send ─────────────────────────────────────────────────────────────────

def _send(text: str):
    """Send a Telegram message. Silently skips if not configured."""
    token, chat = _get_creds()

    if not token or not chat:
        log.warning("📵 Telegram not configured — token or chat_id is empty")
        return
    if token.startswith(("YOUR_", "REPLACE_", "your_")):
        log.warning("📵 Telegram token is a placeholder — update your .env file")
        return
    log.debug(f"📱 Attempting Telegram send to chat {chat} with token {token[:10]}...")

    try:
        # Try plain text first — most reliable, no parsing issues
        plain = (text
                 .replace("*", "").replace("`", "").replace("_", " ")
                 .replace("[", "").replace("]", ""))
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": plain},
            timeout=10,
        )
        if r.status_code == 200:
            result = r.json()
            msg_id = result.get("result", {}).get("message_id", "?")
            log.info(f"📱 Telegram DELIVERED — message_id={msg_id} | {plain[:50]}")
        else:
            err = r.json().get("description", r.text[:100])
            log.warning(f"❌ Telegram FAILED ({r.status_code}): {err}")
            log.warning(f"   Token: {token[:10]}... Chat: {chat}")
            log.warning(f"   Message: {plain[:100]}")
    except Exception as e:
        log.warning(f"❌ Telegram error: {e}")


# ── Notification log (for dashboard live feed) ────────────────────────────────

def _notify(icon: str, title: str, message: str, level: str = "info"):
    """Write to notifications.json for dashboard toast feed."""
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
            "icon":    icon,
            "title":   title,
            "message": message,
            "level":   level,
        })
        notifs = notifs[-50:]   # keep last 50
        state_io.write_json(_NOTIF_FILE, notifs)
    except Exception as e:
        log.debug(f"Notification log error: {e}")


# ── Alert functions ───────────────────────────────────────────────────────────

def _usd(v: float) -> str:
    return ("−$" if v < 0 else "$") + f"{abs(v):,.2f}"


def bot_started():
    _notify("🐶", "Charles started", "Paper account", "info")
    _send("🐶 Charles started (paper account). It checks the plan at 10:00 AM ET on weekdays.")


def rebalanced(summary: str, lines: list):
    """After the daily check placed orders or switched modes."""
    _notify("🧭", "Rebalanced", "; ".join(lines)[:200], "info")
    _send("🧭 Charles rebalanced\n" + "\n".join(lines) + f"\n\n{summary}")


def daily_loss_halted(loss: float, limit: float):
    _notify("🚨", "Buys paused", f"Down {_usd(abs(loss))} today (limit {_usd(limit)})", "error")
    _send(f"🚨 Buys paused for today: the account is down {_usd(abs(loss))}, past the "
          f"{_usd(limit)} daily limit. Sales still go through; buys resume tomorrow.")


def error_alert(msg: str):
    _notify("⚠️", "Bot error", msg[:100], "error")
    _send(f"⚠️ Charles hit an error\n{msg[:300]}")


def daily_summary(summary: dict, plan: dict | None = None):
    total = summary.get("total_value", 0)
    day = summary.get("day_change", 0) or 0
    ret = summary.get("total_return_pct", 0)
    _notify("📊", "Daily summary", f"{_usd(total)} | {'+' if day >= 0 else ''}{_usd(day)} today", "info")
    mode = (plan or {}).get("mode")
    _send(
        f"📊 Charles today: {'+' if day >= 0 else ''}{_usd(day)}\n"
        f"Portfolio {_usd(total)} ({'+' if ret >= 0 else ''}{ret:.2f}% since the start)\n"
        + ({"invest": "📈 Invest mode", "safety": "🛡️ Safety mode"}.get(mode, "") +
           (f" · next re-pick {plan['next_rebalance']}" if plan and plan.get("next_rebalance") else ""))
    )


def heartbeat(status: dict):
    day = status.get("day_change", 0) or 0
    mode = {"invest": "📈 Invest mode", "safety": "🛡️ Safety mode"}.get(status.get("mode"), "Waiting for the first check")
    _send(
        f"💓 Charles is running{' — buys paused today' if status.get('halted') else ''}\n"
        f"Value {_usd(status.get('total_value', 0))} ({'+' if day >= 0 else ''}{_usd(day)} today)\n"
        f"{mode} · {status.get('open_positions', 0)} holdings"
        + (f" · next re-pick {status['next_rebalance']}" if status.get("next_rebalance") else "")
    )


def test_telegram() -> bool:
    """
    Test Telegram connection. Returns True if working.
    Call on startup to verify before relying on alerts.
    """
    token, chat = _get_creds()
    if not token or not chat:
        log.warning("📵 Telegram test FAILED — no token or chat_id configured")
        return False
    if token.startswith(("YOUR_", "REPLACE_", "your_")):
        log.warning("📵 Telegram test FAILED — token is placeholder")
        return False
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{token}/getMe",
            timeout=8,
        )
        if r.status_code == 200:
            bot_name = r.json().get("result", {}).get("username", "?")
            log.info(f"✅ Telegram connected — bot: @{bot_name} | chat: {chat}")
            return True
        else:
            log.warning(f"❌ Telegram test FAILED — {r.status_code}: {r.json().get('description','?')}")
            return False
    except Exception as e:
        log.warning(f"❌ Telegram test FAILED — {e}")
        return False
