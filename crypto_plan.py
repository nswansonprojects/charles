"""
crypto_plan.py — the crypto demo's rules as pure functions (no network, no files).

Charles's safety switch applied to crypto: 60% bitcoin, 40% ether, and each coin is held only while
its price is above its own 200-day average; below it, that share waits in cash (USD). Checked once a
week, so a single bad day doesn't trade. Paper only, outside the Alpaca account (crypto_paper.py).
The same decide() runs on history in crypto_backtest.py.

Your own plan (owner 2026-11-03, "match the stock configuration"): BLOCKS, the user's own coins in blocks, each with a share,
in place of the bitcoin/ether split, and INVESTED, how much of the book is in the plan at all (the rest waits in cash):
  long      Long term: every coin held all the time, an equal share each.
  trend     Trend: each coin held while it's above its own trend line (what Charles does with bitcoin and ether).
  momentum  Momentum: the block's `picks` strongest coins over the past MOM_DAYS, only above their trend line, re-picked at
            every check.
Charles never chooses coins for it: the user adds every one. Its presets change only amounts (PRESETS). Like a custom stock
plan, it's never shared on the leaderboard.
"""
import hashlib
import json
import re
from datetime import date

import portfolio

DEFAULTS = {
    "WEIGHTS": {"BTC-USD": 0.60, "ETH-USD": 0.40},
    "TREND_DAYS": 200,          # same trend line as Charles's safety switch; not tuned to crypto history
    "CHECK_DAYS": 7,            # weekly check
    "FEE": 0.006,               # Coinbase Advanced maker fee at the lowest tier (limit orders); taker is 1.2%
    "MIN_TRADE": 0.05,          # skip rebalances that move less than 5% of the book
    "INVESTED": 1.0,            # the share of the book in the plan; the rest waits in cash
    "BLOCKS": None,             # None: Charles's own bitcoin/ether plan
}
NAMES = {"BTC-USD": "Bitcoin", "ETH-USD": "Ether"}

TYPES = ("long", "trend", "momentum")
TYPE_NAMES = {"long": "Long term", "trend": "Trend", "momentum": "Momentum"}
MOM_DAYS = 90                   # momentum: the return over the past 90 days (crypto moves faster than stocks)
MAX_BLOCKS = portfolio.MAX_BLOCKS
MAX_COINS = 12                  # in one block
MAX_PICKS = 6
PRODUCT = re.compile(r"^[A-Z0-9]{1,10}-USD$")

# Charles's presets: only amounts, never coins (the user's coins stay). settings None: every number back to Charles's own.
PRESETS = [
    {"id": "default", "name": "Default plan", "about": "Charles's own numbers: all of the book in the plan, a 200-day trend line, a check every week. Your coins stay.",
     "settings": None},
    {"id": "half", "name": "0.5× aggressive", "about": "Half of the book in the plan and the other half in cash. Your coins stay.",
     "settings": {"INVESTED": 0.5}},
    {"id": "aggressive", "name": "2× aggressive", "about": "All of the book in the plan, a 100-day trend line, so coins are bought back sooner after a fall, and a check every 3 days. Crypto has no 2× fund here, so it never borrows. Your coins stay.",
     "settings": {"INVESTED": 1.0, "TREND_DAYS": 100, "CHECK_DAYS": 3}},
]


def check_blocks(blocks) -> list | None:
    """The blocks exactly as given if they make a valid crypto plan; ValueError saying why otherwise. None: Charles's plan."""
    return portfolio.check_blocks(blocks, kinds=TYPES, symbol=PRODUCT, max_tickers=MAX_COINS, max_picks=MAX_PICKS, what="coin")


def products(settings: dict | None = None) -> list:
    """Every coin the plan can hold, in order."""
    s = {**DEFAULTS, **(settings or {})}
    return [p for b in s["BLOCKS"] for p in b["tickers"]] if s.get("BLOCKS") else list(s["WEIGHTS"])


def signature(settings: dict | None = None) -> str:
    """Changes with any plan setting, so the book checks again within the hour after a change instead of waiting a week."""
    s = {**DEFAULTS, **(settings or {})}
    raw = json.dumps({k: s.get(k) for k in ("WEIGHTS", "TREND_DAYS", "CHECK_DAYS", "MIN_TRADE", "INVESTED", "BLOCKS")},
                     sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def average(closes: list, n: int):
    """Mean of the last n closes, or None with fewer than n."""
    return sum(closes[-n:]) / n if len(closes) >= n else None


def _coin(c: list, s: dict) -> dict:
    avg = average(c, s["TREND_DAYS"])
    return {"price": c[-1] if c else None, "average": round(avg, 2) if avg else None, "above": bool(avg and c[-1] > avg),
            "gap_pct": round((c[-1] / avg - 1) * 100, 1) if avg else None,
            "momentum": round((c[-1] / c[-1 - MOM_DAYS] - 1) * 100, 1) if len(c) > MOM_DAYS and c[-1 - MOM_DAYS] else None}


def decide(closes: dict, settings: dict | None = None) -> dict:
    """closes: {product: [close, ...] oldest first, up to the latest finished day} →
    {"targets": {product: weight}, "coins": {product: {price, average, above, gap_pct, ...}}}.
    A coin without enough history counts as below its line (cash)."""
    s = {**DEFAULTS, **(settings or {})}
    inv = s.get("INVESTED", 1.0)
    targets, coins = {}, {}
    if not s.get("BLOCKS"):
        for product, weight in s["WEIGHTS"].items():
            coins[product] = info = _coin(closes.get(product) or [], s)
            info.update(type="trend", held=info["above"])
            targets[product] = weight * inv if info["above"] else 0.0
        return {"targets": targets, "coins": coins}
    for b in s["BLOCKS"]:
        infos = {p: _coin(closes.get(p) or [], s) for p in b["tickers"]}
        if b["type"] == "long":
            hold = [p for p in b["tickers"] if infos[p]["price"]]
        elif b["type"] == "trend":
            hold = [p for p in b["tickers"] if infos[p]["above"]]
        else:
            ranked = sorted((p for p in b["tickers"] if infos[p]["momentum"] is not None), key=lambda p: -infos[p]["momentum"])
            for i, p in enumerate(ranked):
                infos[p]["rank"] = i + 1
            hold = [p for p in ranked if infos[p]["above"]][:b["picks"]]
        slot = inv * b["pct"] / (b["picks"] if b["type"] == "momentum" else len(b["tickers"]))
        for p in b["tickers"]:
            coins[p] = {**infos[p], "block": b["name"], "type": b["type"], "held": p in hold, **({"picks": b["picks"]} if b["type"] == "momentum" else {})}
            targets[p] = round(slot, 6) if p in hold else 0.0
    return {"targets": targets, "coins": coins}


def check_due(last_check: str | None, today: str, settings: dict | None = None) -> bool:
    """True when the weekly check hasn't run in CHECK_DAYS days (or ever)."""
    s = {**DEFAULTS, **(settings or {})}
    return not last_check or (date.fromisoformat(today) - date.fromisoformat(last_check[:10])).days >= s["CHECK_DAYS"]


def weights_now(qty: dict, prices: dict, cash: float) -> tuple[float, dict]:
    """(book value, {product: share of the book}) at the given prices."""
    total = cash + sum(q * prices[p] for p, q in qty.items() if q)
    return total, {p: (q * prices[p] / total if total else 0.0) for p, q in qty.items()}


def orders(qty: dict, prices: dict, cash: float, targets: dict, settings: dict | None = None) -> list:
    """Trades that move the book to its targets: [{product, side, value, qty}], sells first.
    Nothing when the total move is under MIN_TRADE of the book (fees would eat it).
    `targets` must name every coin the book holds (0 for one to sell)."""
    s = {**DEFAULTS, **(settings or {})}
    total, now = weights_now({p: qty.get(p, 0.0) for p in targets}, prices, cash)
    if total <= 0 or sum(abs(targets[p] - now.get(p, 0.0)) for p in targets) < s["MIN_TRADE"]:
        return []
    out = []
    for p, t in targets.items():
        diff = (t - now.get(p, 0.0)) * total
        if abs(diff) >= 1.0:                      # under a dollar isn't worth an order
            out.append({"product": p, "side": "BUY" if diff > 0 else "SELL", "value": round(abs(diff), 2),
                        "qty": abs(diff) / prices[p]})
    return sorted(out, key=lambda o: o["side"] != "SELL")


def apply(qty: dict, cash: float, prices: dict, trades: list, fee: float) -> tuple[dict, float]:
    """Fill trades at `prices` paying `fee` on each one's value. Returns (qty, cash); never below zero."""
    qty = dict(qty)
    for o in trades:
        p, v = o["product"], o["value"]
        if o["side"] == "SELL":
            sold = min(qty.get(p, 0.0), o["qty"])
            qty[p] = qty.get(p, 0.0) - sold
            cash += sold * prices[p] * (1 - fee)
        else:
            spend = min(v, cash)
            qty[p] = qty.get(p, 0.0) + spend * (1 - fee) / prices[p]
            cash -= spend
    return qty, max(0.0, cash)


def name_of(product: str, names: dict | None = None) -> str:
    return (names or {}).get(product) or NAMES.get(product) or product.split("-")[0]


def reason(product: str, coin: dict | None, settings: dict | None = None, names: dict | None = None,
           side: str | None = None, target: float | None = None) -> str:
    """Why a coin is bought, held or sold, in one plain sentence. With the trade's side and the coin's new share of the book,
    a trade that only resizes a coin the plan keeps says so (a sale after Invested went down isn't "above its average")."""
    s = {**DEFAULTS, **(settings or {})}
    name = name_of(product, names)
    if not coin:
        return f"{name} isn't in your plan anymore."
    if side and target:
        why = "held all the time" if coin.get("type") == "long" else \
            f"{abs(coin['gap_pct']):.1f}% {'above' if coin['above'] else 'below'} its {s['TREND_DAYS']}-day average" if coin.get("gap_pct") is not None else ""
        return f"{name} {'down' if side.upper() == 'SELL' else 'up'} to its {round(target * 100, 1):g}% share of the book" + (f" ({why})." if why else ".")
    # the block's name only when it says something the coin's name doesn't ("Momentum: Solana", not "Ether: Ether")
    where = f"{coin['block']}: " if coin.get("block") and coin["block"].strip().lower() != name.lower() else ""
    if coin.get("type") == "long":
        return f"{where}{name} is held all the time."
    if coin["average"] is None:
        return f"{where}{name}: not enough history for its {s['TREND_DAYS']}-day average yet, so it waits in cash."
    side = "above" if coin["above"] else "below"
    line = f"{where}{name} is {abs(coin['gap_pct']):.1f}% {side} its {s['TREND_DAYS']}-day average"
    if coin.get("type") == "momentum":
        if coin.get("held"):
            return f"{line}, and one of the block's {coin['picks']} strongest over the past {MOM_DAYS} days."
        if coin["above"]:
            return f"{line}, but not one of the block's {coin['picks']} strongest over the past {MOM_DAYS} days."
    return line + "."
