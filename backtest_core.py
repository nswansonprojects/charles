"""
backtest_core.py — the redesigned strategy (allocator.py) on daily history vs SPY.

Signals use closes up to the day before; orders fill at the next day's open (live: 10:00 ET from
the previous close) with SLIPPAGE_BPS per side. Prices are dividend-adjusted (yfinance auto_adjust),
so SPY buy-and-hold includes dividends. BIL stands in for SGOV before SGOV existed (2020).

Caveat: the stock universe is today's list (survivor bias), so the momentum picks look better here
than they will live. The sector-ETF variant has no such bias and is the honest check.

Usage: ops/heavy-run python backtest_core.py        → docs/backtest.json + a summary on stdout
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime

import allocator as al

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "docs", "backtest.json")
START, WARMUP_FROM = "2007-07-02", "2005-06-01"
SLIPPAGE_BPS = 5.0
SECTORS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB"]   # all trade since 1998


def simulate(dates: list[str], opens: dict[str, list], closes: dict[str, list], settings: dict,
             start_cash: float = 1000.0, start: str = START, universe: list[str] | None = None) -> dict:
    """Run the live decision rules (rebalancer.decide + allocator.orders) day by day.
    opens/closes: {symbol: [price or None per date]}. Returns {"dates", "values", "trades",
    "switches", "picks_log"} from `start` on."""
    import rebalancer
    s = settings
    core, safe = s["CORE"], s["SAFE"]
    universe = universe if universe is not None else [k for k in closes if k not in (core, safe)]
    syms = [k for k in {core, safe, *universe} if k in closes]
    # each symbol's closes without gaps, and how many of them exist up to each day
    clean = {k: [c for c in closes[k] if c is not None] for k in syms}
    upto = {}
    for k in syms:
        n, counts = 0, []
        for c in closes[k]:
            n += c is not None
            counts.append(n)
        upto[k] = counts
    cash, shares, state = start_cash, {}, {}
    out_d, out_v, trades, switches, picks_log = [], [], 0, 0, []
    i0 = next(i for i, d in enumerate(dates) if d >= start)
    slip = SLIPPAGE_BPS / 1e4
    last_close = {}

    for i in range(i0, len(dates)):
        day, prev = dates[i], i - 1
        reb = al.is_rebalance_day(day, state.get("last_rebalance"))
        need = syms if (reb or not state or state.get("pending")) else [core]
        hist = {k: clean[k][: upto[k][prev]] for k in need}
        d = rebalancer.decide(day, hist, state, s, universe=universe, rank_always=False)
        if d["switched"]:
            switches += 1
        if d["full"]:
            picks_log.append([day, d["mode"], list(d["picks"])])
        state = {"mode": d["mode"], "picks": d["picks"],
                 "last_rebalance": day if d["full"] else state.get("last_rebalance")}
        px = {k: opens[k][i] for k in set(shares) | set(d["targets"]) if opens.get(k) and opens[k][i]}
        held = {k: n * px[k] for k, n in shares.items() if k in px}
        equity = cash + sum(held.values())
        tgt = {k: w for k, w in d["targets"].items() if k in px}       # nothing to buy without a price
        for o in al.orders(tgt, held, equity, d["full"], s):
            p = px.get(o["symbol"])
            if not p:
                continue
            if o["side"] == "sell":
                n = shares.get(o["symbol"], 0) if o["all"] else min(shares.get(o["symbol"], 0), o["amount"] / p)
                shares[o["symbol"]] = shares.get(o["symbol"], 0) - n
                cash += n * p * (1 - slip)
            else:
                amt = min(o["amount"], cash)
                if amt < s["MIN_ORDER"]:
                    continue
                shares[o["symbol"]] = shares.get(o["symbol"], 0) + amt / (p * (1 + slip))
                cash -= amt
            trades += 1
        shares = {k: n for k, n in shares.items() if n > 1e-9}
        for k in shares:                       # a missing close keeps the last known one
            c = closes[k][i]
            if c is not None:
                last_close[k] = c
        val = cash + sum(n * last_close.get(k, 0) for k, n in shares.items())
        out_d.append(day)
        out_v.append(round(val, 2))
    return {"dates": out_d, "values": out_v, "trades": trades, "switches": switches, "picks_log": picks_log}


def buy_hold(dates, closes_col, start=START, start_cash=1000.0) -> dict:
    i0 = next(i for i, d in enumerate(dates) if d >= start)
    base = closes_col[i0 - 1]
    return {"dates": dates[i0:], "values": [round(start_cash * c / base, 2) for c in closes_col[i0:]]}


def stats(dates: list[str], values: list[float], start_cash: float = 1000.0) -> dict:
    """CAGR, worst drop, volatility, Sharpe (vs 0%), best/worst calendar year."""
    if len(values) < 2:
        return {}
    years = (datetime.fromisoformat(dates[-1]) - datetime.fromisoformat(dates[0])).days / 365.25
    vals = [start_cash] + values
    rets = [vals[k] / vals[k - 1] - 1 for k in range(1, len(vals))]
    mean = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1))
    peak, worst = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    by_year, last = {}, start_cash
    for d, v in zip(dates, values):
        by_year.setdefault(d[:4], [last, v])
        by_year[d[:4]][1] = v
        last_year = d[:4]
    yearly, prev_end = {}, start_cash
    for y in sorted(by_year):
        yearly[y] = round((by_year[y][1] / prev_end - 1) * 100, 1)
        prev_end = by_year[y][1]
    return {"final": round(values[-1], 2), "total_pct": round((values[-1] / start_cash - 1) * 100, 1),
            "cagr_pct": round(((values[-1] / start_cash) ** (1 / years) - 1) * 100, 2) if years > 0 else None,
            "max_drawdown_pct": round(worst * 100, 1), "vol_pct": round(sd * math.sqrt(252) * 100, 1),
            "sharpe": round(mean / sd * math.sqrt(252), 2) if sd else None,
            "best_year": max(yearly.items(), key=lambda x: x[1]), "worst_year": min(yearly.items(), key=lambda x: x[1]),
            "yearly": yearly}


def weekly(dates, values):
    """Every 5th point plus the last, for the dashboard chart."""
    pts = [[d, v] for k, (d, v) in enumerate(zip(dates, values)) if k % 5 == 0]
    if pts[-1][0] != dates[-1]:
        pts.append([dates[-1], values[-1]])
    return pts


def _download(symbols):
    import yfinance as yf
    df = yf.download(symbols, start=WARMUP_FROM, auto_adjust=True, progress=False, threads=True, group_by="column")
    dates = [str(d.date()) for d in df.index]
    def col(field, sym):
        try:
            return [None if v != v else round(float(v), 4) for v in df[field][sym].tolist()]
        except KeyError:
            return None
    opens = {s: col("Open", s) for s in symbols}
    closes = {s: col("Close", s) for s in symbols}
    return dates, {k: v for k, v in opens.items() if v}, {k: v for k, v in closes.items() if v}


def main():
    s = dict(al.DEFAULTS, SAFE="BIL")      # SGOV only exists from 2020
    syms = sorted(set(al.UNIVERSE) | set(SECTORS) | {"SPY", "BIL", "SSO", "UPRO"})
    dates, opens, closes = _download(syms)
    print(f"{len(dates)} days {dates[0]} → {dates[-1]}, {len(closes)} symbols", file=sys.stderr)
    stocks = [k for k in al.UNIVERSE if k in closes]
    runs = {
        "charles": simulate(dates, opens, closes, s, universe=stocks),
        "charles_sectors": simulate(dates, opens, closes, s, universe=SECTORS),
        "spy_trend_only": simulate(dates, opens, closes, dict(s, CORE_PCT=0.99, PICKS=0), universe=[]),
        "charles_no_switch": simulate(dates, opens, closes, dict(s, SAFETY_SWITCH=False), universe=stocks),
        "charles_steady": simulate(dates, opens, closes, dict(s, AGGRESSIVE=False), universe=stocks),
        "spy": buy_hold(dates, closes["SPY"]),
    }
    about = {
        "charles": "The plan: 80% SPY (a 2x S&P fund while calm and rising) + 4 momentum stocks at 5%, all to T-bills when SPY ends a month below its 200-day average",
        "charles_steady": "The plan without aggressive mode (always plain SPY as the core)",
        "charles_sectors": "Same plan, but the momentum picks are sector funds (no survivor bias)",
        "spy_trend_only": "Only the safety switch: 99% SPY or T-bills, checked monthly",
        "charles_no_switch": "The plan without the safety switch",
        "spy": "Buy SPY and hold it",
    }
    half = "2017-01-01"
    result = {"generated": datetime.now().isoformat(timespec="minutes"), "start": START, "end": dates[-1],
              "slippage_bps": SLIPPAGE_BPS, "start_cash": 1000.0, "runs": {}}
    for name, r in runs.items():
        d, v = r["dates"], r["values"]
        k = next(j for j, x in enumerate(d) if x >= half)
        result["runs"][name] = {
            "about": about[name], "stats": stats(d, v),
            "first_half": stats(d[:k], v[:k]), "second_half": stats(d[k:], [x * 1000 / v[k - 1] for x in v[k:]]),
            "trades": r.get("trades"), "switches": r.get("switches"), "curve": weekly(d, v),
        }
        if name == "charles":
            result["runs"][name]["recent_picks"] = r["picks_log"][-12:]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(result, f, separators=(",", ":"))
    for name, r in result["runs"].items():
        st, a, b = r["stats"], r["first_half"], r["second_half"]
        print(f"{name:18s} CAGR {st['cagr_pct']:6.2f}%  maxDD {st['max_drawdown_pct']:6.1f}%  Sharpe {st['sharpe']}"
              f"  vol {st['vol_pct']}%  worst yr {st['worst_year']}  | 07-16 {a['cagr_pct']}%/{a['max_drawdown_pct']}%"
              f"  17-now {b['cagr_pct']}%/{b['max_drawdown_pct']}%  trades {r['trades']} switches {r['switches']}")


if __name__ == "__main__":
    main()
