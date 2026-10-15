"""Strategy levels: a strategy bets only as much as it has proven (owner 2026-10-15).

  predict  no money; its predictions are logged and graded (predictions.py)
  small    a few dollars a bet, after its predictions beat the market's price and its shadow bets made
           money after fees
  normal   the usual Kelly sizing and caps, after 30+ real small bets made money too

Any strategy above predict drops straight back to predict when its recent predictions stop beating
the market or its bets since promotion lose too much. Every move restarts its evidence count, so
a demoted strategy must prove itself again on fresh data. The gates are automatic and fixed:
neither tuning.json nor the daily review can override them. The owner's caps (tuning.json
strategy_cap_pct) still apply on top, so a cap of 0 keeps a strategy from betting at any level.
"""
import json
import os
from datetime import datetime

import predictions

STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "levels.json")
LEVELS = ("predict", "small", "normal")
GATES = {
    # predict -> small: on predictions made since the strategy entered its current level
    "graded": 50,             # settled markets
    "events": 15,             # ...from this many separate outcomes (one city-day, one game, one CPI release)
    "brier_margin": 0.005,    # blend's Brier must beat the market's by at least this
    "signals": 20,            # shadow bets (where it would have bet under the real rules)
    "signal_events": 8,       # ...on this many separate outcomes
    # small -> normal: also real bets since promotion
    "bets": 30,
    # demotion, checked every scan above predict
    "recent": 100,            # latest graded predictions looked at
    "recent_min": 30,         # ...once there are this many since promotion
    "loss_pct_of_cap": 0.15,  # bets since promotion lost this share of the strategy's dollar cap
    "loss_floor": 25.0,       # ...or this many dollars, whichever is bigger
    # small-level sizing
    "small_max_bet": 5.0,
    "small_cap_pct": 0.05,
}


def load(strategies: tuple, now: datetime, path: str | None = None) -> dict:
    try:
        with open(path or STATE) as f:
            st = json.load(f)
    except (FileNotFoundError, ValueError):
        st = {}
    for s in strategies:
        if st.get(s, {}).get("level") not in LEVELS:
            st[s] = {"level": "predict", "since": now.isoformat(timespec="seconds"), "history": []}
    return st


def save(st: dict, path: str | None = None):
    path = path or STATE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(st, f, indent=1)
    os.replace(path + ".tmp", path)


def cap_dollars(level: str, cap_pct: float, bank: float) -> float:
    if level == "predict":
        return 0.0
    return bank * (min(cap_pct, GATES["small_cap_pct"]) if level == "small" else cap_pct)


def proven(card: dict) -> tuple[bool, str]:
    """Do these predictions clear the predict -> small bar? (passed, plain-words reason)"""
    g = GATES
    short = [f"{card[k]}/{g[k]} {label}" for k, label in
             (("graded", "settled"), ("events", "separate outcomes"), ("signals", "shadow bets"),
              ("signal_events", "outcomes with shadow bets")) if card[k] < g[k]]
    if short:
        return False, "needs more data: " + ", ".join(short)
    if card["beats_market_by"] < g["brier_margin"]:
        return False, f"not beating the market (Brier {card['brier_blend']} vs market {card['brier_market']})"
    if (card["shadow_roi_pct"] or 0) <= 0:
        return False, f"shadow bets lost money ({card['shadow_roi_pct']}% after fees)"
    return True, (f"beats the market (Brier {card['brier_blend']} vs {card['brier_market']} over "
                  f"{card['graded']} markets) and shadow bets made {card['shadow_roi_pct']}% after fees")


def check(s: str, st: dict, rows: list, closed: list, cap_pct: float, bank: float):
    """(new level, why) if strategy `s` should move, else None."""
    lvl, since = st["level"], st["since"]
    card = predictions.scorecard(rows, s, since=since)
    if lvl == "predict":
        ok, why = proven(card)
        return ("small", why) if ok else None
    recent = predictions.scorecard(rows, s, since=since, last=GATES["recent"])
    if recent["graded"] >= GATES["recent_min"] and recent["beats_market_by"] <= 0:
        return "predict", (f"stopped beating the market (Brier {recent['brier_blend']} vs "
                           f"{recent['brier_market']} over its last {recent['graded']} markets)")
    bets = [b for b in closed if b["strategy"] == s and b["opened"] >= since]
    pnl = round(sum(b["pnl"] for b in bets), 2)
    limit = max(GATES["loss_pct_of_cap"] * cap_dollars(lvl, cap_pct, bank), GATES["loss_floor"])
    if pnl <= -limit:
        return "predict", f"bets since promotion lost ${-pnl:.2f} (limit ${limit:.2f})"
    if lvl == "small" and len(bets) >= GATES["bets"] and pnl > 0:
        ok, why = proven(card)
        if ok:
            return "normal", f"{len(bets)} small bets made ${pnl:.2f}, and it still {why}"
    return None


def update(strategies: tuple, led: dict, rows: list, caps: dict, bank: float, now: datetime,
           path: str | None = None) -> tuple[dict, list]:
    """Re-check every strategy, save, and return ({strategy: level}, [moves])."""
    st = load(strategies, now, path)
    moves = []
    for s in strategies:
        hit = check(s, st[s], rows, led["closed"], caps.get(s, 0.0), bank)
        if hit:
            new, why = hit
            st[s]["history"].append({"ts": now.isoformat(timespec="seconds"), "from": st[s]["level"],
                                     "to": new, "why": why})
            st[s].update(level=new, since=now.isoformat(timespec="seconds"))
            moves.append({"strategy": s, "to": new, "why": why})
    save(st, path)
    return {s: st[s]["level"] for s in strategies}, moves


def progress(strategies: tuple, rows: list, now: datetime, path: str | None = None) -> dict:
    """Dashboard/status view: per strategy its level, since, scorecard and how close it is to the next level."""
    st = load(strategies, now, path)
    out = {}
    for s in strategies:
        card = predictions.scorecard(rows, s, since=st[s]["since"])
        ok, why = proven(card)
        parts = [min(1.0, card[k] / GATES[k]) for k in ("graded", "events", "signals", "signal_events")]
        out[s] = {"level": st[s]["level"], "since": st[s]["since"], "card": card, "proven": ok, "why": why,
                  "data_progress": round(sum(parts) / len(parts), 2), "history": st[s]["history"][-5:]}
    return out
