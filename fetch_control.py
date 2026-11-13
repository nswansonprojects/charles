"""
fetch_control.py — the user's two choices for Charles Fetch: which ticker it watches, and pause. Practice money only.

Each market (a coin, a stock) has one engine (charles-fetch, charles-fetch-stocks). The user picks its ticker from a short list of
busy ones, because Fetch narrates the tape trade by trade and a quiet ticker gives it nothing to show (owner 2026-11-13: "choose
one ticker to watch for both crypto and stocks"). Pause stops new buys; an open practice trade still ends at its target, stop or
time limit, so nothing is left without an exit.

Who writes what: the dashboard leaves a request (control.py, data/requests/); control.py runs as root and calls set_ticker() or
set_paused() here. Both write data/<market dir>/control.json ({"product", "paused", "since", "by"}); the
shipped settings file (fetch.json / fetch-stocks.json) only gives the first ticker, since charles-requests may write data/ alone.
The engine reads control.json every few seconds: a new ticker closes an open practice trade at the market price, carries the
book's cash and P&L over and restarts the engine on the new ticker.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
MARKETS = {
    "coin": {"settings": "fetch.json", "dir": "fetch", "default": "BTC-USD"},
    "stock": {"settings": "fetch-stocks.json", "dir": "fetch-stocks", "default": "SPY"},
}
# Busy tickers only: each trades many times a minute on its feed (Coinbase's for coins; IEX, a sample of US trades, for stocks).
CHOICES = {
    "coin": [("BTC-USD", "Bitcoin"), ("ETH-USD", "Ether"), ("SOL-USD", "Solana"), ("XRP-USD", "XRP"), ("DOGE-USD", "Dogecoin"),
             ("LTC-USD", "Litecoin"), ("LINK-USD", "Chainlink"), ("AVAX-USD", "Avalanche")],
    "stock": [("SPY", "S&P 500 fund"), ("QQQ", "Nasdaq 100 fund"), ("IWM", "Small companies fund"), ("AAPL", "Apple"),
              ("MSFT", "Microsoft"), ("NVDA", "Nvidia"), ("AMZN", "Amazon"), ("TSLA", "Tesla"), ("META", "Meta"), ("AMD", "AMD")],
}


def settings_path(market: str, root: str = ROOT) -> str:
    return os.path.join(root, MARKETS[market]["settings"])


def control_path(market: str, root: str = ROOT) -> str:
    return os.path.join(root, "data", MARKETS[market]["dir"], "control.json")


def name_of(product: str) -> str:
    for rows in CHOICES.values():
        for p, n in rows:
            if p == product:
                return n
    return product


def allowed(market: str, product: str) -> bool:
    return market in CHOICES and any(p == product for p, _ in CHOICES[market])


def _read(path: str) -> dict:
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: str, obj: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(obj, f, indent=1)
    os.chmod(path + ".tmp", 0o644)
    os.replace(path + ".tmp", path)


def ticker(market: str, root: str = ROOT) -> str:
    """The user's pick (control.json), else the settings file's PRODUCT, else the default."""
    for p in (_read(control_path(market, root)).get("product"), _read(settings_path(market, root)).get("PRODUCT")):
        if isinstance(p, str) and allowed(market, p):
            return p
    return MARKETS[market]["default"]


def set_ticker(market: str, product: str, root: str = ROOT) -> str:
    """Point one market's engine at another ticker from CHOICES. Returns a sentence for the notice."""
    if market not in MARKETS or not allowed(market, product):
        return "Fetch can't watch that ticker: pick one from the list."
    if ticker(market, root) == product:
        return f"Fetch was already watching {name_of(product)}."
    path = control_path(market, root)
    _write(path, {**_read(path), "product": product})
    return (f"Fetch now watches {name_of(product)} ({product.replace('-USD', '')}). An open practice trade on the old ticker was "
            f"closed at the market price; the P&L carries on from where it was.")


def paused(market: str, root: str = ROOT) -> dict | None:
    """{"since", "by"} while that market's Fetch is paused, else None."""
    d = _read(control_path(market, root))
    return {"since": d.get("since"), "by": d.get("by")} if d.get("paused") else None


def set_paused(market: str, on: bool, by: str, root: str = ROOT) -> str:
    if market not in MARKETS:
        return "Unknown Fetch market."
    if bool(paused(market, root)) == on:
        return "Fetch was already paused." if on else "Fetch wasn't paused."
    path = control_path(market, root)
    _write(path, {**_read(path), "paused": on, "since": datetime.now(timezone.utc).isoformat(timespec="seconds"), "by": by})
    return ("Fetch is paused: no new practice buys. An open trade still ends at its target, stop or time limit." if on
            else "Fetch resumed: it buys again when its rule says so.")
