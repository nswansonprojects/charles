"""
evaluate.py — Claude's monthly rule evaluation: should one of the plan's numbers change?

Runs the plan's current settings and one-step changes to each number (core %, number of picks,
trend line length, how often the switch is checked, the switch itself) through the backtest on
2007-now, on both the stock list and sector funds, and measures the live plan against SPY since it
started. A change only "passes" when it is better in BOTH universes and BOTH halves of history
(more return, worst drop no more than DD_SLACK points deeper). The daily check-up reads the
result (data/evaluation.json) and may apply at most one passing change per quarter
(ops/daily-checkup.md, "Tuning the rules").

Usage: ops/heavy-run python evaluate.py [--out path]      (timer: charles-evaluate, monthly)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

import allocator as al
import backtest_core as bc

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "data", "evaluation.json")
HALF = "2017-01-01"
MIN_GAIN = 0.5          # % a year better, in every universe and half
DD_SLACK = 2.0          # worst drop may be at most this many points deeper
LABEL = {"CORE_PCT": "Share in the whole market", "PICKS": "Momentum picks", "TREND_DAYS": "Trend line (days)",
         "SWITCH_CHECK": "Safety switch check", "SAFETY_SWITCH": "Safety switch",
         "AGGRESSIVE": "Aggressive mode", "AGG_VOL_MAX": "Aggressive mode's calm limit"}


def fit(s: dict) -> dict:
    """Spread what the core leaves over the picks (variants keep the account fully invested)."""
    s = dict(s)
    s["PICK_PCT"] = round((1 - s["CORE_PCT"] - s["CASH_PCT"]) / s["PICKS"], 4) if s["PICKS"] else 0.0
    return s


def variants(cur: dict) -> list[tuple[str, object, object]]:
    """One-step changes to each number: (setting, from, to)."""
    out = []
    for k, step, lo, hi in (("CORE_PCT", 0.05, 0.6, 0.9), ("PICKS", 1, 2, 8), ("TREND_DAYS", 50, 100, 300)):
        for d in (-step, step):
            v = round(cur[k] + d, 4)
            if lo <= v <= hi:
                out.append((k, cur[k], v))
    out.append(("SWITCH_CHECK", cur["SWITCH_CHECK"], "daily" if cur["SWITCH_CHECK"] == "monthly" else "monthly"))
    out.append(("SAFETY_SWITCH", cur["SAFETY_SWITCH"], not cur["SAFETY_SWITCH"]))
    out.append(("AGGRESSIVE", cur["AGGRESSIVE"], not cur["AGGRESSIVE"]))
    if cur["AGGRESSIVE"]:
        for d in (-0.03, 0.03):                      # the exit moves with it (kept 5 points above)
            v = round(cur["AGG_VOL_MAX"] + d, 2)
            if 0.10 <= v <= 0.25:
                out.append(("AGG_VOL_MAX", cur["AGG_VOL_MAX"], v))
    return out


def judge(base: dict, var: dict) -> tuple[bool, str]:
    """base/var: {universe: {"first": stats, "second": stats, "all": stats}}. Pass only if better everywhere."""
    gains, why = [], []
    for u in base:
        for h in ("first", "second"):
            g = var[u][h]["cagr_pct"] - base[u][h]["cagr_pct"]
            gains.append(g)
            if g < MIN_GAIN:
                why.append(f"{u} {'2007-16' if h == 'first' else '2017-now'} {g:+.1f}%/yr")
        dd = var[u]["all"]["max_drawdown_pct"] - base[u]["all"]["max_drawdown_pct"]
        if dd < -DD_SLACK:
            why.append(f"{u} worst drop {dd:+.1f} pts deeper")
    if why:
        return False, "Not better everywhere: " + ", ".join(why[:3]) + "."
    return True, f"Better in every test: at least {min(gains):+.1f}% a year, worst drop no deeper."


def summarize(run: dict) -> dict:
    d, v = run["dates"], run["values"]
    k = next(j for j, x in enumerate(d) if x >= HALF)
    second = [x * 1000 / v[k - 1] for x in v[k:]]
    keep = ("cagr_pct", "max_drawdown_pct")
    return {"all": {x: bc.stats(d, v)[x] for x in keep}, "first": {x: bc.stats(d[:k], v[:k])[x] for x in keep},
            "second": {x: bc.stats(d[k:], second)[x] for x in keep}}


def live(plan_path: str, curve_path: str) -> dict:
    """The plan since it started vs SPY (needs data/plan.json "started" and the equity curve)."""
    try:
        with open(plan_path) as f:
            started = json.load(f).get("started")
        with open(curve_path) as f:
            curve = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"note": "the plan hasn't started yet"}
    if not started:
        return {"note": "the plan hasn't started yet"}
    before = [p for p in curve if p["ts"][:10] < started]
    after = [p for p in curve if p["ts"][:10] >= started]
    if not before or not after:
        return {"started": started, "note": "not enough history yet"}
    import yfinance as yf
    spy = yf.Ticker("SPY").history(start=started)["Close"]
    prior = yf.Ticker("SPY").history(period="10d", end=started)["Close"]
    start_val, vals = before[-1]["value"], [p["value"] for p in after]
    peak, worst = start_val, 0.0
    for x in vals:
        peak = max(peak, x)
        worst = min(worst, x / peak - 1)
    days = (datetime.now(timezone.utc) - datetime.fromisoformat(started).replace(tzinfo=timezone.utc)).days
    return {"started": started, "days": days,
            "charles_pct": round((vals[-1] / start_val - 1) * 100, 2),
            "spy_pct": round((float(spy.iloc[-1]) / float(prior.iloc[-1]) - 1) * 100, 2) if len(spy) and len(prior) else None,
            "charles_worst_drop_pct": round(worst * 100, 2),
            "trusted": days >= 180,
            "note": "Live results count for tuning from 6 months on; before that only the backtest does."}


def main(out: str = OUT, data_dir: str | None = None):
    import strategy_settings
    cur = {**al.DEFAULTS, **strategy_settings.load()}
    syms = sorted(set(al.UNIVERSE) | set(bc.SECTORS) | {"SPY", "BIL", cur["AGG_CORE"]})
    dates, opens, closes = bc._download(syms)
    stocks = [k for k in al.UNIVERSE if k in closes]
    unis = {"stocks": stocks, "sectors": bc.SECTORS}

    def test(s):
        s = dict(s, SAFE="BIL")
        return {u: summarize(bc.simulate(dates, opens, closes, s, universe=lst)) for u, lst in unis.items()}

    base = test(cur)
    rows = []
    for k, frm, to in variants(cur):
        s = {**cur, k: to}
        if k in ("CORE_PCT", "PICKS"):
            s = fit(s)
        if k == "AGG_VOL_MAX":
            s["AGG_VOL_EXIT"] = round(to + (cur["AGG_VOL_EXIT"] - cur["AGG_VOL_MAX"]), 2)
        r = test(s)
        ok, why = judge(base, r)
        rows.append({"setting": k, "label": LABEL[k], "from": frm, "to": to, "passes": ok, "why": why, "results": r})
        print(f"{k} {frm}->{to}: {'PASS' if ok else 'no'} — {why}", file=sys.stderr)
    best = max((r for r in rows if r["passes"]),
               key=lambda r: min(r["results"][u][h]["cagr_pct"] - base[u][h]["cagr_pct"]
                                 for u in base for h in ("first", "second")), default=None)
    data_dir = data_dir or os.path.dirname(out)
    result = {"generated": datetime.now(timezone.utc).isoformat(timespec="minutes"), "history": f"2007-07 to {dates[-1]}",
              "settings": {k: cur[k] for k in LABEL}, "current": base, "variants": rows,
              "candidate": {k: best[k] for k in ("setting", "label", "from", "to", "why")} if best else None,
              "live": live(os.path.join(data_dir, "plan.json"), os.path.join(data_dir, "equity_curve.json")),
              "rules": {"min_gain_pct_per_year": MIN_GAIN, "dd_slack_pts": DD_SLACK}}
    import state_io
    state_io.write_json(out, result, indent=1)
    print(json.dumps({"candidate": result["candidate"], "live": result["live"]}, indent=1))


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else OUT)
