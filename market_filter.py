"""
market_filter.py — Market-wide filters applied before any trade.
  1. SPY trend filter  — suppresses buys in a downtrend
  2. Earnings blackout — skips stocks near earnings dates
  3. Duplicate/cooldown check — prevents re-entering too soon
  4. Conviction-based position sizing — scales trade size with confidence
"""

import json
import logging
import os
from datetime import datetime, datetime as dt, timezone, timedelta

import yfinance as yf

import config
import state_io
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
try:
    import macro_calendar
    import timeofday
except Exception:
    macro_calendar = None
    timeofday = None

log = logging.getLogger(__name__)

DATA_DIR       = os.path.join(os.path.dirname(__file__), "data")
COOLDOWN_FILE  = os.path.join(DATA_DIR, "cooldowns.json")


# ── 1. SPY trend filter ───────────────────────────────────────────────────────

_spy_cache: dict = {}

def market_is_bullish() -> tuple[bool, float]:
    """
    Returns (is_bullish, spy_change_pct).
    Bullish = SPY price > its N-day moving average.
    Cached for 30 min to avoid hammering Yahoo Finance.
    """
    import time
    now = time.time()
    if _spy_cache.get("ts", 0) > now - 1800:   # 30-min cache
        return _spy_cache["bull"], _spy_cache["pct"]

    try:
        hist   = yf.Ticker("SPY").history(period=f"{config.SPY_TREND_PERIOD + 5}d")
        hist   = hist.dropna(subset=["Close"])
        closes = hist["Close"].tolist()
        if len(closes) < config.SPY_TREND_PERIOD:
            return True, 0.0   # not enough data, allow trades
        ma     = sum(closes[-config.SPY_TREND_PERIOD:]) / config.SPY_TREND_PERIOD
        latest = closes[-1]
        pct    = (latest - ma) / ma * 100
        bull   = latest > ma
        _spy_cache.update({"ts": now, "bull": bull, "pct": round(pct, 2)})
        log.info(f"  📊 SPY vs MA{config.SPY_TREND_PERIOD}: {pct:+.2f}% → {'BULLISH' if bull else 'BEARISH'}")
        return bull, round(pct, 2)
    except Exception as e:
        log.warning(f"SPY trend check failed: {e} — allowing trades")
        return True, 0.0


# ── 2. Earnings blackout ──────────────────────────────────────────────────────

_earnings_cache: dict = {}   # (symbol, ET date) -> (near, days); re-checked each trading day


def _earnings_dates(cal) -> list:
    """
    Earnings dates from yfinance's Ticker.calendar, as datetime.date objects.
    yfinance >= 0.2.x returns a dict ({"Earnings Date": [date, ...], ...});
    older versions returned a DataFrame with an "Earnings Date" row.
    """
    if cal is None:
        return []
    if isinstance(cal, dict):
        raw = cal.get("Earnings Date", [])
    elif hasattr(cal, "index"):          # old DataFrame form
        if getattr(cal, "empty", True) or "Earnings Date" not in cal.index:
            return []
        raw = cal.loc["Earnings Date"]
    else:
        return []
    if raw is None or isinstance(raw, (str, bytes)) or not hasattr(raw, "__iter__"):
        raw = [raw]
    out = []
    for d in raw:
        if d is None:
            continue
        d = d.date() if hasattr(d, "date") and callable(d.date) else d
        if hasattr(d, "toordinal"):
            out.append(d)
    return out


def near_earnings(symbol: str) -> tuple[bool, int]:
    """
    Returns (is_near_earnings, days_away): earnings from 1 day ago up to
    config.EARNINGS_BLACKOUT_DAYS ahead (ET calendar days).
    """
    if "/" in symbol:
        return False, 999   # crypto — no earnings

    today = dt.now(timezone.utc).astimezone(_ET).date()
    key = (symbol, today)
    if key in _earnings_cache:
        return _earnings_cache[key]

    try:
        dates = _earnings_dates(yf.Ticker(symbol).calendar)
    except Exception as e:
        # was logged at debug, which hid that the check never worked (backlog #19)
        log.warning(f"Earnings check failed for {symbol}: {e} — not blocking")
        return False, 999

    result = (False, 999)
    for edate in dates:
        days = (edate - today).days
        if -1 <= days <= config.EARNINGS_BLACKOUT_DAYS:
            log.info(f"  📅 {symbol}: earnings in {days}d — blackout active")
            result = (True, days)
            break
    _earnings_cache[key] = result
    return result


# ── 3. Cooldown / duplicate prevention ───────────────────────────────────────

def _load_cooldowns() -> dict:
    if os.path.exists(COOLDOWN_FILE):
        with open(COOLDOWN_FILE) as f:
            return json.load(f)
    return {}

def _save_cooldowns(cd: dict):
    state_io.write_json(COOLDOWN_FILE, cd, indent=2)

def is_on_cooldown(symbol: str) -> bool:
    """Returns True if we've traded this symbol within TRADE_COOLDOWN_HOURS."""
    cd   = _load_cooldowns()
    last = cd.get(symbol)
    if not last:
        return False
    last_dt  = dt.fromisoformat(last)
    elapsed  = (dt.now(timezone.utc) - last_dt).total_seconds() / 3600
    if elapsed < config.TRADE_COOLDOWN_HOURS:
        log.info(f"  ⏳ {symbol}: cooldown active ({elapsed:.1f}h / {config.TRADE_COOLDOWN_HOURS}h)")
        return True
    return False

def record_trade_time(symbol: str):
    """Call this after every executed trade to start the cooldown."""
    cd = _load_cooldowns()
    cd[symbol] = dt.now(timezone.utc).isoformat()
    _save_cooldowns(cd)


# ── 4. Conviction-based position sizing ──────────────────────────────────────

def position_size_pct(confidence: float) -> float:
    """
    Scale position size linearly between MIN and MAX based on confidence.
    60% conf → MIN_POSITION_SIZE_PCT
    100% conf → MAX_POSITION_SIZE_PCT
    """
    min_c  = config.MIN_CONFIDENCE          # e.g. 0.60
    max_c  = 1.0
    min_sz = config.MIN_POSITION_SIZE_PCT   # e.g. 0.02
    max_sz = config.MAX_POSITION_SIZE_PCT   # e.g. 0.08

    if confidence <= min_c:
        return min_sz
    if confidence >= max_c:
        return max_sz

    ratio = (confidence - min_c) / (max_c - min_c)
    size  = min_sz + ratio * (max_sz - min_sz)
    return round(size, 4)




def get_positions_near_earnings(open_positions: dict) -> list[str]:
    """
    Returns list of symbols in open_positions that have earnings within
    config.EARNINGS_BLACKOUT_DAYS. These should be exited ASAP.
    Called from morning routine before market open.
    """
    to_exit = []
    for symbol in open_positions:
        if "/" in symbol:
            continue   # skip crypto
        near, days = near_earnings(symbol)
        if near and days <= config.EARNINGS_BLACKOUT_DAYS:
            log.warning(f"  ⚠️  {symbol}: earnings in {days}d — flagging for exit")
            to_exit.append(symbol)
    return to_exit


def has_large_gap(symbol: str, threshold_pct: float = 4.0) -> tuple[bool, float]:
    """
    Returns (has_large_gap, gap_pct).
    A large gap means today's open is more than threshold_pct% away from
    yesterday's close. Signals that the entry price is stale — skip the trade.
    Only relevant during the first 30 minutes of the session.
    """
    try:
        import timeofday as _tod
        sess = _tod.session_info()
        # Only apply gap filter in the first 30 minutes
        phase = sess.get("phase", "")
        if phase not in ("open", "pre_open"):
            return False, 0.0
    except Exception:
        pass

    try:
        hist = yf.Ticker(symbol).history(period="3d", interval="1d")
        hist = hist.dropna(subset=["Close", "Open"])
        if len(hist) < 2:
            return False, 0.0
        prev_close = hist["Close"].iloc[-2]
        today_open = hist["Open"].iloc[-1]
        gap_pct    = abs(today_open - prev_close) / prev_close * 100
        if gap_pct >= threshold_pct:
            log.info(f"  📊 {symbol}: large gap {gap_pct:+.1f}% at open — skipping")
            return True, round(gap_pct, 2)
        return False, round(gap_pct, 2)
    except Exception as e:
        log.debug(f"Gap check failed for {symbol}: {e}")
        return False, 0.0

# ── Master pre-trade gate ─────────────────────────────────────────────────────

def should_trade(decision: dict) -> tuple[bool, str]:
    """
    Run all filters. Returns (ok_to_trade, reason_if_blocked).
    Call this before executing any order.
    """
    symbol = decision["symbol"]
    action = decision["action"]

    # Only filter BUY orders — sells and stop-losses always go through
    if action != "BUY":
        return True, ""

    # Earnings blackout
    near, days = near_earnings(symbol)
    if near:
        return False, f"earnings blackout ({days}d away)"

    # Cooldown
    if is_on_cooldown(symbol):
        return False, "cooldown active"

    # Regime — enforce what regime.regime_multipliers promises (and what the
    # morning Telegram announces): no new buys where allow_longs is False,
    # and a higher confidence bar (MIN_CONFIDENCE + conf_boost). Backlog #20.
    try:
        import regime as _regime
        reg, _ = _regime.detect_regime()
        mults = _regime.regime_multipliers(reg)
        if not mults.get("allow_longs", True):
            return False, f"regime {reg}: new buys suppressed"
        need = config.MIN_CONFIDENCE + mults.get("conf_boost", 0.0)
        conf = decision.get("confidence", 0)
        if conf < need - 1e-9:
            return False, f"regime {reg}: confidence {conf:.0%} below {need:.0%}"
    except Exception as e:
        log.warning(f"Regime check failed: {e} — not blocking")

    # SPY trend — suppress buys in a downtrend
    bullish, spy_pct = market_is_bullish()
    if not bullish:
        return False, f"market downtrend (SPY {spy_pct:+.1f}% vs MA)"

    # Macro event blackout
    if macro_calendar:
        try:
            in_blackout, event = macro_calendar.in_macro_blackout()
            if in_blackout:
                return False, f"macro blackout: {event}"
        except Exception:
            pass

    # Time-of-day filter
    if timeofday:
        try:
            avoid, reason = timeofday.in_avoid_window()
            if avoid:
                return False, f"time filter: {reason}"
        except Exception:
            pass

    return True, ""
