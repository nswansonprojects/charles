"""
orb.py — Opening Range Breakout (ORB), a separate intraday sleeve beside Charles's AI strategy.

Off unless config.ORB_ENABLED. Each trading day, per symbol in ORB_SYMBOLS (liquid ETFs):
  1. Opening range = high/low of the first ORB_RANGE_MINUTES 1-min bars after 09:30 ET.
  2. A later 1-min bar that CLOSES above the range high → buy at the next bar (long-only:
     the paper account is a cash account and Alpaca refuses shorts there; ORB_ALLOW_SHORT
     exists for the backtest only). No entries after ORB_ENTRY_CUTOFF_MIN, at most
     ORB_MAX_TRADES_PER_DAY, and the range must be ORB_MIN/MAX_RANGE_ATR × daily ATR.
  3. Exits set at the fill: stop at the range low (or ORB_FIXED_STOP_PCT), target at
     ORB_REWARD_RISK × risk, optional trailing stop, and flat by ORB_FLATTEN_ET.

The pure functions (session_times … filled_exit) are the whole strategy; tick() runs them on
live Alpaca IEX bars every minute, and backtest_orb.py runs the same functions on history.
Decisions are made on a completed bar; the order fills on the next bar. The stop rests at the
broker as a DAY stop order; targets, trails and the flatten are market sells.

ORB positions are kept out of Charles's stock logic (alpaca_executor._stock_positions), and
Charles never buys a symbol ORB holds (and vice versa): one strategy per symbol at a time.
Trades: data/orb_trades.json, each tagged strategy="orb". State: data/orb.json.
"""
import json
import logging
import math
import os
import re
from datetime import date, datetime, time as dtime, timedelta, timezone

import config
import state_io
from timeofday import ET

log = logging.getLogger(__name__)

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
STATE_FILE = os.path.join(DATA, "orb.json")
TRADES_FILE = os.path.join(DATA, "orb_trades.json")
SWITCH_FILE = os.path.join(DATA, "control", "orb_switch.json")   # the dashboard on/off button
TAG = "charles-orb-"
OPEN = dtime(9, 30)
ACTIVE = ("entering", "in", "exiting")       # statuses where ORB owns shares or an order


# ── Settings ──────────────────────────────────────────────────────────────────

def settings(**over) -> dict:
    """The ORB settings from config (backtests pass overrides)."""
    cfg = {
        "symbols":        list(getattr(config, "ORB_SYMBOLS", ["QQQ"])),
        "range_min":      int(getattr(config, "ORB_RANGE_MINUTES", 5)),
        "cutoff_min":     int(getattr(config, "ORB_ENTRY_CUTOFF_MIN", 60)),
        "max_trades":     int(getattr(config, "ORB_MAX_TRADES_PER_DAY", 1)),
        "allow_short":    bool(getattr(config, "ORB_ALLOW_SHORT", False)),
        "min_range_atr":  float(getattr(config, "ORB_MIN_RANGE_ATR", 0.0)),
        "max_range_atr":  float(getattr(config, "ORB_MAX_RANGE_ATR", 0.0)),
        "stop_mode":      getattr(config, "ORB_STOP_MODE", "range"),
        "fixed_stop_pct": float(getattr(config, "ORB_FIXED_STOP_PCT", 0.005)),
        "reward_risk":    float(getattr(config, "ORB_REWARD_RISK", 1.5)),
        "trail_pct":      float(getattr(config, "ORB_TRAIL_PCT", 0.0)),
        "flatten":        getattr(config, "ORB_FLATTEN_ET", "15:55"),
        "max_risk":       float(getattr(config, "ORB_MAX_RISK_PER_TRADE", 5.0)),
        "max_pos_pct":    float(getattr(config, "MAX_POSITION_SIZE_PCT", 0.15)),
        "daily_loss":     float(getattr(config, "ORB_DAILY_LOSS_LIMIT", 10.0)),
        "use_regime":     bool(getattr(config, "ORB_USE_REGIME_FILTER", False)),
        "use_llm":        bool(getattr(config, "ORB_USE_LLM_CONFIRM", False)),
    }
    cfg.update(over)
    return cfg


# ── Pure strategy ─────────────────────────────────────────────────────────────

def session_times(day: date, cfg: dict, close: datetime | None = None) -> dict:
    """Key times for `day` in ET (zoneinfo handles DST). `close`: the real close on half days."""
    open_ = datetime.combine(day, OPEN, ET)
    close = close.astimezone(ET) if close else datetime.combine(day, dtime(16, 0), ET)
    h, m = (int(x) for x in cfg["flatten"].split(":"))
    flatten = min(datetime.combine(day, dtime(h, m), ET), close - timedelta(minutes=5))
    return {"open": open_, "range_end": open_ + timedelta(minutes=cfg["range_min"]),
            "cutoff": open_ + timedelta(minutes=cfg["cutoff_min"]), "flatten": flatten, "close": close}


def new_day(day: date, cfg: dict) -> dict:
    return {"day": day.isoformat(), "trades": 0, "pnl": 0.0, "disabled": None,
            "symbols": {s: {"status": "range", "range": None, "note": "", "last_bar": None}
                        for s in cfg["symbols"]}}


def opening_range(bars: list, times: dict) -> dict | None:
    """High/low of the bars that start inside the opening range window. bars: {t,o,h,l,c}."""
    inside = [b for b in bars if times["open"] <= b["t"] < times["range_end"]]
    if not inside:
        return None
    return {"high": max(b["h"] for b in inside), "low": min(b["l"] for b in inside), "bars": len(inside)}


def range_filter(rng: dict, atr: float, cfg: dict) -> str | None:
    """Why the day's range is unusable vs the daily ATR, or None. NaN/0 ATR skips when the filter is on."""
    lo, hi = cfg["min_range_atr"], cfg["max_range_atr"]
    if not lo and not hi:
        return None
    if not atr or atr != atr or atr <= 0:
        return "no usable ATR (NaN/0)"
    ratio = (rng["high"] - rng["low"]) / atr
    if lo and ratio < lo:
        return f"range {ratio:.2f}×ATR < {lo}"
    if hi and ratio > hi:
        return f"range {ratio:.2f}×ATR > {hi}"
    return None


def breakout(bar: dict, rng: dict, cfg: dict) -> str | None:
    """'long' if the bar closed above the range, 'short' below (when allowed)."""
    if bar["c"] > rng["high"]:
        return "long"
    if cfg["allow_short"] and bar["c"] < rng["low"]:
        return "short"
    return None


def exit_levels(side: str, entry: float, rng: dict, cfg: dict) -> tuple[float, float] | None:
    """(stop, target) for a fill at `entry`; None if the stop would be on the wrong side."""
    sign = 1 if side == "long" else -1
    if cfg["stop_mode"] == "fixed":
        stop = entry * (1 - sign * cfg["fixed_stop_pct"])
    else:
        stop = rng["low"] if side == "long" else rng["high"]
    risk = (entry - stop) * sign
    if risk <= 0:
        return None
    return round(stop, 4), round(entry + sign * cfg["reward_risk"] * risk, 4)


def size(entry: float, stop: float, equity: float, cash: float, cfg: dict, dd_mult: float = 1.0) -> float:
    """Shares: ORB_MAX_RISK_PER_TRADE at the stop, capped like a Charles position
    (MAX_POSITION_SIZE_PCT of equity × drawdown multiplier) and by cash."""
    risk = abs(entry - stop)
    if risk <= 0 or entry <= 0:
        return 0.0
    qty = min(cfg["max_risk"] / risk, equity * cfg["max_pos_pct"] * dd_mult / entry, cash * 0.95 / entry)
    return math.floor(max(qty, 0) * 1000) / 1000


def entry_blockers(st: dict, sym: str, cfg: dict, *, halted=False, held_elsewhere=False,
                   regime=None, llm=None, switched_off=False) -> list[str]:
    """Reasons not to take a signal (empty = go). regime: the regime name when the filter is on;
    llm: (ok, confidence) when the LLM check is on; switched_off: the dashboard button is off."""
    out = []
    if switched_off:
        out.append("ORB is switched off on the dashboard")
    if st["disabled"]:
        out.append(st["disabled"])
    if halted:
        out.append("Charles's daily loss limit is active")
    if st["trades"] >= cfg["max_trades"]:
        out.append(f"max {cfg['max_trades']} ORB trade(s) a day")
    if held_elsewhere:
        out.append(f"Charles holds {sym} (ORB skips symbols Charles trades)")
    if cfg["use_regime"] and regime is not None:
        import regime as reg_mod
        if not reg_mod.regime_multipliers(regime).get("allow_longs", True):
            out.append(f"regime {regime} allows no longs")
    if cfg["use_llm"] and llm is not None and not llm[0]:
        out.append(f"LLM did not confirm ({llm[1]:.0%})")
    return out


def on_bar(st: dict, sym: str, bar: dict, times: dict, cfg: dict, atr: float = 0.0) -> dict | None:
    """Feed one COMPLETED 1-min bar. Returns an action for the next bar, or None:
      {"do": "signal", "side", "price"}   a breakout (caller checks entry_blockers, sizes, buys)
      {"do": "exit", "reason"}            target hit on the close / flatten time
      {"do": "trail", "stop"}             move the stop up (trailing)
    Stop fills are not decided here: live they rest at the broker, the backtest uses stop_fill()."""
    s = st["symbols"][sym]
    s["last_bar"] = bar["t"].isoformat()
    end = bar["t"] + timedelta(minutes=1)
    if s["status"] == "range":
        s.setdefault("bars", []).append(bar)
        if end < times["range_end"]:
            return None
        s["range"] = opening_range(s.pop("bars", []), times)
        if not s["range"]:
            s["status"], s["note"] = "done", "no bars in the opening range"
            return None
        why = range_filter(s["range"], atr, cfg)
        if why:
            s["status"], s["note"] = "done", why
            return None
        s["status"], s["note"] = "watch", "waiting for a close outside the range"
        if end == times["range_end"]:
            return None                     # this bar was the last range bar
    if s["status"] == "watch":
        if end > times["cutoff"]:
            s["status"], s["note"] = "done", "no breakout before the cutoff"
            return None
        side = breakout(bar, s["range"], cfg)
        return {"do": "signal", "side": side, "price": bar["c"]} if side else None
    if s["status"] == "in":
        p = s["pos"]
        if end >= times["flatten"]:
            return {"do": "exit", "reason": "flatten"}
        long_ = p["side"] == "long"
        if (bar["c"] >= p["target"]) if long_ else (bar["c"] <= p["target"]):
            return {"do": "exit", "reason": "target"}
        if cfg["trail_pct"]:
            best = max(p.get("best", p["entry"]), bar["c"]) if long_ else min(p.get("best", p["entry"]), bar["c"])
            p["best"] = best
            trail = round(best * (1 - cfg["trail_pct"]) if long_ else best * (1 + cfg["trail_pct"]), 4)
            if (trail > p["stop"]) if long_ else (trail < p["stop"]):
                return {"do": "trail", "stop": trail}
    return None


def stop_fill(pos: dict, bar: dict) -> float | None:
    """Backtest: the fill price if `bar` touched the resting stop (gap-throughs fill at the open).
    Checked before the bar's close is used for anything, so a bar that hits both stop and
    target counts as a stop."""
    if pos["side"] == "long" and bar["l"] <= pos["stop"]:
        return min(bar["o"], pos["stop"])
    if pos["side"] == "short" and bar["h"] >= pos["stop"]:
        return max(bar["o"], pos["stop"])
    return None


def filled_entry(st: dict, sym: str, side: str, price: float, qty: float, ts: datetime, cfg: dict) -> dict | None:
    """Record an entry fill and set its stop/target. None (and no position) if the levels are invalid."""
    s = st["symbols"][sym]
    lv = exit_levels(side, price, s["range"], cfg)
    if not lv or qty <= 0:
        s["status"], s["note"] = "done", "entry fill left no room for a stop"
        return None
    s["pos"] = {"side": side, "entry": round(price, 4), "qty": qty, "stop": lv[0], "target": lv[1],
                "risk": round(abs(price - lv[0]), 4), "opened": ts.isoformat()}
    s["status"], s["note"] = "in", f"{side} {qty:g} @ {price:.2f}, stop {lv[0]:.2f}, target {lv[1]:.2f}"
    st["trades"] += 1
    return s["pos"]


def filled_exit(st: dict, sym: str, price: float, ts: datetime, reason: str, cfg: dict,
                costs: float = 0.0) -> dict:
    """Close the position; returns the trade record (strategy="orb"). Applies the ORB daily loss limit."""
    s = st["symbols"][sym]
    p = s.pop("pos")
    sign = 1 if p["side"] == "long" else -1
    pnl = round((price - p["entry"]) * p["qty"] * sign - costs, 2)
    st["pnl"] = round(st["pnl"] + pnl, 2)
    more = st["trades"] < cfg["max_trades"] and ts.astimezone(ET) < st_cutoff(st, cfg)
    s["status"] = "watch" if more else "done"
    s["note"] = f"closed ({reason}) {pnl:+.2f}"
    if st["pnl"] <= -abs(cfg["daily_loss"]) and not st["disabled"]:
        st["disabled"] = f"ORB daily loss limit ${cfg['daily_loss']:.0f} hit"
    return {"strategy": "orb", "day": st["day"], "symbol": sym, "side": p["side"],
            "entry_ts": p["opened"], "exit_ts": ts.isoformat(), "entry": p["entry"],
            "exit": round(price, 4), "qty": p["qty"], "stop": p["stop"], "target": p["target"],
            "range_high": s["range"]["high"], "range_low": s["range"]["low"],
            "pnl": pnl, "r": round((price - p["entry"]) * sign / p["risk"], 2) if p["risk"] else None,
            "reason": reason}


def st_cutoff(st: dict, cfg: dict) -> datetime:
    return datetime.combine(date.fromisoformat(st["day"]), OPEN, ET) + timedelta(minutes=cfg["cutoff_min"])


# ── State files ───────────────────────────────────────────────────────────────

def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def load_state() -> dict | None:
    st = _load(STATE_FILE, None)
    if st:
        for s in st.get("symbols", {}).values():
            if s.get("range") is None:
                s.pop("bars", None)          # rebuilt from the API after a restart
    return st


def save_state(st: dict):
    out = json.loads(json.dumps(st, default=str))
    for s in out.get("symbols", {}).values():
        s.pop("bars", None)
    state_io.write_json(STATE_FILE, out, indent=1)


def switched_on() -> bool:
    """The dashboard button. No file = on (ORB_ENABLED alone decides whether ORB runs at all)."""
    return bool(_load(SWITCH_FILE, {"on": True}).get("on", True))


def set_switch(on: bool, by: str = "dashboard") -> dict:
    """Off blocks new entries only; a trade already open keeps its stop/target and the 15:55 flatten."""
    rec = {"on": bool(on), "changed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "by": by}
    os.makedirs(os.path.dirname(SWITCH_FILE), exist_ok=True)
    state_io.write_json(SWITCH_FILE, rec)
    log.info(f"[orb] strategy=orb switched {'ON' if on else 'OFF'} by {by}")
    return rec


def held_symbols() -> set:
    """Symbols ORB owns shares or an order in right now (kept out of Charles's stock logic)."""
    st = _load(STATE_FILE, None) or {}
    return {sym for sym, s in st.get("symbols", {}).items() if s.get("status") in ACTIVE}


def trades(limit: int = 500) -> list:
    return _load(TRADES_FILE, [])[-limit:]


def _append_trade(rec: dict):
    rows = _load(TRADES_FILE, [])
    rows.append(rec)
    state_io.write_json(TRADES_FILE, rows, indent=1)


def summary() -> dict:
    """For the dashboard and the daily summary (files only, no API calls)."""
    st = _load(STATE_FILE, None)
    rows = trades()
    wins = [t for t in rows if t["pnl"] > 0]
    installed = bool(getattr(config, "ORB_ENABLED", False))
    return {"enabled": installed or bool(st), "installed": installed, "on": switched_on(), "today": st,
            "total_pnl": round(sum(t["pnl"] for t in rows), 2), "n": len(rows),
            "win_rate": round(len(wins) / len(rows) * 100, 1) if rows else None,
            "recent": rows[-15:], "symbols": settings()["symbols"]}


def summary_line(st: dict | None, rows: list) -> str:
    """One line for the Telegram daily summary."""
    if not st:
        return "ORB: no session yet"
    today = [t for t in rows if t.get("day") == st["day"]]
    total = round(sum(t["pnl"] for t in rows), 2)
    if today:
        what = ", ".join(f"{t['symbol']} {t['reason']} ${t['pnl']:+.2f}" for t in today)
    else:
        what = "; ".join(f"{sym} {s.get('note', '')}" for sym, s in st["symbols"].items()) or "no trade"
    return f"ORB today ${st['pnl']:+.2f} ({what}) | all-time ${total:+.2f} on {len(rows)} trades"


# ── Live (Alpaca paper) ───────────────────────────────────────────────────────

def _bars_today(syms: list, times: dict, now: datetime) -> dict:
    """Completed 1-min IEX bars since the open (free feed; the backtest uses SIP history)."""
    import credential_manager as sec
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    client = StockHistoricalDataClient(sec.alpaca_key(), sec.alpaca_secret())
    req = StockBarsRequest(symbol_or_symbols=syms, timeframe=TimeFrame.Minute,
                           start=times["open"].astimezone(timezone.utc), end=now.astimezone(timezone.utc),
                           feed="iex")
    out = {s: [] for s in syms}
    for sym, rows in client.get_stock_bars(req).data.items():
        for b in rows:
            t = b.timestamp.astimezone(ET)
            if t + timedelta(minutes=1) <= now:
                out[sym].append({"t": t, "o": b.open, "h": b.high, "l": b.low, "c": b.close})
    return out


def _daily_atr(sym: str) -> float:
    """Daily ATR(14) from completed days only (today's partial bar left out), Charles's ATR math."""
    import yfinance as yf
    import risk_manager as rm
    try:
        h = yf.Ticker(sym).history(period="30d", interval="1d").dropna(subset=["High", "Low", "Close"])
        h = h[h.index.date < datetime.now(ET).date()]
        return rm.atr_from_ohlc(h["High"].tolist(), h["Low"].tolist(), h["Close"].tolist(), 14)
    except Exception as e:
        log.warning(f"[orb] ATR for {sym} failed: {e}")
        return 0.0


def _llm_confirm(sym: str, side: str, rng: dict, price: float, regime: str) -> tuple[bool, float]:
    """The ORB signal through Charles's LLM and confidence bar (config.MIN_CONFIDENCE)."""
    import brain
    prompt = (f"You are the risk check for an intraday opening-range-breakout trade.\n"
              f"Symbol {sym}. Opening range high {rng['high']:.2f}, low {rng['low']:.2f}. "
              f"A 1-minute bar just closed at {price:.2f}, a {side} breakout. Market regime: {regime}.\n"
              f"The trade is closed by 15:55 ET at the latest. Reply only with JSON: "
              f'{{"action": "BUY" or "SKIP", "confidence": 0.0-1.0, "reason": "short"}}')
    text, _ = brain._ask_llm(prompt)
    try:
        d = json.loads(re.search(r"\{.*\}", text or "", re.S).group(0))
        conf = float(d.get("confidence", 0))
        return d.get("action", "").upper() == "BUY" and conf >= config.MIN_CONFIDENCE, conf
    except Exception:
        return False, 0.0


def _order_status(c, oid) -> tuple[str, float | None]:
    o = c.get_order_by_id(oid)
    px = float(o.filled_avg_price) if o.filled_avg_price else None
    return str(getattr(o.status, "value", o.status)), px


def _wait_fill(c, oid, timeout=15.0) -> tuple[str, float | None]:
    import time
    deadline = time.time() + timeout
    status, px = "", None
    while time.time() < deadline:
        status, px = _order_status(c, oid)
        if status in ("filled", "canceled", "expired", "rejected"):
            break
        time.sleep(0.5)
    return status, px


def _market(c, sym: str, qty: float, buy: bool, kind: str):
    import time
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce
    return c.submit_order(MarketOrderRequest(
        symbol=sym, qty=qty, side=OrderSide.BUY if buy else OrderSide.SELL, time_in_force=TimeInForce.DAY,
        client_order_id=f"{TAG}{kind}-{sym}-{int(time.time())}"))


def _place_stop(c, sym: str, pos: dict):
    import time
    from alpaca.trading.requests import StopOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce
    o = c.submit_order(StopOrderRequest(
        symbol=sym, qty=pos["qty"], side=OrderSide.SELL if pos["side"] == "long" else OrderSide.BUY,
        time_in_force=TimeInForce.DAY, stop_price=round(pos["stop"], 2),
        client_order_id=f"{TAG}stop-{sym}-{int(time.time())}"))
    pos["stop_order"] = str(o.id)


def _alert(text: str, title: str = "ORB"):
    try:
        import alerts
        alerts._notify("📐", title, text)
        alerts._send(f"📐 *{title}*\n{text}")
    except Exception:
        pass


def _close_live(c, st: dict, sym: str, reason: str, now: datetime, cfg: dict):
    """Cancel the broker stop, sell at market, book the trade. A stop that filled first wins."""
    import alpaca_executor as ax
    s = st["symbols"][sym]
    p = s["pos"]
    s["status"] = "exiting"
    save_state(st)
    if p.get("stop_order") and ax._cancel_and_wait(c, p["stop_order"]) == "filled":
        _, px = _order_status(c, p["stop_order"])
        return _book(st, sym, px, now, "stop", cfg)
    o = _market(c, sym, p["qty"], p["side"] != "long", "exit")
    status, px = _wait_fill(c, o.id)
    if status != "filled":
        p["exit_order"], s["exit_reason"] = str(o.id), reason
        log.warning(f"[orb] {sym} exit order {status or 'pending'} — checking again next minute")
        save_state(st)
        return None
    return _book(st, sym, px, now, reason, cfg)


def _book(st, sym, px, now, reason, cfg):
    import risk_manager as rm
    s = st["symbols"][sym]
    s["pos"].pop("stop_order", None)
    s.pop("exit_reason", None)
    rec = filled_exit(st, sym, px, now, reason, cfg)
    _append_trade(rec)
    rm.record_daily_pnl(rec["pnl"])          # ORB losses count toward Charles's kill switch too
    log.info(f"[orb] strategy=orb EXIT {sym} {reason} @ {px:.2f} P&L ${rec['pnl']:+.2f} ({rec['r']:+.2f}R)")
    _alert(f"{sym} closed ({reason}) @ ${px:.2f} | P&L ${rec['pnl']:+.2f} ({rec['r']:+.2f}R) | "
           f"ORB today ${st['pnl']:+.2f}" + (f"\n⛔ {st['disabled']}" if st["disabled"] else ""), "ORB exit")
    save_state(st)
    return rec


def _reconcile(c, st: dict, now: datetime, cfg: dict):
    """Pick up fills of orders sent in an earlier minute (entry, exit, broker stop)."""
    for sym, s in st["symbols"].items():
        if s["status"] == "entering" and s.get("entry_order"):
            status, px = _order_status(c, s["entry_order"])
            if status == "filled":
                _entered(c, st, sym, s.pop("entry_order"), px, s.pop("entry_qty"), now, cfg)
            elif status in ("canceled", "expired", "rejected"):
                s.pop("entry_order"); s.pop("entry_qty", None)
                s["status"], s["note"] = "done", f"entry order {status}"
        elif s["status"] in ("in", "exiting") and s.get("pos"):
            p = s["pos"]
            if p.get("exit_order"):
                status, px = _order_status(c, p["exit_order"])
                if status == "filled":
                    p.pop("exit_order")
                    _book(st, sym, px, now, s.get("exit_reason", "exit"), cfg)
                continue
            if p.get("stop_order"):
                status, px = _order_status(c, p["stop_order"])
                if status == "filled":
                    _book(st, sym, px, now, "stop", cfg)
                elif status in ("canceled", "expired", "rejected"):
                    _place_stop(c, sym, p)          # keep a stop resting while ORB holds shares
            elif s["status"] == "exiting":
                _close_live(c, st, sym, s.get("exit_reason", "exit"), now, cfg)


def _entered(c, st, sym, oid, px, qty, now, cfg):
    pos = filled_entry(st, sym, st["symbols"][sym].pop("entry_side", "long"), px, qty, now, cfg)
    if not pos:
        o = _market(c, sym, qty, False, "exit")       # no valid stop: get out at once
        _wait_fill(c, o.id)
        log.warning(f"[orb] {sym} filled at {px:.2f} below the range stop — sold again")
        return
    try:
        _place_stop(c, sym, pos)
    except Exception as e:
        log.error(f"[orb] {sym} broker stop failed ({e}) — closing")
        return _close_live(c, st, sym, "no_stop", now, cfg)
    log.info(f"[orb] strategy=orb ENTRY {sym} {pos['side']} {qty:g} @ {px:.2f} stop {pos['stop']:.2f} "
             f"target {pos['target']:.2f} | order {oid}")
    _alert(f"{sym} {pos['side']} {qty:g} sh @ ${px:.2f}\nRange {st['symbols'][sym]['range']['low']:.2f}–"
           f"{st['symbols'][sym]['range']['high']:.2f} | stop ${pos['stop']:.2f} | target ${pos['target']:.2f}",
           "ORB entry")


def _enter_live(c, st, sym, sig, now, cfg, regime):
    import alpaca_executor as ax
    import risk_manager as rm
    s = st["symbols"][sym]
    held = sym in {p.symbol for p in ax._stock_positions(c)}
    llm = _llm_confirm(sym, sig["side"], s["range"], sig["price"], regime) if cfg["use_llm"] else None
    why = entry_blockers(st, sym, cfg, halted=rm.is_trading_halted(), held_elsewhere=held,
                         regime=regime if cfg["use_regime"] else None, llm=llm,
                         switched_off=not switched_on())
    if why:
        s["status"], s["note"] = "done", "skipped: " + "; ".join(why)
        log.info(f"[orb] strategy=orb {sym} breakout skipped — {'; '.join(why)}")
        return
    acct = c.get_account()
    equity, cash = float(acct.portfolio_value), float(acct.cash)
    est = exit_levels(sig["side"], sig["price"], s["range"], cfg)
    qty = size(sig["price"], est[0], equity, cash, cfg, rm.drawdown_size_multiplier(equity)) if est else 0
    if qty <= 0:
        s["status"], s["note"] = "done", "size came out at 0"
        return
    o = _market(c, sym, qty, sig["side"] == "long", "entry")
    s.update(status="entering", entry_order=str(o.id), entry_qty=qty, entry_side=sig["side"])
    save_state(st)
    status, px = _wait_fill(c, o.id)
    if status == "filled":
        _entered(c, st, sym, s.pop("entry_order"), px, s.pop("entry_qty"), now, cfg)


def tick(now: datetime | None = None):
    """Every minute in market hours (main.py schedules it only when ORB_ENABLED)."""
    if not getattr(config, "ORB_ENABLED", False):
        return
    import alpaca_executor as ax
    cfg = settings()
    now = (now or datetime.now(timezone.utc)).astimezone(ET)
    c = ax._client()
    clock = c.get_clock()                     # Charles's market-open guard (handles holidays)
    st = load_state()
    if not st or st["day"] != now.date().isoformat():
        if st and any(s.get("status") in ACTIVE for s in st["symbols"].values()):
            if not st.get("stale_alerted"):
                log.error("[orb] a position from an earlier day is still on file — check data/orb.json")
                _alert("A position from an earlier day is still on file (data/orb.json). Check it.", "ORB ⚠️")
                st["stale_alerted"] = True
                save_state(st)
            return
        if not clock.is_open:
            return
        st = new_day(now.date(), cfg)
        log.info(f"[orb] strategy=orb new session {st['day']}: {', '.join(cfg['symbols'])}")
    if not clock.is_open:
        return
    times = session_times(now.date(), cfg, close=clock.next_close)
    _reconcile(c, st, now, cfg)

    import regime as reg_mod
    regime, _ = reg_mod.detect_regime()       # the same source as the Telegram regime alerts
    st["regime"] = regime
    st.setdefault("atr", {})
    live = [sym for sym, s in st["symbols"].items() if s["status"] in ("range", "watch", "in")]
    if live:
        bars = _bars_today(live, times, now)
        for sym in live:
            s = st["symbols"][sym]
            if s["status"] == "range" and sym not in st["atr"]:
                st["atr"][sym] = round(_daily_atr(sym), 4)
            if s["status"] == "range" and s.get("range") is None and not s.get("bars"):
                done = [b for b in bars[sym]]       # replay the session so far (also after a restart)
            else:
                last = datetime.fromisoformat(s["last_bar"]) if s.get("last_bar") else None
                done = [b for b in bars[sym] if not last or b["t"] > last]
            for b in done:
                act = on_bar(st, sym, b, times, cfg, st["atr"].get(sym, 0.0))
                if not act:
                    continue
                if act["do"] == "signal" and s["status"] == "watch":
                    if now - (b["t"] + timedelta(minutes=1)) > timedelta(minutes=2):
                        continue                    # an old bar (replayed after a restart): too late to act
                    _enter_live(c, st, sym, act, now, cfg, regime)
                elif act["do"] == "trail" and s["status"] == "in":
                    p = s["pos"]
                    if p.get("stop_order") and ax._cancel_and_wait(c, p["stop_order"]) == "filled":
                        _, px = _order_status(c, p["stop_order"])
                        _book(st, sym, px, now, "stop", cfg)
                        break
                    p["stop"] = act["stop"]
                    _place_stop(c, sym, p)
                    log.info(f"[orb] strategy=orb {sym} trailing stop → {act['stop']:.2f}")
                elif act["do"] == "exit" and s["status"] == "in":
                    _close_live(c, st, sym, act["reason"], now, cfg)
                    break
    # Hard flatten, even without new bars
    if now >= times["flatten"]:
        for sym, s in st["symbols"].items():
            if s["status"] == "in":
                _close_live(c, st, sym, "flatten", now, cfg)
    save_state(st)
