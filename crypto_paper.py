"""
crypto_paper.py — the crypto demo's paper book: $1,000 of practice money, kept in data/crypto_paper.json.

Never touches the Alpaca account or any exchange account: prices come from Coinbase's public feed
(crypto_market.py) and fills are simulated at the latest price minus the fee. Run it hourly
(`python crypto_paper.py`); it trades only when the weekly check is due.
"""
import argparse
import json
import os
from datetime import datetime, timezone, timedelta

import crypto_market
import crypto_plan
import crypto_settings

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("CRYPTO_DATA") or os.path.join(ROOT, "data")
PATH = os.path.join(DATA, "crypto_paper.json")
START_CASH = 1000.0
SHADOW_PATH = os.path.join(DATA, "crypto_shadow.json")
SHADOW_RULE = {"RISING_DAYS": 45}     # the rule on trial (ops/lab/crypto_rules.py, 2026-11-11): about the same return on past prices,
                                      # a smaller worst drop; it fails self-tune's test, which counts only return, so it is watched instead
WAIT_HOURS = 6        # a due check whose coins' prices didn't come waits this long for them, then goes ahead without them


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


def step(book: dict, closes: dict, prices: dict, now: datetime, settings: dict | None = None, force: bool = False,
         names: dict | None = None) -> dict:
    """One run: on a due weekly check, decide and fill at `prices`; always add a value point. A saved change to the plan
    (its signature) brings the check forward to this run. A coin the book holds but the plan no longer names is sold.
    A coin whose price or history didn't come (Coinbase's feed down) holds a due check back to the next run, for up to WAIT_HOURS;
    then the check goes ahead and leaves that coin as it is. The book values a coin at its last price seen, never at $0."""
    s = {**crypto_plan.DEFAULTS, **(settings or {})}
    stamp = now.isoformat(timespec="seconds")
    seen = book.setdefault("last_prices", {t["product"]: t["price"] for t in book.get("trades", []) if t.get("price")})
    seen.update({p: v for p, v in prices.items() if v})
    start = book.setdefault("start_prices", {})          # for "vs just holding bitcoin"; a price that didn't come is filled in later
    start.update({p: prices[p] for p in ("BTC-USD", "ETH-USD") if p not in start and prices.get(p)})
    sig = crypto_plan.signature(s)
    # a book from before 1.4.0 has no signature: it ran fully invested on bitcoin and ether, so a new "My own" or Invested choice
    # made before its first run on 1.4.0 still brings the check forward
    changed = (book.get("sig") or crypto_plan.signature({**s, "INVESTED": 1.0, "BLOCKS": None})) != sig
    held = {p for p, q in book["qty"].items() if q > 1e-12}
    missing = {p for p in held | set(crypto_plan.products(s)) if not prices.get(p) or p not in closes}
    due = force or changed or crypto_plan.check_due(book.get("last_check"), now.date().isoformat(), s)
    wait = bool(due and missing and not force
                and now - datetime.fromisoformat(book.get("check_waiting") or stamp) < timedelta(hours=WAIT_HOURS))
    if wait:
        book.setdefault("check_waiting", stamp)
    else:
        book.pop("check_waiting", None)
    if due and not wait:
        d = crypto_plan.decide(closes, s)
        px = {**{p: 0.0 for p in held}, **seen}
        _, share = crypto_plan.weights_now({p: book["qty"][p] for p in held}, px, book["cash"])
        stuck = held & missing          # still no price or history after the wait: left as it is, and still counted in the book
        targets = {**{p: 0.0 for p in held}, **d["targets"], **{p: share[p] for p in stuck}}
        targets = {p: t for p, t in targets.items() if prices.get(p) or p in stuck}       # no price this run: can't be bought
        room, rest = 1.0 - sum(share[p] for p in stuck), sum(t for p, t in targets.items() if p not in stuck)
        if stuck and rest > room:       # the others can't use money that's still in a stuck coin
            targets = {p: t if p in stuck else t * room / rest for p, t in targets.items()}
        trades = crypto_plan.orders(book["qty"], px, book["cash"], targets, s)
        book["qty"], book["cash"] = crypto_plan.apply(book["qty"], book["cash"], prices, trades, s["FEE"])
        book["qty"] = {p: q for p, q in book["qty"].items() if q > 1e-12 or p in d["targets"]}
        for o in trades:
            book["trades"].append({"timestamp": stamp, "product": o["product"], "side": o["side"],
                                   "value": o["value"], "price": prices[o["product"]],
                                   "fee": round(o["value"] * s["FEE"], 2),
                                   "reason": crypto_plan.reason(o["product"], d["coins"].get(o["product"]), s, names,
                                                                side=o["side"] if o["product"] in held else None,      # a resize, not a coin coming in
                                                                target=d["targets"].get(o["product"]))})
        old = book.get("coins") or {}
        book.update(last_check=stamp, sig=sig, targets={**d["targets"], **{p: targets[p] for p in stuck}},
                    coins={**d["coins"], **{p: old[p] for p in missing if p not in closes and p in old}})
    total, _ = crypto_plan.weights_now(book["qty"], {**{p: 0.0 for p in book["qty"]}, **seen}, book["cash"])
    book["curve"].append([stamp, round(total, 2)])
    return book


def run(force: bool = False, path: str = PATH) -> dict:
    now = datetime.now(timezone.utc)
    settings = crypto_settings.load()
    book = load(path) or new_book(now.isoformat(timespec="seconds"))
    products = list(dict.fromkeys(crypto_plan.products(settings) + [p for p, q in book["qty"].items() if q > 1e-12] + ["BTC-USD"]))
    closes, prices = {}, {}
    for p in products:
        try:
            closes[p] = [c for _, c in crypto_market.daily_closes(p)]
            prices[p] = crypto_market.spot(p)
        except Exception as e:                         # one coin's feed being down doesn't stop the others
            print(f"crypto paper book: no price for {p} ({e})")
    book = step(book, closes, prices, now, settings=settings, force=force, names=crypto_settings.coin_names())
    if book.get("check_waiting"):
        print(f"crypto paper book: the check waits for Coinbase's prices (since {book['check_waiting']}, up to {WAIT_HOURS} hours)")
    save(book, path)
    try:
        shadow(closes, prices, now, settings)
    except Exception as e:                             # the trial book must never get in the real book's way
        print(f"crypto shadow book: skipped ({e})")
    return book


def shadow(closes: dict, prices: dict, now: datetime, settings: dict | None, path: str | None = None) -> dict | None:
    """A second pretend book that follows the same plan plus SHADOW_RULE, on the same prices at the same moments, so the two
    can be compared from the day it started. It runs only where its file exists (`crypto_paper.py --start-shadow` makes it)
    and only for Charles's own bitcoin/ether plan: with your own coins it stops stepping. It never touches the real book."""
    path = path or SHADOW_PATH
    book = load(path)
    if book is None or (settings or {}).get("BLOCKS"):
        return book
    book = step(book, closes, prices, now, settings={**(settings or {}), **book.get("rule", SHADOW_RULE)}, names=crypto_settings.coin_names())
    save(book, path)
    return book


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--force", action="store_true", help="run the weekly check now")
    ap.add_argument("--start-shadow", action="store_true", help="start the trial book that runs SHADOW_RULE beside the real one")
    args = ap.parse_args()
    if args.start_shadow and load(SHADOW_PATH) is None:
        save({**new_book(datetime.now(timezone.utc).isoformat(timespec="seconds")), "rule": dict(SHADOW_RULE)}, SHADOW_PATH)
    b = run(force=args.force)
    print(f"crypto paper book ${b['curve'][-1][1]:,.2f}; cash ${b['cash']:,.2f}; "
          + ", ".join(f"{p} {q:.6f}" for p, q in b["qty"].items()))
