"""
crypto_paper.py — the crypto demo's paper book: $1,000 of practice money, kept in data/crypto_paper.json.

Never touches the Alpaca account or any exchange account: prices come from Coinbase's public feed
(crypto_market.py) and fills are simulated at the latest price minus the fee. Run it hourly
(`python crypto_paper.py`); it trades only when the weekly check is due.
"""
import argparse
import json
import os
from datetime import datetime, timezone

import crypto_market
import crypto_plan

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("CRYPTO_DATA") or os.path.join(ROOT, "data")
PATH = os.path.join(DATA, "crypto_paper.json")
START_CASH = 1000.0


def new_book(now: str) -> dict:
    return {"started": now, "start_cash": START_CASH, "cash": START_CASH, "qty": {}, "last_check": None,
            "coins": {}, "targets": {}, "curve": [], "trades": []}


def load(path: str = PATH) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save(book: dict, path: str = PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(book, f, indent=1)
    os.replace(path + ".tmp", path)


def step(book: dict, closes: dict, prices: dict, now: datetime, settings: dict | None = None, force: bool = False) -> dict:
    """One run: on a due weekly check, decide and fill at `prices`; always add a value point."""
    s = {**crypto_plan.DEFAULTS, **(settings or {})}
    stamp = now.isoformat(timespec="seconds")
    book.setdefault("start_prices", dict(prices))      # for "vs just holding bitcoin" since the start
    if force or crypto_plan.check_due(book.get("last_check"), now.date().isoformat(), s):
        d = crypto_plan.decide(closes, s)
        trades = crypto_plan.orders(book["qty"], prices, book["cash"], d["targets"], s)
        book["qty"], book["cash"] = crypto_plan.apply(book["qty"], book["cash"], prices, trades, s["FEE"])
        for o in trades:
            book["trades"].append({"timestamp": stamp, "product": o["product"], "side": o["side"],
                                   "value": o["value"], "price": prices[o["product"]],
                                   "fee": round(o["value"] * s["FEE"], 2),
                                   "reason": crypto_plan.reason(o["product"], d["coins"][o["product"]], s)})
        book.update(last_check=stamp, coins=d["coins"], targets=d["targets"])
    total, _ = crypto_plan.weights_now(book["qty"], prices, book["cash"])
    book["curve"].append([stamp, round(total, 2)])
    return book


def run(force: bool = False, path: str = PATH) -> dict:
    now = datetime.now(timezone.utc)
    products = list(crypto_plan.DEFAULTS["WEIGHTS"])
    closes = {p: [c for _, c in crypto_market.daily_closes(p)] for p in products}
    prices = {p: crypto_market.spot(p) for p in products}
    book = load(path) or new_book(now.isoformat(timespec="seconds"))
    book = step(book, closes, prices, now, force=force)
    save(book, path)
    return book


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--force", action="store_true", help="run the weekly check now")
    b = run(force=ap.parse_args().force)
    print(f"crypto paper book ${b['curve'][-1][1]:,.2f}; cash ${b['cash']:,.2f}; "
          + ", ".join(f"{p} {q:.6f}" for p, q in b["qty"].items()))
