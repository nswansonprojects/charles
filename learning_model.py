"""
learning_model.py — Charles learning from its own decisions (roadmap task 10).

Data: data/decisions.jsonl rows that carry `features` (logged since 2026-10-03), entries only
(stock not held), one row per symbol per ET day. Label: did the close 5 trading days later end
above the decision price. Model: logistic regression in numpy (nothing new to install).

Walk-forward only: each test week is predicted by a model trained on weeks that ended at least a
week before it (so every training label was already known), and results are pooled over all test
weeks. Compared with "always up" (accuracy) and with the AI's BUY picks (share that went up).
Until READY_WEEKS weeks of labelled data exist it only reports progress (no network, no training).
If it ever beats both out of sample, the next step is a shadow book on its top picks (owner sees it
first in the check-up). Runs nightly after decision_scorer; writes data/learning_model.json.
"""
import json
import logging
import os
from datetime import date, datetime, timezone

import state_io
import timeofday

log = logging.getLogger(__name__)

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OUT_FILE = os.path.join(DATA, "learning_model.json")
READY_WEEKS = 12            # ≈ 2027-01-04 for data logged from 2026-10-03
MIN_TRAIN_WEEKS = 6
HORIZON = 5                 # trading days
MIN_TEST_ROWS = 200         # pooled test rows needed before calling a result
TREND = {"bullish": 1.0, "bearish": -1.0}
REGIMES = ("TRENDING_BULL", "TRENDING_BEAR", "RANGING", "VOLATILE")


def week(day: str) -> str:
    y, w, _ = date.fromisoformat(day).isocalendar()
    return f"{y}-W{w:02d}"


def rows(decisions: list) -> list:
    """Entry decisions with features, first one per symbol per ET day."""
    seen, out = set(), []
    for d in decisions:
        day = timeofday.market_date(d.get("ts", ""))
        if d.get("held") or not d.get("features") or not d.get("price") or not day:
            continue
        if (d["symbol"], day) in seen:
            continue
        seen.add((d["symbol"], day))
        out.append(dict(d, day=day))
    return out


def vector(d: dict) -> list:
    f = d["features"]
    num = lambda v: float(v) if isinstance(v, (int, float)) else 0.0
    x = [num(f.get(k)) for k in ("change_pct", "intraday_change_pct", "vol_ratio", "rsi_7", "rsi_21",
                                 "bb_pos", "atr_pct", "ma50_dist_pct", "hourly_bb_pos")]
    x.append(num(f.get("macd")) - num(f.get("macd_signal")))
    x.append(num(f.get("hourly_macd")) - num(f.get("hourly_macd_signal")))
    x.append(1.0 if f.get("above_ma50") else 0.0)
    x.append(TREND.get(f.get("hourly_trend"), 0.0))
    x += [num(d.get("rsi")), num(d.get("hourly_rsi")), num(d.get("sentiment")), num(d.get("rule_score"))]
    x += [1.0 if d.get("regime") == r else 0.0 for r in REGIMES]
    return x


def label(d: dict, closes: list):
    """1 if the close HORIZON trading days after the decision day is above the decision price,
    0 if not, None if not known yet. closes: [(day, close)] sorted."""
    after = [c for day, c in closes if day > d["day"]]
    return None if len(after) < HORIZON else int(after[HORIZON - 1] > float(d["price"]))


def fit(X, y, l2=1.0, steps=500, lr=0.1):
    """Logistic regression by gradient descent on standardised inputs. Returns predict(X) -> probs."""
    import numpy as np
    X, y = np.asarray(X, float), np.asarray(y, float)
    mu, sd = X.mean(0), X.std(0)
    sd[sd == 0] = 1.0
    Z = np.hstack([(X - mu) / sd, np.ones((len(X), 1))])
    w = np.zeros(Z.shape[1])
    for _ in range(steps):
        p = 1 / (1 + np.exp(-Z @ w))
        grad = Z.T @ (p - y) / len(y) + l2 * np.r_[w[:-1], 0] / len(y)
        w -= lr * grad
    return lambda Xn: 1 / (1 + np.exp(-np.hstack([(np.asarray(Xn, float) - mu) / sd,
                                                   np.ones((len(Xn), 1))]) @ w))


def walk_forward(labelled: list) -> dict:
    """labelled: rows with "y". Pooled out-of-sample results over every testable week."""
    weeks = sorted({week(r["day"]) for r in labelled})
    tested = []
    for i, wk in enumerate(weeks):
        train = [r for r in labelled if week(r["day"]) in weeks[:max(0, i - 1)]]   # skip the week before
        if len({week(r["day"]) for r in train}) < MIN_TRAIN_WEEKS or len({r["y"] for r in train}) < 2:
            continue
        test = [r for r in labelled if week(r["day"]) == wk]
        predict = fit([vector(r) for r in train], [r["y"] for r in train])
        for r, p in zip(test, predict([vector(r) for r in test])):
            tested.append((r, float(p)))
    n = len(tested)
    if not n:
        return {"test_rows": 0}
    up = sum(r["y"] for r, _ in tested) / n
    acc = sum((p > 0.5) == bool(r["y"]) for r, p in tested) / n
    picks = [r for r, p in tested if p > 0.5]
    ai = [r for r, _ in tested if str(r.get("ai_action")).upper() == "BUY"]
    rate = lambda rs: round(100 * sum(r["y"] for r in rs) / len(rs), 1) if rs else None
    out = {"test_rows": n, "test_weeks": len({week(r["day"]) for r, _ in tested}),
           "accuracy_pct": round(100 * acc, 1), "always_up_pct": round(100 * max(up, 1 - up), 1),
           "model_picks": len(picks), "model_picks_up_pct": rate(picks),
           "ai_buys": len(ai), "ai_buys_up_pct": rate(ai)}
    out["beats_both"] = bool(n >= MIN_TEST_ROWS and out["accuracy_pct"] > out["always_up_pct"]
                             and out["model_picks_up_pct"] is not None and out["ai_buys_up_pct"] is not None
                             and out["model_picks_up_pct"] > out["ai_buys_up_pct"])
    return out


def readiness(entry_rows: list, today: str) -> dict:
    """Weeks whose decisions are old enough to be labelled (about a week past the decision)."""
    old = {week(r["day"]) for r in entry_rows if (date.fromisoformat(today) - date.fromisoformat(r["day"])).days >= 8}
    return {"labelled_weeks": len(old), "needs_weeks": READY_WEEKS,
            "progress": round(min(1.0, len(old) / READY_WEEKS), 2), "rows": len(entry_rows)}


def run(closes_for=None, now: datetime | None = None, decisions: list | None = None) -> dict:
    """Nightly: report progress; once ready, train walk-forward and save the result."""
    import decision_scorer
    now = now or datetime.now(timezone.utc)
    today = timeofday.market_date(now.isoformat())
    entries = rows(decisions if decisions is not None else decision_scorer.load_decisions())
    out = {"generated_at": now.isoformat(timespec="seconds"), **readiness(entries, today)}
    if out["labelled_weeks"] >= READY_WEEKS and entries:
        start = min(r["day"] for r in entries)
        closes_for = closes_for or (lambda s: decision_scorer._yf_closes(s, start))
        cache, labelled = {}, []
        for r in entries:
            if r["symbol"] not in cache:
                try:
                    cache[r["symbol"]] = closes_for(r["symbol"]) or []
                except Exception:
                    cache[r["symbol"]] = []
            y = label(r, cache[r["symbol"]])
            if y is not None:
                labelled.append(dict(r, y=y))
        out["result"] = walk_forward(labelled)
        log.info(f"🧠 Learning model: {out['result']}")
    state_io.write_json(OUT_FILE, out, indent=1)
    return out
