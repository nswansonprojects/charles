"""
sector_rotation.py — Tracks which market sectors are leading or lagging.
Scores the 11 SPDR sector ETFs by momentum and returns a bonus/penalty
for each stock based on its sector's current strength.

Used by screener.py to weight candidates toward hot sectors.
No paid data — uses yfinance only.
"""

import logging
import time
from datetime import datetime, timezone

import yfinance as yf

log = logging.getLogger(__name__)

# 11 SPDR sector ETFs mapped to their member stocks
SECTOR_ETFS = {
    "XLK":  "Technology",
    "XLF":  "Financials",
    "XLV":  "Healthcare",
    "XLY":  "Consumer Discretionary",
    "XLP":  "Consumer Staples",
    "XLE":  "Energy",
    "XLI":  "Industrials",
    "XLC":  "Communication Services",
    "XLRE": "Real Estate",
    "XLB":  "Materials",
    "XLU":  "Utilities",
}

# Map individual stocks to their primary sector ETF
STOCK_SECTOR_MAP = {
    # Technology (XLK)
    "AAPL":"XLK","MSFT":"XLK","NVDA":"XLK","AMD":"XLK","INTC":"XLK",
    "QCOM":"XLK","MU":"XLK","AMAT":"XLK","LRCX":"XLK","KLAC":"XLK",
    "MRVL":"XLK","ARM":"XLK","ASML":"XLK","AVGO":"XLK","TXN":"XLK",
    "ON":"XLK","SMCI":"XLK","DELL":"XLK","HPE":"XLK","WDC":"XLK","STX":"XLK",
    "ORCL":"XLK","CRM":"XLK","ADBE":"XLK","NOW":"XLK","SNOW":"XLK",
    "DDOG":"XLK","ZS":"XLK","CRWD":"XLK","S":"XLK","NET":"XLK",
    "GTLB":"XLK","HUBS":"XLK","PLTR":"XLK",
    # Communication Services (XLC)
    "GOOGL":"XLC","META":"XLC","NFLX":"XLC","TSLA":"XLC",
    # Consumer Discretionary (XLY)
    "AMZN":"XLY","SHOP":"XLY","ETSY":"XLY","UBER":"XLY","LYFT":"XLY",
    "ABNB":"XLY","NKE":"XLY","LULU":"XLY","RH":"XLY","DECK":"XLY","ONON":"XLY",
    # Consumer Staples (XLP)
    "WMT":"XLP","COST":"XLP","TGT":"XLP","HD":"XLP",
    # Financials (XLF)
    "JPM":"XLF","BAC":"XLF","GS":"XLF","MS":"XLF","C":"XLF","WFC":"XLF",
    "BX":"XLF","KKR":"XLF","APO":"XLF","SCHW":"XLF",
    "V":"XLF","MA":"XLF","PYPL":"XLF","XYZ":"XLF","COIN":"XLF",
    "SOFI":"XLF","AFRM":"XLF","BILL":"XLF","HOOD":"XLF","NU":"XLF",
    # Healthcare (XLV)
    "LLY":"XLV","NVO":"XLV","ABBV":"XLV","MRK":"XLV","PFE":"XLV",
    "AMGN":"XLV","GILD":"XLV","VRTX":"XLV","REGN":"XLV","MRNA":"XLV",
    # Energy (XLE)
    "XOM":"XLE","CVX":"XLE","OXY":"XLE","COP":"XLE","PSX":"XLE",
    # Industrials (XLI)
    "CAT":"XLI","DE":"XLI","GE":"XLI","BA":"XLI","LMT":"XLI",
    "RTX":"XLI","NOC":"XLI","HON":"XLI","MMM":"XLI","UNP":"XLI",
    # Materials (XLB)
    "WOLF":"XLB","CRUS":"XLB",
}

# Cache so we don't hit yfinance every cycle
_cache: dict = {"ts": 0, "scores": {}}
CACHE_TTL = 1800  # 30 minutes


def get_sector_scores() -> dict:
    """
    Returns a dict of {ETF_ticker: score} where score is -15 to +15.
    Positive = sector is outperforming, negative = underperforming.
    Cached for 30 minutes.
    """
    now = time.time()
    if _cache["ts"] > now - CACHE_TTL and _cache["scores"]:
        return _cache["scores"]

    scores = {}
    spy_return_10d = 0.0

    # Get SPY as benchmark
    try:
        spy_hist = yf.Ticker("SPY").history(period="30d", interval="1d")
        spy_hist = spy_hist.dropna(subset=["Close"])
        spy_closes = spy_hist["Close"].tolist()
        if len(spy_closes) >= 10:
            spy_return_10d = (spy_closes[-1] - spy_closes[-10]) / spy_closes[-10] * 100
    except Exception:
        pass

    for etf, sector_name in SECTOR_ETFS.items():
        try:
            hist = yf.Ticker(etf).history(period="30d", interval="1d")
            hist = hist.dropna(subset=["Close"])
            closes = hist["Close"].tolist()
            if len(closes) < 10:
                scores[etf] = 0
                continue

            # 10-day momentum
            mom_10d = (closes[-1] - closes[-10]) / closes[-10] * 100
            # 5-day momentum (short-term acceleration)
            mom_5d  = (closes[-1] - closes[-5]) / closes[-5] * 100 if len(closes) >= 5 else 0
            # Relative strength vs SPY
            rs = mom_10d - spy_return_10d

            # Score: blend of absolute momentum and relative strength
            raw = (mom_10d * 0.4) + (mom_5d * 0.3) + (rs * 0.3)
            score = round(max(-15, min(15, raw * 1.5)), 1)
            scores[etf] = score

            log.debug(f"  {etf} ({sector_name}): {mom_10d:+.1f}% 10d, RS={rs:+.1f}% → score {score:+.1f}")
            time.sleep(0.3)
        except Exception as e:
            log.debug(f"Sector score failed for {etf}: {e}")
            scores[etf] = 0

    _cache.update({"ts": now, "scores": scores})

    # Log top/bottom sectors
    if scores:
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top    = [(SECTOR_ETFS.get(e, e), s) for e, s in ranked[:3]]
        bot    = [(SECTOR_ETFS.get(e, e), s) for e, s in ranked[-3:]]
        log.info(f"  📊 Leading sectors:  {', '.join(f'{n} {s:+.1f}' for n, s in top)}")
        log.info(f"  📊 Lagging sectors:  {', '.join(f'{n} {s:+.1f}' for n, s in bot)}")

    return scores


def get_sector_bonus(symbol: str) -> float:
    """
    Returns a score bonus/penalty for a stock based on its sector's momentum.
    Range: -15 to +15 points. Call this from screener._score_stock().
    """
    etf = STOCK_SECTOR_MAP.get(symbol)
    if not etf:
        return 0.0
    scores = get_sector_scores()
    return scores.get(etf, 0.0)


def get_leading_sectors(top_n: int = 3) -> list[dict]:
    """Returns the top N leading sectors with their ETF and score."""
    scores = get_sector_scores()
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [
        {"etf": etf, "sector": SECTOR_ETFS.get(etf, etf), "score": score}
        for etf, score in ranked[:top_n]
    ]
