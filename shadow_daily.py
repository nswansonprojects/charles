"""
shadow_daily.py — shadow books that run on DAILY data, independent of the AI.

shadow.py tests changes to the live AI-driven rules; these books test whole other
strategies (research-backed ones that quant funds use) against the same $1,000 start,
so we can see whether a simple rule beats the AI. No orders, ever.

Runs once per trading day, at the first trading cycle at or after 15:30 ET (called from
main._update_shadow). Prices: yfinance daily closes, one download per run; the 15:30
"close" is the price at that time. Each strategy is a pure function
  signal(closes, book, today) -> {symbol: weight 0..1, or KEEP}
and the engine trades the book to those weights. KEEP leaves a position as it is.
Stock strategies (marked "bars") get signal(bars, book, today, earnings) instead: daily
OHLC of the screener universe (a second download) and an earnings-date lookup (cached in
data/earnings_cache.json).
State: data/shadow_daily.json.
"""
import logging
import os
from datetime import datetime, timezone

import state_io
import timeofday

log = logging.getLogger(__name__)

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "shadow_daily.json")
START_CASH = 1000.0
RUN_AFTER = "15:30"      # ET
KEEP = "keep"

SECTORS = ["XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB", "XLRE", "XLC"]


# ── indicators (closes = list of floats, oldest first) ───────────────────────

def sma(closes, n):
    return sum(closes[-n:]) / n if len(closes) >= n else None


def ret(closes, n):
    """Return over the last n trading days, as a fraction."""
    return closes[-1] / closes[-1 - n] - 1 if len(closes) > n else None


def rsi(closes, n=2):
    if len(closes) <= n:
        return None
    gains = losses = 0.0
    for a, b in zip(closes[-n - 1:-1], closes[-n:]):
        gains += max(b - a, 0)
        losses += max(a - b, 0)
    if losses == 0:
        return 100.0
    return 100 - 100 / (1 + gains / losses)


# ── strategies ───────────────────────────────────────────────────────────────

def etf_mean_reversion(closes, book, today):
    """SPY/QQQ above their 200-day average that just dropped hard (2-day RSI < 10, or the
    lowest close in 5 days): buy 45% each. Sell above the 5-day average or after 10 days."""
    out = {}
    for s in ("SPY", "QQQ"):
        c = closes.get(s) or []
        held = book["positions"].get(s)
        if held:
            days = held.get("days", 0)
            out[s] = 0 if (sma(c, 5) and c[-1] > sma(c, 5)) or days >= 10 else KEEP
        elif sma(c, 200) and c[-1] > sma(c, 200) and len(c) >= 5 and \
                ((rsi(c, 2) is not None and rsi(c, 2) < 10) or c[-1] <= min(c[-5:])):
            out[s] = 0.45
    return out


def crypto_trend(closes, book, today):
    """BTC and ETH, 50% each while the 20-day average is above the 50-day, else cash."""
    out = {}
    for s in ("BTC-USD", "ETH-USD"):
        c = closes.get(s) or []
        fast, slow = sma(c, 20), sma(c, 50)
        out[s] = 0.5 if fast and slow and fast > slow else 0
    return out


def dual_momentum(closes, book, today):
    """Monthly: hold whichever of SPY, EFA, GLD has the best 12-month return if it beats
    BIL (T-bills); otherwise BIL, or AGG if BIL is negative too."""
    r = {s: ret(closes.get(s) or [], 252) for s in ("SPY", "EFA", "GLD", "BIL", "AGG")}
    risky = {s: v for s, v in r.items() if s in ("SPY", "EFA", "GLD") and v is not None}
    if not risky or r["BIL"] is None:
        return {}
    best = max(risky, key=risky.get)
    pick = best if risky[best] > r["BIL"] else ("BIL" if r["BIL"] >= 0 else "AGG")
    return {pick: 1.0}


def sector_momentum(closes, book, today):
    """Monthly: the 3 sector ETFs with the best 6-month return, 1/3 each, only if above
    their 200-day average; any empty slot goes to BIL."""
    ranked = []
    for s in SECTORS:
        c = closes.get(s) or []
        r6, m200 = ret(c, 126), sma(c, 200)
        if r6 is not None and m200:
            ranked.append((r6, s, c[-1] > m200))
    ranked.sort(reverse=True)
    picks = [s for _, s, up in ranked[:3] if up]
    if not ranked:
        return {}
    out = {s: 1 / 3 for s in picks}
    if len(picks) < 3:
        out["BIL"] = (3 - len(picks)) / 3
    return out


def trend_following(closes, book, today):
    """SPY, QQQ, EFA, GLD, TLT: 20% each while above its 200-day average, else cash."""
    out = {}
    for s in ("SPY", "QQQ", "EFA", "GLD", "TLT"):
        c = closes.get(s) or []
        m = sma(c, 200)
        out[s] = 0.2 if m and c[-1] > m else 0
    return out


def atr(bars, n=14):
    """Average true range over the last n days. bars: {"high", "low", "close": lists}."""
    h, l, c = bars["high"], bars["low"], bars["close"]
    if len(c) <= n:
        return None
    tr = [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(len(c) - n, len(c))]
    return sum(tr) / n


def earnings_drift(bars, book, today, earnings):
    """Stocks that gapped up more than 4% at the open on earnings (report that morning or
    the evening before) and, the next day, still trade above the gap day's open: buy 20%
    each, max 4. Sell after 10 trading days, or when the close falls 2×ATR below the highest
    close since entry (checked once a day). bars: {symbol: {"date", "open", "high", "low",
    "close": lists, oldest first}}; earnings(symbol) -> set of "YYYY-MM-DD" report dates."""
    out = {}
    for s, held in book["positions"].items():
        b = bars.get(s)
        if not b:
            out[s] = KEEP
            continue
        since = [c for d, c in zip(b["date"], b["close"]) if d >= held["opened"]] or b["close"][-1:]
        a = atr(b)
        stop = max(since) - 2 * a if a else None
        out[s] = 0 if held.get("days", 0) >= 10 or (stop and b["close"][-1] < stop) else KEEP
    slots = 4 - len(book["positions"])
    gaps = []
    for s, b in bars.items():
        if s in book["positions"] or len(b["close"]) < 16 or b["date"][-1] != today:
            continue
        gap = b["open"][-2] / b["close"][-3] - 1        # yesterday's open vs the close before it
        if gap > 0.04 and b["close"][-1] > b["open"][-2]:
            gaps.append((gap, s))
    for gap, s in sorted(gaps, reverse=True):
        if slots <= 0:
            break
        b = bars[s]
        if earnings(s) & {b["date"][-2], b["date"][-3]}:   # reported before the open or after the close
            out[s] = 0.2
            slots -= 1
    return out


def trading_days_between(a: str, b: str) -> int:
    """Weekdays d with a < d <= b ("YYYY-MM-DD"); negative if b is before a. Exchange
    holidays are not known here, so they count as trading days."""
    from datetime import date, timedelta
    d0, d1 = date.fromisoformat(a), date.fromisoformat(b)
    sign = 1 if d1 >= d0 else -1
    lo, hi = min(d0, d1), max(d0, d1)
    return sign * sum((lo + timedelta(i)).weekday() < 5 for i in range(1, (hi - lo).days + 1))


def next_earnings(dates: set, today: str) -> str | None:
    """The first earnings date on or after today, or None if none is known."""
    ahead = sorted(d for d in dates if d >= today)
    return ahead[0] if ahead else None


def pre_earnings_runup(bars, book, today, earnings):
    """Stocks above their 50-day average with earnings exactly 7 trading days away: buy 20%
    each, max 4 (strongest vs the 50-day average first). Always sell the trading day before
    earnings, never holding through the report; sell at once if the date is missing or has
    moved since entry. bars/earnings as in earnings_drift."""
    out = {}
    for s, held in book["positions"].items():
        nxt = next_earnings(earnings(s), today)
        moved = nxt is None or trading_days_between(held["opened"], nxt) != 7
        out[s] = 0 if moved or trading_days_between(today, nxt) <= 1 else KEEP
    slots = 4 - len(book["positions"])
    picks = []
    for s, b in bars.items():
        c = b["close"]
        if s in book["positions"] or len(c) < 50 or b["date"][-1] != today or c[-1] <= sma(c, 50):
            continue                                  # price filter first: fewer earnings lookups
        nxt = next_earnings(earnings(s), today)
        if nxt and trading_days_between(today, nxt) == 7:
            picks.append((c[-1] / sma(c, 50), s))
    for _, s in sorted(picks, reverse=True)[:max(slots, 0)]:
        out[s] = 0.2
    return out


STRATEGIES = {
    "etf_mean_reversion": {"fn": etf_mean_reversion, "monthly": False,
                           "about": "daily: buy SPY/QQQ dips in an uptrend, sell on the bounce"},
    "crypto_trend":       {"fn": crypto_trend, "monthly": False,
                           "about": "daily: hold BTC/ETH while the 20-day avg is above the 50-day"},
    "dual_momentum":      {"fn": dual_momentum, "monthly": True,
                           "about": "monthly: strongest of US stocks / world stocks / gold, else T-bills"},
    "sector_momentum":    {"fn": sector_momentum, "monthly": True,
                           "about": "monthly: top 3 sectors by 6-month return, in uptrends only"},
    "trend_following":    {"fn": trend_following, "monthly": False,
                           "about": "daily: SPY/QQQ/world/gold/bonds, each held only above its 200-day avg"},
    "earnings_drift":     {"fn": earnings_drift, "monthly": False, "bars": True,
                           "about": "daily: buy stocks the day after a >4% earnings gap up that holds; "
                                    "10-day hold or 2×ATR trailing stop"},
    "pre_earnings_runup": {"fn": pre_earnings_runup, "monthly": False, "bars": True,
                           "about": "daily: buy uptrending stocks 7 trading days before earnings; "
                                    "sell the day before the report"},
}
TICKERS = sorted({"SPY", "QQQ", "EFA", "GLD", "BIL", "AGG", "TLT", "BTC-USD", "ETH-USD", *SECTORS})


# ── engine ───────────────────────────────────────────────────────────────────

def _new_book(today: str) -> dict:
    return {"cash": START_CASH, "positions": {}, "closed": [], "equity": [],
            "started": today, "month": None}


def _value(book, prices):
    return book["cash"] + sum(p["qty"] * prices.get(s, p["last"]) for s, p in book["positions"].items())


def _sell(book, s, price, today, reason="signal"):
    p = book["positions"].pop(s)
    book["cash"] += p["qty"] * price
    book["closed"].append({"symbol": s, "entry": round(p["entry"], 4), "exit": round(price, 4),
                           "opened": p["opened"], "closed": today, "reason": reason,
                           "pnl": round((price - p["entry"]) * p["qty"], 2)})


def rebalance(book: dict, targets: dict, prices: dict, today: str, monthly_day: bool) -> None:
    """Trade the book to the target weights. Trades only when the set of holdings changes
    (or on a monthly strategy's rebalance day), so a steady signal doesn't churn."""
    for p in book["positions"].values():
        p["days"] = p.get("days", 0) + 1
    for s, p in book["positions"].items():
        if s in prices:
            p["last"] = prices[s]
    equity = _value(book, prices)
    want = {s: w for s, w in targets.items() if w == KEEP or (w and w > 0)}
    held = set(book["positions"])
    changed = set(want) != held
    if not (changed or monthly_day):
        return
    for s in list(held):
        if s not in want and s in prices:
            _sell(book, s, prices[s], today)
    for s, w in want.items():
        if w == KEEP or s not in prices:
            continue
        price, goal = prices[s], w * equity
        have = book["positions"].get(s)
        cur = have["qty"] * price if have else 0.0
        if have and not monthly_day and abs(goal - cur) <= 0.25 * goal:
            continue                                  # close enough: don't churn
        if have and goal < cur:                       # trim
            qty = (cur - goal) / price
            have["qty"] -= qty
            book["cash"] += qty * price
        elif goal > cur:                              # open or add
            spend = min(goal - cur, book["cash"])
            if spend < 1.0:
                continue
            book["cash"] -= spend
            if have:
                have["entry"] = (have["entry"] * have["qty"] + spend) / (have["qty"] + spend / price)
                have["qty"] += spend / price
            else:
                book["positions"][s] = {"qty": spend / price, "entry": price, "last": price,
                                        "opened": today, "days": 0}


def run(closes_by_symbol: dict, today: str, books: dict, bars: dict | None = None,
        earnings=lambda s: set()) -> dict:
    """One daily step for every strategy. closes_by_symbol: {symbol: [floats, oldest first]};
    bars (stock OHLC, see earnings_drift) and earnings go to strategies marked "bars"."""
    bars = bars or {}
    prices = {s: b["close"][-1] for s, b in bars.items() if b["close"]}
    prices.update({s: c[-1] for s, c in closes_by_symbol.items() if c})
    month = today[:7]
    for name, spec in STRATEGIES.items():
        book = books.setdefault(name, _new_book(today))
        if book.get("last_run") == today:
            continue
        monthly_day = spec["monthly"] and book.get("month") != month
        if spec["monthly"] and not monthly_day:
            targets = {s: KEEP for s in book["positions"]}
        else:
            try:
                targets = spec["fn"](bars, book, today, earnings) if spec.get("bars") \
                    else spec["fn"](closes_by_symbol, book, today)
            except Exception as e:
                log.warning(f"Daily shadow {name}: signal failed: {e}")
                continue
        if monthly_day and not targets:               # no data yet: try again tomorrow
            book["last_run"] = today
            continue
        rebalance(book, targets, prices, today, monthly_day)
        if monthly_day:
            book["month"] = month
        book["last_run"] = today
        book["equity"].append([today, round(_value(book, prices), 2)])
        book["equity"] = book["equity"][-1500:]
    return books


# ── live entry point ─────────────────────────────────────────────────────────

def _fetch_closes() -> dict:
    import yfinance as yf
    df = yf.download(TICKERS, period="2y", interval="1d", auto_adjust=True, progress=False,
                     threads=False)["Close"]
    return {s: [float(x) for x in df[s].dropna().tolist()] for s in TICKERS if s in df}


def _fetch_bars() -> dict:
    """Daily OHLC for the screener's stock universe (one download)."""
    import yfinance as yf
    import screener
    syms = list(screener.STOCK_UNIVERSE)
    df = yf.download(syms, period="3mo", interval="1d", auto_adjust=True, progress=False, threads=False)
    out = {}
    for s in syms:
        try:
            d = df.xs(s, axis=1, level=1)[["Open", "High", "Low", "Close"]].dropna()
        except KeyError:
            continue
        out[s] = {"date": [t.strftime("%Y-%m-%d") for t in d.index],
                  "open": d["Open"].tolist(), "high": d["High"].tolist(),
                  "low": d["Low"].tolist(), "close": d["Close"].tolist()}
    return out


def _earnings_dates(symbol: str) -> set:
    """Recent and upcoming earnings report dates (ET), or an empty set if unknown."""
    try:
        import yfinance as yf
        df = yf.Ticker(symbol).get_earnings_dates(limit=8)
        return {t.strftime("%Y-%m-%d") for t in df.index} if df is not None else set()
    except Exception as e:
        log.warning(f"Daily shadow: earnings dates for {symbol} failed: {e}")
        return set()


EARNINGS_MAX_AGE = 7       # days a far-off earnings date stays cached
EARNINGS_SOON = 14         # calendar days: dates this close are looked up again every day
EARNINGS_LOOKUPS = 30      # network lookups per run at most, so the 15:30 cycle stays quick


def cached_earnings(lookup, today: str, cache: dict, max_lookups: int = EARNINGS_LOOKUPS):
    """Wrap an earnings lookup with a cache ({symbol: {"fetched", "dates"}}, updated in place).
    A symbol is looked up again once a day if its next date is within EARNINGS_SOON days (or
    unknown), else once every EARNINGS_MAX_AGE days; past max_lookups the cache answers."""
    from datetime import date
    days = lambda a, b: (date.fromisoformat(b) - date.fromisoformat(a)).days
    used = [0]

    def get(symbol):
        hit = cache.get(symbol)
        if hit:
            nxt = next_earnings(set(hit["dates"]), today)
            fresh = hit["fetched"] == today or \
                (days(hit["fetched"], today) < EARNINGS_MAX_AGE and nxt and days(today, nxt) > EARNINGS_SOON)
            if fresh or used[0] >= max_lookups:
                return set(hit["dates"])
        elif used[0] >= max_lookups:
            return set()
        used[0] += 1
        dates = set(lookup(symbol))
        cache[symbol] = {"fetched": today, "dates": sorted(dates)}
        return dates
    return get


def maybe_run(now: datetime | None = None, fetch=_fetch_closes, path: str | None = None,
              fetch_bars=_fetch_bars, earnings=_earnings_dates) -> bool:
    """Run the daily step once per trading day, at the first call at or after 15:30 ET."""
    now = now or datetime.now(timezone.utc)
    et = now.astimezone(timeofday.ET)
    if et.weekday() >= 5 or et.strftime("%H:%M") < RUN_AFTER:
        return False
    today = et.strftime("%Y-%m-%d")
    books = load(path)
    if books and all(b.get("last_run") == today for b in books.values()) and \
            set(books) >= set(STRATEGIES):
        return False
    closes = fetch()
    if not closes:
        log.warning("Daily shadow books: no price data, skipped")
        return False
    try:
        bars = fetch_bars()
    except Exception as e:
        log.warning(f"Daily shadow books: stock bars failed ({e}); stock books hold")
        bars = {}
    cache_path = os.path.join(os.path.dirname(path or STATE_FILE), "earnings_cache.json")
    cache = load(cache_path)
    run(closes, today, books, bars, cached_earnings(earnings, today, cache))
    state_io.write_json(cache_path, cache, indent=1)
    state_io.write_json(path or STATE_FILE, books, indent=1)
    log.info(f"  🧪 Daily shadow books updated ({len(STRATEGIES)} strategies)")
    return True


def load(path: str | None = None) -> dict:
    import json
    try:
        with open(path or STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def summary(books: dict | None = None) -> dict:
    """Same fields as shadow.summary(), for the status script and dashboard."""
    books = books if books is not None else load()
    out = {}
    for name, b in books.items():
        closed = b.get("closed", [])
        curve = [v for _, v in b.get("equity", [])]
        value = curve[-1] if curve else START_CASH
        out[name] = {"about": STRATEGIES.get(name, {}).get("about", ""), "since": b.get("started", ""),
                     "value": value, "return_pct": round((value / START_CASH - 1) * 100, 2),
                     "max_drawdown_pct": max_drawdown_pct(curve),
                     "open": len(b.get("positions", {})), "closed": len(closed),
                     "win_pct": round(100 * sum(t["pnl"] > 0 for t in closed) / len(closed)) if closed else None,
                     "realized_pnl": round(sum(t["pnl"] for t in closed), 2), "exits": {}}
    return out


def max_drawdown_pct(curve: list) -> float:
    """Largest drop from a peak, in % (0 if none)."""
    peak, worst = None, 0.0
    for v in curve:
        peak = v if peak is None or v > peak else peak
        worst = min(worst, v / peak - 1)
    return round(worst * 100, 2)
