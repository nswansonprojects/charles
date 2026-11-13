"""
fetch_test.py — how a Fetch rule would have done on past minutes, after fees. Practice money only; it never places an order.

Run it before switching Fetch to a new rule or new settings (yours, or ones your Claude Code wrote):

    python fetch_test.py                                   # the coin Fetch watches now, its settings, the last 30 days
    python fetch_test.py --market stock --days 20          # the stock (needs your Alpaca keys in .env: read-only market data)
    python fetch_test.py --rule mine                       # your own rule in fetch_my_rule.py
    python fetch_test.py TARGET=1.0 STOP=0.5 HOLD=60       # try other settings without saving them
    python fetch_test.py --product ETH-USD --json          # any coin on the list; --json for the numbers only

It feeds each past minute to the same code the live Fetch runs (fetch_plan.step), so the two can't disagree. It compares
the rule with simply holding, and with buying at random minutes with the same exits ("luck"): a rule that doesn't beat luck
after fees has no edge. Each half of the span is shown too: a rule that only worked in one half probably got lucky.

Limits: one-minute bars, not every trade; fills at the next minute's open plus a little slip; no queue or partial fills; a
stock uses Alpaca's IEX bars (a sample of US trades). A good past result is not a promise of anything.
Prices are kept in ~/.cache/charles-fetch-test so a second run is quick.
"""
import argparse
import json
import os
import random
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import fetch_control
import fetch_engine as fe
import fetch_plan as rt

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(os.path.expanduser("~"), ".cache", "charles-fetch-test")
LUCK_RUNS = 200


# ------------------------------------------------------------------ prices

def _get(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": "charles-fetch-test", **(headers or {})})
    for wait in (1, 2, 4, 8, 0):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code != 429 or not wait:
                raise
            time.sleep(wait)


def _cached(name: str, day: str, fetch):
    path = os.path.join(CACHE, name, f"{day}.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        pass
    rows = fetch()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(rows, f)
    return rows


def coin_bars(product: str, days: int, now: datetime | None = None) -> list:
    """One-minute bars of a coin from Coinbase's public API (no key), for finished UTC days. A minute with no trade has no bar."""
    now = now or datetime.now(timezone.utc)
    out = []
    for k in range(days, 0, -1):
        d = (now - timedelta(days=k)).date()

        def fetch(d=d):
            rows, t = [], datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
            for _ in range(5):                             # 300 bars per call: five calls cover a day
                end = t + timedelta(minutes=300)
                q = urllib.parse.urlencode({"granularity": 60, "start": t.isoformat(), "end": (end - timedelta(minutes=1)).isoformat()})
                rows += _get(f"{fe.REST}/products/{product}/candles?{q}") or []
                t = end
                time.sleep(0.15)                           # well under the public API's rate limit
            lo = datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()
            return sorted({r[0]: r for r in rows if lo <= r[0] < lo + 86400}.values())
        for t, lo, hi, op, cl, vol in _cached(product, d.isoformat(), fetch):
            out.append(rt.Bar(float(t), op, hi, lo, cl, vol))
        print(f"\r  prices: {days - k + 1} of {days} days", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)
    return out


def _keys() -> dict:
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
    except ImportError:
        pass
    k, s = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not (k and s):
        sys.exit("A stock needs your Alpaca keys (ALPACA_API_KEY, ALPACA_SECRET_KEY in .env; add them with charles-keys).")
    return {"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": s}


def stock_bars(symbol: str, days: int, now: datetime | None = None) -> tuple[list, list]:
    """(bars, sessions) of a stock's regular hours for the last `days` finished sessions, from Alpaca's IEX feed (read-only)."""
    now = now or datetime.now(timezone.utc)
    h = _keys()
    start, end = (now - timedelta(days=days * 7 // 5 + 7)).date(), (now - timedelta(days=1)).date()
    try:
        cal = _get(f"https://paper-api.alpaca.markets/v2/calendar?start={start}&end={end}", h)
    except Exception:                                      # noqa: BLE001  (live keys: the live address has the same calendar)
        cal = _get(f"https://api.alpaca.markets/v2/calendar?start={start}&end={end}", h)
    sessions = [rt.us_session(c["date"], c["open"], c["close"]) for c in cal][-days:]
    bars = []
    for i, s in enumerate(sessions):
        def fetch(s=s):
            rows, token = [], None
            while True:
                q = {"timeframe": "1Min", "start": datetime.fromtimestamp(s.open, timezone.utc).isoformat().replace("+00:00", "Z"),
                     "end": datetime.fromtimestamp(s.close, timezone.utc).isoformat().replace("+00:00", "Z"),
                     "limit": 10000, "feed": "iex", "adjustment": "split"}
                if token:
                    q["page_token"] = token
                got = _get(f"{fe.STOCK_DATA}/stocks/{symbol}/bars?{urllib.parse.urlencode(q)}", h)
                rows += [[fe.rfc_ts(b["t"]), b["o"], b["h"], b["l"], b["c"], b["v"]] for b in got.get("bars") or []]
                token = got.get("next_page_token")
                if not token:
                    return rows
        bars += [rt.Bar(*r) for r in _cached(symbol, s.day, fetch) if s.open <= r[0] < s.close]
        print(f"\r  prices: {i + 1} of {len(sessions)} sessions", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)
    return bars, sessions


# ------------------------------------------------------------------ the replay

def replay(bars: list, s: dict, sessions: list | None = None) -> dict:
    """The rule over the bars, with the live engine's own step(). Returns the result in percent and each trade's result."""
    st = rt.new_state(1.0)
    by_day = {x.day: x for x in sessions or []}
    tz = None
    if sessions:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/New_York")
    results, curve = [], []
    for b in bars:
        sess = by_day.get(datetime.fromtimestamp(b.ts, tz).date().isoformat()) if tz else None
        if sessions and sess is None:
            continue
        for e in rt.step(st, b, s, sess):
            if e.kind == "sell":
                results.append(e.result_pct)
        curve.append((b.ts, rt.value(st, b.close)))
    if st.pos and bars:                                    # still holding at the end: valued at the last close, after the fee
        curve.append((bars[-1].ts, st.cash + st.pos["qty"] * bars[-1].close * (1 - s["FEE"])))
    return {"return_pct": (curve[-1][1] - 1) * 100 if curve else 0.0, "results": results, "curve": curve}


def luck(bars: list, s: dict, trades: int, sessions: list | None = None, runs: int = LUCK_RUNS, seed: int = 7) -> float | None:
    """The mean result of buying at random minutes with the same exits (target, stop, hold; a stock out by its close) and fees,
    as many trades as the rule made. A yardstick for luck, not a second bot (trades may overlap)."""
    if not trades or len(bars) < 10:
        return None
    rng = random.Random(seed)
    close_of = {}
    if sessions:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/New_York")
        by_day = {x.day: x.close for x in sessions}
        close_of = {i: by_day.get(datetime.fromtimestamp(b.ts, tz).date().isoformat()) for i, b in enumerate(bars)}
    keep = (1 - s["FEE"]) ** 2

    def one(i):
        entry = bars[i].open * (1 + s["SLIP"])
        stop, target, end_ts = entry * (1 - s["STOP"] / 100), entry * (1 + s["TARGET"] / 100), bars[i].ts + s["HOLD"] * 60
        cut = close_of.get(i)
        for b in bars[i:]:
            if b.ts >= end_ts or (cut and b.ts + 60 >= cut):
                return b.close * (1 - s["SLIP"]) * keep / entry
            hit = rt.bar_exit(stop, target, b)
            if hit:
                return hit[1] * (1 - s["SLIP"]) * keep / entry
        return bars[-1].close * keep / entry
    pool = [one(rng.randrange(0, len(bars) - 1)) for _ in range(min(3000, max(trades * 20, 300)))]
    total = 0.0
    for _ in range(runs):
        v = 1.0
        for _ in range(trades):
            v *= rng.choice(pool)
        total += v
    return (total / runs - 1) * 100


def report(product: str, bars: list, s: dict, sessions: list | None = None) -> dict:
    r = replay(bars, s, sessions)
    res, curve = r["results"], r["curve"]
    days = len(sessions) if sessions else max(1.0, (bars[-1].ts - bars[0].ts) / 86400) if bars else 0
    mid = len(curve) // 2
    half = lambda a, b: (b / a - 1) * 100 if a else 0.0                       # noqa: E731
    out = {
        "product": product, "rule": s["RULE"], "days": round(days, 1), "bars": len(bars),
        "settings": {k: s[k] for k in ("RULE", "TARGET", "STOP", "HOLD", "FEE", "MAX_TRADES")},
        "return_pct": round(r["return_pct"], 2), "trades": len(res), "trades_per_day": round(len(res) / days, 2) if days else 0,
        "won": sum(1 for x in res if x > 0), "avg_trade_pct": round(sum(res) / len(res), 3) if res else None,
        "hold_pct": round((bars[-1].close / bars[0].open - 1) * 100, 2) if bars else None,
        "first_half_pct": round(half(1.0, curve[mid][1]), 2) if curve else None,
        "second_half_pct": round(half(curve[mid][1], curve[-1][1]), 2) if curve else None,
    }
    lk = luck(bars, s, len(res), sessions)
    out["luck_pct"] = None if lk is None else round(lk, 2)
    return out


def verdict(r: dict) -> list:
    lines = []
    if not r["trades"]:
        return ["It made no trades in this span: the rule never fired. Try a longer span or a looser setting."]
    beat_luck = r["luck_pct"] is not None and r["return_pct"] > r["luck_pct"]
    beat_hold = r["hold_pct"] is not None and r["return_pct"] > r["hold_pct"]
    both = (r["first_half_pct"] or 0) > 0 and (r["second_half_pct"] or 0) > 0
    lines.append(("Beat " if beat_luck else "Did not beat ") + "random entries with the same exits (luck).")
    lines.append(("Beat " if beat_hold else "Did not beat ") + "simply holding.")
    lines.append("Made money in both halves." if both else "Did not make money in both halves.")
    if beat_luck and both:
        lines.append("Worth watching on practice money. One good span is not proof: try another span or ticker too.")
    else:
        lines.append("No sign of an edge here. That's the usual result for minute rules once fees are counted.")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description="How a Fetch rule would have done on past minutes, after fees. Practice only.")
    ap.add_argument("--market", choices=("coin", "stock"), default="coin")
    ap.add_argument("--product", help="a ticker from Fetch's list (default: the one it watches now)")
    ap.add_argument("--days", type=int, default=30, help="how many past days (a stock: sessions); 1 to 180")
    ap.add_argument("--rule", choices=rt.RULES)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("set", nargs="*", help="KEY=VALUE settings to try, e.g. TARGET=1.0 STOP=0.5")
    a = ap.parse_args(argv)
    product = a.product or fetch_control.ticker(a.market)
    if not fetch_control.allowed(a.market, product):
        sys.exit(f"{product} isn't on Fetch's {a.market} list: {', '.join(p for p, _ in fetch_control.CHOICES[a.market])}")
    over = {}
    for kv in a.set:
        k, _, v = kv.partition("=")
        try:
            over[k.upper()] = float(v) if "." in v else int(v)
        except ValueError:
            sys.exit(f"{kv}: a setting needs a number, like TARGET=1.0")
    if a.rule:
        over["RULE"] = a.rule
    path = fetch_control.settings_path(a.market)
    base = {k: v for k, v in (fetch_control._read(path) or {}).items() if k != "PRODUCT"}
    try:
        rt.check({**base, **over})
    except ValueError as e:
        sys.exit(f"Settings: {e}")
    s = rt.settings({**base, **over})
    if s["RULE"] == "mine" and rt.my_rule()[0] is None:
        sys.exit(f"RULE mine: {rt.my_rule()[1]}. Copy fetch_my_rule.example.py to fetch_my_rule.py to start.")
    days = max(1, min(180, a.days))
    if a.market == "stock":
        bars, sessions = stock_bars(product, days)
    else:
        bars, sessions = coin_bars(product, days), None
    if not bars:
        sys.exit("No prices came back. Try again in a minute.")
    r = report(product, bars, s, sessions)
    if a.json:
        print(json.dumps(r, indent=1))
        return 0
    f = lambda v: "n/a" if v is None else f"{v:+.2f}%"                          # noqa: E731
    st = r["settings"]
    print(f"{product}, rule {st['RULE']}, last {r['days']:g} {'sessions' if sessions else 'days'} ({r['bars']:,} one-minute bars)")
    print(f"  Sells at +{st['TARGET']}% or -{st['STOP']}% or after {st['HOLD']} minutes; fee {st['FEE'] * 100:.2f}% each way.")
    print(f"  Result after fees: {f(r['return_pct'])}  ({r['trades']} trade{'' if r['trades'] == 1 else 's'}, {r['trades_per_day']} a day, {r['won']} won)")
    print(f"  Simply holding:    {f(r['hold_pct'])}")
    print(f"  Random entries:    {f(r['luck_pct'])}  (same exits and fees, same number of trades; the luck yardstick)")
    print(f"  First half {f(r['first_half_pct'])}, second half {f(r['second_half_pct'])}")
    for line in verdict(r):
        print("  " + line)
    print("  Past minutes only; practice fills. Not a promise of any result.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
