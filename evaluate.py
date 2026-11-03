"""
evaluate.py — Claude's monthly rule evaluation: should one of the plan's numbers change?

Runs the plan's current settings and one-step changes to each number (core %, number of picks,
trend line length, how often the switch is checked, the switch itself) through the backtest on
2007-now, on both the stock list and sector funds, and measures the live plan against SPY since it
started. A change only "passes" when it is better in BOTH universes and BOTH halves of history
(more return, worst drop no more than DD_SLACK points deeper). The daily check-up reads the
result (data/evaluation.json) and may apply at most one passing change per quarter
(ops/daily-checkup.md, "Tuning the rules").

A Custom Plan (portfolio.py, the user's own blocks) is tested on its own tickers: its rules and its blocks' amounts,
never which tickers it holds. Self-tune (SELF_TUNE, the Self-Tune tab's switch, owner 2026-11-03): on an install without
Claude's check-up, a passing change is applied by itself, at most one a quarter, through the updater like any slider
change (so it can be undone); with self-tune off, nothing is applied and the results are only shown.

Usage: ops/heavy-run python evaluate.py [--out path]      (timer: charles-evaluate, monthly)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

import copy

import allocator as al
import backtest_core as bc
import portfolio

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "data", "evaluation.json")
CHECKUP = os.path.join(ROOT, "ops", "run-checkup")      # Claude's daily check-up applies changes where it runs
QUARTER_DAYS = 91
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


def custom_variants(cur: dict) -> list[dict]:
    """One-step changes for a Custom Plan: its rules and its blocks' amounts, never its tickers or how much is invested.
    [{"setting", "label", "from", "to", "change"}]; `change` is what applying it writes."""
    out, blocks = [], cur["BLOCKS"]

    def add(setting, label, frm, to, change):
        out.append({"setting": setting, "label": label, "from": frm, "to": to, "change": change})
    for d in (-50, 50):
        v = cur["TREND_DAYS"] + d
        if 100 <= v <= 300:
            add("TREND_DAYS", LABEL["TREND_DAYS"], cur["TREND_DAYS"], v, {"TREND_DAYS": v})
    add("SWITCH_CHECK", LABEL["SWITCH_CHECK"], cur["SWITCH_CHECK"], "daily" if cur["SWITCH_CHECK"] == "monthly" else "monthly",
        {"SWITCH_CHECK": "daily" if cur["SWITCH_CHECK"] == "monthly" else "monthly"})
    add("SAFETY_SWITCH", LABEL["SAFETY_SWITCH"], cur["SAFETY_SWITCH"], not cur["SAFETY_SWITCH"], {"SAFETY_SWITCH": not cur["SAFETY_SWITCH"]})
    if any(b["type"] == "long" and cur["CORE"] in b["tickers"] for b in blocks):      # aggressive mode needs SPY in a long-term block
        add("AGGRESSIVE", LABEL["AGGRESSIVE"], cur["AGGRESSIVE"], not cur["AGGRESSIVE"], {"AGGRESSIVE": not cur["AGGRESSIVE"]})
    for i, b in enumerate(blocks):
        if b["type"] == "momentum":
            for d in (-1, 1):
                n = b["picks"] + d
                if 1 <= n <= min(portfolio.MAX_PICKS, len(b["tickers"])):
                    nb = copy.deepcopy(blocks)
                    nb[i]["picks"] = n
                    add("BLOCKS", f"{b['name']}: picks", b["picks"], n, {"BLOCKS": nb})
    for i, a in enumerate(blocks):
        for j, b in enumerate(blocks):
            if i != j and a["pct"] >= 0.10 - 1e-9:
                nb = copy.deepcopy(blocks)
                nb[i]["pct"], nb[j]["pct"] = round(a["pct"] - 0.05, 4), round(b["pct"] + 0.05, 4)
                add("BLOCKS", f"5 points from {a['name']} to {b['name']}", f"{round(a['pct'] * 100)}% / {round(b['pct'] * 100)}%",
                    f"{round(nb[i]['pct'] * 100)}% / {round(nb[j]['pct'] * 100)}%", {"BLOCKS": nb})
    return out


def self_tune(result: dict, cur: dict, data_dir: str, now: datetime | None = None, queue=None, checkup: str = CHECKUP) -> str:
    """Apply the candidate by itself where nobody else does (an install without Claude's check-up), if self-tune is on and
    nothing was changed this way in the last quarter. Goes through the updater like a slider change. Returns what happened."""
    now = now or datetime.now(timezone.utc)
    if not cur.get("SELF_TUNE", True):
        return "Self-tune is off, so nothing was changed."
    if os.path.exists(checkup):
        return "Claude's daily check-up decides here."
    cand = result.get("candidate")
    if not cand:
        return "No change passed every test, so nothing was changed."
    try:
        with open(os.path.join(data_dir, "settings_history.jsonl")) as f:
            rows = [json.loads(x) for x in f if x.strip()]
    except (OSError, ValueError):
        rows = []
    for r in reversed(rows):
        if r.get("by") == "self-tune" and not r.get("undo"):
            try:
                if (now - datetime.fromisoformat(r["at"])).days < QUARTER_DAYS:
                    return "Self-tune already changed one number this quarter."
            except (KeyError, ValueError):
                pass
            break
    if queue is None:
        import updates
        queue = updates.queue
    said = queue("apply-settings", "self-tune", settings=cand["change"], reason=f"self-tune: {cand['why']}", bot="stocks", source="self-tune")
    return f"Self-tune: {cand['label']} {cand['from']} → {cand['to']} ({said})."


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
    import plan_vs_spy
    from datetime import date, timedelta
    s = yf.Ticker("SPY").history(start=str(date.fromisoformat(started[:10]) - timedelta(days=10)))["Close"]
    same = plan_vs_spy.since_started(curve, started, [(str(i.date()), float(c)) for i, c in s.items()])
    if not same:
        return {"started": started, "note": "not enough history yet"}
    start_val, vals = before[-1]["value"], [p["value"] for p in after]
    peak, worst = start_val, 0.0
    for x in vals:
        peak = max(peak, x)
        worst = min(worst, x / peak - 1)
    days = (datetime.now(timezone.utc) - datetime.fromisoformat(started).replace(tzinfo=timezone.utc)).days
    return {"started": started, "days": days,
            # Both at the same close (after-hours and weekend curve points left out).
            "charles_pct": same["charles_pct"], "spy_pct": same["spy_pct"], "as_of_close": same["as_of_close"],
            "charles_worst_drop_pct": round(worst * 100, 2),
            "trusted": days >= 180,
            "note": "Live results count for tuning from 6 months on; before that only the backtest does."}


def main(out: str = OUT, data_dir: str | None = None):
    import strategy_settings
    cur = {**al.DEFAULTS, **strategy_settings.load()}
    custom = bool(cur.get("BLOCKS"))
    syms = sorted(set(al.UNIVERSE) | set(bc.SECTORS) | {"SPY", "BIL", cur["AGG_CORE"]} | set(portfolio.symbols(cur)))
    dates, opens, closes = bc._download(syms)
    stocks = [k for k in al.UNIVERSE if k in closes]
    unis = {"your plan": None} if custom else {"stocks": stocks, "sectors": bc.SECTORS}

    def test(s):
        s = dict(s, SAFE="BIL")
        return {u: summarize(bc.simulate(dates, opens, closes, s, universe=lst)) for u, lst in unis.items()}

    base = test(cur)
    rows = []
    if custom:
        for v in custom_variants(cur):
            r = test({**cur, **v["change"]})
            ok, why = judge(base, r)
            rows.append({**v, "passes": ok, "why": why, "results": r})
            print(f"{v['label']} {v['from']}->{v['to']}: {'PASS' if ok else 'no'} — {why}", file=sys.stderr)
    for k, frm, to in ([] if custom else variants(cur)):
        s = {**cur, k: to}
        if k in ("CORE_PCT", "PICKS"):
            s = fit(s)
        if k == "AGG_VOL_MAX":
            s["AGG_VOL_EXIT"] = round(to + (cur["AGG_VOL_EXIT"] - cur["AGG_VOL_MAX"]), 2)
        r = test(s)
        ok, why = judge(base, r)
        rows.append({"setting": k, "label": LABEL[k], "from": frm, "to": to, "passes": ok, "why": why, "results": r,
                     "change": {x: s[x] for x in s if x in strategy_settings.LIMITS and s[x] != cur.get(x)}})
        print(f"{k} {frm}->{to}: {'PASS' if ok else 'no'} — {why}", file=sys.stderr)
    best = max((r for r in rows if r["passes"]),
               key=lambda r: min(r["results"][u][h]["cagr_pct"] - base[u][h]["cagr_pct"]
                                 for u in base for h in ("first", "second")), default=None)
    data_dir = data_dir or os.path.dirname(out)
    result = {"generated": datetime.now(timezone.utc).isoformat(timespec="minutes"), "history": f"2007-07 to {dates[-1]}",
              "settings": {k: cur[k] for k in LABEL}, "current": base, "variants": rows, "custom": custom,
              "candidate": {k: best[k] for k in ("setting", "label", "from", "to", "why", "change")} if best else None,
              "live": live(os.path.join(data_dir, "plan.json"), os.path.join(data_dir, "equity_curve.json")),
              "rules": {"min_gain_pct_per_year": MIN_GAIN, "dd_slack_pts": DD_SLACK}, "self_tune": cur.get("SELF_TUNE", True) is not False}
    if custom:
        result["settings"]["BLOCKS"] = cur["BLOCKS"]
    try:
        result["self_tune_said"] = self_tune(result, cur, data_dir)
    except Exception as e:                       # the results still count when the change can't be queued
        result["self_tune_said"] = f"Self-tune couldn't apply the change: {e}"
    import state_io
    state_io.write_json(out, result, indent=1)
    print(json.dumps({"candidate": result["candidate"], "live": result["live"]}, indent=1))


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else OUT)
