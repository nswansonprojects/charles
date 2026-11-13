"""
fetch_engine.py — Charles Fetch, live: one coin watched trade by trade, with a practice book. Practice money only.

It listens to Coinbase's public feed (no key, no account), builds one-minute bars from the trades, and feeds them to the same
rules the replay tests (fetch_plan.step on each closed bar, fetch_plan.tick on each trade). Nothing here can place a real order.

Files (data/fetch/):
  book.json    the practice book (fetch_plan.dump of its state, plus the product), rewritten at every closed minute and every event
  events.jsonl every buy and sell, one JSON line each, with the narrator's sentence
  live.json    what the page shows, rewritten about once a second: the last trades on the tape, the bars, the book, the
               latest events and what Charles is watching for right now

A stock (PRODUCT like "SPY" in fetch-stocks.json, FETCH_SETTINGS=fetch-stocks.json, FETCH_DATA=data/fetch-stocks) comes from
Alpaca's free IEX stream instead: read-only market data with the owner's paper keys (about 2.5% of US volume, so the tape is a sample).
The session (open and close, half days, holidays) comes from Alpaca's calendar; trades outside it are left out, as the replay does.
IEX trades carry no taker side, so the side is read from the quote (at or over the ask: a buyer; at or under the bid: a seller).

Run: python fetch_engine.py [--product BTC-USD|SPY]   (a long-running process; systemd restarts it)
"""
import argparse
import asyncio
import json
import os
import time
from collections import deque

import fetch_plan as rt

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("FETCH_DATA") or os.path.join(ROOT, "data", "fetch")
SETTINGS_PATH = os.path.join(ROOT, os.environ.get("FETCH_SETTINGS") or "fetch.json")
WS = "wss://ws-feed.exchange.coinbase.com"
STOCK_WS = "wss://stream.data.alpaca.markets/v2/iex"
STOCK_DATA = "https://data.alpaca.markets/v2"
STOCK_CAL = ("https://paper-api.alpaca.markets/v2/calendar", "https://api.alpaca.markets/v2/calendar")   # paper keys, then live keys
REST = "https://api.exchange.coinbase.com"
START_CASH = 1000.0
TAPE = 60                     # trades kept for the page
BARS = 240                    # closed bars kept for the page
EVENTS = 40                   # events kept for the page
WRITE_EVERY = 1.0             # seconds between live.json writes
SAY_EVERY = 20.0              # seconds between "watching" sentences when nothing happens
PNL_POINTS = 2000             # P&L points kept (one per closed minute while it changes, else one every 15 minutes)


# ------------------------------------------------------------------ pure parts (tested)

class BarBuilder:
    """Trades in, closed one-minute bars out. A minute with no trade makes no bar (as Coinbase's own candles)."""

    def __init__(self):
        self.cur = None                                   # [minute_ts, open, high, low, close, volume]

    def add(self, ts: float, price: float, size: float = 0.0) -> list:
        """Add one trade; return the bars this trade closed (zero or one)."""
        m = float(int(ts // 60) * 60)
        out = []
        if self.cur and m > self.cur[0]:
            out.append(rt.Bar(*self.cur))
            self.cur = None
        if self.cur is None:
            self.cur = [m, price, price, price, price, size]
        elif m == self.cur[0]:
            c = self.cur
            c[2], c[3], c[4], c[5] = max(c[2], price), min(c[3], price), price, c[5] + size
        return out                                        # a trade from an older minute (late) only closes nothing

    def flush(self, now: float) -> list:
        """The forming bar, if its minute is over (called on a timer so a quiet minute still closes)."""
        if self.cur and now >= self.cur[0] + 60:
            b, self.cur = rt.Bar(*self.cur), None
            return [b]
        return []

    def forming(self):
        return list(self.cur) if self.cur else None


def settings_from(path: str = SETTINGS_PATH) -> dict:
    """fetch.json's settings over fetch_plan.DEFAULTS, clamped (fetch_plan.settings). A missing or broken file gives the defaults."""
    try:
        with open(path) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    return rt.settings({k: v for k, v in (raw or {}).items() if k != "PRODUCT"})


def product_from(path: str = SETTINGS_PATH) -> str:
    try:
        with open(path) as f:
            p = json.load(f).get("PRODUCT")
        return p if isinstance(p, str) and (p.endswith("-USD") or is_stock(p)) else "BTC-USD"
    except (OSError, ValueError, AttributeError):
        return "BTC-USD"


def is_stock(product: str) -> bool:
    """A US ticker (SPY, AAPL, BRK.B), not a coin pair (BTC-USD)."""
    return isinstance(product, str) and 1 <= len(product) <= 6 and product.replace(".", "").isalpha() and product.isupper()


def taker_side(price: float, bid: float | None, ask: float | None, last: float | None) -> str:
    """Who crossed the spread on a trade that doesn't say (IEX): at or over the ask a buyer, at or under the bid a seller; inside the
    spread, the nearer side; at the middle (or with no quote), up from the last trade is a buyer (the tick rule)."""
    if ask and price >= ask:
        return "buy"
    if bid and price <= bid:
        return "sell"
    if bid and ask:
        mid = (bid + ask) / 2
        if abs(price - mid) > mid * 1e-9:
            return "buy" if price > mid else "sell"
    return "sell" if last is not None and price < last else "buy"


def record(e: rt.Event) -> dict:
    d = e.asdict()
    d["say"] = rt.say(e)
    return d


class Engine:
    """Everything but the network: feed it trades (on_trade) and time (on_clock); it keeps the book and what the page shows."""

    def __init__(self, product: str, s: dict, state: rt.State | None = None, data: str = DATA, session=None):
        self.product, self.s, self.data = product, s, data
        self.session = session                            # a stock: rt.Session of today (None while the market is shut); a coin: None
        self.stock = is_stock(product)
        self.st = state or rt.new_state(START_CASH)
        self.bars = BarBuilder()
        self.tape = deque(maxlen=TAPE)
        self.closed = deque(maxlen=BARS)
        self.events = deque(maxlen=EVENTS)
        self.bid = self.ask = None
        self.last_price = None
        self.watch_say, self.watch_at, self.watch_n = "", 0.0, {}
        self.since = time.time()                          # when this book started: its P&L counts from $0 there
        self.pnl_hist = deque(maxlen=PNL_POINTS)          # [ts, total P&L]
        self.wins = self.losses = 0
        self.dirty = True

    def pnl(self, price: float | None = None) -> dict:
        """Charles's P&L in practice dollars, from $0 when the book started: closed trades (realized), the open trade at the
        market's buying price after the fee (open), and both together (total)."""
        st, mark = self.st, self.bid or price or self.last_price
        invest = st.pos["invest"] if st.pos else 0.0
        realized = st.cash + invest - START_CASH
        if st.pos and mark:
            total = st.cash + st.pos["qty"] * mark * (1 - self.s["FEE"]) * (1 - self.s["SLIP"]) - START_CASH
        else:
            total = realized
        return {"total": total, "realized": realized, "open": total - realized, "since": self.since,
                "wins": self.wins, "losses": self.losses, "trades": self.wins + self.losses}

    def _note_pnl(self, ts: float):
        t = round(self.pnl()["total"], 4)
        h = self.pnl_hist
        if not h or h[-1][1] != t or ts - h[-1][0] >= 900:
            h.append([ts, t])

    def warm(self, past: list):
        rt.warm(self.st, past)
        self.closed.extend(past[-BARS:])

    def on_quote(self, bid: float | None, ask: float | None):
        self.bid, self.ask = bid or self.bid, ask or self.ask

    def is_open(self, ts: float) -> bool:
        return not self.stock or bool(self.session and self.session.open <= ts < self.session.close)

    def on_trade(self, ts: float, price: float, size: float, side: str) -> list:
        """One trade of the market's tape. `side` is the taker's: "buy" lifted the ask, "sell" hit the bid. Returns new events.
        A stock's trade outside the session (pre-market, after hours) is left out, as in the replay."""
        if not self.is_open(ts):
            return []
        self.tape.append({"t": ts, "p": price, "q": size, "side": side})
        self.last_price = price
        ev = []
        for b in self.bars.add(ts, price, size):
            ev += self._close(b)
        ev += rt.tick(self.st, ts, price, self.s, self.bid, self.ask)
        self._keep(ev)
        self.dirty = True
        return ev

    def on_clock(self, now: float) -> list:
        ev = []
        for b in self.bars.flush(now):
            ev += self._close(b)
        self._keep(ev)
        if self.stock and not self.is_open(now):
            if now - self.watch_at >= SAY_EVERY:
                self.watch_say, self.watch_at, self.watch_n = self.closed_say(now), now, {}
                self.dirty = True
        elif self.last_price and (ev or now - self.watch_at >= SAY_EVERY):
            w = rt.watch(self.st, self.last_price, self.s, self.session, ts=now)
            self.watch_say, self.watch_at, self.watch_n = rt.say(w), now, record(w)
            self.dirty = True
        return ev

    def closed_say(self, now: float) -> str:
        if self.session and now < self.session.open:
            return f"The market opens in {(self.session.open - now) / 60:.0f} minutes. I am waiting for the first trades."
        return "The market is closed. I start again when it opens."

    def _close(self, b: rt.Bar) -> list:
        self.closed.append(b)
        ev = rt.step(self.st, b, self.s, self.session if self.stock else None)
        self._note_pnl(b.ts + 60)
        self.save_book()                                  # once a minute: an order signalled at this close survives a restart
        return ev

    def _keep(self, ev: list):
        for e in ev:
            if e.kind == "sell" and e.pnl is not None:
                if e.pnl > 0:
                    self.wins += 1
                else:
                    self.losses += 1
                self._note_pnl(e.ts + 60)
            r = record(e)
            self.events.append(r)
            os.makedirs(self.data, exist_ok=True)
            with open(os.path.join(self.data, "events.jsonl"), "a") as f:
                f.write(json.dumps(r) + "\n")
        if ev:
            self.save_book()

    def view(self, now: float) -> dict:
        price = self.last_price
        return {
            "product": self.product, "now": now, "practice": True, "market": "stock" if self.stock else "coin",
            "open": self.is_open(now), "session": [self.session.open, self.session.close] if self.session else None,
            "price": price, "bid": self.bid, "ask": self.ask,
            "tape": list(self.tape), "bars": [list(b) for b in self.closed], "forming": self.bars.forming(),
            "book": {"start": START_CASH, "cash": self.st.cash, "value": rt.value(self.st, price) if price else self.st.cash,
                     "pos": self.st.pos, "trades_today": self.st.trades_today, "max_trades": self.s["MAX_TRADES"]},
            "pnl": {**self.pnl(price), "today": self.pnl_today(now, price), "history": list(self.pnl_hist)},
            "events": list(self.events), "watching": self.watch_say, "watch": self.watch_n,
            "rule": self.s["RULE"], "settings": {k: self.s[k] for k in ("RULE", "TARGET", "STOP", "HOLD", "FEE", "SLIP")},
        }

    def pnl_today(self, now: float, price: float | None) -> float:
        """Today's P&L: the total now less the total at the start of the day (UTC for a coin, the session for a stock)."""
        start = self.session.open if self.stock and self.session else float(int(now // 86400) * 86400)
        before = [t for ts, t in self.pnl_hist if ts <= start]
        return self.pnl(price)["total"] - (before[-1] if before else 0.0)

    def save_book(self):
        _write(os.path.join(self.data, "book.json"), {"product": self.product, "state": rt.dump(self.st), "saved": time.time(),
                                                      "since": self.since, "pnl": list(self.pnl_hist),
                                                      "wins": self.wins, "losses": self.losses})

    def restore(self, saved: dict | None):
        """since, the P&L line and the win count from book.json (the state itself comes through load_book)."""
        if saved:
            self.since = saved.get("since") or saved.get("saved") or self.since
            self.pnl_hist.extend(p for p in saved.get("pnl") or [] if isinstance(p, list) and len(p) == 2)
            self.wins, self.losses = int(saved.get("wins") or 0), int(saved.get("losses") or 0)

    def write_live(self, now: float):
        if self.dirty:
            _write(os.path.join(self.data, "live.json"), self.view(now))
            self.dirty = False


def _write(path: str, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(obj, f, separators=(",", ":"))
    os.replace(path + ".tmp", path)


def saved_book(product: str, data: str = DATA) -> dict | None:
    try:
        with open(os.path.join(data, "book.json")) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("product") == product else None


def load_book(product: str, data: str = DATA) -> rt.State | None:
    """The saved book if it is for this product, else None (a new product starts a new book)."""
    try:
        with open(os.path.join(data, "book.json")) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return rt.load(d["state"]) if d.get("product") == product and isinstance(d.get("state"), dict) else None


# ------------------------------------------------------------------ the network

def past_bars(product: str, minutes: int = 300) -> list:
    """The last closed one-minute candles from Coinbase's public REST API, oldest first (for the averages at start)."""
    import urllib.request
    req = urllib.request.Request(f"{REST}/products/{product}/candles?granularity=60", headers={"User-Agent": "charles-fetch"})
    with urllib.request.urlopen(req, timeout=20) as r:
        rows = json.loads(r.read())
    now = time.time()
    out = [rt.Bar(float(t), op, hi, lo, cl, vol) for t, lo, hi, op, cl, vol in rows if t + 60 <= now]
    return sorted(out)[-minutes:]


def _iso_ts(s: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


async def run(product: str):
    import websockets
    s = settings_from()
    eng = Engine(product, s, load_book(product))
    eng.restore(saved_book(product))
    try:
        eng.warm(past_bars(product))
    except Exception as e:                                # noqa: BLE001
        print(f"warm-up failed, collecting live: {type(e).__name__}: {e}", flush=True)
    sub = {"type": "subscribe", "product_ids": [product], "channels": ["matches", "ticker"]}
    wait = 1
    while True:
        try:
            async with websockets.connect(WS, ping_interval=20, max_size=2 ** 20) as ws:
                await ws.send(json.dumps(sub))
                print(f"connected: {product}", flush=True)
                wait = 1
                last_write = 0.0
                while True:
                    try:
                        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
                    except asyncio.TimeoutError:
                        msg = None
                    now = time.time()
                    if msg:
                        typ = msg.get("type")
                        if typ == "ticker":
                            eng.on_quote(_num(msg.get("best_bid")), _num(msg.get("best_ask")))
                        elif typ in ("match", "last_match") and msg.get("price"):
                            for e in eng.on_trade(_iso_ts(msg["time"]), float(msg["price"]), float(msg.get("size") or 0), msg.get("side") == "sell" and "buy" or "sell"):
                                print(rt.say(e), flush=True)
                    for e in eng.on_clock(now):
                        print(rt.say(e), flush=True)
                    if now - last_write >= WRITE_EVERY:
                        eng.write_live(now)
                        last_write = now
        except Exception as e:                            # noqa: BLE001
            print(f"feed dropped: {type(e).__name__}: {e}; again in {wait}s", flush=True)
            eng.save_book()
            await asyncio.sleep(wait)
            wait = min(wait * 2, 60)


# ------------------------------------------------------------------ a stock: Alpaca's free IEX stream (read-only market data)

def rfc_ts(s: str) -> float:
    """Alpaca's RFC 3339 time (nanoseconds, Z) as unix seconds."""
    s = s.replace("Z", "+00:00")
    if "." in s:
        head, rest = s.split(".", 1)
        i = next((k for k, ch in enumerate(rest) if not ch.isdigit()), len(rest))
        s = f"{head}.{rest[:i][:6].ljust(6, '0')}{rest[i:]}"
    from datetime import datetime
    return datetime.fromisoformat(s).timestamp()


def _alpaca_keys() -> dict:
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
    except ImportError:
        pass
    k, sec = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not (k and sec):
        print("A stock needs ALPACA_API_KEY and ALPACA_SECRET_KEY in .env; trying again in 10 minutes.", flush=True)
        time.sleep(600)                                   # systemd restarts it: a quiet retry, not a loop every 15 seconds
        raise SystemExit("A stock needs ALPACA_API_KEY and ALPACA_SECRET_KEY (read-only market data; no order is ever sent)")
    return {"key": k, "secret": sec}


def _get(url: str, keys: dict):
    import urllib.request
    req = urllib.request.Request(url, headers={"APCA-API-KEY-ID": keys["key"], "APCA-API-SECRET-KEY": keys["secret"],
                                               "User-Agent": "charles-fetch"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _et_day(now: float) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(now, ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def session_today(keys: dict, now: float | None = None):
    """Today's US session from Alpaca's calendar (a half day has its own close), or None on a day the market is shut."""
    day = _et_day(now or time.time())
    import urllib.error
    for i, base in enumerate(STOCK_CAL):
        try:
            rows = _get(f"{base}?start={day}&end={day}", keys)
            break
        except urllib.error.HTTPError as e:
            if e.code not in (401, 403) or i == len(STOCK_CAL) - 1:
                raise
    for r in rows or []:
        if r.get("date") == day:
            return rt.us_session(day, r.get("open", "09:30"), r.get("close", "16:00"))
    return None


def stock_past_bars(symbol: str, keys: dict, session) -> list:
    """Today's closed one-minute IEX bars so far (the averages at a restart mid-session); none before the open."""
    if not session or time.time() < session.open:
        return []
    from datetime import datetime, timezone
    start = datetime.fromtimestamp(session.open, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    d = _get(f"{STOCK_DATA}/stocks/{symbol}/bars?timeframe=1Min&feed=iex&limit=1000&start={start}", keys)
    now = time.time()
    out = [rt.Bar(rfc_ts(b["t"]), b["o"], b["h"], b["l"], b["c"], b["v"]) for b in (d.get("bars") or [])]
    return [b for b in out if b.ts + 60 <= now and b.ts < session.close]


async def run_stock(symbol: str):
    import websockets
    keys = _alpaca_keys()
    s = settings_from()
    eng = Engine(symbol, s, load_book(symbol))
    eng.restore(saved_book(symbol))
    day = None
    wait = 1
    while True:
        try:
            async with websockets.connect(STOCK_WS, ping_interval=20, max_size=2 ** 20) as ws:
                await ws.send(json.dumps({"action": "auth", **keys}))
                for _ in range(3):                        # "connected", then "authenticated" (or an error)
                    got = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                    if any(m.get("T") == "error" for m in got):
                        raise RuntimeError(f"Alpaca: {got}")
                    if any(m.get("msg") == "authenticated" for m in got):
                        break
                await ws.send(json.dumps({"action": "subscribe", "trades": [symbol], "quotes": [symbol]}))
                print(f"connected: {symbol} (IEX)", flush=True)
                wait = 1
                last_write = 0.0
                while True:
                    now = time.time()
                    today = _et_day(now)
                    if today != day:                      # once a day (and at start): the session, and today's bars so far
                        eng.session = session_today(keys, now)
                        day = today
                        try:
                            past = stock_past_bars(symbol, keys, eng.session)
                            if past:                      # a restart mid-session: the averages from today's bars, the book kept
                                rt.warm(eng.st, past)
                                eng.closed.extend(past[-BARS:])
                        except Exception as e:            # noqa: BLE001
                            print(f"warm-up failed, collecting live: {type(e).__name__}: {e}", flush=True)
                    try:
                        msgs = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
                    except asyncio.TimeoutError:
                        msgs = []
                    for m in msgs if isinstance(msgs, list) else [msgs]:
                        typ = m.get("T")
                        if typ == "q":
                            eng.on_quote(_num(m.get("bp")), _num(m.get("ap")))
                        elif typ == "t" and m.get("p"):
                            p = float(m["p"])
                            side = taker_side(p, eng.bid, eng.ask, eng.last_price)
                            for e in eng.on_trade(rfc_ts(m["t"]), p, float(m.get("s") or 0), side):
                                print(rt.say(e), flush=True)
                        elif typ == "error":
                            raise RuntimeError(f"Alpaca: {m.get('code')} {m.get('msg')}")
                    for e in eng.on_clock(now):
                        print(rt.say(e), flush=True)
                    if now - last_write >= WRITE_EVERY:
                        eng.write_live(now)
                        last_write = now
        except Exception as e:                            # noqa: BLE001
            print(f"feed dropped: {type(e).__name__}: {e}; again in {wait}s", flush=True)
            eng.save_book()
            await asyncio.sleep(wait)
            wait = min(wait * 2, 60)


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--product", default=None)
    a = ap.parse_args()
    product = a.product or product_from()
    asyncio.run(run_stock(product) if is_stock(product) else run(product))


if __name__ == "__main__":
    main()
