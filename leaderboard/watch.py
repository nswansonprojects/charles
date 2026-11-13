"""leaderboard/watch.py — the website's "Watch Charles trade" (roadmap g-watch): this server's Charles Fetch, live and read-only.

The Fetch engine (fetch_engine.py, unit charles-fetch) writes data/fetch/live.json about once a second. GET /api/watch passes a
public cut of it: the price, the minute bars, the market's last trades, whether Charles is holding and what he is waiting for.
Never Charles's results: no P&L, no book, no sell amounts, and no sentence that carries a gain or loss (a public results feed is a
question for the lawyer first; the website shows no results anywhere). Nothing about a visitor is used.
"""
import json
import os
import re
import time

LIVE = os.environ.get("FETCH_LIVE") or "/root/charles/data/fetch/live.json"
STALE = 120                 # seconds: an older live.json means Fetch isn't running, and the section hides
BARS, TAPE = 90, 30
RESULTISH = re.compile(r"gain|loss|profit|\bup \d|\bdown \d|\$", re.I)


def public_view(live: dict, now: float) -> dict | None:
    """The public cut of live.json, or None when it's missing or stale."""
    if not isinstance(live, dict) or now - float(live.get("now") or 0) > STALE or not live.get("price"):
        return None
    holding = bool((live.get("book") or {}).get("pos"))
    name = {"BTC-USD": "bitcoin", "ETH-USD": "ether"}.get(live.get("product"), live.get("product"))
    say = live.get("watching") or ""
    if holding or RESULTISH.search(say):
        say = f"Charles is holding {name} and waiting for his target or his stop." if holding else "Charles is watching the market."
    moves = [{"kind": e["kind"], "ts": e["ts"], "price": e["price"]} for e in (live.get("events") or [])[-5:]
             if isinstance(e, dict) and e.get("kind") in ("buy", "sell")]
    return {
        "product": live.get("product"), "now": live.get("now"), "price": live.get("price"), "bid": live.get("bid"), "ask": live.get("ask"),
        "bars": (live.get("bars") or [])[-BARS:], "forming": live.get("forming"),
        "tape": [{"t": t["t"], "p": t["p"], "q": t["q"], "side": t["side"]} for t in (live.get("tape") or [])[-TAPE:]],
        "holding": holding, "level": ((live.get("watch") or {}).get("numbers") or {}).get("level"),
        "watching": say, "moves": moves, "practice": True,
    }


_cache = {"at": 0.0, "body": None}


def current(path: str = LIVE, now: float | None = None) -> dict | None:
    """public_view of the file on disk, read at most once a second."""
    now = now or time.time()
    if now - _cache["at"] >= 1.0:
        try:
            with open(path) as f:
                _cache["body"] = public_view(json.load(f), now)
        except (OSError, ValueError):
            _cache["body"] = None
        _cache["at"] = now
    return _cache["body"]
