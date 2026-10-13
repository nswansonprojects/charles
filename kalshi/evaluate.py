"""Price relations against real order books, rate them, and pick the network of bets.

Pure functions: order books come in as data. Nothing here places orders.
"""
import math
from datetime import datetime, timezone

from network import CONFIDENCE

# Network rules (paper book). Change here, nowhere else.
RULES = {
    "min_edge_per_set": 0.01,     # $ guaranteed profit per contract set after fees
    "min_profit": 0.25,           # $ guaranteed profit per bet
    "min_annual_return": 0.15,    # profit/cost scaled to a year
    "max_days": 400,              # don't lock money up longer than this
    "max_bet_pct": 0.10,          # of book equity per bet
    "max_event_pct": 0.20,        # of book equity per event
    "cash_reserve_pct": 0.10,     # always keep this much cash
    "max_sets": 1000,             # contract sets per bet
}


def asks(book: dict, side: str) -> list[tuple[float, float]]:
    """Offers to sell `side`, cheapest first. A YES offer is a NO bid at 1-p."""
    other = book.get("no" if side == "yes" else "yes", [])
    return sorted(((round(1 - p, 4), q) for p, q in other if 0 < p < 1), key=lambda x: x[0])


def buy_cost(levels, n: float):
    """(cost, fee, avg price) to buy n contracts walking the offers; None if too thin."""
    left, cost, fees = n, 0.0, 0.0
    for p, q in levels:
        take = min(left, q)
        cost += take * p
        fees += 0.07 * take * p * (1 - p)
        left -= take
        if left <= 1e-9:
            return cost, math.ceil(round(fees * 100, 6)) / 100, cost / n
    return None


def days_left(close_time: str, now: datetime) -> float:
    try:
        t = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
    except ValueError:
        return 9999
    return max((t - now).total_seconds() / 86400, 0.5)


def price(rel: dict, books: dict, budget: float, now: datetime | None = None) -> dict | None:
    """Best whole number of contract sets for this relation within `budget`."""
    now = now or datetime.now(timezone.utc)
    ladders = [asks(books[l["ticker"]], l["side"]) for l in rel["legs"]]
    if any(not lv for lv in ladders):
        return None
    depth = min(sum(q for _, q in lv) for lv in ladders)
    best = None
    n = 1
    while n <= min(int(depth), RULES["max_sets"]):
        legs = [buy_cost(lv, n) for lv in ladders]
        if any(x is None for x in legs):
            break
        cost = sum(c + f for c, f, _ in legs)
        if cost > budget:
            break
        profit = rel["payout"] * n - cost
        if profit / n < RULES["min_edge_per_set"]:
            break  # deeper levels only get worse
        if best is None or profit > best["profit"]:
            best = {"sets": n, "cost": round(cost, 2), "profit": round(profit, 2),
                    "fills": [{**l, "contracts": n, "avg_price": round(a, 4), "fee": f}
                              for l, (_, f, a) in zip(rel["legs"], legs)]}
        n += 1 if n < 20 else max(1, n // 10)
    if not best:
        return None
    d = days_left(rel["close_time"], now)
    conf = CONFIDENCE[rel["type"]]
    ret = best["profit"] / best["cost"]
    return {**rel, **best, "days": round(d, 1),
            "return_pct": round(ret * 100, 2),
            "annual_return_pct": round(ret * 365 / d * 100, 1),
            "confidence": conf,
            # if the rules relationship fails, assume the whole stake is lost
            "expected_profit": round(conf * best["profit"] - (1 - conf) * best["cost"], 2),
            "worst_case": -best["cost"]}


def passes(bet: dict) -> str | None:
    """None if the bet clears the rules, else the reason it doesn't."""
    if bet["days"] > RULES["max_days"]:
        return f"locks money {bet['days']:.0f} days"
    if bet["profit"] < RULES["min_profit"]:
        return f"profit ${bet['profit']:.2f} too small"
    if bet["annual_return_pct"] < RULES["min_annual_return"] * 100:
        return f"{bet['annual_return_pct']}%/yr too low"
    if bet["expected_profit"] <= 0:
        return "negative after confidence"
    return None


def choose(candidates: list[dict], books: dict, equity: float, cash: float,
           held_events: dict | None = None, held_tickers: set | None = None,
           now: datetime | None = None):
    """Evaluate the whole network, then pick bets: best yearly return first,
    one bet per market, caps per bet and per event, cash reserve kept.
    Returns (chosen, rejected) where rejected items carry a 'reason'."""
    held_events = dict(held_events or {})
    priced, rejected = [], []
    per_bet = equity * RULES["max_bet_pct"]
    for rel in candidates:
        bet = price(rel, books, per_bet, now)
        if bet is None:
            rejected.append({**rel, "reason": "no profitable size at real depth"})
            continue
        why = passes(bet)
        (rejected.append({**bet, "reason": why}) if why else priced.append(bet))

    priced.sort(key=lambda b: (b["annual_return_pct"] * b["confidence"], b["profit"]), reverse=True)
    chosen, used = [], set(held_tickers or ())
    spendable = cash - equity * RULES["cash_reserve_pct"]
    for bet in priced:
        tickers = {l["ticker"] for l in bet["legs"]}
        ev_room = equity * RULES["max_event_pct"] - held_events.get(bet["event"], 0)
        if tickers & used:
            rejected.append({**bet, "reason": "shares a market with a held or better bet"})
        elif bet["cost"] > spendable:
            rejected.append({**bet, "reason": "not enough cash"})
        elif bet["cost"] > ev_room:
            rejected.append({**bet, "reason": "event limit reached"})
        else:
            chosen.append(bet)
            used |= tickers
            spendable -= bet["cost"]
            held_events[bet["event"]] = held_events.get(bet["event"], 0) + bet["cost"]
    return chosen, rejected
