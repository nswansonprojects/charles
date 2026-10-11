"""
decision_scorer.py — grade every logged decision by what the stock did next.

For each decision in data/decisions.jsonl, compare the decision price with the
close 1, 3 and 5 trading days later. Groups answer the questions that matter:
  - entries (stock not held): did the AI's BUY picks beat the stocks it passed
    on, the rule signal's picks, and the average candidate (the baseline)?
  - exits (stock held): after an AI SELL, did the stock fall (a good sell)?
  - AI confidence buckets: does higher confidence mean better picks?
Each stock counts once per day per group, so the every-30-minutes repeats don't
inflate the numbers. Runs nightly (main.py, 5:05 PM ET) and writes
data/decision_scorecard.json for the dashboard and the daily check-up.
"""
import json
import logging
import os
import statistics
from collections import defaultdict
from datetime import datetime, timezone

import state_io
import timeofday

log = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DECISIONS_FILE = os.path.join(DATA_DIR, "decisions.jsonl")
MODEL_B_FILE = os.path.join(DATA_DIR, "decisions_model_b.jsonl")    # shadow_model.py
MODEL_B_PREFIX = "model B · "
SCORECARD_FILE = os.path.join(DATA_DIR, "decision_scorecard.json")
HORIZONS = (1, 3, 5)   # trading days


def load_decisions(path: str | None = None) -> list:
    rows = []
    try:
        with open(path or DECISIONS_FILE) as f:   # resolved at call time, not import time
            for line in f:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except FileNotFoundError:
        pass
    return rows


def _conf_bucket(c) -> str:
    if c is None:
        return "?"
    return "<0.65" if c < 0.65 else "0.65-0.75" if c < 0.75 else "0.75-0.85" if c < 0.85 else ">=0.85"


def _groups(d: dict) -> list:
    """The groups one decision belongs to."""
    ai, rule = str(d.get("ai_action", "")).upper(), str(d.get("rule_signal", "")).upper()
    src = str(d.get("source", ""))
    if d.get("held"):
        return ["held: AI said SELL"] if ai == "SELL" else ["held: AI said HOLD"]
    g = ["all candidates (baseline)"]
    if src.startswith("llm"):
        if ai == "BUY":
            g += ["AI BUY", f"AI BUY conf {_conf_bucket(d.get('ai_conf'))}"]
        else:
            g.append("AI passed (HOLD/SELL)")
    if rule == "BUY":
        g.append("rule BUY")
    return g


def score(decisions: list, closes_for, prefix: str = "") -> dict:
    """
    closes_for(symbol) -> list of (ET date 'YYYY-MM-DD', close) sorted by date.
    Returns {group: {"1d": {n, mean_pct, median_pct, up_pct}, ...}}; prefix goes before each
    group name (used for the second model's decisions).
    """
    seen, samples = set(), defaultdict(lambda: defaultdict(list))
    cache = {}
    for d in decisions:
        sym, price, day = d.get("symbol"), d.get("price"), timeofday.market_date(d.get("ts", ""))
        if not sym or not price or not day:
            continue
        if sym not in cache:
            try:
                cache[sym] = closes_for(sym) or []
            except Exception as e:
                log.warning(f"Scorer: no prices for {sym}: {e}")
                cache[sym] = []
        after = [c for (dt, c) in cache[sym] if dt > day]   # trading days after the decision day
        for group in (prefix + g for g in _groups(d)):
            key = (group, sym, day)
            if key in seen:
                continue
            seen.add(key)
            for h in HORIZONS:
                if len(after) >= h:
                    samples[group][f"{h}d"].append((after[h - 1] / float(price) - 1) * 100)
    out = {}
    for group, by_h in samples.items():
        out[group] = {}
        for h, rets in by_h.items():
            out[group][h] = {"n": len(rets),
                             "mean_pct": round(statistics.mean(rets), 2),
                             "median_pct": round(statistics.median(rets), 2),
                             "up_pct": round(100 * sum(r > 0 for r in rets) / len(rets))}
    return out


def _yf_closes(symbol: str, start: str) -> list:
    import yfinance as yf
    hist = yf.Ticker(symbol).history(start=start, interval="1d")
    return [(timeofday.market_date(ts.isoformat()), float(c)) for ts, c in hist["Close"].items()]


def run() -> dict:
    """Score all decisions and write the scorecard. Safe to call any time."""
    decisions = load_decisions()
    if not decisions:
        log.info("📏 Decision scorer: no decisions logged yet")
        return {}
    start = min(timeofday.market_date(d.get("ts", "")) or "9999" for d in decisions)
    cache = {}

    def closes(s):
        if s not in cache:
            cache[s] = _yf_closes(s, start)
        return cache[s]
    groups = score(decisions, closes)
    model_b = load_decisions(MODEL_B_FILE)
    if model_b:
        groups.update(score(model_b, closes, prefix=MODEL_B_PREFIX))
    card = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "decisions": len(decisions),
        "model_b_decisions": len(model_b),
        "first_decision_day": start,
        "horizons_trading_days": list(HORIZONS),
        "groups": groups,
        "how_to_read": ("mean_pct = average % move from the decision price to the close N trading days "
                        "later; up_pct = share of cases that went up. For entries, AI BUY should beat "
                        "'all candidates (baseline)' and 'AI passed'. After 'held: AI said SELL', a "
                        "negative move means the sell was right."),
    }
    state_io.write_json(SCORECARD_FILE, card, indent=2)
    base = groups.get("all candidates (baseline)", {}).get("3d", {})
    buy = groups.get("AI BUY", {}).get("3d", {})
    log.info(f"📏 Decision scorecard: {len(decisions)} decisions | 3d: AI BUY "
             f"{buy.get('mean_pct', '—')}% (n={buy.get('n', 0)}) vs baseline "
             f"{base.get('mean_pct', '—')}% (n={base.get('n', 0)})")
    return card
