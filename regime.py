"""
regime.py — Market regime detector.
Classifies the current market into one of four modes:
  TRENDING_BULL  — strong uptrend, be aggressive
  TRENDING_BEAR  — strong downtrend, short or hedge
  RANGING        — sideways chop, reduce size, mean-revert
  VOLATILE       — high uncertainty, reduce all exposure

Used by brain.py to switch strategy mode automatically.
All free — uses only yfinance data.
"""

import logging
import time
import yfinance as yf

log = logging.getLogger(__name__)

# Cache so we don't hammer Yahoo Finance
_cache: dict = {"ts": 0, "regime": "TRENDING_BULL", "detail": {}}
CACHE_TTL = 1800   # 30 minutes


def detect_regime() -> tuple[str, dict]:
    """
    Returns (regime_name, detail_dict).
    Regime names: TRENDING_BULL | TRENDING_BEAR | RANGING | VOLATILE
    """
    now = time.time()
    if _cache["ts"] > now - CACHE_TTL:
        return _cache["regime"], _cache["detail"]

    try:
        spy  = yf.Ticker("SPY")
        hist = spy.history(period="60d", interval="1d")
        if hist.empty or len(hist) < 30:
            return "TRENDING_BULL", {}

        hist    = hist.dropna(subset=["Close", "High", "Low"])
        regime, detail = classify(hist["Close"].tolist(), hist["High"].tolist(),
                                  hist["Low"].tolist(), hist["Volume"].tolist())
        _cache.update({"ts": now, "regime": regime, "detail": detail})
        log.info(f"  🌡️  Market regime: {regime} | ATR={detail['atr_pct']:.1f}% | "
                 f"mom={detail['momentum_10d']:+.1f}% | chop={detail['choppiness']:.2f}")
        return regime, detail

    except Exception as e:
        log.warning(f"Regime detection failed: {e} — defaulting to TRENDING_BULL")
        return "TRENDING_BULL", {}


def classify(closes: list, highs: list, lows: list, volumes: list) -> tuple[str, dict]:
    """The regime from SPY daily bars, oldest first (pure; detect_regime fetches them).
    Shared with backtest_orb.py so the ORB regime filter is tested on the same rule."""

    # ── Trend strength: ADX proxy ──
    # Compare 20-day MA vs 50-day MA direction
    ma20 = sum(closes[-20:]) / 20
    ma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else sum(closes) / len(closes)
    price = closes[-1]
    trend_up   = price > ma20 > ma50
    trend_down = price < ma20 < ma50

    # ── Volatility: ATR as % of price ──
    atr = _calc_atr(highs, lows, closes, 14)
    atr_pct = (atr / price) * 100

    # ── Momentum: 10-day return ──
    momentum_10d = (closes[-1] - closes[-10]) / closes[-10] * 100 if len(closes) >= 10 else 0

    # ── Volume trend ──
    avg_vol_20 = sum(volumes[-20:]) / 20
    avg_vol_5  = sum(volumes[-5:])  / 5
    vol_expanding = avg_vol_5 > avg_vol_20 * 1.1

    # ── Choppiness: price range vs net move ──
    price_range = max(closes[-20:]) - min(closes[-20:])
    net_move    = abs(closes[-1] - closes[-20])
    choppiness  = 1 - (net_move / price_range) if price_range > 0 else 0.5

    # ── Classify ──
    if atr_pct > 2.5:
        regime = "VOLATILE"
    elif trend_up and momentum_10d > 2 and not choppiness > 0.7:
        regime = "TRENDING_BULL"
    elif trend_down and momentum_10d < -2 and not choppiness > 0.7:
        regime = "TRENDING_BEAR"
    else:
        regime = "RANGING"

    detail = {
        "regime":        regime,
        "price":         round(price, 2),
        "ma20":          round(ma20, 2),
        "ma50":          round(ma50, 2),
        "atr_pct":       round(atr_pct, 2),
        "momentum_10d":  round(momentum_10d, 2),
        "choppiness":    round(choppiness, 2),
        "vol_expanding": vol_expanding,
        "trend_up":      trend_up,
        "trend_down":    trend_down,
    }
    return regime, detail


def regime_multipliers(regime: str) -> dict:
    """
    Returns strategy parameter multipliers for the given regime.
    Used to scale position sizes, confidence thresholds, etc.
    """
    return {
        "TRENDING_BULL": {
            "size_mult":       1.0,    # normal sizing
            "conf_boost":      0.0,    # no change to confidence threshold
            "allow_longs":     True,
            "allow_shorts":    False,  # no need to short in bull
            "allow_hedges":    False,
            "description":     "Normal long trading",
        },
        "TRENDING_BEAR": {
            "size_mult":       0.6,    # reduce long exposure
            "conf_boost":      0.10,   # raise bar for longs
            "allow_longs":     False,  # suppress new longs
            "allow_shorts":    True,   # enable shorting
            "allow_hedges":    True,   # enable inverse ETFs
            "description":     "Short & hedge mode",
        },
        "RANGING": {
            "size_mult":       0.7,    # smaller positions
            "conf_boost":      0.05,   # slightly higher bar
            "allow_longs":     True,
            "allow_shorts":    False,
            "allow_hedges":    False,
            "description":     "Reduced size, mean-revert only",
        },
        "VOLATILE": {
            "size_mult":       0.4,    # much smaller positions
            "conf_boost":      0.15,   # high bar required
            "allow_longs":     True,
            "allow_shorts":    False,
            "allow_hedges":    True,   # hedge with inverse ETFs
            "description":     "Defensive mode, reduced all exposure",
        },
    }.get(regime, {"size_mult": 1.0, "conf_boost": 0.0,
                   "allow_longs": True, "allow_shorts": False,
                   "allow_hedges": False, "description": "Unknown"})


def _calc_atr(highs, lows, closes, period=14) -> float:
    if len(closes) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i]  - lows[i],
            abs(highs[i]  - closes[i-1]),
            abs(lows[i]   - closes[i-1]),
        )
        trs.append(tr)
    return sum(trs[-period:]) / period
