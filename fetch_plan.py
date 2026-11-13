"""
fetch_plan.py — Charles Fetch: one ticker, watched trade by trade. Its rules as pure functions (no network, no files, no clock).
Practice money only.

One ticker, long only, one position at a time, the whole book (a SIZE share of it) in or out, one-minute bars.
Three rules, picked by RULE:
  pullback  The trend is up (close above the SLOW minute average, the FAST average above the SLOW one), the price fell at least
            DIP% from the highest high of the last LOOK minutes, and a bar closes back above the FAST average: buy.
  breakout  A bar closes above the highest high of the previous LOOK minutes while its close is above the SLOW average: buy.
  revert    The close is at least DIP% below the SLOW average while the SLOW average is above where it was SLOW minutes ago: buy.
            It also sells when the close returns to the SLOW average.
Exits, for all: TARGET% gain, STOP% loss, HOLD minutes in the trade, and (pullback and breakout) a close below the SLOW average.
Guards: MAX_TRADES a day, COOL minutes of rest after a losing trade, DAY_STOP% (no new entry once the day's loss reaches it).
A stock (a `session` is passed) also takes no entry in the first 5 or the last 15 minutes and is flat by the close, never overnight.
Percent settings (DIP, TARGET, STOP, DAY_STOP) are written as percent: 0.5 means 0.5%. SLIP and FEE are fractions per side.

The streaming core: State + step(state, bar, settings, session) is fed ONE CLOSED bar at a time and returns a list of Events. The
replay (ops/lab/fetch_replay.py) calls this very function, so a replay and a live engine can never disagree.

Fill model (applied inside step, in this order for each bar):
  1. An order signalled on the PREVIOUS bar's close fills at THIS bar's open, a buy worsened by SLIP (price * (1 + SLIP)),
     a sell worsened by SLIP (price * (1 - SLIP)). Every fill pays FEE of its value, so a trade's result is
     (1 - FEE)^2 * sell / buy - 1 once both prices are slipped.
  2. While in a position, this bar's low at or under the stop sells at the stop price (at the bar's open if it gapped under the
     stop); otherwise its high at or over the target sells at the target. If one bar touches both, the STOP came first. Slip applies
     to these sells too. The bar a buy fills on is checked the same way.
  3. A stock's last session minute sells at that bar's close (reason "close"), so nothing is held overnight.
  4. Then the bar's close is read for the exit signals (time, trend) and the entry signal; they fill on the next bar's open.
Minute bars cannot show what happened inside a minute: that is a stated limit, not a detail.

A live engine also calls tick(state, ts, price, settings, bid, ask) on every trade of the tape: the waiting order fills at once at the
market's own buying or selling price, and the stop and the target act the moment a trade reaches them. warm() gives it past bars for the
averages, dump()/load() keep the state in a file.

watch(state, price, settings) is for the time BETWEEN orders: it changes nothing and returns an Event of kind "watch" saying what the rules are
waiting for and how far away it is (flat), or how the open trade stands (the distance to the target and the stop, the minutes left).
say(event) turns an event into one or two plain sentences for a narrator, from the event's numbers only.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import NamedTuple, Optional
from zoneinfo import ZoneInfo

RULES = ("pullback", "breakout", "revert")

DEFAULTS = {
    "RULE": "pullback",
    "SIZE": 1.0,               # the share of the book bought; 1.0 is the whole book
    "FAST": 20,                # minutes in the fast average
    "SLOW": 60,                # minutes in the slow average (the trend line)
    "LOOK": 30,                # minutes looked back for the highest high
    "DIP": 0.3,                # pullback: the dip from that high; revert: the gap under the slow average (percent)
    "TARGET": 0.5,             # sell at this gain (percent)
    "STOP": 0.25,              # sell at this loss (percent)
    "HOLD": 90,                # sell after this many minutes in the trade
    "MAX_TRADES": 6,           # buys a day
    "COOL": 10,                # minutes without a new buy after a losing trade
    "DAY_STOP": 1.0,           # no new buy once the day's realized loss reaches this (percent of the day's starting book)
    "SLIP": 0.0001,            # worse price per side, as a fraction (0.0001 is 1 basis point)
    "FEE": 0.0,                # charged per side, as a fraction of the value traded
}

# name: (min, max, type)
LIMITS = {
    "SIZE":       (0.05, 1.0,   float),
    "FAST":       (2,    60,    int),
    "SLOW":       (5,    240,   int),
    "LOOK":       (5,    240,   int),
    "DIP":        (0.05, 5.0,   float),
    "TARGET":     (0.05, 10.0,  float),
    "STOP":       (0.05, 10.0,  float),
    "HOLD":       (5,    720,   int),
    "MAX_TRADES": (1,    50,    int),
    "COOL":       (0,    240,   int),
    "DAY_STOP":   (0.1,  10.0,  float),
    "SLIP":       (0.0,  0.01,  float),
    "FEE":        (0.0,  0.02,  float),
}

SKIP_OPEN = 5 * 60             # a stock: no buy in the first 5 minutes of the session
SKIP_CLOSE = 15 * 60           # ... or the last 15
GAP = 15 * 60                  # a hole this long in the bars restarts the averages


class Bar(NamedTuple):
    ts: float                  # the minute's start, unix seconds
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class Session(NamedTuple):
    """One stock session as the caller knows it (the bot reads no clock). `day` is any label; open and close are unix seconds."""
    day: str
    open: float
    close: float


def us_session(day: str, open_time: str = "09:30", close_time: str = "16:00") -> Session:
    """The regular US session of `day` (YYYY-MM-DD). A half day passes its own close_time."""
    tz = ZoneInfo("America/New_York")
    d = datetime.fromisoformat(day)

    def at(hm):
        h, m = (int(x) for x in hm.split(":"))
        return d.replace(hour=h, minute=m, tzinfo=tz).timestamp()
    return Session(day, at(open_time), at(close_time))


def check(overrides: dict | None) -> dict:
    """The overrides exactly as given if they are all valid; ValueError saying why otherwise (the refuse half of the house pattern)."""
    o = dict(overrides or {})
    for k, v in o.items():
        if k == "RULE":
            if v not in RULES:
                raise ValueError(f"RULE must be one of {', '.join(RULES)}")
        elif k in LIMITS:
            lo, hi, typ = LIMITS[k]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
                raise ValueError(f"{k} must be a number")
            if typ is int and v != int(v):
                raise ValueError(f"{k} must be a whole number")
            if not lo <= v <= hi:
                raise ValueError(f"{k} must be between {lo} and {hi}")
        else:
            raise ValueError(f"unknown setting {k}")
    s = {**DEFAULTS, **o}
    if s["FAST"] >= s["SLOW"]:
        raise ValueError("FAST must be shorter than SLOW")
    return o


def settings(overrides: dict | None = None) -> dict:
    """DEFAULTS with the overrides on top, each clamped to LIMITS (the clamp half of the house pattern). Unknown keys, a bad RULE and
    values that are not numbers are ignored, and FAST is kept shorter than SLOW."""
    s = dict(DEFAULTS)
    for k, v in (overrides or {}).items():
        if k == "RULE":
            if v in RULES:
                s[k] = v
        elif k in LIMITS and not isinstance(v, bool) and isinstance(v, (int, float)) and v == v:
            lo, hi, typ = LIMITS[k]
            s[k] = typ(min(max(v, lo), hi))
    if s["FAST"] >= s["SLOW"]:
        s["FAST"] = s["SLOW"] - 1
    return s


@dataclass
class Event:
    kind: str                          # "buy" or "sell"
    ts: float                          # the bar the fill happened on
    rule: str
    price: float                       # the fill, after slip
    basis: float                       # the price before slip (open, stop, target or close)
    numbers: dict = field(default_factory=dict)   # what triggered it
    stop: Optional[float] = None       # levels in force (a buy: the new ones; a sell: the ones it had)
    target: Optional[float] = None
    reason: Optional[str] = None       # sell only: target, stop, time, trend or close
    result_pct: Optional[float] = None  # sell only: the trade's result after slip and fees
    pnl: Optional[float] = None        # sell only: in the book's own money
    trade_n: int = 0                   # the day's count of buys, including this trade
    max_trades: int = 0
    held_min: Optional[float] = None   # sell only

    def asdict(self) -> dict:
        return asdict(self)


@dataclass
class State:
    cash: float = 1.0                  # the book's money (set to e.g. 1000.0 for a $ narrator)
    pos: Optional[dict] = None         # {entry, qty, invest, ts, stop, target, rule}
    pending: Optional[tuple] = None    # ("buy", numbers) or ("sell", reason, numbers): fills on the next bar's open
    day: object = None
    day_start: float = 1.0
    trades_today: int = 0
    cool_until: float = 0.0
    last_ts: float = 0.0
    c: list = field(default_factory=list)
    h: list = field(default_factory=list)
    l: list = field(default_factory=list)


def new_state(cash: float = 1.0) -> State:
    return State(cash=cash, day_start=cash)


def value(state: State, price: float) -> float:
    """The book marked at `price`."""
    return state.cash + (state.pos["qty"] * price if state.pos else 0.0)


def bar_exit(stop: float, target: float, bar: Bar):
    """(reason, price before slip) if this bar reaches the stop or the target, else None. The stop wins a bar that touches both,
    and a bar that gapped under the stop sells at its open."""
    if bar.low <= stop:
        return "stop", min(stop, bar.open)
    if bar.high >= target:
        return "target", target
    return None


def signal_exit(rule: str, held_min: float, close: float, slow: Optional[float], s: dict) -> Optional[str]:
    """The exit read at a bar's close: "time", "trend" (pullback/breakout: close under the slow average; revert: back at it) or None."""
    if held_min >= s["HOLD"]:
        return "time"
    if slow is not None:
        if rule == "revert":
            if close >= slow:
                return "trend"
        elif close < slow:
            return "trend"
    return None


def _avg(xs: list, n: int) -> Optional[float]:
    return sum(xs[-n:]) / n if len(xs) >= n else None


def entry_signal(rule: str, c: list, h: list, l: list, s: dict) -> Optional[dict]:
    """The numbers that triggered a buy on the latest closed bar, or None. c, h, l: closes, highs, lows, oldest first, latest last."""
    slow_n, fast_n, look = s["SLOW"], s["FAST"], s["LOOK"]
    slow = _avg(c, slow_n)
    if slow is None:
        return None
    close = c[-1]
    if rule == "pullback":
        fast = _avg(c, fast_n)
        if len(c) < max(slow_n, fast_n + 1, look) or not (close > slow and fast > slow and close > fast):
            return None
        prev_fast = sum(c[-fast_n - 1:-1]) / fast_n
        if c[-2] > prev_fast:
            return None                              # already above its fast average: not "back above"
        highs = h[-look:]
        top = max(highs)
        at = len(highs) - 1 - highs[::-1].index(top)
        low = min(l[-look:][at:])
        dip = (top - low) / top * 100
        if dip < s["DIP"]:
            return None
        return {"close": close, "fast": fast, "slow": slow, "high": top, "dip_pct": dip, "fast_minutes": fast_n, "look_minutes": look}
    if rule == "breakout":
        if len(h) < look + 1:
            return None
        top = max(h[-look - 1:-1])
        if not (close > top and close > slow):
            return None
        return {"close": close, "slow": slow, "high": top, "look_minutes": look, "slow_minutes": slow_n}
    if rule == "revert":
        if len(c) < 2 * slow_n:
            return None
        ago = sum(c[-2 * slow_n:-slow_n]) / slow_n
        gap = (slow - close) / slow * 100
        if gap < s["DIP"] or not slow > ago:
            return None
        return {"close": close, "slow": slow, "slow_ago": ago, "gap_pct": gap, "slow_minutes": slow_n}
    raise ValueError(f"unknown rule {rule}")


def _buy(state: State, bar: Bar, s: dict, numbers: dict) -> Event:
    entry = bar.open * (1 + s["SLIP"])
    invest = state.cash * s["SIZE"]
    qty = invest * (1 - s["FEE"]) / entry
    state.cash -= invest
    stop, target = entry * (1 - s["STOP"] / 100), entry * (1 + s["TARGET"] / 100)
    state.pos = {"entry": entry, "qty": qty, "invest": invest, "ts": bar.ts, "stop": stop, "target": target, "rule": s["RULE"]}
    state.trades_today += 1
    return Event("buy", bar.ts, s["RULE"], entry, bar.open, dict(numbers), stop, target,
                 trade_n=state.trades_today, max_trades=s["MAX_TRADES"])


def _sell(state: State, bar: Bar, raw: float, reason: str, s: dict, numbers: dict) -> Event:
    p = state.pos
    price = raw * (1 - s["SLIP"])
    net = p["qty"] * price * (1 - s["FEE"])
    state.cash += net
    state.pos = None
    pnl = net - p["invest"]
    if pnl < 0:
        state.cool_until = bar.ts + s["COOL"] * 60
    return Event("sell", bar.ts, p["rule"], price, raw, dict(numbers), p["stop"], p["target"], reason, (net / p["invest"] - 1) * 100,
                 pnl, state.trades_today, s["MAX_TRADES"], (bar.ts - p["ts"]) / 60)


def _may_buy(state: State, bar: Bar, s: dict, session: Optional[Session]) -> bool:
    fill_at = bar.ts + 60
    if state.trades_today >= s["MAX_TRADES"] or fill_at < state.cool_until:
        return False
    if (state.cash / state.day_start - 1) * 100 <= -s["DAY_STOP"]:
        return False
    if session and not session.open + SKIP_OPEN <= fill_at < session.close - SKIP_CLOSE:
        return False
    return True


def step(state: State, bar: Bar, settings: dict, session: Optional[Session] = None) -> list:
    """Feed one CLOSED bar. Changes `state`, returns the events (buy, sell) that happened on this bar, in order.
    session None: a 24 hour coin, days cut at 00:00 UTC. A Session: a stock; its day, open and close come from the caller."""
    s, ev = settings, []
    day = session.day if session else int(bar.ts // 86400)
    if state.day != day:
        if state.day is not None and session:        # a new stock session: nothing carries over
            if state.pos:
                ev.append(_sell(state, bar, bar.open, "close", s, {"close": bar.open}))
            state.pending = None
            state.c, state.h, state.l = [], [], []
        state.day, state.day_start, state.trades_today = day, value(state, bar.open), 0
    elif state.last_ts and bar.ts - state.last_ts > GAP:
        state.pending = None
        state.c, state.h, state.l = [], [], []
    state.last_ts = bar.ts

    if state.pending:                                # 1. last bar's signal fills at this open
        order, state.pending = state.pending, None
        if order[0] == "buy" and not state.pos:
            ev.append(_buy(state, bar, s, order[1]))
        elif order[0] == "sell" and state.pos:
            ev.append(_sell(state, bar, bar.open, order[1], s, order[2]))

    if state.pos:                                    # 2. the stop and the target inside this bar
        hit = bar_exit(state.pos["stop"], state.pos["target"], bar)
        if hit:
            ev.append(_sell(state, bar, hit[1], hit[0], s, {"close": bar.close, "level": hit[1]}))
        elif session and bar.ts + 60 >= session.close:   # 3. flat by the close
            ev.append(_sell(state, bar, bar.close, "close", s, {"close": bar.close}))

    state.c.append(bar.close)
    state.h.append(bar.high)
    state.l.append(bar.low)
    cap = 2 * max(2 * s["SLOW"], s["LOOK"] + 2, s["FAST"] + 2)
    if len(state.c) > cap:
        keep = cap // 2
        state.c, state.h, state.l = state.c[-keep:], state.h[-keep:], state.l[-keep:]

    if session and bar.ts + 60 >= session.close:    # 4. the close's reading: nothing new at the end of a session
        return ev
    slow = _avg(state.c, s["SLOW"])
    if state.pos and not state.pending:
        why = signal_exit(state.pos["rule"], (bar.ts - state.pos["ts"]) / 60, bar.close, slow, s)
        if why:
            state.pending = ("sell", why, {"close": bar.close, "slow": slow, "held_min": (bar.ts - state.pos["ts"]) / 60})
    elif not state.pos and not state.pending and _may_buy(state, bar, s, session):
        sig = entry_signal(s["RULE"], state.c, state.h, state.l, s)
        if sig:
            state.pending = ("buy", sig)
    return ev


# ---------------------------------------------------------------- a live engine's extras

def tick(state: State, ts: float, price: float, settings: dict, bid: Optional[float] = None, ask: Optional[float] = None) -> list:
    """One live trade on the tape, between closed bars. A live engine calls this on every trade and step() on every closed bar; a
    replay calls only step(). It does two things step() can only do a minute late:
      1. An order signalled on the last closed bar fills NOW: a buy at the market's selling price (`ask`, else this trade's price),
         a sell at its buying price (`bid`, else this trade's price), each worsened by SLIP like every fill.
      2. A trade at or under the stop sells there (lower if the market is already lower); a trade at or over the target sells at the
         target, the way an order resting at that price would. The trade that filled a buy is not checked.
    step() then finds no order waiting and no position past its levels, so the two never act twice."""
    s, ev = settings, []
    if state.pending:
        order, state.pending = state.pending, None
        if order[0] == "buy" and not state.pos:
            p = ask if ask else price
            ev.append(_buy(state, Bar(ts, p, p, p, p), s, order[1]))
            return ev
        if order[0] == "sell" and state.pos:
            p = bid if bid else price
            ev.append(_sell(state, Bar(ts, p, p, p, p), p, order[1], s, order[2]))
            return ev
    pos = state.pos
    if pos:
        if price <= pos["stop"]:
            raw = min(pos["stop"], bid if bid else price)
            ev.append(_sell(state, Bar(ts, raw, raw, raw, raw), raw, "stop", s, {"close": price, "level": pos["stop"]}))
        elif price >= pos["target"]:
            raw = pos["target"]
            ev.append(_sell(state, Bar(ts, raw, raw, raw, raw), raw, "target", s, {"close": price, "level": raw}))
    return ev


def warm(state: State, bars: list) -> None:
    """Past closed bars for the averages only: nothing is bought or sold on them. A live engine calls this once at its start, so
    it does not sit through SLOW minutes of "still collecting prices". Whatever the state had collected is replaced."""
    state.c, state.h, state.l = [b.close for b in bars], [b.high for b in bars], [b.low for b in bars]
    if bars:
        state.last_ts = bars[-1].ts


def dump(state: State) -> dict:
    """The state as plain JSON types, for a book file."""
    d = asdict(state)
    d["pending"] = list(state.pending) if state.pending else None
    return d


def load(d: dict) -> State:
    """The state back from dump(). Unknown keys are ignored, missing ones take their defaults."""
    st = State()
    for k in ("cash", "pos", "day", "day_start", "trades_today", "cool_until", "last_ts", "c", "h", "l"):
        if k in d:
            setattr(st, k, d[k])
    st.pending = tuple(d["pending"]) if d.get("pending") else None
    return st


# ---------------------------------------------------------------- between orders

def watch(state: State, price: float, settings: dict, session: Optional[Session] = None, ts: Optional[float] = None) -> Event:
    """What the bot is doing right now, as an Event of kind "watch". Pure: `state` is not changed, so a live engine can call it on any
    trade. `ts` defaults to the last bar's time. In a position: the trade's gain, the distance to the target and the stop, the minutes
    left. Flat: numbers["waiting"] names what holds it back (cap, daystop, cool, edge_open, edge_close, warmup, or the rule's own
    trend, dip, cross, level) and how far away that is."""
    s, now = settings, (state.last_ts if ts is None else ts)
    p = state.pos
    if p:
        n = {"waiting": "exit", "gain_pct": (price / p["entry"] - 1) * 100, "to_target_pct": (p["target"] / price - 1) * 100,
             "to_stop_pct": (1 - p["stop"] / price) * 100, "minutes_left": max(0.0, s["HOLD"] - (now - p["ts"]) / 60)}
        return Event("watch", now, p["rule"], price, price, n, p["stop"], p["target"], trade_n=state.trades_today, max_trades=s["MAX_TRADES"])
    rule, n = s["RULE"], {}
    if state.trades_today >= s["MAX_TRADES"]:
        n = {"waiting": "cap"}
    elif (state.cash / state.day_start - 1) * 100 <= -s["DAY_STOP"]:
        n = {"waiting": "daystop", "limit_pct": s["DAY_STOP"]}
    elif now < state.cool_until:
        n = {"waiting": "cool", "minutes": (state.cool_until - now) / 60}
    elif session and now + 60 < session.open + SKIP_OPEN:
        n = {"waiting": "edge_open", "minutes": (session.open + SKIP_OPEN - now - 60) / 60}
    elif session and now + 60 >= session.close - SKIP_CLOSE:
        n = {"waiting": "edge_close"}
    else:
        c, h, l = state.c, state.h, state.l
        slow, fast, look = _avg(c, s["SLOW"]), _avg(c, s["FAST"]), s["LOOK"]
        need = {"pullback": max(s["SLOW"], s["FAST"] + 1, look), "breakout": max(s["SLOW"], look + 1), "revert": 2 * s["SLOW"]}[rule]
        if len(c) < need:
            n = {"waiting": "warmup", "have": len(c), "need": need}
        elif rule == "pullback":
            if not (price > slow and fast > slow):
                n = {"waiting": "trend", "slow": slow, "slow_minutes": s["SLOW"], "fast": fast, "fast_minutes": s["FAST"]}
            else:
                highs = h[-look:]
                top = max(highs)
                dip = (top - min(l[-look:][len(highs) - 1 - highs[::-1].index(top):])) / top * 100
                if dip < s["DIP"]:
                    n = {"waiting": "dip", "dip_pct": dip, "need_pct": s["DIP"] - dip, "high": top}
                else:
                    n = {"waiting": "cross", "level": fast, "fast_minutes": s["FAST"], "gap_pct": (fast / price - 1) * 100}
        elif rule == "breakout":
            level = max(max(h[-look:]), slow)
            n = {"waiting": "level", "level": level, "gap_pct": (level / price - 1) * 100, "look_minutes": look}
        else:
            level = slow * (1 - s["DIP"] / 100)
            ago = sum(c[-2 * s["SLOW"]:-s["SLOW"]]) / s["SLOW"]
            n = {"waiting": "level", "level": level, "gap_pct": (price / level - 1) * 100, "slow_minutes": s["SLOW"], "rising": slow > ago}
    return Event("watch", now, rule, price, price, n, trade_n=state.trades_today, max_trades=s["MAX_TRADES"])


# ---------------------------------------------------------------- narrator

def _p(x: float) -> str:
    return f"{x:,.2f}"


def _pct(x: float) -> str:
    return f"{x:.1f}" if abs(x) >= 0.1 else f"{x:.2f}"


def _money(x: float) -> str:
    return f"${abs(x):,.2f}"


def say(e: Event) -> str:
    """One or two plain sentences for an event, built only from its numbers. No advice, no predictions."""
    n = e.numbers
    if e.kind == "watch":
        return _say_watch(e)
    if e.kind == "buy":
        lead = {
            "pullback": f"Price is back above its {n.get('fast_minutes')} minute average after a {_pct(n.get('dip_pct', 0))}% dip.",
            "breakout": f"Price closed above its {n.get('look_minutes')} minute high with the trend up.",
            "revert": f"Price is {_pct(n.get('gap_pct', 0))}% under its {n.get('slow_minutes')} minute average, which is rising.",
        }[e.rule]
        return f"{lead} Buying at {_p(e.price)}. I will sell at {_p(e.target)} for a gain, or at {_p(e.stop)} if I am wrong."
    won = (e.pnl or 0) > 0
    result = ("a gain of " + _money(e.pnl)) if won else ("a loss of " + _money(e.pnl)) if (e.pnl or 0) < 0 else "no gain or loss"
    count = f"That is trade {e.trade_n} of {e.max_trades} today."
    held = f"{e.held_min:.0f}"
    lead = {
        "target": "Target reached.",
        "stop": "Stop reached.",
        "time": f"The {held} minute time limit is up.",
        "trend": ("Price is back at its average." if e.rule == "revert" else "Price closed under its slow average."),
        "close": "The session is ending, so I am out for the night.",
    }[e.reason]
    return f"{lead} Sold at {_p(e.price)} for {result}. {count}"


def _say_watch(e: Event) -> str:
    n, w = e.numbers, e.numbers["waiting"]
    if w == "exit":
        g = n["gain_pct"]
        lead = f"Up {_pct(g)}% on this trade." if g >= 0.005 else f"Down {_pct(-g)}% on this trade." if g <= -0.005 else "Even on this trade."
        return f"{lead} Target {_p(e.target)}, stop {_p(e.stop)}, {n['minutes_left']:.0f} minutes left."
    if w == "cap":
        return f"Done for today: {e.trade_n} of {e.max_trades} trades used."
    if w == "daystop":
        return f"Done for today: the day's loss limit of {_pct(n['limit_pct'])}% is reached."
    if w == "cool":
        return f"Resting after a loss. About {n['minutes']:.0f} more minutes before I look for a buy."
    if w == "edge_open":
        return f"The session has only just opened. I start looking for a buy in {n['minutes']:.0f} minutes."
    if w == "edge_close":
        return "Too close to the end of the session for a new buy."
    if w == "warmup":
        return f"Still collecting prices: {n['have']} of {n['need']} minutes so far."
    if w == "trend":
        return (f"The trend is not up yet. I need price above its {n['slow_minutes']} minute average at {_p(n['slow'])}, "
                f"with the {n['fast_minutes']} minute average above that.")
    if w == "dip":
        return f"The trend is up. I am waiting for a dip of {_pct(n['need_pct'])}% more from the recent high of {_p(n['high'])}."
    if w == "cross":
        if n["gap_pct"] > 0:
            return f"Price is {_pct(n['gap_pct'])}% under my {n['fast_minutes']} minute average. I am waiting for a close above {_p(n['level'])}."
        return f"Price is above its {n['fast_minutes']} minute average already. I am waiting for a dip under it and a close back above."
    if w == "level" and e.rule == "revert":
        tail = "" if n["rising"] else f" The {n['slow_minutes']} minute average is not rising, so I am waiting."
        return f"Price is {_pct(n['gap_pct'])}% above the level I buy at. I am waiting for a close at or under {_p(n['level'])}.{tail}"
    if n["gap_pct"] > 0:
        return f"Price is {_pct(n['gap_pct'])}% under the level it must clear. I am waiting for a close above {_p(n['level'])}."
    return f"Price is already above {_p(n['level'])}. I am waiting for a close that clears the {n['look_minutes']} minute high."
