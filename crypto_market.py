"""
crypto_market.py — daily crypto prices from Coinbase's public market data (no account, no keys).

Used by the crypto demo (crypto_plan.py, crypto_paper.py, crypto_backtest.py). Stdlib only.
Daily candles are cached in backtest_cache/ so the backtest and the paper book don't refetch history.
"""
import json
import os
import time
import urllib.request
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, "backtest_cache")
BASE = "https://api.exchange.coinbase.com"
FIRST_DAY = {"BTC-USD": "2015-07-20", "ETH-USD": "2016-05-18"}
DAY = 86400
PAGE = 300                                   # Coinbase returns at most 300 candles per request


def _get(path: str, params: dict | None = None):
    q = "?" + "&".join(f"{k}={v}" for k, v in params.items()) if params else ""
    req = urllib.request.Request(BASE + path + q, headers={"User-Agent": "charles-crypto-demo"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def candles_to_closes(rows) -> list:
    """Coinbase candles [[time, low, high, open, close, volume], ...] (newest first) → [[day, close]]
    oldest first, one per UTC day, without duplicates."""
    out = {}
    for t, _lo, _hi, _op, close, *_ in rows:
        out[datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")] = float(close)
    return [[d, out[d]] for d in sorted(out)]


def daily_closes(product: str, start: str | None = None, max_age: float = 6 * 3600) -> list:
    """[[YYYY-MM-DD, close]] for every finished UTC day from `start` (default: the product's first day)."""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"crypto-{product}.json")
    try:
        with open(path) as f:
            cached = json.load(f)
        if time.time() - os.path.getmtime(path) < max_age:
            return [c for c in cached if c[0] >= (start or "")]
    except (OSError, ValueError):
        cached = []
    today = datetime.now(timezone.utc).date()
    begin = datetime.fromisoformat(cached[-1][0]).date() if cached else datetime.fromisoformat(FIRST_DAY[product]).date()
    rows = []
    while begin < today:
        end = min(today, begin + timedelta(days=PAGE - 1))
        rows += _get(f"/products/{product}/candles", {"granularity": DAY, "start": begin.isoformat(), "end": end.isoformat()})
        begin = end
        time.sleep(0.2)                       # public limit is ~10 requests/s
    merged = {d: c for d, c in cached}
    merged.update({d: c for d, c in candles_to_closes(rows) if d < today.isoformat()})   # today's candle isn't closed
    closes = [[d, merged[d]] for d in sorted(merged)]
    with open(path + ".tmp", "w") as f:
        json.dump(closes, f)
    os.replace(path + ".tmp", path)
    return [c for c in closes if c[0] >= (start or "")]


def spot(product: str) -> float:
    """The latest trade price."""
    return float(_get(f"/products/{product}/ticker")["price"])


def hourly_closes(product: str, start: str) -> list:
    """[[iso UTC time the hour ENDED, close]] from `start` (ISO) to now, oldest first; the hour still
    forming is left out, like the stocks chart's SPY line."""
    begin = datetime.fromisoformat(start).astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    now = datetime.now(timezone.utc)
    rows = []
    while begin < now:
        end = min(now, begin + timedelta(hours=PAGE - 1))
        rows += _get(f"/products/{product}/candles", {"granularity": 3600, "start": begin.strftime("%Y-%m-%dT%H:%M:%SZ"), "end": end.strftime("%Y-%m-%dT%H:%M:%SZ")})
        begin = end
    return hours_from_candles(rows, now)


def hours_from_candles(rows, now: datetime) -> list:
    out = {}
    for t, _lo, _hi, _op, close, *_ in rows:
        ended = datetime.fromtimestamp(t + 3600, timezone.utc)
        if ended <= now:
            out[ended.isoformat(timespec="minutes")] = float(close)
    return [[k, out[k]] for k in sorted(out)]
