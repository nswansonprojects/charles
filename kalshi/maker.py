"""Resting (maker) orders on near-miss sets, plus Kalshi liquidity rewards — PAPER ONLY.

Taker bets (evaluate.py) buy every leg at the offer, which almost never pays. Here the bot
instead rests its own bids 1c above the best bid on each leg of a set whose legs, bought at
those bids, pay more than they cost after the (lower) maker fee.

Simulation rules (honest, from public data only):
- A resting bid fills only from real trades: our YES bid at b fills when a taker SOLD yes at
  b or lower (that seller would have hit our better bid first); same for NO. Fill size is
  capped by the traded size.
- A set is only "locked" once every leg is filled. If it isn't complete when the plan
  expires, the missing legs are bought at the offer when the whole set still pays at least
  what it cost; otherwise the filled legs are sold at the bid. That loss is the real risk
  of resting orders (getting filled on one side just before the price moves).
- Money for resting orders is held, as Kalshi holds collateral, and released on expiry.
- Rewards: Kalshi pays programs per market, scored every second by size (capped at the
  target) x discount^(cents behind the best bid). Each scan we estimate our share of the
  pool from the order book as it stands and accrue it for the time since the last scan.
  This is an estimate; Kalshi's exact scoring and payout timing differ.
"""
import math
from datetime import datetime, timedelta, timezone

import network

MAKER = {
    "max_plans": 8,           # resting sets at a time
    "max_legs": 4,            # sets with more legs rarely fill completely
    "min_edge_per_set": 0.01, # $ per set after maker fees, at our bid prices
    "plan_hours": 24,         # then complete or unwind
    "close_buffer_hours": 1,  # plans end this long before the market closes (books vanish at close)
    "min_hours_to_close": 6,  # no new plan on a market closing sooner than this
    "max_new_per_scan": 1,    # rest new plans one scan at a time, so expiries don't all land together
    "max_sets": 100,
    "max_plan_pct": 0.08,     # of account per plan
    "max_days": 120,          # resolution within this many days
    "min_price": 0.03,        # no penny legs: nothing to earn, slow to fill
}
MAKER_FEE = 0.0175
REWARD_SIDE_SHARE = 0.5      # assume a program's pool is split between the YES and NO books


def maker_fee(contracts: float, price: float) -> float:
    return math.ceil(round(MAKER_FEE * contracts * price * (1 - price) * 100, 6)) / 100


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def bid_price(m: dict, side: str) -> float | None:
    """Our bid: 1c above the best bid, but never at or through the offer."""
    best = _f(m.get(f"{side}_bid_dollars")) or 0.0
    ask = _f(m.get(f"{side}_ask_dollars"))
    if ask is None or not 0 < ask < 1:
        return None
    b = round(min(best + 0.01, ask - 0.01), 2)
    return b if b >= MAKER["min_price"] and b < ask else None


def plan_for(rel: dict, by_ticker: dict, equity: float, now: datetime) -> dict | None:
    """A resting plan for this relation, or None if it can't pay at our bid prices."""
    if len(rel["legs"]) > MAKER["max_legs"]:
        return None
    prices = []
    for l in rel["legs"]:
        b = bid_price(by_ticker[l["ticker"]], l["side"])
        if b is None:
            return None
        prices.append(b)
    per_set = sum(p + MAKER_FEE * p * (1 - p) for p in prices)
    edge = rel["payout"] - per_set
    if edge < MAKER["min_edge_per_set"]:
        return None
    close = _ts(rel["close_time"])
    if close - now < timedelta(hours=MAKER["min_hours_to_close"]):
        return None
    sets = int(min(MAKER["max_sets"], equity * MAKER["max_plan_pct"] // per_set))
    if sets < 1:
        return None
    held = round(sum(p * sets for p in prices), 2)
    return {
        "type": rel["type"], "event": rel["event"], "category": rel["category"],
        "title": rel["title"], "why": rel["why"], "payout": rel["payout"], "sets": sets,
        "close_time": rel["close_time"], "confidence": network.CONFIDENCE[rel["type"]],
        "edge_per_set": round(edge, 4), "held": held,
        "legs": [{"ticker": l["ticker"], "side": l["side"], "sub": l.get("sub", ""), "price": p,
                  "size": sets, "filled": 0.0, "fee": 0.0} for l, p in zip(rel["legs"], prices)],
        "placed": now.isoformat(timespec="seconds"),
        "expires": min(now + timedelta(hours=MAKER["plan_hours"]),
                       close - timedelta(hours=MAKER["close_buffer_hours"])).isoformat(timespec="seconds"),
        "last_check": now.isoformat(timespec="seconds"),
        "rewards": 0.0,
    }


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def ends_at(plan: dict) -> datetime:
    """When a plan must be completed or unwound: its expiry, or the close buffer if sooner
    (also covers plans saved before expiries were capped at the close)."""
    ends = [_ts(plan["expires"])] if "expires" in plan else []
    if "close_time" in plan:
        ends.append(_ts(plan["close_time"]) - timedelta(hours=MAKER["close_buffer_hours"]))
    return min(ends, default=datetime.max.replace(tzinfo=timezone.utc))


def apply_trades(leg: dict, trades: list[dict]) -> float:
    """Fill a resting bid from real trades (newest-first or any order). Returns contracts filled."""
    got = 0.0
    for t in trades:
        left = leg["size"] - leg["filled"]
        if left <= 1e-9:
            break
        # a taker on the other side sold our side to the book at this price
        if t.get("taker_side") == leg["side"]:
            continue
        px = _f(t.get(f"{leg['side']}_price_dollars"))
        if px is None or px > leg["price"] + 1e-9:
            continue
        q = min(left, _f(t.get("count_fp")) or 0.0)
        leg["filled"] = round(leg["filled"] + q, 4)
        got += q
    if got:
        leg["fee"] = maker_fee(leg["filled"], leg["price"])
    return got


def reward_share(leg: dict, book: dict, program: dict) -> float:
    """Our estimated fraction of the program's per-side score for this resting bid."""
    df = program.get("discount_factor_bps", 5000) / 10000
    target = _f(program.get("target_size_fp")) or 1000.0
    bids = [(p, q) for p, q in book.get(leg["side"], []) if q > 0]
    best = max([p for p, _ in bids] + [leg["price"]])
    score = lambda p, q: min(q, target) * df ** round((best - p) * 100)
    left = leg["size"] - leg["filled"]
    if left <= 0:
        return 0.0
    ours = score(leg["price"], left)
    theirs = sum(score(p, q) for p, q in bids)
    return ours / (ours + theirs) if ours > 0 else 0.0


def reward_rate(program: dict) -> float:
    """$ per second the program pays across its period (period_reward is in centi-cents)."""
    try:
        start = datetime.fromisoformat(program["start_date"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(program["end_date"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return 0.0
    secs = (end - start).total_seconds()
    return program.get("period_reward", 0) / 10000 / secs if secs > 0 else 0.0


def accrue_rewards(plan: dict, books: dict, programs: dict, now: datetime) -> float:
    """Add rewards earned since the plan's last check; returns the amount added."""
    since = datetime.fromisoformat(plan["last_check"])
    secs = max(0.0, (min(now, ends_at(plan)) - since).total_seconds())
    total = 0.0
    for leg in plan["legs"]:
        prog = programs.get(leg["ticker"])
        if not prog or leg["ticker"] not in books:
            continue
        share = reward_share(leg, books[leg["ticker"]], prog)
        total += reward_rate(prog) * secs * share * REWARD_SIDE_SHARE
    plan["rewards"] = round(plan["rewards"] + total, 4)
    return total


def complete(plan: dict) -> bool:
    return all(l["filled"] >= l["size"] - 1e-9 for l in plan["legs"])


def finish_with_buys(plan: dict, buys: list) -> None:
    """Fill the missing contracts at the offer: legs get a blended price and the taker fee."""
    for i, need, ask, f in buys:
        l = plan["legs"][i]
        cost = l["filled"] * l["price"] + need * ask
        l["filled"] = l["size"]
        l["price"] = round(cost / l["size"], 4)
        l["fee"] = round(l["fee"] + f, 2)


def to_bet(plan: dict, now: datetime) -> dict:
    """A fully filled plan becomes an open bet held to settlement (ledger format)."""
    cost = sum(l["filled"] * l["price"] + l["fee"] for l in plan["legs"])
    return {
        "type": plan["type"], "event": plan["event"], "category": plan["category"],
        "title": plan["title"], "why": plan["why"], "payout": plan["payout"], "sets": plan["sets"],
        "cost": round(cost, 2), "profit": round(plan["payout"] * plan["sets"] - cost, 2),
        "confidence": plan["confidence"], "close_time": plan["close_time"], "via": "maker",
        "expected_profit": round(plan["confidence"] * (plan["payout"] * plan["sets"] - cost)
                                 - (1 - plan["confidence"]) * cost, 2),
        "annual_return_pct": None,
        "legs": [{"ticker": l["ticker"], "side": l["side"], "contracts": l["filled"],
                  "avg_price": l["price"], "fee": l["fee"]} for l in plan["legs"]],
        "opened": now.isoformat(timespec="seconds"),
        "id": f"{now:%Y%m%d%H%M%S}-m-{plan['event']}",
    }


def resolve_expired(plan: dict, by_ticker: dict) -> dict:
    """What to do with an expired, incomplete plan, priced at the current book.
    Returns {'action': 'cancel'|'complete'|'unwind', 'cash_delta': ..., 'pnl': ...}
    where cash_delta is what comes back to cash beyond releasing the held money."""
    filled_cost = sum(l["filled"] * l["price"] + l["fee"] for l in plan["legs"])
    if all(l["filled"] <= 1e-9 for l in plan["legs"]):
        return {"action": "cancel", "extra_cost": 0.0, "proceeds": 0.0, "pnl": 0.0}
    # complete: buy each leg's missing contracts at the offer (taker)
    extra, buys = 0.0, []
    ok = True
    for i, l in enumerate(plan["legs"]):
        need = l["size"] - l["filled"]
        if need <= 1e-9:
            continue
        ask = network.top_ask(by_ticker.get(l["ticker"], {}), l["side"])
        if ask is None:
            ok = False
            break
        f = network.fee(need, ask)
        extra += need * ask + f
        buys.append((i, need, ask, f))
    if ok and filled_cost + extra <= plan["payout"] * plan["sets"]:
        return {"action": "complete", "extra_cost": round(extra, 2), "proceeds": 0.0, "buys": buys,
                "pnl": round(plan["payout"] * plan["sets"] - filled_cost - extra, 2)}
    # unwind: sell what filled at the bid (taker fee)
    proceeds = 0.0
    for l in plan["legs"]:
        if l["filled"] > 1e-9:
            bid = _f(by_ticker.get(l["ticker"], {}).get(f"{l['side']}_bid_dollars")) or 0.0
            proceeds += l["filled"] * bid - (network.fee(l["filled"], bid) if bid > 0 else 0)
    return {"action": "unwind", "extra_cost": 0.0, "proceeds": round(proceeds, 2),
            "pnl": round(proceeds - filled_cost, 2)}
