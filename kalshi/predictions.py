"""Prediction log: every strategy's probability on every market it covers, graded against Kalshi's own
price when the market settles, whether or not anything was bet (owner 2026-10-15).

Bets alone grade a model slowly and only where it already thought it had an edge. Logging every market
gives ~10x the evidence for free, so gates.py can decide which strategies have earned money.

Per market we keep:
- first:  model / blend / market mid the first time the strategy priced it (Brier is graded on this:
          one unbiased snapshot per market, model and market taken at the same moment);
- last:   the same, refreshed every scan until the market closes;
- signal: the first time the strategy would have bet under the real rules (side, ask, cost per
          contract incl. fee), so its hypothetical "shadow" bet can be scored at settlement.
Nothing here bets or knows about the ledger.
"""
import json
import os
from datetime import datetime, timedelta

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PRED = os.path.join(DATA, "predictions.json")             # markets not settled yet
GRADED = os.path.join(DATA, "predictions_graded.jsonl")   # one line per settled market, append-only
MAX_LOOKUPS = 300       # settled-market lookups per scan (100 per request)
GIVE_UP_DAYS = 14       # drop a closed market still unsettled after this long


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _when(s: str):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def load(path: str | None = None) -> dict:
    try:
        with open(path or PRED) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {"open": {}}


def save(book: dict, path: str | None = None):
    path = path or PRED
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(book, f)
    os.replace(path + ".tmp", path)


def load_graded(path: str | None = None) -> list:
    out = []
    try:
        with open(path or GRADED) as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except FileNotFoundError:
        pass
    return out


def record(book: dict, picks: list, signals: dict, keys: dict, by_ticker: dict, now: datetime) -> int:
    """Log this scan's blended picks. `signals` = {ticker: {side, ask, cost, edge, fair}} where the
    strategy would bet now; `keys` = {ticker: exposure key}. Returns how many markets were new."""
    new = 0
    ts = now.isoformat(timespec="seconds")
    for p in picks:
        m = by_ticker.get(p["ticker"])
        if m is None:
            continue
        yb, ya = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
        if not (0 < yb <= ya < 1):
            continue  # no two-sided market: nothing to compare the model with
        snap = {"ts": ts, "model_yes": p.get("model_yes", p["fair_yes"]), "fair_yes": p["fair_yes"],
                "market_yes": round((yb + ya) / 2, 4)}
        rec = book["open"].get(p["ticker"])
        if rec is None:
            rec = book["open"][p["ticker"]] = {
                **{k: p.get(k, "") for k in ("strategy", "ticker", "event", "title", "sub", "source")},
                "key": keys.get(p["ticker"], p.get("event", "")), "close_time": m.get("close_time", ""),
                "first": snap, "signal": None}
            new += 1
        rec["last"] = snap
        if rec["signal"] is None and p["ticker"] in signals:
            rec["signal"] = {"ts": ts, **signals[p["ticker"]]}
    return new


def _due(book: dict, by_ticker: dict, now: datetime) -> list:
    out = []
    for t, rec in book["open"].items():
        close = _when(rec.get("close_time", ""))
        if t not in by_ticker or (close and close <= now):
            out.append(t)
    return sorted(out, key=lambda t: book["open"][t].get("close_time", ""))[:MAX_LOOKUPS]


def grade(book: dict, by_ticker: dict, lookup, now: datetime, path: str | None = None) -> list:
    """Move settled markets from `book` to the graded log. `lookup(tickers)` -> [market dicts]."""
    due = _due(book, by_ticker, now)
    if not due:
        return []
    found = {m["ticker"]: m for m in lookup(due)}
    graded = []
    for t in due:
        rec, m = book["open"][t], found.get(t)
        close = _when(rec.get("close_time", ""))
        stale = close is not None and now - close > timedelta(days=GIVE_UP_DAYS)
        if m and m.get("status") in ("settled", "finalized"):
            del book["open"][t]
            if m.get("result") in ("yes", "no"):
                y = 1.0 if m["result"] == "yes" else 0.0
                sig = rec.get("signal")
                if sig:
                    sig = {**sig, "pnl": round((1.0 if sig["side"] == m["result"] else 0.0) - sig["cost"], 4)}
                graded.append({**rec, "signal": sig, "result": m["result"], "y": y,
                               "graded": now.isoformat(timespec="seconds")})
        elif stale or (m is None and close is None):
            del book["open"][t]  # voided, delisted or never settling: nothing to grade
    if graded:
        path = path or GRADED
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            for g in graded:
                f.write(json.dumps(g) + "\n")
    return graded


def _brier(rows: list, key: str):
    return round(sum((r["first"][key] - r["y"]) ** 2 for r in rows) / len(rows), 4) if rows else None


def scorecard(rows: list, strategy: str, since: str | None = None, last: int | None = None) -> dict:
    """How one strategy's predictions made after `since` (optionally only the latest `last`) did:
    Brier of the raw model, the blend it bets on and the market (lower is better), and its shadow bets."""
    r = [x for x in rows if x["strategy"] == strategy and (since is None or x["first"]["ts"] >= since)]
    if last:
        r = r[-last:]
    sig = [x["signal"] for x in r if x.get("signal")]
    cost = sum(s["cost"] for s in sig)
    pnl = sum(s["pnl"] for s in sig)
    bb, bk = _brier(r, "fair_yes"), _brier(r, "market_yes")
    return {"graded": len(r), "events": len({x["key"] for x in r}),
            "brier_model": _brier(r, "model_yes"), "brier_blend": bb, "brier_market": bk,
            "beats_market_by": round(bk - bb, 4) if r else None,
            "signals": len(sig), "signal_events": len({x["key"] for x in r if x.get("signal")}),
            "shadow_won": sum(s["pnl"] > 0 for s in sig), "shadow_pnl_per_contract": round(pnl, 4),
            "shadow_roi_pct": round(pnl / cost * 100, 1) if cost else None}
