"""
backtest_orb.py — the ORB sleeve (orb.py) on 1-minute history, with orb.py's own functions.

No second copy of the strategy: every range, filter, breakout, stop/target, trail, flatten,
size and daily-limit decision here is an orb.py call. This file only supplies the bars and
the fills:
  * decisions on a completed bar, fills at the NEXT bar's open (+ slippage);
  * the resting stop fills when a bar touches it (at the open if it gapped through), and it
    is checked before that bar's close is used, so stop-and-target in one bar = stop;
  * costs: ORB_SLIPPAGE_BPS per side and ORB_COMMISSION_PER_SHARE (also run at 2× slippage);
  * ATR(14) and the regime (regime.classify, same rule as live) from completed days only.
Data: Alpaca SIP 1-min bars 2016→ (free for history; live uses the free IEX feed), cached in
backtest_cache/orb/. Days are split 60% in-sample (tuning) / 40% out-of-sample (looked at once).
The LLM confirmation is not backtested: the model knows how those days ended, and replaying
thousands of calls would also cost tokens.

Run:  python backtest_orb.py [--download]      → reports/orb/
Nothing is written to data/ and no orders or alerts are sent.
"""
import argparse
import csv
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config            # noqa: E402
import orb               # noqa: E402
import regime            # noqa: E402
import risk_manager as rm  # noqa: E402
from timeofday import ET  # noqa: E402

CACHE = os.path.join(HERE, "backtest_cache", "orb")
OUT = os.path.join(HERE, "reports", "orb")
SYMBOLS = ["QQQ", "SPY"]
FIRST_YEAR = 2016
CAPITAL = 1000.0


# ── Data ──────────────────────────────────────────────────────────────────────

def download(symbols=SYMBOLS, end: date | None = None):
    import credential_manager as sec
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    client = StockHistoricalDataClient(sec.alpaca_key(), sec.alpaca_secret())
    end = end or (datetime.now(ET).date() - timedelta(days=1))
    os.makedirs(CACHE, exist_ok=True)
    for sym in symbols:
        for y in range(FIRST_YEAR, end.year + 1):
            path = os.path.join(CACHE, f"{sym}_{y}.pkl")
            if os.path.exists(path) and y < end.year:
                continue
            stop = min(datetime(y + 1, 1, 1, tzinfo=timezone.utc),
                       datetime.combine(end + timedelta(days=1), datetime.min.time(), timezone.utc))
            df = client.get_stock_bars(StockBarsRequest(
                symbol_or_symbols=sym, timeframe=TimeFrame.Minute, feed="sip",
                start=datetime(y, 1, 1, tzinfo=timezone.utc), end=stop)).df
            df = df.droplevel(0).tz_convert(ET).between_time("09:30", "15:59")
            df[["open", "high", "low", "close", "volume"]].to_pickle(path)
            print(f"  {sym} {y}: {len(df)} bars", flush=True)


def load(sym: str) -> pd.DataFrame:
    parts = [pd.read_pickle(os.path.join(CACHE, f)) for f in sorted(os.listdir(CACHE))
             if f.startswith(sym + "_") and f.endswith(".pkl")]
    df = pd.concat(parts).sort_index()
    return df[~df.index.duplicated()]


class Market:
    """Per-day 1-min bars plus as-of daily ATR and regime for each symbol."""

    def __init__(self, symbols):
        self.symbols = symbols
        self.days_bars = {}      # sym -> {day: (t list, o, h, l, c arrays)}
        self.daily = {}
        for sym in symbols + (["SPY"] if "SPY" not in symbols else []):
            df = load(sym)
            d = df.groupby(df.index.date).agg(o=("open", "first"), h=("high", "max"), l=("low", "min"),
                                               c=("close", "last"), v=("volume", "sum"), n=("close", "size"))
            self.daily[sym] = d[d["n"] >= 180]          # drop broken/partial-data days (half days have 210)
            if sym in symbols:
                by = {}
                for day, g in df.groupby(df.index.date):
                    if day in self.daily[sym].index:
                        by[day] = (list(g.index.to_pydatetime()), g["open"].tolist(), g["high"].tolist(),
                                   g["low"].tolist(), g["close"].tolist())
                self.days_bars[sym] = by
        self.days = sorted(set.intersection(*(set(self.days_bars[s]) for s in symbols)))
        self.atr, self.regime = {}, {}
        for sym in symbols:
            d = self.daily[sym]
            idx = {day: i for i, day in enumerate(d.index)}
            self.atr[sym] = {day: rm.atr_from_ohlc(d["h"].iloc[i - 15:i].tolist(), d["l"].iloc[i - 15:i].tolist(),
                                                    d["c"].iloc[i - 15:i].tolist(), 14) if i >= 15 else 0.0
                             for day, i in idx.items()}
        spy = self.daily["SPY"]
        for i, day in enumerate(spy.index):
            if i >= 30:
                w = spy.iloc[max(0, i - 60):i]           # the 60 days before, like detect_regime's period
                self.regime[day] = regime.classify(w["c"].tolist(), w["h"].tolist(),
                                                   w["l"].tolist(), w["v"].tolist())[0]


# ── Simulation (fills only; decisions are orb.py) ────────────────────────────

def simulate(mkt: Market, days: list, cfg: dict, slip_bps: float = None, commission: float = None,
             capital: float = CAPITAL) -> dict:
    slip = (config.ORB_SLIPPAGE_BPS if slip_bps is None else slip_bps) / 10000
    comm = config.ORB_COMMISSION_PER_SHARE if commission is None else commission
    equity, peak = capital, capital
    trades, curve = [], []
    syms = cfg["symbols"]
    for day in days:
        bars = {s: mkt.days_bars[s].get(day) for s in syms}
        if not all(bars.values()):
            continue
        last_t = max(bars[s][0][-1] for s in syms)
        close = last_t + timedelta(minutes=1) if last_t.hour < 15 else None   # half day
        times = orb.session_times(day, cfg, close=close)
        st = orb.new_day(day, cfg)
        pending = {}
        stream = sorted((t, s, i) for s in syms for i, t in enumerate(bars[s][0]))
        for t, sym, i in stream:
            _, o, h, l, c = bars[sym]
            bar = {"t": t, "o": o[i], "h": h[i], "l": l[i], "c": c[i]}
            s = st["symbols"][sym]
            act = pending.pop(sym, None)
            if act and act["do"] == "enter":
                buy = act["side"] == "long"
                orb.filled_entry(st, sym, act["side"], bar["o"] * (1 + slip if buy else 1 - slip), act["qty"], t, cfg)
            elif act and act["do"] == "exit":
                long_ = s["pos"]["side"] == "long"
                px = bar["o"] * (1 - slip if long_ else 1 + slip)
                trades.append(orb.filled_exit(st, sym, px, t, act["reason"], cfg, costs=2 * comm * s["pos"]["qty"]))
            if s["status"] == "in":
                fp = orb.stop_fill(s["pos"], bar)
                if fp is not None:
                    long_ = s["pos"]["side"] == "long"
                    px = fp * (1 - slip if long_ else 1 + slip)
                    trades.append(orb.filled_exit(st, sym, px, t, "stop", cfg, costs=2 * comm * s["pos"]["qty"]))
            act = orb.on_bar(st, sym, bar, times, cfg, mkt.atr[sym].get(day, 0.0))
            if not act:
                continue
            if act["do"] == "signal":
                why = orb.entry_blockers(st, sym, cfg, regime=mkt.regime.get(day) if cfg["use_regime"] else None)
                est = orb.exit_levels(act["side"], act["price"], s["range"], cfg)
                qty = orb.size(act["price"], est[0], equity, equity, cfg,
                               rm.drawdown_size_multiplier(equity, peak)) if est and not why else 0
                if why or qty <= 0:
                    s["status"], s["note"] = "done", "; ".join(why) or "size 0"
                    continue
                s["status"] = "entering"
                pending[sym] = {"do": "enter", "side": act["side"], "qty": qty}
            elif act["do"] == "exit":
                s["status"] = "exiting"
                pending[sym] = act
            elif act["do"] == "trail":
                s["pos"]["stop"] = act["stop"]
        for sym in syms:                                   # data ended before the flatten bar
            s = st["symbols"][sym]
            if s.get("pos"):
                long_ = s["pos"]["side"] == "long"
                px = bars[sym][4][-1] * (1 - slip if long_ else 1 + slip)
                trades.append(orb.filled_exit(st, sym, px, bars[sym][0][-1], "flatten", cfg,
                                              costs=2 * comm * s["pos"]["qty"]))
        equity = round(equity + st["pnl"], 2)
        peak = max(peak, equity)
        curve.append((day.isoformat(), equity))
    return {"trades": trades, "curve": curve, "capital": capital}


# ── Stats ─────────────────────────────────────────────────────────────────────

def stats(trades: list, capital: float = CAPITAL) -> dict:
    n = len(trades)
    if not n:
        return {"trades": 0, "net": 0.0}
    pnl = [t["pnl"] for t in trades]
    wins, losses = [p for p in pnl if p > 0], [p for p in pnl if p <= 0]
    eq, peak, mdd, streak, worst = capital, capital, 0.0, 0, 0
    for p in pnl:
        eq += p
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
        streak = streak + 1 if p <= 0 else 0
        worst = max(worst, streak)
    rs = [t["r"] for t in trades if t["r"] is not None]
    return {"trades": n, "net": round(sum(pnl), 2), "net_pct": round(sum(pnl) / capital * 100, 2),
            "win_rate": round(len(wins) / n * 100, 1),
            "avg_win": round(statistics.mean(wins), 2) if wins else 0.0,
            "avg_loss": round(statistics.mean(losses), 2) if losses else 0.0,
            "profit_factor": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
            "expectancy": round(sum(pnl) / n, 3), "expectancy_r": round(statistics.mean(rs), 3) if rs else None,
            "max_drawdown": round(mdd, 2), "longest_losing_streak": worst,
            "exits": dict(sorted(_count(t["reason"] for t in trades).items()))}


def _count(xs):
    out = defaultdict(int)
    for x in xs:
        out[x] += 1
    return out


def split_by(trades, key) -> dict:
    groups = defaultdict(list)
    for t in trades:
        groups[key(t)].append(t)
    return {k: stats(v) for k, v in sorted(groups.items())}


# ── Report helpers ────────────────────────────────────────────────────────────

def svg_curve(series: dict, split_day: str, path: str, title: str):
    """Plain SVG line chart of equity curves ({name: [(day, value)]}), split marked."""
    W, H, P = 900, 360, 50
    allv = [v for s in series.values() for _, v in s]
    days = sorted({d for s in series.values() for d, _ in s})
    if not allv or not days:
        return
    lo, hi = min(allv), max(allv)
    hi = hi if hi > lo else lo + 1
    xi = {d: i for i, d in enumerate(days)}
    X = lambda d: P + (W - 2 * P) * xi[d] / max(1, len(days) - 1)
    Y = lambda v: H - P - (H - 2 * P) * (v - lo) / (hi - lo)
    colors = ["#2a78d6", "#c2410c", "#15803d", "#7c3aed"]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="sans-serif" font-size="12">',
           f'<rect width="{W}" height="{H}" fill="white"/><text x="{P}" y="24" font-size="15">{title}</text>']
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        out.append(f'<line x1="{P}" x2="{W-P}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="#e5e5e5"/>'
                   f'<text x="{P-6}" y="{Y(v)+4:.1f}" text-anchor="end" fill="#666">{v:,.0f}</text>')
    if split_day in xi:
        x = X(split_day)
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{P}" y2="{H-P}" stroke="#999" stroke-dasharray="4 4"/>'
                   f'<text x="{x+4:.1f}" y="{P+12}" fill="#666">out-of-sample →</text>')
    for j, (name, s) in enumerate(series.items()):
        pts = " ".join(f"{X(d):.1f},{Y(v):.1f}" for d, v in s)
        out.append(f'<polyline points="{pts}" fill="none" stroke="{colors[j % 4]}" stroke-width="2"/>'
                   f'<text x="{W-P}" y="{40+16*j}" text-anchor="end" fill="{colors[j % 4]}">{name}</text>')
    for d in (days[0], days[len(days) // 2], days[-1]):
        out.append(f'<text x="{X(d):.1f}" y="{H-P+18}" text-anchor="middle" fill="#666">{d}</text>')
    out.append("</svg>")
    with open(path, "w") as f:
        f.write("\n".join(out))


def write_csv(trades, path):
    if not trades:
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(trades[0].keys()))
        w.writeheader()
        w.writerows(trades)


def buy_and_hold(mkt, sym, days, capital=CAPITAL):
    d = mkt.daily[sym]
    first, last = d.loc[days[0], "o"], d.loc[days[-1], "c"]
    return round(capital * (last / first - 1), 2)


def charles_live(start: str, end: str) -> dict:
    """Charles's own closed-trade P&L (data/trades.json, read only) between two days."""
    try:
        with open(os.path.join(HERE, "data", "trades.json")) as f:
            rows = json.load(f)
    except Exception:
        return {}
    import timeofday
    closed = [t for t in rows if "pnl" in t and start <= timeofday.market_date(t["timestamp"]) <= end]
    return {"trades": len(closed), "net": round(sum(t["pnl"] for t in closed), 2)}


# ── Main ──────────────────────────────────────────────────────────────────────

GRID = {
    "range_min":     [5, 15, 30],
    "reward_risk":   [1.0, 1.5, 2.0, 3.0],
    "stop":          [("range", 0.0), ("fixed", 0.0025), ("fixed", 0.005)],
    "atr_band":      [(0.0, 0.0), (0.1, 0.0), (0.0, 0.5), (0.1, 0.5)],
}


def grid_cfgs(sym):
    for rm_ in GRID["range_min"]:
        for rr in GRID["reward_risk"]:
            for mode, pct in GRID["stop"]:
                for lo, hi in GRID["atr_band"]:
                    yield (rm_, rr, (mode, pct), (lo, hi)), orb.settings(
                        symbols=[sym], range_min=rm_, reward_risk=rr, stop_mode=mode,
                        fixed_stop_pct=pct or config.ORB_FIXED_STOP_PCT, min_range_atr=lo, max_range_atr=hi,
                        trail_pct=0.0, use_regime=False, use_llm=False, allow_short=False, max_trades=1,
                        cutoff_min=60)


def neighbours(key, results):
    """Grid points one step away in exactly one parameter."""
    out = []
    names = list(GRID)
    for i, name in enumerate(names):
        vals = GRID[name]
        j = vals.index(key[i])
        for k in (j - 1, j + 1):
            if 0 <= k < len(vals):
                nk = list(key); nk[i] = vals[k]
                if tuple(nk) in results:
                    out.append(results[tuple(nk)])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true", help="fetch/refresh the 1-min history first")
    args = ap.parse_args()
    if args.download or not os.path.isdir(CACHE):
        download()
    os.makedirs(OUT, exist_ok=True)
    mkt = Market(SYMBOLS)
    days = mkt.days
    cut = int(len(days) * 0.6)
    ins, oos = days[:cut], days[cut:]
    print(f"{len(days)} days {days[0]}..{days[-1]}; in-sample to {ins[-1]}, out-of-sample from {oos[0]}", flush=True)
    rep = {"days": [days[0].isoformat(), days[-1].isoformat()], "in_sample": [ins[0].isoformat(), ins[-1].isoformat()],
           "out_of_sample": [oos[0].isoformat(), oos[-1].isoformat()], "costs": {
               "slippage_bps_per_side": config.ORB_SLIPPAGE_BPS, "commission_per_share": config.ORB_COMMISSION_PER_SHARE}}

    # 1. Spec default (5-min range, range stop, 1.5R, no filters), both symbols, both halves
    rep["spec_default"] = {}
    for sym in SYMBOLS:
        cfg = orb.settings(symbols=[sym], range_min=5, reward_risk=1.5, stop_mode="range", min_range_atr=0,
                           max_range_atr=0, trail_pct=0, use_regime=False, use_llm=False, allow_short=False,
                           max_trades=1, cutoff_min=60)
        rep["spec_default"][sym] = {"in_sample": stats(simulate(mkt, ins, cfg)["trades"]),
                                    "out_of_sample": stats(simulate(mkt, oos, cfg)["trades"])}
        print(sym, "default", rep["spec_default"][sym]["in_sample"]["net"], rep["spec_default"][sym]["out_of_sample"]["net"], flush=True)

    # 2. Tune on in-sample only
    best = {}
    rep["grid"] = {}
    for sym in SYMBOLS:
        res = {}
        for key, cfg in grid_cfgs(sym):
            res[key] = stats(simulate(mkt, ins, cfg)["trades"])
        ranked = sorted(res.items(), key=lambda kv: kv[1]["net"], reverse=True)
        top_key, top = ranked[0]
        nb = neighbours(top_key, res)
        nb_med = statistics.median(x["net"] for x in nb) if nb else None
        positive = sum(1 for v in res.values() if v["net"] > 0)
        spike = top["net"] <= 0 or nb_med is None or nb_med < 0.5 * top["net"]
        rep["grid"][sym] = {"combos": len(res), "positive": positive,
                            "median_net": statistics.median(v["net"] for v in res.values()),
                            "best": {"params": str(top_key), **top}, "neighbour_median_net": nb_med,
                            "isolated_spike": spike,
                            "top5": [{"params": str(k), "net": v["net"], "trades": v["trades"],
                                      "pf": v.get("profit_factor")} for k, v in ranked[:5]]}
        best[sym] = (top_key, dict(grid_cfgs_lookup(sym, top_key)))
        print(sym, "best", top_key, top["net"], "nb", nb_med, "positive", positive, "/", len(res), flush=True)

    # 3. Out-of-sample, once, for each symbol's in-sample winner (+ variants)
    rep["oos"] = {}
    curves = {}
    for sym in SYMBOLS:
        key, cfg = best[sym]
        base = simulate(mkt, oos, cfg)
        full = simulate(mkt, days, cfg)
        rep["oos"][sym] = {
            "params": str(key),
            "pure": stats(base["trades"]),
            "double_slippage": stats(simulate(mkt, oos, cfg, slip_bps=2 * config.ORB_SLIPPAGE_BPS)["trades"]),
            "with_regime_filter": stats(simulate(mkt, oos, {**cfg, "use_regime": True})["trades"]),
            "long_and_short_hypothetical": stats(simulate(mkt, oos, {**cfg, "allow_short": True})["trades"]),
            "by_side_hypothetical": split_by(simulate(mkt, oos, {**cfg, "allow_short": True})["trades"], lambda t: t["side"]),
            "by_year": split_by(base["trades"], lambda t: t["day"][:4]),
            "by_month": {k: {"trades": v["trades"], "net": v["net"]} for k, v in
                         split_by(base["trades"], lambda t: t["day"][:7]).items()},
            "buy_and_hold_net": buy_and_hold(mkt, sym, oos),
            "full_period": stats(full["trades"]),
        }
        write_csv(base["trades"], os.path.join(OUT, f"trades_oos_{sym}.csv"))
        write_csv(full["trades"], os.path.join(OUT, f"trades_all_{sym}.csv"))
        curves[f"{sym} {key}"] = full["curve"]
        print(sym, "OOS", rep["oos"][sym]["pure"], flush=True)
    svg_curve(curves, oos[0].isoformat(), os.path.join(OUT, "equity.svg"),
              "ORB, in-sample winner per symbol, $1,000 start (live sizing, costs included)")

    # 4. Against Charles over Charles's live period
    start = "2026-06-05"
    live_days = [d for d in days if d.isoformat() >= start]
    rep["vs_charles"] = {"window": [start, days[-1].isoformat()],
                         "charles_live_closed_pnl": charles_live(start, days[-1].isoformat()),
                         "orb": {sym: stats(simulate(mkt, live_days, best[sym][1])["trades"]) for sym in SYMBOLS},
                         "buy_and_hold": {sym: buy_and_hold(mkt, sym, live_days) for sym in SYMBOLS}}
    with open(os.path.join(OUT, "summary.json"), "w") as f:
        json.dump(rep, f, indent=1, default=str)
    print("wrote", OUT)


def grid_cfgs_lookup(sym, key):
    for k, cfg in grid_cfgs(sym):
        if k == key:
            return cfg.items()
    raise KeyError(key)


if __name__ == "__main__":
    main()
