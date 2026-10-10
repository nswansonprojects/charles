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

def bot_started():
    _notify("🤖", "Bot started", "Paper trading mode active", "info")
    _send("🤖 *AI Trading Bot started*\nPaper trading mode active. Watching the market...")


def trade_executed(trade: dict):
    action = trade.get("action", "?")
    symbol = trade.get("symbol", "?")
    value  = float(trade.get("value", 0) or 0)
    price  = float(trade.get("price", 0) or 0)
    conf   = float(trade.get("confidence", 0) or 0)
    reason = trade.get("reasoning", "")
    source = trade.get("source", "llm")
    pnl    = trade.get("pnl")

    # Dashboard notification
    pnl_str = f" | P&L ${pnl:+,.2f}" if pnl is not None else ""
    icon    = "📈" if action == "BUY" else ("📉" if action == "SELL" else "⚡")
    level   = "success" if (pnl or 0) >= 0 else "warning"
    _notify(icon, f"{action} {symbol}",
            f"${value:,.2f}{pnl_str} | {reason[:80]}", level=level)

    # Telegram message
    trade_icon = "🟢" if action == "BUY" else "🔴"
    pnl_line = ""
    if action == "SELL" and pnl is not None:
        pct  = trade.get("pnl_pct", 0)
        sign = "+" if pnl >= 0 else ""
        pnl_line = f"\n💰 *P&L:* `{sign}${pnl:,.2f} ({sign}{pct:.1f}%)`"

    _send(
        f"{trade_icon} *{action} {symbol}*\n"
        f"💵 *Price:* `${price:,.4f}`\n"
        f"💼 *Value:* `${value:,.2f}`\n"
        f"🎯 *Confidence:* `{conf:.0%}` _{source}_\n"
        f"🧠 *Reason:* {reason}"
        f"{pnl_line}"
    )


def stop_loss_hit(symbol: str, pnl_pct: float):
    _notify("🛑", f"Stop loss: {symbol}", f"Loss: {pnl_pct:.1f}% — closed", "error")
    _send(f"🛑 *Stop loss hit: {symbol}*\nLoss: `{pnl_pct:.1f}%` — position closed.")


def take_profit_hit(symbol: str, pnl_pct: float):
    _notify("🎯", f"Take profit: {symbol}", f"Gain: +{pnl_pct:.1f}% — closed", "success")
    _send(f"🎯 *Take profit hit: {symbol}*\nGain: `+{pnl_pct:.1f}%` — position closed.")


def earnings_skip(symbol: str, days: int):
    _notify("📅", f"Skipped {symbol}", f"Earnings in {days} day(s)", "warning")
    _send(f"📅 *Skipped {symbol}* — earnings in {days} day(s). Blackout active.")


def market_trend(regime: str, momentum_pct: float, allow_longs: bool = True, description: str = ""):
    icons = {"TRENDING_BULL": "📈", "TRENDING_BEAR": "📉",
             "RANGING": "↔️", "VOLATILE": "⚡"}
    icon = icons.get(regime, "🌡️")
    buy_status = "enabled" if allow_longs else "suppressed"
    _notify(icon, f"Market regime: {regime}",
            f"Momentum {momentum_pct:+.1f}% — buys {buy_status}",
            "info")
    _send(f"{icon} *Market regime: {regime}*\n"
          f"{description}\n"
          f"10-day momentum {momentum_pct:+.1f}% — "
          f"buy signals {buy_status}.")


def regime_changed(old: str, new: str, detail: dict):
    icons = {"TRENDING_BULL": "📈", "TRENDING_BEAR": "📉",
             "RANGING": "↔️", "VOLATILE": "⚡"}
    icon  = icons.get(new, "🌡️")
    atr   = detail.get("atr_pct", 0)
    mom   = detail.get("momentum_10d", 0)
    _notify(icon, f"Regime: {old} → {new}",
            f"ATR={atr:.1f}% Mom={mom:+.1f}%", "info")
    _send(
        f"{icon} *Regime changed: {old} → {new}*\n"
        f"ATR: `{atr:.1f}%`  Mom: `{mom:+.1f}%`\n"
        f"{'✅ Shorts enabled' if new == 'TRENDING_BEAR' else '❌ Shorts off'}  |  "
        f"{'✅ Hedges enabled' if new in ('TRENDING_BEAR','VOLATILE') else '❌ Hedges off'}"
    )


def trailing_stop_raised(symbol: str, old_stop: float, new_stop: float, high: float):
    _notify("📈", f"Trailing stop raised: {symbol}",
            f"${old_stop:,.2f} → ${new_stop:,.2f} (high ${high:,.2f})", "info")
    _send(
        f"📈 *Trailing stop raised: {symbol}*\n"
        f"New high: `${high:,.2f}`\n"
        f"Stop: `${old_stop:,.2f}` → `${new_stop:,.2f}`"
    )


def position_skipped(symbol: str, reason: str):
    if any(k in reason.lower() for k in ["earnings", "halted", "cooldown", "blackout"]):
        _notify("⏭️", f"Skipped {symbol}", reason, "warning")
        _send(f"⏭️ *Skipped {symbol}*\n_{reason}_")


def daily_loss_halted(loss: float, limit: float):
    _notify("🚨", "Trading HALTED",
            f"Daily loss ${abs(loss):,.2f} hit limit ${limit:,.2f}", "error")
    _send(
        f"🚨 *TRADING HALTED*\n"
        f"Daily loss `${abs(loss):,.2f}` hit limit `${limit:,.2f}`\n"
        f"No new trades until midnight."
    )


def error_alert(msg: str):
    _notify("⚠️", "Bot error", msg[:100], "error")
    _send(f"⚠️ *Bot error*\n`{msg[:300]}`")


def daily_summary(summary: dict, extra: list | None = None):
    total  = summary.get("total_value", 0)
    ret    = summary.get("total_return_pct", 0)
    wins   = summary.get("win_rate", 0)
    trades = summary.get("total_trades", 0)
    sign   = "+" if ret >= 0 else ""
    _notify("📊", "Daily summary",
            f"${total:,.2f} | {sign}{ret:.2f}% | {wins:.0f}% win rate", "info")
    _send(
        f"📊 *Daily Summary*\n"
        f"💼 Portfolio: `${total:,.2f}`\n"
        f"📈 Return: `{sign}{ret:.2f}%`\n"
        f"✅ Win rate: `{wins:.1f}%`\n"
        f"🔄 Total trades: `{trades}`"
        + "".join(f"\n{line}" for line in (extra or []))
    )


def heartbeat(status: dict):
    active    = status.get("active", True)
    halted    = status.get("halted", False)
    daily_pnl = status.get("daily_pnl", 0)
    daily_pct = status.get("daily_pnl_pct", 0)
    total     = status.get("total_value", 0)
    open_pos  = status.get("open_positions", 0)
    regime    = status.get("regime", "UNKNOWN")
    trades    = status.get("trades_today", 0)
    next_macro= status.get("next_macro")
    last_trade= status.get("last_trade", "None today")
    finbert   = status.get("finbert_on", False)

    status_line = ("🚨 *HALTED*" if halted
                   else "✅ *ACTIVE*" if active
                   else "⚠️ *INACTIVE*")
    pnl_icon = "📈" if daily_pnl >= 0 else "📉"
    sign     = "+" if daily_pnl >= 0 else ""

    regime_icons = {"TRENDING_BULL": "📈", "TRENDING_BEAR": "📉",
                    "RANGING": "↔️", "VOLATILE": "⚡"}
    r_icon = regime_icons.get(regime, "🌡️")

    macro_line = ""
    if next_macro:
        macro_line = (f"\n📅 Next event: *{next_macro['name']}* "
                      f"in {next_macro['hours_away']:.1f}h"
                      + (" ⚠️" if next_macro["hours_away"] < 4 else ""))

    _send(
        f"💓 *Bot Heartbeat*\n"
        f"─────────────────\n"
        f"Status:   {status_line}\n"
        f"P&L:      {pnl_icon} `{sign}${daily_pnl:,.2f}` ({sign}{daily_pct:.2f}%) today\n"
        f"Value:    `${total:,.2f}`\n"
        f"Regime:   {r_icon} {regime.replace('_', ' ')}\n"
        f"Positions: `{open_pos}` open\n"
        f"Trades:   `{trades}` today\n"
        f"Last:     _{last_trade}_\n"
        f"Sentiment: {'🧠 FinBERT' if finbert else '📊 VADER'}"
        f"{macro_line}"
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
