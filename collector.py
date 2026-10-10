"""
collector.py — Fetches market data, news, and sentiment.
New in v4: Bollinger Bands, ATR, volume confirmation, multi-timeframe RSI.
"""

import os
import time
import logging
from datetime import datetime, timezone

import feedparser
import requests
import yfinance as yf
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
import finbert_sentiment as fbs

import config
import state_io

log    = logging.getLogger(__name__)
vader  = SentimentIntensityAnalyzer()

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)


# ── Stocks ────────────────────────────────────────────────────────────────────

def fetch_stock_data(symbol: str) -> dict:
    try:
        ticker = yf.Ticker(symbol)
        hist   = ticker.history(period="90d", interval="1d")   # longer for multi-TF
        info   = ticker.fast_info

        if hist.empty or len(hist) < 30:
            return {}

        # Drop any NaN rows (yfinance sometimes returns incomplete final bar)
        hist    = hist.dropna(subset=["Close", "High", "Low"])
        closes  = hist["Close"].tolist()
        highs   = hist["High"].tolist()
        lows    = hist["Low"].tolist()
        volumes = hist["Volume"].tolist()

        # ── Hourly candles for intraday signals ──
        hourly_data = _fetch_hourly(symbol)

        # ── Technical indicators (daily) ──
        rsi_14    = _calc_rsi(closes, 14)
        rsi_7     = _calc_rsi(closes, 7)     # short-term TF
        rsi_21    = _calc_rsi(closes, 21)    # longer TF
        macd, signal = _calc_macd(closes)
        bb_upper, bb_mid, bb_lower = _calc_bollinger(closes, 20, 2.0)
        atr       = _calc_atr(highs, lows, closes, 14)
        # 50-day MA breakout: price crosses above MA50 with momentum
        ma50        = sum(closes[-50:]) / 50 if len(closes) >= 50 else sum(closes) / len(closes)
        ma50_prev   = sum(closes[-51:-1]) / 50 if len(closes) >= 51 else ma50
        crossed_ma50 = closes[-1] > ma50 and closes[-2] <= ma50_prev
        above_ma50   = closes[-1] > ma50
        ma50_dist_pct = round((closes[-1] - ma50) / ma50 * 100, 2) if ma50 > 0 else 0.0

        # Volume confirmation: today vs 20-day avg
        avg_vol_20  = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else volumes[-1]
        vol_ratio   = volumes[-1] / avg_vol_20 if avg_vol_20 > 0 else 1.0
        vol_confirm = vol_ratio >= config.VOLUME_CONFIRM_RATIO

        # Bollinger Band position (0=at lower, 1=at upper)
        bb_range   = bb_upper - bb_lower
        bb_pos     = (closes[-1] - bb_lower) / bb_range if bb_range > 0 else 0.5

        # Multi-TF alignment: all three RSIs agree?
        mtf_buy  = rsi_7 < config.RSI_OVERSOLD and rsi_14 < (config.RSI_OVERSOLD + 5)
        mtf_sell = rsi_7 > config.RSI_OVERBOUGHT and rsi_14 > (config.RSI_OVERBOUGHT - 5)

        return {
            "symbol":        symbol,
            "type":          "stock",
            "price":         round(closes[-1], 4),
            "change_pct":    round((closes[-1] - closes[-2]) / closes[-2] * 100, 2),
            "volume":        int(volumes[-1]),
            "avg_volume":    int(avg_vol_20),
            "vol_ratio":     round(vol_ratio, 2),
            "vol_confirm":   vol_confirm,
            "market_cap":    getattr(info, "market_cap", None),
            # RSI multi-timeframe
            "rsi":           round(rsi_14, 2),
            "rsi_7":         round(rsi_7, 2),
            "rsi_21":        round(rsi_21, 2),
            "mtf_buy":       mtf_buy,
            "mtf_sell":      mtf_sell,
            # MACD
            "macd":          round(macd, 4),
            "macd_signal":   round(signal, 4),
            # Bollinger Bands
            "bb_upper":      round(bb_upper, 4),
            "bb_mid":        round(bb_mid, 4),
            "bb_lower":      round(bb_lower, 4),
            "bb_pos":        round(bb_pos, 3),  # 0=lower band, 1=upper band
            # ATR
            "atr":           round(atr, 4),
            "atr_pct":       round(atr / closes[-1] * 100, 2),
            # 50MA breakout
            "ma50":          round(ma50, 4),
            "above_ma50":    above_ma50,
            "crossed_ma50":  crossed_ma50,
            "ma50_dist_pct": ma50_dist_pct,
            # History
            "price_history": [round(p, 4) for p in closes[-30:]],
            "fetched_at":    datetime.now(timezone.utc).isoformat(),
            # Hourly intraday data
            "hourly_rsi":    hourly_data.get("rsi", rsi_14),
            "hourly_macd":   hourly_data.get("macd", macd),
            "hourly_macd_signal": hourly_data.get("macd_signal", signal),
            "hourly_bb_pos": hourly_data.get("bb_pos", bb_pos),
            "hourly_trend":  hourly_data.get("trend", "neutral"),
            "intraday_change_pct": hourly_data.get("change_pct", 0),
            "mtf_agreement": _mtf_agreement(
                rsi_14, macd, signal, bb_pos,
                hourly_data.get("rsi", rsi_14),
                hourly_data.get("macd", macd),
                hourly_data.get("macd_signal", signal),
            ),
        }
    except Exception as e:
        log.warning(f"Stock fetch failed {symbol}: {e}")
        return {}


# ── Crypto ────────────────────────────────────────────────────────────────────


# ── News & Sentiment ──────────────────────────────────────────────────────────

def fetch_news(symbols: list) -> list:
    articles = []
    # Check if FinBERT is available
    finbert_active = fbs.is_finbert_active()
    log.info(f"  📰 Sentiment engine: {'FinBERT' if finbert_active else 'VADER'}")

    for feed_url in config.RSS_FEEDS:
        try:
            # feedparser has no timeout; one stalled feed would hang the whole cycle
            resp = requests.get(feed_url, timeout=15,
                                headers={"User-Agent": feedparser.USER_AGENT})
            feed = feedparser.parse(resp.content)
            if not feed.entries:
                log.warning(f"  ⚠️  No entries from {feed_url}")
                continue
            log.info(f"  📡 {len(feed.entries)} articles from {feed_url[:50]}")
            for entry in feed.entries[:40]:
                title   = entry.get("title", "").strip()
                summary = entry.get("summary", "").strip()
                if not title:
                    continue
                text      = f"{title}. {summary}"
                mentioned = [s.split("/")[0] for s in symbols
                             if s.split("/")[0] in text.upper()]
                # Use FinBERT if available, else VADER
                if finbert_active:
                    sentiment = fbs.score(text)
                else:
                    sentiment = round(vader.polarity_scores(text)["compound"], 3)
                articles.append({
                    "title":     title,
                    "summary":   summary[:400],
                    "source":    feed_url,
                    "published": entry.get("published", ""),
                    "url":       entry.get("link", ""),
                    "sentiment": sentiment,
                    "mentioned": mentioned,
                    "engine":    "finbert" if finbert_active else "vader",
                })
        except Exception as e:
            log.warning(f"RSS failed {feed_url}: {e}")

    log.info(f"  📰 Total articles collected: {len(articles)}")
    articles.sort(key=lambda x: abs(x["sentiment"]), reverse=True)
    return articles[:60]



def collect_all() -> dict:
    log.info("📡 Collecting market data...")
    all_syms = config.STOCK_WATCHLIST
    market   = {}

    for sym in config.STOCK_WATCHLIST:
        log.info(f"  stock  → {sym}")
        d = fetch_stock_data(sym)
        if d:
            market[sym] = d
        time.sleep(0.3)

    log.info("📰 Fetching news & sentiment...")
    news       = fetch_news(all_syms)
    snapshot = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "market":       market,
        "news":         news,
    }
    state_io.write_json(os.path.join(DATA_DIR, "latest.json"), snapshot, indent=2)
    log.info(f"✅ Snapshot saved — {len(market)} symbols")
    return snapshot


# ── Technical indicators ──────────────────────────────────────────────────────

def _calc_rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _calc_macd(closes: list, fast=12, slow=26, signal=9):
    def ema(data, n):
        k = 2 / (n + 1)
        r = [data[0]]
        for p in data[1:]:
            r.append(p * k + r[-1] * (1 - k))
        return r
    if len(closes) < slow + signal:
        return 0.0, 0.0
    ef  = ema(closes, fast)
    es  = ema(closes, slow)
    ml  = [f - s for f, s in zip(ef, es)]
    sl  = ema(ml, signal)
    return ml[-1], sl[-1]


def _calc_bollinger(closes: list, period: int = 20, std_dev: float = 2.0):
    if len(closes) < period:
        p = closes[-1]
        return p, p, p
    window = closes[-period:]
    mid    = sum(window) / period
    var    = sum((x - mid) ** 2 for x in window) / period
    std    = var ** 0.5
    return mid + std_dev * std, mid, mid - std_dev * std


def _calc_atr(highs, lows, closes, period=14) -> float:
    if len(closes) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i]  - closes[i-1]),
        ))
    return sum(trs[-period:]) / period


# ── Hourly candle fetch ───────────────────────────────────────────────────────

def _fetch_hourly(symbol: str) -> dict:
    """
    Fetch 5 days of 1-hour candles and compute intraday indicators.
    Returns dict of hourly RSI, MACD, BB position, trend direction.
    """
    try:
        ticker = yf.Ticker(symbol)
        hist   = ticker.history(period="5d", interval="1h")
        if hist.empty or len(hist) < 20:
            return {}

        closes = hist["Close"].tolist()
        highs  = hist["High"].tolist()
        lows   = hist["Low"].tolist()

        rsi    = _calc_rsi(closes, 14)
        macd, signal = _calc_macd(closes, fast=12, slow=26, signal=9)
        bb_upper, bb_mid, bb_lower = _calc_bollinger(closes, 20, 2.0)
        bb_range = bb_upper - bb_lower
        bb_pos   = (closes[-1] - bb_lower) / bb_range if bb_range > 0 else 0.5

        # Intraday trend: compare current price vs 4-hour ago
        lookback = min(4, len(closes) - 1)
        intraday_change = (closes[-1] - closes[-lookback]) / closes[-lookback] * 100

        trend = "up" if intraday_change > 0.3 else ("down" if intraday_change < -0.3 else "neutral")

        return {
            "rsi":         round(rsi, 2),
            "macd":        round(macd, 4),
            "macd_signal": round(signal, 4),
            "bb_pos":      round(bb_pos, 3),
            "trend":       trend,
            "change_pct":  round(intraday_change, 2),
        }
    except Exception as e:
        log.debug(f"Hourly fetch failed {symbol}: {e}")
        return {}


def _mtf_agreement(
    daily_rsi: float, daily_macd: float, daily_signal: float, daily_bb: float,
    hourly_rsi: float, hourly_macd: float, hourly_signal: float,
) -> str:
    """
    Returns 'bullish', 'bearish', or 'mixed' based on
    whether daily and hourly indicators agree.
    """
    daily_bull  = daily_rsi < 50 and daily_macd > daily_signal and daily_bb < 0.5
    daily_bear  = daily_rsi > 50 and daily_macd < daily_signal and daily_bb > 0.5
    hourly_bull = hourly_rsi < 50 and hourly_macd > hourly_signal
    hourly_bear = hourly_rsi > 50 and hourly_macd < hourly_signal

    if daily_bull and hourly_bull:
        return "bullish"
    if daily_bear and hourly_bear:
        return "bearish"
    return "mixed"
