"""
risk_manager.py — Advanced risk controls.
  1. Trailing stops — moves stop up as price rises, locking in gains
  2. ATR-based stop sizing — volatility-proportional stops
  3. Daily max loss kill switch — halts trading if daily loss exceeds limit
  4. Drawdown guard — reduces position sizes after significant drawdown
"""

import json
import logging
import os
from datetime import datetime, date, timezone

import yfinance as yf

import config
import state_io
import timeofday

log = logging.getLogger(__name__)

DATA_DIR        = os.path.join(os.path.dirname(__file__), "data")
TRAILING_FILE   = os.path.join(DATA_DIR, "trailing_stops.json")
DAILY_LOSS_FILE = os.path.join(DATA_DIR, "daily_loss.json")


# ── 1. ATR calculation (pure Python) ─────────────────────────────────────────

def calc_atr(symbol: str, period: int = 14) -> float:
    """Fetch recent OHLC and compute ATR. Returns ATR in price units."""
    try:
        hist = yf.Ticker(symbol).history(period=f"{period + 5}d", interval="1d")
        if hist.empty or len(hist) < period:
            return 0.0
        result = atr_from_ohlc(hist["High"].tolist(), hist["Low"].tolist(),
                               hist["Close"].tolist(), period)
        if result == 0.0:
            log.debug(f"ATR calc for {symbol} produced NaN — falling back to 0.0")
        return result
    except Exception as e:
        log.debug(f"ATR calc failed for {symbol}: {e}")
        return 0.0


def atr_from_ohlc(highs: list, lows: list, closes: list, period: int = 14) -> float:
    """ATR of daily bars (pure). 0.0 if it comes out NaN, so callers never size off NaN."""
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(
            highs[i]  - lows[i],
            abs(highs[i]  - closes[i-1]),
            abs(lows[i]   - closes[i-1]),
        ))
    result = sum(trs[-period:]) / period
    if result != result:  # NaN check (NaN is the only value that != itself)
        return 0.0
    return result


def atr_stop_distance(symbol: str, multiplier: float = 2.0) -> float:
    """
    Returns the stop-loss distance in price units based on ATR.
    Default: 2x ATR — wide enough to not get stopped by noise,
    tight enough to limit losses on a real move against you.
    """
    atr = calc_atr(symbol)
    return atr * multiplier


# ── 2. Trailing stop manager ──────────────────────────────────────────────────

def _load_trailing() -> dict:
    if os.path.exists(TRAILING_FILE):
        try:
            with open(TRAILING_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}   # symbol → {"entry": price, "highest": price, "stop": price}


def _save_trailing(data: dict):
    state_io.write_json(TRAILING_FILE, data, indent=2)


def _capped_dist(dist: float, entry_price: float) -> float:
    """Trailing distance never wider than the fixed stop (STOP_LOSS_PCT of entry)."""
    cap = entry_price * config.STOP_LOSS_PCT
    return min(dist, cap) if dist > 0 else cap


def register_trailing_stop(symbol: str, entry_price: float):
    """Call when a BUY is placed. Initialises the trailing stop."""
    atr  = calc_atr(symbol)
    dist = _capped_dist(atr * config.TRAILING_STOP_ATR_MULT if atr > 0 else 0, entry_price)
    stop = entry_price - dist

    data = _load_trailing()
    data[symbol] = {
        "entry":   round(entry_price, 4),
        "highest": round(entry_price, 4),
        "stop":    round(stop, 4),
        "atr":     round(atr, 4),
        "dist":    round(dist, 4),
    }
    _save_trailing(data)
    log.info(f"  🎯 Trailing stop set: {symbol} entry=${entry_price:.2f} "
             f"stop=${stop:.2f} (ATR×{config.TRAILING_STOP_ATR_MULT}={dist:.2f})")


def locked_stop(entry: float, highest: float) -> float | None:
    """The profit-lock stop for a position, or None while its best gain is under the trigger."""
    trigger, keep = getattr(config, "PROFIT_LOCK_TRIGGER", 0), getattr(config, "PROFIT_LOCK_KEEP", 0)
    if not trigger or not entry:
        return None
    best = highest / entry - 1
    return entry * (1 + keep * best) if best >= trigger else None


def update_trailing_stops(current_prices: dict) -> list[str]:
    """
    Update trailing stops for all tracked positions.
    Returns list of symbols that have been stopped out.
    """
    data      = _load_trailing()
    stopped   = []
    changed   = False

    for symbol, ts in list(data.items()):
        price = current_prices.get(symbol) or current_prices.get(symbol.replace("/",""))
        if not price:
            continue

        # Positions opened before the cap existed may carry a wider distance
        dist = _capped_dist(ts["dist"], ts["entry"])
        if dist < ts["dist"]:
            capped_stop = round(max(ts["stop"], ts["highest"] - dist), 4)
            log.info(f"  🎯 Trailing distance capped: {symbol} "
                     f"{ts['dist']:.2f} → {dist:.2f}, stop=${capped_stop:.2f}")
            data[symbol]["dist"] = round(dist, 4)
            data[symbol]["stop"] = capped_stop
            changed = True

        new_high = max(ts["highest"], price)
        new_stop = new_high - dist

        if new_high > ts["highest"]:
            old_stop = data[symbol]["stop"]
            log.info(f"  📈 Trailing stop raised: {symbol} "
                     f"high=${new_high:.2f} → stop=${new_stop:.2f}")
            data[symbol]["highest"] = round(new_high, 4)
            data[symbol]["stop"]    = round(new_stop, 4)
            changed = True
            try:
                import alerts
                alerts.trailing_stop_raised(symbol, old_stop, new_stop, new_high)
            except Exception:
                pass

        # Profit lock: once the best gain reaches the trigger, keep part of it
        lock = locked_stop(ts.get("avg_entry") or ts["entry"], data[symbol]["highest"])
        if lock and lock > data[symbol]["stop"] + 1e-9:
            log.info(f"  🔒 Profit lock: {symbol} stop ${data[symbol]['stop']:.2f} → ${lock:.2f} "
                     f"(best ${data[symbol]['highest']:.2f})")
            data[symbol]["stop"] = round(lock, 4)
            data[symbol]["locked"] = True
            changed = True

        # Check if stopped out
        if price <= data[symbol]["stop"]:
            log.info(f"  🛑 Trailing stop triggered: {symbol} "
                     f"price=${price:.2f} ≤ stop=${data[symbol]['stop']:.2f}")
            stopped.append(symbol)
            del data[symbol]
            changed = True

    if changed:
        _save_trailing(data)

    return stopped


def remove_trailing_stop(symbol: str):
    """Remove trailing stop when position is closed."""
    data = _load_trailing()
    if symbol in data:
        del data[symbol]
        _save_trailing(data)


def get_trailing_stops() -> dict:
    return _load_trailing()


def set_trailing_field(symbol: str, key: str, value):
    """Store extra per-position data (e.g. broker avg_entry) alongside the stop."""
    data = _load_trailing()
    if symbol in data:
        data[symbol][key] = value
        _save_trailing(data)


# ── 3. Daily max loss kill switch ─────────────────────────────────────────────

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
    """Call after every closed trade. Tracks today's cumulative P&L."""
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


def is_trading_halted() -> bool:
    """Returns True if the daily loss kill switch has fired."""
    return _load_daily_loss().get("halted", False)


def get_daily_stats() -> dict:
    return _load_daily_loss()


# ── 4. Drawdown guard ─────────────────────────────────────────────────────────

def drawdown_size_multiplier(current_value: float, peak_value: float = None) -> float:
    """
    Reduce position sizes proportionally during significant drawdowns,
    measured from peak equity (equity_tracker), not the starting balance.
    >5% drawdown  → 80% of normal size
    >10% drawdown → 60% of normal size
    >15% drawdown → 40% of normal size
    >20% drawdown → 20% of normal size (near-defensive mode)
    """
    if peak_value is None:
        import equity_tracker
        peak_value = equity_tracker.peak_value()
    peak_value = max(peak_value, current_value)

    drawdown_pct = (peak_value - current_value) / peak_value * 100
    if drawdown_pct <= 5:
        return 1.0
    elif drawdown_pct <= 10:
        return 0.8
    elif drawdown_pct <= 15:
        return 0.6
    elif drawdown_pct <= 20:
        return 0.4
    else:
        return 0.2
