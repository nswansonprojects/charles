"""Paper ledger: simulated fills at real order-book prices, settled from real results."""
import json
import os
from datetime import datetime, timezone

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
LEDGER = os.path.join(DATA, "ledger.json")
START_CASH = 1000.0


def load(path: str = LEDGER) -> dict:
    try:
        with open(path) as f:
            led = json.load(f)
    except FileNotFoundError:
        led = {"start_cash": START_CASH, "cash": START_CASH, "open": [], "closed": []}
    for k, v in (("resting", []), ("maker_closed", []), ("rewards_total", 0.0)):
        led.setdefault(k, v)
    return led


def save(led: dict, path: str = LEDGER):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(led, f, indent=1)
    os.replace(tmp, path)


def held(led: dict) -> float:
    """Money held for resting (maker) orders, as Kalshi holds collateral."""
    return round(sum(p["held"] for p in led.get("resting", [])), 2)


def equity(led: dict) -> float:
    """Cash, money held for resting orders, and open bets at cost (held to settlement)."""
    return round(led["cash"] + held(led) + sum(b["cost"] for b in led["open"]), 2)


def held_by_event(led: dict) -> dict:
    out = {}
    for b in led["open"]:
        out[b["event"]] = out.get(b["event"], 0) + b["cost"]
    return out


def place(led: dict, bet: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    rec = {k: bet[k] for k in ("type", "event", "category", "title", "payout", "sets", "cost",
                               "profit", "confidence", "expected_profit", "annual_return_pct",
                               "close_time", "why")}
    rec["legs"] = [{k: f[k] for k in ("ticker", "side", "contracts", "avg_price", "fee")}
                   for f in bet["fills"]]
    rec["opened"] = now.isoformat(timespec="seconds")
    rec["id"] = f"{now:%Y%m%d%H%M%S}-{len(led['open']) + len(led['closed'])}"
    led["cash"] = round(led["cash"] - bet["cost"], 2)
    led["open"].append(rec)
    return rec


def settle(led: dict, get_market, now: datetime | None = None) -> list[dict]:
    """Close bets whose markets have all resolved. `get_market(ticker)` -> market dict.
    A voided leg (finalized with no yes/no result) refunds what it cost."""
    now = now or datetime.now(timezone.utc)
    done = []
    for bet in list(led["open"]):
        markets = {l["ticker"]: get_market(l["ticker"]) for l in bet["legs"]}
        if not all(m.get("status") in ("settled", "finalized") for m in markets.values()):
            continue
        received = 0.0
        for l in bet["legs"]:
            res = markets[l["ticker"]].get("result")
            if res in ("yes", "no"):
                received += l["contracts"] * (1.0 if res == l["side"] else 0.0)
            else:
                received += l["contracts"] * l["avg_price"] + l["fee"]
        bet["received"] = round(received, 2)
        bet["pnl"] = round(received - bet["cost"], 2)
        bet["closed"] = now.isoformat(timespec="seconds")
        led["cash"] = round(led["cash"] + received, 2)
        led["open"].remove(bet)
        led["closed"].append(bet)
        done.append(bet)
    return done


def _bid(m: dict, side: str) -> float:
    try:
        return float(m.get(f"{side}_bid_dollars") or 0)
    except (TypeError, ValueError):
        return 0.0


def mark(led: dict, by_ticker: dict, now: datetime | None = None) -> dict:
    """Value open bets like a broker would: each leg at today's best bid for its side
    (what selling now would fetch, before fees). Missing markets keep their last mark.
    Returns the account snapshot."""
    now = now or datetime.now(timezone.utc)
    marked = settle_value = 0.0
    for bet in led["open"]:
        val = 0.0
        for l in bet["legs"]:
            m = by_ticker.get(l["ticker"])
            if m is not None:
                l["bid"] = _bid(m, l["side"])
            val += l["contracts"] * l.get("bid", l["avg_price"])
        bet["mark_value"] = round(val, 2)
        marked += val
        settle_value += bet["payout"] * bet["sets"]
    h = held(led)
    return {"ts": now.isoformat(timespec="seconds"), "cash": led["cash"], "held": h,
            "positions_value": round(marked, 2), "account_value": round(led["cash"] + h + marked, 2),
            "settlement_value": round(led["cash"] + h + settle_value, 2), "open_bets": len(led["open"]),
            "resting": len(led.get("resting", [])), "rewards_total": round(led.get("rewards_total", 0.0), 2)}
