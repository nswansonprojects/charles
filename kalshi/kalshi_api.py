"""Read-only Kalshi public market data (no API key, never places orders)."""
import time
import requests

BASE = "https://api.elections.kalshi.com/trade-api/v2"
_session = requests.Session()


def _get(path: str, params: dict | None = None, tries: int = 4) -> dict:
    for attempt in range(tries):
        r = _session.get(BASE + path, params=params, timeout=30)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(1.5 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return r.json()


def open_events() -> list[dict]:
    """Every open event with its markets nested (about 75 pages of 200)."""
    events, cursor = [], None
    while True:
        params = {"status": "open", "limit": 200, "with_nested_markets": "true"}
        if cursor:
            params["cursor"] = cursor
        d = _get("/events", params)
        events += d.get("events", [])
        cursor = d.get("cursor")
        if not cursor or not d.get("events"):
            return events


def orderbook(ticker: str) -> dict:
    """{'yes': [(price, qty)...], 'no': [...]} — resting BIDS, as floats."""
    ob = _get(f"/markets/{ticker}/orderbook").get("orderbook_fp") or {}
    side = lambda k: [(float(p), float(q)) for p, q in (ob.get(k) or [])]
    return {"yes": side("yes_dollars"), "no": side("no_dollars")}


def market(ticker: str) -> dict:
    return _get(f"/markets/{ticker}")["market"]


def trades(ticker: str, min_ts: int) -> list[dict]:
    """Trades in one market since unix time `min_ts` (up to 1000, newest first)."""
    return _get("/markets/trades", {"ticker": ticker, "min_ts": int(min_ts), "limit": 1000}).get("trades", [])


def incentive_programs() -> list[dict]:
    """Active liquidity-reward programs (one per market)."""
    out, cursor = [], None
    while True:
        params = {"status": "active", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        d = _get("/incentive_programs", params)
        out += d.get("incentive_programs", [])
        cursor = d.get("next_cursor")
        if not cursor or not d.get("incentive_programs"):
            return out
