"""Betting book: directional PAPER bets where a strategy's probability (strategies.py) beats the
Kalshi price by more than fees. Separate $1,000 ledger from the arbitrage scanner.

Each scan: settle finished bets, sell bets the market has caught up with, then buy the best new
edges at real order-book prices, sized by a fraction of the Kelly stake. Never sends real orders.
"""
import copy
import json
import logging
import math
import os
from datetime import datetime, timezone

import evaluate
import forecasts
import kalshi_api
import strategies

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
LEDGER = os.path.join(DATA, "bets.json")
EQUITY = os.path.join(DATA, "bets_equity.jsonl")
LATEST = os.path.join(DATA, "bets_latest.json")
TUNING = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tuning.json")
START_CASH = 1000.0

# Betting rules (paper book). Change here, nowhere else.
RULES = {
    "kelly_fraction": 0.25,       # bet a quarter of the Kelly stake: models are rough
    "max_bet_pct": 0.03,          # of book equity per bet
    "max_event_pct": 0.06,        # of book equity per event (one game, one city-day...)
    "strategy_cap_pct": {"weather": 0.30, "sports": 0.30, "econ": 0.20, "longshot": 0.30},
    "min_edge": {"weather": 0.07, "sports": 0.04, "econ": 0.06, "longshot": 0.015},  # $/contract after fees
    "price_range": (0.05, 0.97),  # don't buy below/above these
    # Owner's rule (not tuned): a win must pay at least this multiple of the stake after fees.
    # 2026-10-12 first 2.0 (buys <=~33c), then 0.5 the same day: risk $20 to win $10+, buys <=~65c.
    # Settled bets bought at 50-67c had done best (3 of 4 won); those under 34c went 0 for 4.
    "min_win_ratio": 0.5,
    "min_expected_profit": 0.10,  # $ per bet
    "cash_reserve_pct": 0.10,
    "max_books_per_strategy": 15, # order books fetched per scan, best edges first
    "max_new_per_scan": 12,
    "max_new_per_strategy": 3,    # per scan, so one strategy's edges can't fill the book at once
    "exit_margin": 0.02,          # sell when the bid beats our fair value by this much after fees
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
    kelly = (fair - top) / (1 - top)
    stake = min(kelly * RULES["kelly_fraction"] * bank, budget)
    n, cost = 0, 0.0
    for p, q in levels:
        take = int(min(q, (stake - cost) / p))
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
    if exp < RULES["min_expected_profit"] or n - cost - f < RULES["min_win_ratio"] * (cost + f):
        return None
    return {"side": side, "contracts": n, "avg_price": round(avg, 4), "fee": f, "cost": round(cost + f, 2),
            "fair": round(fair, 4), "edge": round(fair - (cost + f) / n, 4), "expected_profit": round(exp, 2)}


def place(led: dict, pick: dict, fill: dict, m: dict, now: datetime) -> dict:
    yb, ya = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
    rec = {**{k: pick[k] for k in ("strategy", "ticker", "event", "category", "title", "sub", "source")},
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


def exits(led: dict, fair_now: dict, by_ticker: dict, now: datetime) -> list[dict]:
    """Sell a bet when the best bid, after the fee, beats today's fair value by exit_margin:
    the price has caught up with (or gone past) our model, so holding adds nothing.
    Longshot bets hold to settlement: their fair value comes from the price itself."""
    sold = []
    for bet in list(led["open"]):
        fy = fair_now.get(bet["ticker"])
        m = by_ticker.get(bet["ticker"])
        if bet["strategy"] == "longshot" or fy is None or m is None:
            continue
        fair = fy if bet["side"] == "yes" else 1 - fy
        bid, depth = _f(m.get(f"{bet['side']}_bid_dollars")), _f(m.get(f"{bet['side']}_bid_size_fp"))
        n = bet["contracts"]
        if bid <= 0 or depth < n:
            continue
        net = n * bid - fee(n, bid)
        if net / n >= fair + RULES["exit_margin"]:
            bet["exit_bid"], bet["fair_at_exit"] = bid, round(fair, 4)
            _close(led, bet, net, "sold", now)
            sold.append(bet)
    return sold


def mark(led: dict, by_ticker: dict, fair_now: dict, now: datetime) -> dict:
    """Open bets at today's best bid (what selling would fetch) and at our current fair value."""
    val = 0.0
    for b in led["open"]:
        m = by_ticker.get(b["ticker"])
        if m is not None:
            b["bid"] = _f(m.get(f"{b['side']}_bid_dollars"))
        if b["ticker"] in fair_now:
            b["fair_now"] = round(fair_now[b["ticker"]] if b["side"] == "yes" else 1 - fair_now[b["ticker"]], 4)
        val += b["contracts"] * b.get("bid", b["avg_price"])
    return {"ts": now.isoformat(timespec="seconds"), "cash": led["cash"], "positions_value": round(val, 2),
            "account_value": round(led["cash"] + val, 2), "open_bets": len(led["open"])}


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


def gather(events: list, now: datetime) -> tuple[list, dict]:
    """Picks from every strategy whose data loaded, plus per-strategy status."""
    picks, status = [], {}
    sources = (("weather", lambda: strategies.weather_picks(events, forecasts.weather(), forecasts.nws(), now)),
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


def run(events: list, by_ticker: dict, now: datetime | None = None, get_book=None, get_market=None) -> dict:
    now = now or datetime.now(timezone.utc)
    apply_tuning()
    get_book = get_book or kalshi_api.orderbook
    get_market = get_market or kalshi_api.market
    led = load()

    settled = []
    try:
        settled = settle(led, lambda t: by_ticker.get(t) or get_market(t), now)
    except Exception as e:
        log.warning(f"betting settle: {e}")
    picks, status = gather(events, now)
    picks = blend(picks, by_ticker)
    fair_now = {p["ticker"]: p["fair_yes"] for p in picks}
    sold = exits(led, fair_now, by_ticker, now)

    scored = []
    for p in picks:
        m = by_ticker.get(p["ticker"])
        q = quick_edge(p, m) if m else None
        if q:
            scored.append({**p, "side": q[0], "quick_edge": q[1]})
    scored.sort(key=lambda p: p["quick_edge"], reverse=True)
    cands = []
    for s in STRATEGIES:
        cands += [p for p in scored if p["strategy"] == s
                  and p["quick_edge"] >= RULES["min_edge"][s]][:RULES["max_books_per_strategy"]]

    bank = equity(led)
    held = {b["ticker"] for b in led["open"]}
    by_event, by_strat = {}, {}
    for b in led["open"]:
        by_event[b["event"]] = by_event.get(b["event"], 0) + b["cost"]
        by_strat[b["strategy"]] = by_strat.get(b["strategy"], 0) + b["cost"]
    placed, new_by = [], {}
    for p in cands:
        if len(placed) >= RULES["max_new_per_scan"]:
            break
        if p["ticker"] in held or new_by.get(p["strategy"], 0) >= RULES["max_new_per_strategy"]:
            continue
        budget = min(bank * RULES["max_bet_pct"],
                     bank * RULES["max_event_pct"] - by_event.get(p["event"], 0),
                     bank * RULES["strategy_cap_pct"][p["strategy"]] - by_strat.get(p["strategy"], 0),
                     led["cash"] - bank * RULES["cash_reserve_pct"])
        if budget < 1:
            continue
        try:
            book = get_book(p["ticker"])
        except Exception as e:
            log.warning(f"betting book {p['ticker']}: {e}")
            continue
        fill = size(p, p["side"], book, budget, bank)
        if not fill:
            continue
        rec = place(led, p, fill, by_ticker[p["ticker"]], now)
        placed.append(rec)
        held.add(p["ticker"])
        new_by[p["strategy"]] = new_by.get(p["strategy"], 0) + 1
        by_event[p["event"]] = by_event.get(p["event"], 0) + rec["cost"]
        by_strat[p["strategy"]] = by_strat.get(p["strategy"], 0) + rec["cost"]
        log.info(f"BET {rec['strategy']} {rec['side'].upper()} {rec['ticker']} x{rec['contracts']} @ {rec['avg_price']} "
                 f"(fair {rec['fair']}, edge {rec['edge']}) cost ${rec['cost']} — {rec['source']}")
    for b in sold:
        log.info(f"SOLD {b['strategy']} {b['ticker']} @ {b['exit_bid']}: pnl ${b['pnl']:+.2f}")
    for b in settled:
        log.info(f"SETTLED {b['strategy']} {b['ticker']} {b['how']}: pnl ${b['pnl']:+.2f}")

    account = mark(led, by_ticker, fair_now, now)
    save(led)
    with open(EQUITY, "a") as f:
        f.write(json.dumps(account) + "\n")
    for s in status:
        status[s]["edges"] = sum(1 for p in scored if p["strategy"] == s and p["quick_edge"] >= RULES["min_edge"][s])
    summary = {"ts": account["ts"], "picks": len(picks), "candidates": len(cands), "placed": len(placed),
               "sold": len(sold), "settled": len(settled), "strategies": status,
               "account_value": account["account_value"], "cash": led["cash"], "open_bets": len(led["open"])}
    top = [{k: p[k] for k in ("strategy", "ticker", "title", "sub", "side", "fair_yes", "model_yes", "quick_edge", "source")}
           | {"ask": _f(by_ticker[p["ticker"]].get(f"{p['side']}_ask_dollars"))}
           for p in scored[:25]]
    with open(LATEST + ".tmp", "w") as f:
        json.dump({"summary": summary, "top_edges": top}, f, indent=1)
    os.replace(LATEST + ".tmp", LATEST)
    log.info(f"betting: {summary}")
    return summary


def report():
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
