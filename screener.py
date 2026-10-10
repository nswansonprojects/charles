"""
screener.py — Autonomous stock screener.
Scans S&P 500 every morning and builds its own watchlist
based on momentum, volume spikes, and news sentiment.
Also runs a performance feedback loop that tunes strategy thresholds.
"""

import json
import logging
import os
import time
import random
from datetime import datetime, timezone

import requests
import yfinance as yf

import state_io

log = logging.getLogger(__name__)

# Sector rotation — imported lazily to avoid circular deps at startup
try:
    import sector_rotation as _sr
    _SR_AVAILABLE = True
except Exception:
    _SR_AVAILABLE = False

DATA_DIR        = os.path.join(os.path.dirname(__file__), "data")
WATCHLIST_FILE  = os.path.join(DATA_DIR, "watchlist.json")

# SPY reference cache for relative strength calculation
_spy_ref: dict = {"ts": 0, "close_10d_ago": None, "close_now": None}
PERFORMANCE_FILE= os.path.join(DATA_DIR, "performance.json")


# Broad stock universe — 100 liquid names across all sectors
# Re-scored every morning + 1 PM. Charles picks the top movers dynamically.
STOCK_UNIVERSE = [
    # ── Mega-cap tech ─────────────────────────────────────────────────────────
    "AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","ORCL","CRM","ADBE",
    # ── Semiconductors ────────────────────────────────────────────────────────
    "AMD","INTC","QCOM","MU","AMAT","LRCX","KLAC","ASML","MRVL","ARM",
    "AVGO","TXN","ON","WOLF","CRUS",
    # ── AI / Cloud / Software ─────────────────────────────────────────────────
    "PLTR","SNOW","NET","DDOG","ZS","CRWD","S","GTLB","HUBS","NOW",
    "SMCI","DELL","HPE","WDC","STX",
    # ── Fintech / Payments ────────────────────────────────────────────────────
    "V","MA","PYPL","XYZ","COIN","SOFI","AFRM","BILL","HOOD","NU",
    # ── Consumer / Retail ─────────────────────────────────────────────────────
    "WMT","COST","HD","TGT","SHOP","ETSY","UBER","LYFT","ABNB",   # AMZN listed under mega-cap
    # ── Finance / Banks ───────────────────────────────────────────────────────
    "JPM","BAC","GS","MS","C","WFC","BX","KKR","APO","SCHW",
    # ── Healthcare / Biotech ─────────────────────────────────────────────────
    "LLY","NVO","ABBV","MRK","PFE","AMGN","GILD","VRTX","REGN","MRNA",
    # ── Energy ────────────────────────────────────────────────────────────────
    "XOM","CVX","OXY","COP","PSX",
    # ── Industrials / Defense ─────────────────────────────────────────────────
    "CAT","DE","GE","BA","LMT","RTX","NOC","HON","MMM","UNP",
    # ── Consumer discretionary ───────────────────────────────────────────────
    "NKE","LULU","RH","DECK","ONON",
]


# ── Scoring ───────────────────────────────────────────────────────────────────


def _get_spy_return_10d() -> float:
    """Get SPY 10-day return for relative strength calculation. Cached 30 min."""
    import time as _t
    now = _t.time()
    if _spy_ref["ts"] > now - 1800 and _spy_ref["close_10d_ago"]:
        c0, c1 = _spy_ref["close_10d_ago"], _spy_ref["close_now"]
        return (c1 - c0) / c0 * 100 if c0 else 0.0
    try:
        hist = yf.Ticker("SPY").history(period="20d", interval="1d")
        hist = hist.dropna(subset=["Close"])
        closes = hist["Close"].tolist()
        if len(closes) >= 10:
            _spy_ref.update({"ts": now, "close_10d_ago": closes[-10], "close_now": closes[-1]})
            return (closes[-1] - closes[-10]) / closes[-10] * 100
    except Exception:
        pass
    return 0.0

def _calc_rsi_screener(closes, period=14):
    """Simple RSI calculation for screener — avoids importing collector."""
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [d for d in deltas if d > 0]
    losses = [-d for d in deltas if d < 0]
    avg_gain = sum(gains[-period:]) / period if gains else 0
    avg_loss = sum(losses[-period:]) / period if losses else 0.0001
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _calc_macd_screener(closes):
    """Simple MACD for screener."""
    if len(closes) < 26:
        return 0.0, 0.0
    def ema(data, n):
        k = 2 / (n + 1)
        e = data[0]
        for p in data[1:]:
            e = p * k + e * (1 - k)
        return e
    ema12 = ema(closes[-26:], 12)
    ema26 = ema(closes[-26:], 26)
    macd  = ema12 - ema26
    # Signal: 9-period EMA of MACD (approximate with last value)
    return macd, macd * 0.9  # simplified signal line


def _score_stock(symbol: str) -> dict | None:
    """
    Score a stock 0-100 based on:
      - 5-day momentum           (up to ±30 pts)
      - Volume spike             (up to +20 pts)
      - RSI positioning          (up to +20 pts — rewards momentum zone 45-68)
      - 50MA breakout/trend      (up to +20 pts)
      - MACD bullish crossover   (up to +10 pts)
    """
    try:
        ticker = yf.Ticker(symbol)
        hist   = ticker.history(period="60d", interval="1d")
        hist   = hist.dropna(subset=["Close", "High", "Low"])
        if hist.empty or len(hist) < 15:
            return None

        closes  = hist["Close"].tolist()
        volumes = hist["Volume"].tolist()

        # ── Base signals ──────────────────────────────────────────────────────
        momentum_5d = (closes[-1] - closes[-5]) / closes[-5] * 100 if len(closes) >= 5 else 0
        avg_vol     = sum(volumes[-10:]) / 10 if len(volumes) >= 10 else volumes[-1]
        vol_spike   = volumes[-1] / avg_vol if avg_vol > 0 else 1.0
        ma20        = sum(closes[-20:]) / min(20, len(closes))
        above_ma20  = closes[-1] > ma20

        # ── RSI scoring ───────────────────────────────────────────────────────
        rsi = _calc_rsi_screener(closes)
        if 45 <= rsi <= 68:
            rsi_score = 20    # momentum zone — ideal entry
        elif 35 <= rsi < 45:
            rsi_score = 12    # oversold recovery
        elif 68 < rsi <= 78:
            rsi_score = 5     # extended but still running
        elif rsi < 35:
            rsi_score = 8     # deep oversold dip
        else:
            rsi_score = -5    # overbought

        # ── 50MA breakout scoring ─────────────────────────────────────────────
        ma50_score = 0
        if len(closes) >= 51:
            ma50      = sum(closes[-50:]) / 50
            ma50_prev = sum(closes[-51:-1]) / 50
            crossed_up = closes[-1] > ma50 and closes[-2] <= ma50_prev
            above_ma50 = closes[-1] > ma50
            dist_pct   = (closes[-1] - ma50) / ma50 * 100 if ma50 > 0 else 0
            if crossed_up:
                ma50_score = 20   # fresh breakout — strongest signal
            elif above_ma50 and 0 < dist_pct <= 5:
                ma50_score = 12   # just above MA, continuation zone
            elif above_ma50:
                ma50_score = 5    # above MA but extended
            else:
                ma50_score = -5   # below MA — downtrend
        else:
            above_ma50  = above_ma20
            ma50_score  = 5 if above_ma50 else -5
            crossed_up  = False
            dist_pct    = 0.0

        # ── MACD scoring ──────────────────────────────────────────────────────
        macd, macd_sig = _calc_macd_screener(closes)
        macd_score = 10 if macd > macd_sig else -5

        # ── Composite score ───────────────────────────────────────────────────
        score = (
            min(max(momentum_5d * 3, -30), 30) +   # momentum   (±30)
            min((vol_spike - 1) * 15, 20) +         # vol spike  (0–20)
            rsi_score +                              # RSI        (−5 to +20)
            ma50_score +                             # 50MA       (−5 to +20)
            macd_score +                             # MACD       (−5 to +10)
            30                                       # base
        )
        score = round(min(max(score, 0), 100), 1)

        # ── Relative strength vs SPY ──────────────────────────────────────────
        spy_ret = _get_spy_return_10d()
        stock_ret_10d = (closes[-1] - closes[-10]) / closes[-10] * 100 if len(closes) >= 10 else 0
        rs_score = stock_ret_10d - spy_ret   # positive = outperforming market
        rs_bonus = round(max(-10, min(10, rs_score * 0.8)), 1)

        # ── 52-week high breakout ─────────────────────────────────────────────
        w52_bonus = 0
        near_52w_high = False
        if len(closes) >= 50:
            try:
                hist_52w = yf.Ticker(symbol).history(period="252d", interval="1d")
                hist_52w = hist_52w.dropna(subset=["Close"])
                if len(hist_52w) >= 50:
                    high_52w = max(hist_52w["Close"].tolist())
                    dist_to_high = (closes[-1] - high_52w) / high_52w * 100
                    if dist_to_high >= -3:       # within 3% of 52W high
                        w52_bonus = 12
                        near_52w_high = True
                    elif dist_to_high >= -8:     # within 8% — approaching
                        w52_bonus = 6
            except Exception:
                pass

        # ── Sector rotation bonus ─────────────────────────────────────────────
        sector_bonus = 0.0
        if _SR_AVAILABLE:
            try:
                sector_bonus = _sr.get_sector_bonus(symbol)
            except Exception:
                pass

        # Apply bonuses to final score
        score = round(min(max(score + rs_bonus + w52_bonus + sector_bonus, 0), 100), 1)

        return {
            "symbol":        symbol,
            "type":          "stock",
            "score":         score,
            "momentum_5d":   round(momentum_5d, 2),
            "vol_spike":     round(vol_spike, 2),
            "above_ma20":    bool(above_ma20),
            "above_ma50":    bool(above_ma50),
            "rsi":           rsi,
            "macd_bull":     bool(macd > macd_sig),
            "ma50_breakout": bool(crossed_up),
            "near_52w_high": near_52w_high,
            "rs_vs_spy":     round(rs_score, 2),
            "sector_bonus":  round(sector_bonus, 1),
            "price":         round(closes[-1], 2),
        }
    except Exception as e:
        log.debug(f"Score failed {symbol}: {e}")
        return None




# ── Main screener ─────────────────────────────────────────────────────────────

def run_screener(top_stocks: int | None = None, top_crypto: int = 0) -> dict:
    """
    Screen the full universe and return the best candidates.
    Saves results to data/watchlist.json.
    """
    log.info("🔍 Running autonomous screener...")
    if top_stocks is None:
        import config
        top_stocks = config.MAX_SYMBOLS_PER_CYCLE

    # Score stocks (shuffle to avoid always hitting same ones first)
    universe = STOCK_UNIVERSE.copy()
    random.shuffle(universe)

    stock_scores = []
    for i, sym in enumerate(universe):
        result = _score_stock(sym)
        if result:
            stock_scores.append(result)
        time.sleep(0.5)   # throttle every symbol
        if i % 10 == 9:
            time.sleep(5.0)   # longer pause every 10 symbols

    stock_scores.sort(key=lambda x: x["score"], reverse=True)
    top_stocks_list = stock_scores[:top_stocks]

    watchlist = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stocks":  [s["symbol"] for s in top_stocks_list],
        "scores":  top_stocks_list,
    }

    state_io.write_json(WATCHLIST_FILE, watchlist, indent=2)

    if top_stocks_list:
        log.info(f"  📈 Top stocks: {watchlist['stocks']}")
    else:
        log.warning("  ⚠️  Screener returned 0 stocks — rate limited. Using config watchlist.")
    return watchlist


def get_current_watchlist() -> tuple[list, list]:
    """
    Return (stock_list, crypto_list) from saved screener results.
    Falls back to config defaults if screener hasn't run yet.
    """
    import config
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE) as f:
            data = json.load(f)
        return data.get("stocks", config.STOCK_WATCHLIST), []
    return config.STOCK_WATCHLIST, []


# ── Performance feedback loop ─────────────────────────────────────────────────

def update_performance_feedback():
    """
    Analyse closed trades and adjust strategy thresholds.
    Saves tuned settings to data/performance.json.
    """
    import config

    trades_file = os.path.join(DATA_DIR, "trades.json")
    if not os.path.exists(trades_file):
        return

    with open(trades_file) as f:
        trades = json.load(f)

    sells = [t for t in trades if t["action"] == "SELL" and "pnl" in t]
    if len(sells) < 5:
        log.info("📊 Not enough closed trades yet for feedback loop (need 5+)")
        return

    wins   = [t for t in sells if t["pnl"] > 0]
    losses = [t for t in sells if t["pnl"] <= 0]
    win_rate = len(wins) / len(sells)

    # Track which symbols performed best/worst
    by_symbol = {}
    for t in sells:
        sym = t["symbol"]
        if sym not in by_symbol:
            by_symbol[sym] = []
        by_symbol[sym].append(t["pnl"])

    good_symbols = [s for s, pnls in by_symbol.items() if sum(pnls) > 0]
    bad_symbols  = [s for s, pnls in by_symbol.items() if sum(pnls) <= 0]

    # Tune confidence threshold based on win rate
    current_conf = config.MIN_CONFIDENCE
    if win_rate < 0.40:
        new_conf = min(current_conf + 0.05, 0.85)   # raise bar if losing
        log.info(f"📉 Win rate {win_rate:.0%} — raising confidence threshold to {new_conf:.0%}")
    elif win_rate > 0.65:
        new_conf = max(current_conf - 0.05, config.MIN_CONFIDENCE_FLOOR)   # lower bar if winning
        log.info(f"📈 Win rate {win_rate:.0%} — lowering confidence threshold to {new_conf:.0%}")
    else:
        new_conf = current_conf

    perf = {
        "updated_at":       datetime.now(timezone.utc).isoformat(),
        "win_rate":         round(win_rate * 100, 1),
        "total_closed":     len(sells),
        "total_pnl":        round(sum(t["pnl"] for t in sells), 2),
        "good_symbols":     good_symbols,
        "bad_symbols":      bad_symbols,
        "tuned_confidence": new_conf,
        "avg_win":          round(sum(t["pnl"] for t in wins) / len(wins), 2) if wins else 0,
        "avg_loss":         round(sum(t["pnl"] for t in losses) / len(losses), 2) if losses else 0,
    }

    state_io.write_json(PERFORMANCE_FILE, perf, indent=2)

    log.info(f"✅ Performance feedback saved — win rate {win_rate:.0%}, "
             f"confidence threshold → {new_conf:.0%}")
    return perf


def get_tuned_confidence() -> float:
    """Return the performance-tuned confidence threshold, or config default."""
    import config
    if os.path.exists(PERFORMANCE_FILE):
        with open(PERFORMANCE_FILE) as f:
            data = json.load(f)
        tuned = data.get("tuned_confidence", config.MIN_CONFIDENCE)
        return max(tuned, config.MIN_CONFIDENCE_FLOOR)
    return config.MIN_CONFIDENCE
