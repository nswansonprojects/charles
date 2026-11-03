"""Betting book: directional PAPER bets where a strategy's probability (strategies.py) beats the
Kalshi price by more than fees. Separate $1,000 ledger from the arbitrage scanner.

Each scan: grade settled predictions, log every strategy's predictions (predictions.py), then for each
book: settle finished bets and sell bets the market has caught up with. Charles's own book buys the
best new edges at real order-book prices, sized by a fraction of the Kelly stake; Evil Charles's own book
takes the other side of exactly those bets; the account copies the new bets of the bot the switch
selects. Copies are scaled to each book's money. The levels (gates.py) are off during the side-by-side
test. Never sends real orders.
"""
import copy
import json
import logging
import math
import os
from datetime import datetime, timedelta, timezone

import evaluate
import forecasts
import gates
import mode
import kalshi_api
import predictions
import strategies

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
LEDGER = os.path.join(DATA, "bets.json")
EQUITY = os.path.join(DATA, "bets_equity.jsonl")
LATEST = os.path.join(DATA, "bets_latest.json")
# Charles and Evil Charles each keep their own practice book and bet on every scan, whichever one is selected (owner
# 2026-11-03), so each has a record of its own and the two are never mixed. The account above (LEDGER, what the dashboard
# shows) copies the bets of the one the switch selects (mode.py), and each of its equity points says which one that was.
BOTS = ("charles", "evil")
BOOKS = os.path.join(DATA, "bots")
# The side-by-side test (owner 2026-11-03): no gates (gates.ON) and no tuning until Claude's review on this day (9:15 AM ET)
# compares the two bots and says what should change for each.
TEST_REVIEW = "2026-11-30"


def bot_paths(bot: str) -> tuple[str, str]:
    """(book, equity log) of one bot's own book. Looked up at call time, so tests can point BOOKS elsewhere."""
    return os.path.join(BOOKS, f"{bot}.json"), os.path.join(BOOKS, f"{bot}_equity.jsonl")
TUNING = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tuning.json")
START_CASH = 1000.0

# Betting rules (paper book). Change here, nowhere else.
RULES = {
    "kelly_fraction": 0.25,       # bet a quarter of the Kelly stake: models are rough
    "max_bet_pct": 0.03,          # of book equity per bet
    "max_event_pct": 0.06,        # of book equity per event (one game, one city-day, one CPI release)
    # Strategies allowed only one bet per event. A game's two markets ("A wins" / "B wins") are one
    # outcome: NO on A and YES on B is the same bet twice (2026-10-13: two NBA games lost $110 that way).
    "one_bet_per_event": ("sports",),
    # Walk the order book at most this far above the best offer: in thin books the deeper offers are
    # often stale (2026-10-14: an Oct CPI bet averaged 45c with the best offer at 22c).
    "max_walk": 0.05,
    # Paper fills take only this share of each displayed offer: a real order competes with other
    # buyers and resting offers get pulled, so assuming the whole book overstates what we'd get.
    "fill_share": 0.5,
    "strategy_cap_pct": {"weather": 0.30, "sports": 0.30, "econ": 0.20, "longshot": 0.30},
    "min_edge": {"weather": 0.07, "sports": 0.04, "econ": 0.06, "longshot": 0.015},  # $/contract after fees
    "price_range": (0.05, 0.97),  # don't buy below/above these
    # Owner's rule (not tuned): a win must pay at least this multiple of the stake after fees.
    # 2026-10-12 first 2.0 (buys <=~33c), then 0.5 the same day: risk $20 to win $10+, buys <=~65c.
    # Settled bets bought at 50-67c had done best (3 of 4 won); those under 34c went 0 for 4.
    # 2026-10-15 owner raised it to 1.0: a win must at least match the stake, so buys <=~49c.
    "min_win_ratio": 1.0,
    # Owner 2026-11-03: every bet, Evil Charles's flips and the account's copies too, must stand to win at least half its
    # stake after fees (cost incl. fee <= ~67c a contract). Flips and copies skip min_win_ratio, so this is their only bar.
    "min_payout_any": 0.5,
    "min_expected_profit": 0.10,  # $ per bet
    "cash_reserve_pct": 0.10,
    "max_books_per_strategy": 15, # order books fetched per scan, best edges first
    "max_new_per_scan": 12,
    "max_new_per_strategy": 3,    # per scan, so one strategy's edges can't fill the book at once
    # Sell when the bid beats our fair value by this much after fees. 0.02 -> 0.01 on 2026-10-15: one of
    # 83 bets had ever been sold; at fair value holding adds risk, not expected profit.
    "exit_margin": 0.01,
    # How much to trust each model over the market's own price: fair = w x model + (1-w) x market mid.
    # Weather and inflation disagree with liquid markets by a lot, and the market may know things the
    # model doesn't. Raise w only when the scorecard shows the model beating the market (Brier).
    "trust_model": {"weather": 0.5, "sports": 1.0, "econ": 0.5, "longshot": 1.0},
}
STRATEGIES = ("weather", "sports", "econ", "longshot")
DEFAULT_RULES = copy.deepcopy(RULES)

# What the daily review (ops/run-review) may tune in tuning.json, and how far. Values outside
# min/max are clamped; max_step is the most it may move one setting per week (its prompt enforces that).
# A strategy cap of 0 pauses that strategy (no new bets; open ones still settle or get sold).
LIMITS = {
    "trust_model": {"min": 0.0, "max": 1.0, "max_step": 0.25},
    "min_edge": {"min": 0.01, "max": 0.20, "max_step": 0.02},
    "strategy_cap_pct": {"min": 0.0, "max": 0.40, "max_step": 0.10},
    "kelly_fraction": {"min": 0.10, "max": 0.35, "max_step": 0.05},
    "max_bet_pct": {"min": 0.01, "max": 0.04, "max_step": 0.01},
}


def _clamp(v, lim: dict) -> float:
    return round(min(lim["max"], max(lim["min"], float(v))), 4)


def apply_tuning(path: str | None = None) -> dict:
    """Rebuild RULES from the defaults above plus tuning.json "settings" (clamped to LIMITS).
    Unknown keys and bad values are ignored, so a broken file can only fall back to the defaults."""
    RULES.clear()
    RULES.update(copy.deepcopy(DEFAULT_RULES))
    try:
        with open(path or TUNING) as f:
            settings = json.load(f).get("settings", {})
    except (FileNotFoundError, ValueError, AttributeError):
        return RULES
    for key, val in settings.items():
        lim = LIMITS.get(key)
        if not lim:
            continue
        try:
            if isinstance(DEFAULT_RULES[key], dict):
                for s, v in (val or {}).items():
                    if s in DEFAULT_RULES[key]:
                        RULES[key][s] = _clamp(v, lim)
            else:
                RULES[key] = _clamp(val, lim)
        except (TypeError, ValueError, AttributeError):
            log.warning(f"tuning.json: bad value for {key}, using the default")
    return RULES
log = logging.getLogger("kalshi-net")


def load(path: str | None = None) -> dict:
    path = path or LEDGER  # looked up at call time, so tests can point LEDGER elsewhere
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"start_cash": START_CASH, "cash": START_CASH, "open": [], "closed": []}


def save(led: dict, path: str | None = None):
    path = path or LEDGER
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(led, f, indent=1)
    os.replace(path + ".tmp", path)


def equity(led: dict) -> float:
    """Cash plus open bets at cost (the mark-to-market view is in mark())."""
    return round(led["cash"] + sum(b["cost"] for b in led["open"]), 2)


def fee(n: float, p: float) -> float:
    """Kalshi taker fee for one order: 7% x contracts x p x (1-p), rounded up to the cent."""
    return math.ceil(round(0.07 * n * p * (1 - p) * 100, 6)) / 100


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def exposure_key(pick: dict) -> str:
    """What one outcome decides. Every CPI series (headline, core, monthly, yearly) settles on the same
    BLS release, so one month's CPI markets share one key and one max_event_pct cap (2026-10-14: six
    September bets on the same side lost $146 together). Anything else: its Kalshi event."""
    ev = pick.get("event", "")
    if pick.get("strategy") == "econ" and ev.split("-")[0] in strategies.ECON and "-" in ev:
        return "CPI-" + ev.rsplit("-", 1)[1]
    return ev


def blend(picks: list, by_ticker: dict) -> list:
    """Keep each model's own number as model_yes; fair_yes becomes the trusted blend with the market."""
    out = []
    for p in picks:
        m = by_ticker.get(p["ticker"])
        if m is None:
            continue
        w = RULES["trust_model"][p["strategy"]]
        mid = (_f(m.get("yes_bid_dollars")) + _f(m.get("yes_ask_dollars"))) / 2
        out.append({**p, "model_yes": p["fair_yes"], "fair_yes": round(w * p["fair_yes"] + (1 - w) * mid, 4)})
    return out


def pays_enough(p: float) -> bool:
    """A contract bought at p (plus its fee) wins at least min_win_ratio of what it cost."""
    return (1 - p - fee(1, p) / 2) >= RULES["min_win_ratio"] * (p + fee(1, p) / 2)


def pays_half(fill: dict) -> bool:
    """A filled bet (cost includes the fee) wins at least min_payout_any of its cost if it's right."""
    return fill["contracts"] - fill["cost"] >= RULES["min_payout_any"] * fill["cost"] - 1e-9


def quick_edge(pick: dict, m: dict):
    """(side, edge) at the top-of-book offer from market data, before fetching the book."""
    best = None
    for side, fair in (("yes", pick["fair_yes"]), ("no", 1 - pick["fair_yes"])):
        ask = _f(m.get(f"{side}_ask_dollars"))
        if RULES["price_range"][0] <= ask <= RULES["price_range"][1] and pays_enough(ask):
            e = fair - ask - fee(1, ask) / 2  # rough per-contract fee on a decent-size order
            if best is None or e > best[1]:
                best = (side, round(e, 4))
    return best


def size(pick: dict, side: str, book: dict, budget: float, bank: float) -> dict | None:
    """Contracts to buy walking the offers while each level still clears min_edge, capped by a
    fraction of the Kelly stake and `budget`. Returns the fill or None."""
    fair = pick["fair_yes"] if side == "yes" else 1 - pick["fair_yes"]
    need = RULES["min_edge"][pick["strategy"]]
    levels = [(p, q) for p, q in evaluate.asks(book, side)
              if RULES["price_range"][0] <= p <= RULES["price_range"][1] and pays_enough(p) and fair - p - fee(1, p) / 2 >= need]
    if not levels:
        return None
    top = levels[0][0]
    levels = [(p, q) for p, q in levels if p <= top + RULES["max_walk"] + 1e-9]
    kelly = (fair - top) / (1 - top)
    stake = min(kelly * RULES["kelly_fraction"] * bank, budget)
    n, cost = 0, 0.0
    for p, q in levels:
        take = int(min(q * RULES["fill_share"], (stake - cost) / p))
        if take <= 0:
            break
        n += take
        cost += take * p
    while n > 0:
        f = fee(n, cost / n)
        if cost + f <= budget and fair - (cost + f) / n >= need:
            break
        cost -= cost / n  # drop one contract at the average price and retry
        n -= 1
    if n <= 0:
        return None
    f = fee(n, cost / n)
    avg = cost / n
    exp = n * fair - cost - f
    if (exp < RULES["min_expected_profit"] or n - cost - f < RULES["min_win_ratio"] * (cost + f)
            or n - cost - f < RULES["min_payout_any"] * (cost + f)):
        return None
    return {"side": side, "contracts": n, "avg_price": round(avg, 4), "fee": f, "cost": round(cost + f, 2),
            "fair": round(fair, 4), "edge": round(fair - (cost + f) / n, 4), "expected_profit": round(exp, 2)}


def take(side: str, book: dict, dollars: float, cap: float | None = None) -> dict | None:
    """Buy `side` for `dollars` (fee included), walking its offers (price_range, max_walk, fill_share as usual) with no edge
    or payout checks: the bet was already decided. At most `dollars`; with `cap`, the whole number of contracts whose cost is
    closest to `dollars` without passing `cap` (a copied bet costs the same as the one it copies, owner 2026-11-03).
    Returns {side, contracts, avg_price, fee, cost} or None."""
    levels = [(p, q) for p, q in evaluate.asks(book, side) if RULES["price_range"][0] <= p <= RULES["price_range"][1]]
    if not levels:
        return None
    top, limit = levels[0][0], max(dollars, cap if cap is not None else dollars)
    prices = []                                    # the price of each contract on offer, cheapest first
    for p, q in levels:
        if p > top + RULES["max_walk"] + 1e-9 or len(prices) * p > limit:
            break
        prices += [p] * int(min(q * RULES["fill_share"], limit / p + 1))

    def total(n):
        c = sum(prices[:n])
        return round(c + fee(n, c / n), 2), c

    n = 0
    while n < len(prices) and total(n + 1)[0] <= dollars:
        n += 1
    if cap is not None and n < len(prices):
        over = total(n + 1)[0]
        if over <= cap and (n == 0 or over - dollars < dollars - total(n)[0]):
            n += 1
    if n <= 0:
        return None
    cost = sum(prices[:n])
    f = fee(n, cost / n)
    return {"side": side, "contracts": n, "avg_price": round(cost / n, 4), "fee": f, "cost": round(cost + f, 2)}


def take_exact(side: str, book: dict, dollars: float) -> dict | None:
    """Buy `side` for exactly `dollars` (fee included), in part-contracts (owner 2026-11-03: a copied bet costs exactly what
    the bet it copies cost, so both bots' values match to the cent; these are practice books). Walks the offers like take().
    Returns {side, contracts, avg_price, fee, cost} or None."""
    levels = [(p, q) for p, q in evaluate.asks(book, side) if RULES["price_range"][0] <= p <= RULES["price_range"][1]]
    dollars = round(dollars, 2)
    if not levels or dollars <= 0:
        return None
    top = levels[0][0]

    def buy(stake):                                # part-contracts that `stake` buys, walking the offers; None if too thin
        n, left = 0.0, stake
        for p, q in levels:
            if p > top + RULES["max_walk"] + 1e-9:
                break
            k = min(q * RULES["fill_share"], left / p)
            n, left = n + k, left - k * p
            if left <= 1e-9:
                return n
        return None

    f = 0.0
    for _ in range(4):                             # the fee depends on the size, and the size on what the fee leaves
        n = buy(dollars - f)
        if not n:
            return None
        f = fee(n, (dollars - f) / n)
    n = buy(dollars - f)
    if not n or dollars - f <= 0:
        return None
    return {"side": side, "contracts": round(n, 4), "avg_price": round((dollars - f) / n, 4), "fee": f, "cost": dollars}


def flip(fill: dict, book: dict, cap: float | None = None) -> dict | None:
    """Evil Charles: the same dollars as Charles's `fill`, on the other side. Skips the edge and min_win_ratio checks: by
    Charles's own numbers this bet is a loser, and that's the point. `fair` stays Charles's view of that side."""
    got = take_exact("no" if fill["side"] == "yes" else "yes", book, fill["cost"])
    if not got:
        return None
    fair = 1 - fill["fair"]
    return {**got, "fair": round(fair, 4), "edge": round(fair - got["cost"] / got["contracts"], 4),
            "expected_profit": round(got["contracts"] * fair - got["cost"], 2), "evil": True, "charles_side": fill["side"]}


def place(led: dict, pick: dict, fill: dict, m: dict, now: datetime, level: str = "normal", bot: str = "charles") -> dict:
    """`bot`: who picked the bet ("charles" or "evil"), so the account's bets can be told apart."""
    yb, ya = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
    rec = {**{k: pick[k] for k in ("strategy", "ticker", "event", "category", "title", "sub", "source")}, "level": level, "bot": bot,
           **fill, "fair_yes": pick["fair_yes"], "model_yes": pick.get("model_yes", pick["fair_yes"]), "market_yes": round((yb + ya) / 2, 4),
           "close_time": m.get("close_time", ""), "opened": now.isoformat(timespec="seconds"),
           "id": f"{now:%Y%m%d%H%M%S}-{len(led['open']) + len(led['closed'])}"}
    led["cash"] = round(led["cash"] - fill["cost"], 2)
    led["open"].append(rec)
    return rec


def _close(led: dict, bet: dict, received: float, how: str, now: datetime, result: str | None = None):
    bet.update({"received": round(received, 2), "pnl": round(received - bet["cost"], 2), "how": how,
                "result": result, "closed": now.isoformat(timespec="seconds")})
    led["cash"] = round(led["cash"] + received, 2)
    led["open"].remove(bet)
    led["closed"].append(bet)


def settle(led: dict, get_market, now: datetime) -> list[dict]:
    """Close bets whose market resolved; a voided market refunds the cost."""
    done = []
    for bet in list(led["open"]):
        m = get_market(bet["ticker"])
        if m.get("status") not in ("settled", "finalized"):
            continue
        res = m.get("result")
        if res in ("yes", "no"):
            _close(led, bet, bet["contracts"] * (res == bet["side"]), "won" if res == bet["side"] else "lost", now, res)
        else:
            _close(led, bet, bet["cost"], "void", now)
        done.append(bet)
    return done


def split(led: dict, bet: dict, k: int) -> dict:
    """Carve k contracts off an open bet into their own open bet (cost and fee shared pro rata)."""
    n, share = bet["contracts"], k / bet["contracts"]
    part = {**bet, "contracts": k, "cost": round(bet["cost"] * share, 2), "fee": round(bet["fee"] * share, 2),
            "expected_profit": round(bet["expected_profit"] * share, 2), "id": f"{bet['id']}-p{n - k}"}
    bet.update(contracts=n - k, cost=round(bet["cost"] - part["cost"], 2), fee=round(bet["fee"] - part["fee"], 2),
               expected_profit=round(bet["expected_profit"] - part["expected_profit"], 2))
    led["open"].append(part)
    return part


def exits(led: dict, fair_now: dict, by_ticker: dict, now: datetime) -> list[dict]:
    """Sell a bet when the best bid, after the fee, beats today's fair value by exit_margin:
    the price has caught up with (or gone past) our model, so holding adds nothing. If the best
    bid can't take the whole bet (fill_share of its size), sell the part it can and keep the rest.
    Longshot and Evil Charles bets hold to settlement (an evil bet is already past Charles's fair value)."""
    sold = []
    for bet in list(led["open"]):
        fy = fair_now.get(bet["ticker"])
        m = by_ticker.get(bet["ticker"])
        if bet["strategy"] == "longshot" or bet.get("evil") or fy is None or m is None:
            continue
        fair = fy if bet["side"] == "yes" else 1 - fy
        bid, depth = _f(m.get(f"{bet['side']}_bid_dollars")), _f(m.get(f"{bet['side']}_bid_size_fp"))
        k = min(bet["contracts"], int(depth * RULES["fill_share"]))
        if bid <= 0 or k < 1:
            continue
        net = k * bid - fee(k, bid)
        if net / k >= fair + RULES["exit_margin"]:
            if k < bet["contracts"]:
                bet = split(led, bet, k)
            bet["exit_bid"], bet["fair_at_exit"] = bid, round(fair, 4)
            _close(led, bet, net, "sold", now)
            sold.append(bet)
    return sold


def mark(led: dict, by_ticker: dict, fair_now: dict, now: datetime) -> dict:
    """The book's value (owner 2026-11-03): its cash, so a bet takes its whole cost off the value when it's placed and puts
    its payout back when it settles or sells. positions_value: the open bets at today's best bid (what selling would fetch),
    for reference only."""
    val = 0.0
    for b in led["open"]:
        m = by_ticker.get(b["ticker"])
        if m is not None:
            b["bid"] = _f(m.get(f"{b['side']}_bid_dollars"))
        if b["ticker"] in fair_now:
            b["fair_now"] = round(fair_now[b["ticker"]] if b["side"] == "yes" else 1 - fair_now[b["ticker"]], 4)
        val += b["contracts"] * b.get("bid", b["avg_price"])
    return {"ts": now.isoformat(timespec="seconds"), "cash": led["cash"], "positions_value": round(val, 2),
            "account_value": led["cash"], "open_bets": len(led["open"])}


def _brier(bets: list, outcomes: list, key: str):
    if not outcomes:
        return None
    return round(sum((b.get(key, b["fair_yes"]) - v) ** 2 for b, v in zip(bets, outcomes)) / len(outcomes), 4)


def stats(led: dict) -> dict:
    """Per strategy: bets, wins, P&L, and Brier scores (lower is better) of the raw model, the
    blend we bet on, and the market price at entry. Model below market = the model knows something."""
    out = {}
    for s in STRATEGIES:
        closed = [b for b in led["closed"] if b["strategy"] == s]
        opened = [b for b in led["open"] if b["strategy"] == s]
        graded = [b for b in closed if b.get("result") in ("yes", "no")]
        y = [1.0 if b["result"] == "yes" else 0.0 for b in graded]
        out[s] = {"open": len(opened), "at_risk": round(sum(b["cost"] for b in opened), 2),
                  "closed": len(closed), "won": sum(b["how"] == "won" for b in closed),
                  "lost": sum(b["how"] == "lost" for b in closed), "sold": sum(b["how"] == "sold" for b in closed),
                  "pnl": round(sum(b["pnl"] for b in closed), 2), "graded": len(graded),
                  **{f"brier_{k}": _brier(graded, y, key) for k, key in
                     (("model", "model_yes"), ("blend", "fair_yes"), ("market", "market_yes"))}}
    return out


def _optional(fetch):
    """Extra data a strategy can do without: None (logged) if it fails to load."""
    try:
        return fetch()
    except Exception as e:
        log.warning(f"betting optional data: {e}")
        return None


def gather(events: list, now: datetime) -> tuple[list, dict]:
    """Picks from every strategy whose data loaded, plus per-strategy status."""
    picks, status = [], {}
    sources = (("weather", lambda: strategies.weather_picks(events, forecasts.weather(), forecasts.nws(), now,
                                                            _optional(forecasts.models))),
               ("sports", lambda: strategies.sports_picks(events, forecasts.sports(), now)),
               ("econ", lambda: strategies.econ_picks(events, forecasts.nowcasts())),
               ("longshot", lambda: strategies.longshot_picks(events, now)))
    for name, fn in sources:
        try:
            got = fn()
            picks += got
            status[name] = {"ok": True, "markets": len(got)}
        except Exception as e:
            log.warning(f"betting {name}: {e}")
            status[name] = {"ok": False, "error": str(e)[:200], "markets": 0}
    return picks, status


def signals_of(scored: list, by_ticker: dict) -> dict:
    """{ticker: shadow bet} for every pick the real rules would bet on now (edge >= min_edge at the best
    offer, price allowed, pays enough). Cost per contract includes the fee as on a 100-contract order.
    opp_cost: the same for the other side, so the dashboard can show how Evil Charles would have done."""
    out = {}
    for p in scored:
        if p["quick_edge"] < RULES["min_edge"][p["strategy"]]:
            continue
        ask = _f(by_ticker[p["ticker"]].get(f"{p['side']}_ask_dollars"))
        fair = p["fair_yes"] if p["side"] == "yes" else 1 - p["fair_yes"]
        out[p["ticker"]] = {"side": p["side"], "ask": ask, "cost": round(ask + fee(100, ask) / 100, 4),
                            "edge": p["quick_edge"], "fair": round(fair, 4)}
        opp = _f(by_ticker[p["ticker"]].get(f"{'no' if p['side'] == 'yes' else 'yes'}_ask_dollars"))
        if 0 < opp < 1:
            out[p["ticker"]]["opp_cost"] = round(opp + fee(100, opp) / 100, 4)
    return out


def bet(led: dict, scored: list, by_ticker: dict, levels: dict, book_of, now: datetime, name: str = "charles") -> list:
    """Charles's new bets: his best edges for every strategy allowed to bet, sized on this book's money and caps."""
    bank = equity(led)
    cands = []
    for s in STRATEGIES:
        if levels[s] == "predict":
            continue  # predictions only: it hasn't earned money yet
        cands += [p for p in scored if p["strategy"] == s
                  and p["quick_edge"] >= RULES["min_edge"][s]][:RULES["max_books_per_strategy"]]
    held = {b["ticker"] for b in led["open"]}
    by_event, by_strat, bets_in = {}, {}, {}
    for b in led["open"]:
        k = exposure_key(b)
        by_event[k] = by_event.get(k, 0) + b["cost"]
        bets_in[k] = bets_in.get(k, 0) + 1
        by_strat[b["strategy"]] = by_strat.get(b["strategy"], 0) + b["cost"]
    placed, new_by = [], {}
    for p in cands:
        if len(placed) >= RULES["max_new_per_scan"]:
            break
        if p["ticker"] in held or new_by.get(p["strategy"], 0) >= RULES["max_new_per_strategy"]:
            continue
        key = exposure_key(p)
        if p["strategy"] in RULES["one_bet_per_event"] and bets_in.get(key):
            continue
        lvl = levels[p["strategy"]]
        budget = min(bank * RULES["max_bet_pct"],
                     bank * RULES["max_event_pct"] - by_event.get(key, 0),
                     gates.cap_dollars(lvl, RULES["strategy_cap_pct"][p["strategy"]], bank) - by_strat.get(p["strategy"], 0),
                     led["cash"] - bank * RULES["cash_reserve_pct"])
        if lvl == "small":
            budget = min(budget, gates.GATES["small_max_bet"])
        if budget < 1:
            continue
        try:
            book = book_of(p["ticker"])
        except Exception as e:
            log.warning(f"betting book {p['ticker']}: {e}")
            continue
        fill = size(p, p["side"], book, budget, bank)
        if not fill:
            continue
        other = flip(fill, book)                   # owner 2026-11-03: both bots place the same bets, so Charles bets only
        if not other or not pays_half(other):      # where Evil Charles can take the other side under the payout floor
            why = "no offers" if not other else other["side"].upper() + " would win less than half its stake"
            log.info(f"skip [{name}] {p['ticker']}: Evil Charles couldn't take the other side ({why})")
            continue
        rec = place(led, p, fill, by_ticker[p["ticker"]], now, lvl, bot="charles")
        placed.append(rec)
        held.add(p["ticker"])
        new_by[p["strategy"]] = new_by.get(p["strategy"], 0) + 1
        by_event[key] = by_event.get(key, 0) + rec["cost"]
        bets_in[key] = bets_in.get(key, 0) + 1
        by_strat[p["strategy"]] = by_strat.get(p["strategy"], 0) + rec["cost"]
        log.info(f"BET [{name}] {rec['strategy']} {rec['side'].upper()} {rec['ticker']} x{rec['contracts']} @ {rec['avg_price']} "
                 f"(fair {rec['fair']}, edge {rec['edge']}) cost ${rec['cost']} — {rec['source']}")
    return placed


def copy_bets(led: dict, placed: list, src_bank: float, opposite: bool, by_ticker: dict, book_of, now: datetime,
              bot: str, name: str) -> list:
    """Another book's new bets of this scan, scaled to this book's money (the same share of it): on the other side for Evil
    Charles, who bets against exactly what Charles's book placed, or on the same side for the account, which copies the
    bot the switch selects. The source already decided each bet, so only the cash reserve applies here."""
    bank, out = equity(led), []
    held = {b["ticker"] for b in led["open"]}
    for src in placed:
        if src["ticker"] in held:
            continue
        room = led["cash"] - bank * RULES["cash_reserve_pct"]
        dollars = min(src["cost"] * bank / src_bank if src_bank > 0 else 0.0, room)
        if dollars < 1:
            continue
        try:
            book = book_of(src["ticker"])
        except Exception as e:
            log.warning(f"betting book {src['ticker']}: {e}")
            continue
        if opposite:
            fill = flip({"side": src["side"], "cost": dollars, "fair": src["fair"]}, book, room)
        else:
            got = take_exact(src["side"], book, dollars)
            fill = got and {**got, "fair": src["fair"], "edge": round(src["fair"] - got["cost"] / got["contracts"], 4),
                            "expected_profit": round(got["contracts"] * src["fair"] - got["cost"], 2),
                            **({"evil": True, "charles_side": src["charles_side"]} if src.get("evil") else {})}
        if not fill or not pays_half(fill):
            if fill:
                log.info(f"skip [{name}] {src['ticker']}: {fill['side'].upper()} at {fill['cost'] / fill['contracts']:.2f} would win "
                         f"less than {RULES['min_payout_any']:.0%} of its stake")
            continue
        pick = {k: src[k] for k in ("strategy", "ticker", "event", "category", "title", "sub", "source", "fair_yes")}
        pick["model_yes"] = src.get("model_yes", src["fair_yes"])
        rec = place(led, pick, fill, by_ticker.get(src["ticker"]) or {"close_time": src.get("close_time", "")}, now,
                    src.get("level", "normal"), bot=bot)
        held.add(src["ticker"])
        out.append(rec)
        log.info(f"{'EVIL BET' if rec.get('evil') else 'BET'} [{name}] {rec['strategy']} {rec['side'].upper()} {rec['ticker']} "
                 f"x{rec['contracts']} @ {rec['avg_price']} cost ${rec['cost']} (copies {src['bot']}'s ${src['cost']})")
    return out


def run_book(name: str, path: str, eq_path: str, new_bets, by_ticker: dict, fair_now: dict, market_of, now: datetime,
             tag: str | None = None) -> dict:
    """One book's part of a scan: settle what finished, sell what the market caught up with, place `new_bets(led)`, then save
    the book and add its equity point (`tag` on it: which bot the account was following). `bank`: its money before betting."""
    led = load(path)
    if name in BOTS:
        led.setdefault("started", now.isoformat(timespec="seconds"))
    settled = []
    try:
        settled = settle(led, market_of, now)
    except Exception as e:
        log.warning(f"betting settle [{name}]: {e}")
    sold = exits(led, fair_now, by_ticker, now)
    bank = equity(led)
    placed = new_bets(led)
    for b in sold:
        log.info(f"SOLD [{name}] {b['strategy']} {b['ticker']} @ {b['exit_bid']}: pnl ${b['pnl']:+.2f}")
    for b in settled:
        log.info(f"SETTLED [{name}] {b['strategy']} {b['ticker']} {b['how']}: pnl ${b['pnl']:+.2f}")
    acct = mark(led, by_ticker, fair_now, now)
    if tag:
        acct["mode"] = tag
    paid = {}                                      # whose bets paid out this scan: the line's rise is drawn in that bot's color
    for b in settled + sold:
        if b.get("received", 0) > 0:
            who = b.get("bot") or name
            paid[who] = paid.get(who, 0) + b["received"]
    if paid:
        acct["by"] = max(paid, key=paid.get)
    save(led, path)
    with open(eq_path, "a") as f:
        if not f.tell():                           # a new book: its line starts at its starting money, a second before its first bets
            start = {"ts": (now - timedelta(seconds=1)).isoformat(timespec="seconds"), "cash": led.get("start_cash", START_CASH),
                     "positions_value": 0.0, "account_value": led.get("start_cash", START_CASH), "open_bets": 0}
            f.write(json.dumps({**start, **({"mode": tag} if tag else {})}) + "\n")
        f.write(json.dumps(acct) + "\n")
    return {"led": led, "account": acct, "placed": placed, "sold": sold, "settled": settled, "bank": bank}


def run(events: list, by_ticker: dict, now: datetime | None = None, get_book=None, get_market=None,
        get_markets=None) -> dict:
    now = now or datetime.now(timezone.utc)
    apply_tuning()
    selected = "evil" if mode.is_evil() else "charles"
    get_book = get_book or kalshi_api.orderbook
    get_market = get_market or kalshi_api.market
    get_markets = get_markets or kalshi_api.markets
    books_seen, markets_seen = {}, {}

    def book_of(t):                     # one fetch per market per scan, however many books bet on it
        if t not in books_seen:
            books_seen[t] = get_book(t)
        return books_seen[t]

    def market_of(t):
        if by_ticker.get(t):
            return by_ticker[t]
        if t not in markets_seen:
            markets_seen[t] = get_market(t)
        return markets_seen[t]

    preds = predictions.load()
    try:
        predictions.grade(preds, by_ticker, get_markets, now)
    except Exception as e:
        log.warning(f"predictions grade: {e}")

    picks, status = gather(events, now)
    picks = blend(picks, by_ticker)
    fair_now = {p["ticker"]: p["fair_yes"] for p in picks}

    scored = []
    for p in picks:
        m = by_ticker.get(p["ticker"])
        q = quick_edge(p, m) if m else None
        if q:
            scored.append({**p, "side": q[0], "quick_edge": q[1]})
    scored.sort(key=lambda p: p["quick_edge"], reverse=True)
    predictions.record(preds, picks, signals_of(scored, by_ticker), {p["ticker"]: exposure_key(p) for p in picks},
                       by_ticker, now)
    predictions.save(preds)

    # Levels (gates.py) make a strategy prove itself before it bets. Off while Charles and Evil Charles are tested side by
    # side (owner 2026-11-03: no waiting), so every strategy bets at the normal rules from the first scan, in every book.
    if gates.ON:
        acct_led = load()
        levels, moves = gates.update(STRATEGIES, acct_led, predictions.load_graded(), RULES["strategy_cap_pct"],
                                     equity(acct_led), now)
        for mv in moves:
            log.info(f"LEVEL {mv['strategy']} -> {mv['to']}: {mv['why']}")
    else:
        levels, moves = {s: "normal" for s in STRATEGIES}, []

    # Each bot's own book, whichever one is selected (owner 2026-11-03): Charles picks; Evil Charles bets against exactly
    # what Charles's book placed. Then the account copies the selected bot's new bets.
    books = {"charles": run_book("charles", *bot_paths("charles"),
                                 lambda led: bet(led, scored, by_ticker, levels, book_of, now), by_ticker, fair_now, market_of, now)}
    c = books["charles"]
    books["evil"] = run_book("evil", *bot_paths("evil"),
                             lambda led: copy_bets(led, c["placed"], c["bank"], True, by_ticker, book_of, now, "evil", "evil"),
                             by_ticker, fair_now, market_of, now)
    src = books[selected]
    books["account"] = run_book("account", LEDGER, EQUITY,
                                lambda led: copy_bets(led, src["placed"], src["bank"], False, by_ticker, book_of, now, selected, "account"),
                                by_ticker, fair_now, market_of, now, tag=selected)

    for s in status:
        status[s]["edges"] = sum(1 for p in scored if p["strategy"] == s and p["quick_edge"] >= RULES["min_edge"][s])
    mine = books["account"]
    summary = {"ts": mine["account"]["ts"], "picks": len(picks), "candidates": sum(min(RULES["max_books_per_strategy"], sum(1 for p in scored if p["strategy"] == s and p["quick_edge"] >= RULES["min_edge"][s]))
                                                                    for s in STRATEGIES if levels[s] != "predict"),
               "placed": len(mine["placed"]), "sold": len(mine["sold"]), "settled": len(mine["settled"]),
               "evil": selected == "evil", "selected": selected, "gates": gates.ON,
               "books": {k: {"placed": len(v["placed"]), "sold": len(v["sold"]), "settled": len(v["settled"]),
                             "account_value": v["account"]["account_value"], "open_bets": len(v["led"]["open"])} for k, v in books.items()},
               "strategies": status, "levels": levels, "level_moves": moves,
               "predictions_open": len(preds["open"]),
               "account_value": mine["account"]["account_value"], "cash": mine["led"]["cash"], "open_bets": len(mine["led"]["open"])}
    top = [{k: p[k] for k in ("strategy", "ticker", "title", "sub", "side", "fair_yes", "model_yes", "quick_edge", "source")}
           | {"ask": _f(by_ticker[p["ticker"]].get(f"{p['side']}_ask_dollars"))}
           for p in scored[:25]]
    with open(LATEST + ".tmp", "w") as f:
        json.dump({"summary": summary, "top_edges": top}, f, indent=1)
    os.replace(LATEST + ".tmp", LATEST)
    log.info(f"betting: {summary}")
    return summary


def report():
    for b in BOTS:
        own = load(bot_paths(b)[0])
        print(f"{'Evil Charles' if b == 'evil' else 'Charles'}'s own book: equity ${equity(own):,.2f} (at cost)  open {len(own['open'])}  "
              f"closed {len(own['closed'])}  realised P&L ${sum(x['pnl'] for x in own['closed']):+.2f}")
    led = load()
    s = stats(led)
    print(f"Betting book: equity ${equity(led):,.2f} (at cost)  cash ${led['cash']:,.2f}  open {len(led['open'])}  "
          f"closed {len(led['closed'])}  realised P&L ${sum(b['pnl'] for b in led['closed']):+.2f}")
    for k, v in s.items():
        print(f"  {k:9} open {v['open']:3} (${v['at_risk']:>7.2f})  closed {v['closed']:3}  won {v['won']:3}  "
              f"lost {v['lost']:3}  sold {v['sold']:3}  P&L ${v['pnl']:+8.2f}  brier model {v['brier_model']} blend {v['brier_blend']} market {v['brier_market']}")
    for b in led["open"][-15:]:
        print(f"  OPEN  {b['opened'][:16]}  {b['strategy']:8} {b['side'].upper():3} {b['ticker']:38} x{b['contracts']:<4} "
              f"@ {b['avg_price']:.2f} fair {b['fair']:.2f}  cost ${b['cost']:.2f}")
