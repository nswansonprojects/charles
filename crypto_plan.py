"""
crypto_plan.py — the crypto demo's rules as pure functions (no network, no files).

Charles's safety switch applied to crypto: 60% bitcoin, 40% ether, and each coin is held only while
its price is above its own 200-day average; below it, that share waits in cash (USD). Checked once a
week, so a single bad day doesn't trade. Paper only, outside the Alpaca account (crypto_paper.py).
The same decide() runs on history in crypto_backtest.py.
"""
from datetime import date

DEFAULTS = {
    "WEIGHTS": {"BTC-USD": 0.60, "ETH-USD": 0.40},
    "TREND_DAYS": 200,          # same trend line as Charles's safety switch; not tuned to crypto history
    "CHECK_DAYS": 7,            # weekly check
    "FEE": 0.006,               # Coinbase Advanced maker fee at the lowest tier (limit orders); taker is 1.2%
    "MIN_TRADE": 0.05,          # skip rebalances that move less than 5% of the book
}
NAMES = {"BTC-USD": "Bitcoin", "ETH-USD": "Ether"}


def average(closes: list, n: int):
    """Mean of the last n closes, or None with fewer than n."""
    return sum(closes[-n:]) / n if len(closes) >= n else None


def decide(closes: dict, settings: dict | None = None) -> dict:
    """closes: {product: [close, ...] oldest first, up to the latest finished day} →
    {"targets": {product: weight}, "coins": {product: {price, average, above, gap_pct}}}.
    A coin without enough history counts as below its line (cash)."""
    s = {**DEFAULTS, **(settings or {})}
    targets, coins = {}, {}
    for product, weight in s["WEIGHTS"].items():
        c = closes.get(product) or []
        avg = average(c, s["TREND_DAYS"])
        above = bool(avg and c[-1] > avg)
        targets[product] = weight if above else 0.0
        coins[product] = {"price": c[-1] if c else None, "average": round(avg, 2) if avg else None, "above": above,
                          "gap_pct": round((c[-1] / avg - 1) * 100, 1) if avg else None}
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
    Nothing when the total move is under MIN_TRADE of the book (fees would eat it)."""
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


def reason(product: str, coin: dict, settings: dict | None = None) -> str:
    s = {**DEFAULTS, **(settings or {})}
    name = NAMES.get(product, product)
    if coin["average"] is None:
        return f"{name}: not enough history for its {s['TREND_DAYS']}-day average yet, so it waits in cash."
    side = "above" if coin["above"] else "below"
    return f"{name} is {abs(coin['gap_pct']):.1f}% {side} its {s['TREND_DAYS']}-day average."
