"""
options_bot.py — call-debit-spread sleeve on Charles's stock buys (Alpaca PAPER account).

When Charles's rules buy a stock, this also buys one call debit spread on it: buy a call near
the current price, sell a higher-strike call, 2–6 weeks out. The most it can lose is what it
pays (owner chose ≤ $60 per trade, ≤ 20% of the account in all, ≤ 3 open; 2026-10-09).

It manages its own spreads every 15 minutes in market hours: take profit at half the maximum
profit, cut at half the debit lost, and close 5 days before expiry (never held into expiry).
Option contracts are kept out of the stock logic (alpaca_executor filters them), and this
sleeve keeps its own record in data/options.json so stock statistics stay unchanged.
Realised P&L counts toward the daily loss limit, like any other trade.

Pure helpers (choose_spread, exit_reason, risk_budget) are unit-tested; the Alpaca calls are
thin wrappers around them. Every order is tagged with client_order_id "charles-opt-...".
"""
import json
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone

import state_io

log = logging.getLogger(__name__)

STATE_FILE = os.path.join(os.path.dirname(__file__), "data", "options.json")
TAG = "charles-opt-"

OPTIONS = {
    "enabled": True,
    "max_risk_pct": 0.20,        # of portfolio value at risk across all open spreads
    "max_loss_per_trade": 60.0,  # $ — the debit paid for one spread
    "max_open": 3,
    "dte_min": 14, "dte_max": 45, "dte_target": 28,
    "max_debit_to_width": 0.65,  # pay at most 65% of the strike width (max profit ≥ ~0.54× risk)
    "min_debit": 0.10,           # $ per share
    "max_leg_spread_pct": 0.40,  # (ask − bid) / mid per leg
    "take_profit_frac": 0.50,    # close when value gained ≥ half the max profit
    "stop_loss_frac": 0.50,      # close when half the debit is lost
    "exit_dte": 5,               # close this many days before expiry
}


# ── Pure helpers ──────────────────────────────────────────────────────────────

def mid(q):
    """q = (bid, ask) → mid, or None when either side is missing."""
    if not q or q[0] is None or q[1] is None or q[0] <= 0 or q[1] <= 0 or q[1] < q[0]:
        return None
    return (q[0] + q[1]) / 2


def _leg_ok(q) -> bool:
    m = mid(q)
    return m is not None and (q[1] - q[0]) / m <= OPTIONS["max_leg_spread_pct"]


def risk_budget(portfolio_value: float, open_spreads: list) -> float:
    """$ of max loss still allowed for one new spread."""
    used = sum(s["max_loss"] for s in open_spreads)
    room = portfolio_value * OPTIONS["max_risk_pct"] - used
    return max(0.0, min(OPTIONS["max_loss_per_trade"], room))


def choose_spread(spot: float, contracts: list, quotes: dict, today: date, max_loss: float):
    """Pick a call debit spread. contracts: [{symbol, strike, expiry(date)}]; quotes: {symbol: (bid, ask)}.
    Returns (spread dict, None) or (None, reason)."""
    if max_loss < OPTIONS["min_debit"] * 100:
        return None, "options risk budget used up"
    by_exp = {}
    for c in contracts:
        dte = (c["expiry"] - today).days
        if OPTIONS["dte_min"] <= dte <= OPTIONS["dte_max"]:
            by_exp.setdefault(c["expiry"], []).append(c)
    if not by_exp:
        return None, "no expiry 2–6 weeks out"
    reasons = []
    for exp in sorted(by_exp, key=lambda e: abs((e - today).days - OPTIONS["dte_target"])):
        chain = sorted(by_exp[exp], key=lambda c: c["strike"])
        longs = [c for c in chain if _leg_ok(quotes.get(c["symbol"]))]
        if not longs:
            reasons.append(f"{exp}: no liquid calls")
            continue
        long = min(longs, key=lambda c: abs(c["strike"] - spot))
        lm = mid(quotes[long["symbol"]])
        best = None
        for short in chain:
            if short["strike"] <= long["strike"] or not _leg_ok(quotes.get(short["symbol"])):
                continue
            width = short["strike"] - long["strike"]
            debit = round(lm - mid(quotes[short["symbol"]]), 2)
            if debit < OPTIONS["min_debit"] or debit * 100 > max_loss:
                continue
            if debit / width > OPTIONS["max_debit_to_width"]:
                continue
            if best is None or width > best["width"]:     # widest that fits: most upside
                natural = quotes[long["symbol"]][1] - quotes[short["symbol"]][0]
                best = {"long": long["symbol"], "short": short["symbol"], "expiry": exp.isoformat(),
                        "long_strike": long["strike"], "short_strike": short["strike"],
                        "width": round(width, 2), "debit": debit, "natural": round(natural, 2),
                        "max_loss": round(debit * 100, 2), "max_profit": round((width - debit) * 100, 2)}
        if best:
            return best, None
        reasons.append(f"{exp}: no short strike fits ${max_loss:.0f} max loss at ≤{OPTIONS['max_debit_to_width']:.0%} of width")
    return None, "; ".join(reasons[:2])


def retry_price(spread: dict, max_loss: float) -> float | None:
    """Second entry try: halfway from mid to the natural price, if it still fits the rules."""
    p = round((spread["debit"] + spread["natural"]) / 2, 2)
    if p <= spread["debit"] or p * 100 > max_loss or p / spread["width"] > OPTIONS["max_debit_to_width"]:
        return None
    return p


def exit_reason(spread: dict, value: float | None, today: date) -> str | None:
    """value = spread mid per share now. Returns 'take_profit' | 'stop_loss' | 'expiry' | None."""
    dte = (date.fromisoformat(spread["expiry"]) - today).days
    if dte <= OPTIONS["exit_dte"]:
        return "expiry"
    if value is None:
        return None
    debit, width = spread["debit"], spread["width"]
    if value >= debit + OPTIONS["take_profit_frac"] * (width - debit):
        return "take_profit"
    if value <= debit * (1 - OPTIONS["stop_loss_frac"]):
        return "stop_loss"
    return None


# ── State ─────────────────────────────────────────────────────────────────────

def load() -> dict:
    try:
        with open(STATE_FILE) as f:
            s = json.load(f)
    except (FileNotFoundError, ValueError):
        s = {}
    s.setdefault("open", [])
    s.setdefault("closed", [])
    return s


def save(s: dict):
    state_io.write_json(STATE_FILE, s, indent=1)


# ── Alpaca I/O ────────────────────────────────────────────────────────────────

def _data_client():
    import credential_manager as sec
    from alpaca.data.historical.option import OptionHistoricalDataClient
    return OptionHistoricalDataClient(sec.alpaca_key(), sec.alpaca_secret())


def _quotes(symbols: list) -> dict:
    from alpaca.data.requests import OptionSnapshotRequest
    out, d = {}, _data_client()
    for i in range(0, len(symbols), 100):
        snap = d.get_option_snapshot(OptionSnapshotRequest(symbol_or_symbols=symbols[i:i + 100]))
        for s, v in snap.items():
            q = getattr(v, "latest_quote", None)
            if q:
                out[s] = (float(q.bid_price or 0), float(q.ask_price or 0))
    return out


def _chain(c, symbol: str, spot: float, today: date) -> list:
    from alpaca.trading.requests import GetOptionContractsRequest
    from alpaca.trading.enums import AssetStatus, ContractType
    r = c.get_option_contracts(GetOptionContractsRequest(
        underlying_symbols=[symbol], status=AssetStatus.ACTIVE, type=ContractType.CALL,
        expiration_date_gte=today + timedelta(days=OPTIONS["dte_min"]),
        expiration_date_lte=today + timedelta(days=OPTIONS["dte_max"]),
        strike_price_gte=str(round(spot * 0.85, 2)), strike_price_lte=str(round(spot * 1.25, 2)),
        limit=1000))
    return [{"symbol": x.symbol, "strike": float(x.strike_price), "expiry": x.expiration_date}
            for x in (r.option_contracts or [])]


def _submit(c, legs: list, limit: float, tag: str):
    from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest
    from alpaca.trading.enums import OrderClass, OrderSide, PositionIntent, TimeInForce
    return c.submit_order(LimitOrderRequest(
        qty=1, limit_price=round(limit, 2), order_class=OrderClass.MLEG, time_in_force=TimeInForce.DAY,
        client_order_id=f"{TAG}{tag}-{int(time.time())}",
        legs=[OptionLegRequest(symbol=s, ratio_qty=1, side=getattr(OrderSide, side),
                               position_intent=getattr(PositionIntent, intent)) for s, side, intent in legs]))


def _fill_or_cancel(c, order, wait: float = 20.0):
    """Wait for a fill; cancel if it doesn't come. Returns the filled order or None."""
    import alpaca_executor as ax
    status = ax._wait_final(c, order.id, wait)
    if status != "filled":
        status = ax._cancel_and_wait(c, order.id)
    o = c.get_order_by_id(order.id)
    return o if str(getattr(o.status, "value", "")) == "filled" else None


def _notify(msg: str):
    try:
        import alerts
        alerts._notify("🧩", "Options", msg)
    except Exception:
        pass


def on_stock_buys(buys: list):
    """Called after Charles's stock BUYs fill. Opens at most one spread per bought symbol."""
    if not OPTIONS["enabled"] or not buys:
        return
    import alpaca_executor as ax
    c = ax._client()
    if not c.get_clock().is_open:
        return
    st = load()
    today = datetime.now(timezone.utc).date()
    for b in buys:
        sym = b["symbol"]
        if len(st["open"]) >= OPTIONS["max_open"]:
            log.info(f"  🧩 {sym}: options max open ({OPTIONS['max_open']}) reached")
            break
        if any(s["symbol"] == sym for s in st["open"]):
            continue
        try:
            port = float(c.get_account().portfolio_value)
            budget = risk_budget(port, st["open"])
            spot = float(b.get("price") or 0) or float(c.get_open_position(sym).current_price)
            chain = _chain(c, sym, spot, today)
            quotes = _quotes([x["symbol"] for x in chain]) if chain else {}
            sp, why = choose_spread(spot, chain, quotes, today, budget)
            if not sp:
                log.info(f"  🧩 {sym}: no spread — {why}")
                continue
            legs = [(sp["long"], "BUY", "BUY_TO_OPEN"), (sp["short"], "SELL", "SELL_TO_OPEN")]
            filled = _fill_or_cancel(c, _submit(c, legs, sp["debit"], f"open-{sym}"))
            retry = retry_price(sp, budget)
            if not filled and retry:
                filled = _fill_or_cancel(c, _submit(c, legs, retry, f"open-{sym}"))
            if not filled:
                log.info(f"  🧩 {sym}: spread at ${sp['debit']:.2f}{f' / ${retry:.2f}' if retry else ''} didn't fill — cancelled")
                continue
            debit = float(filled.filled_avg_price or sp["debit"])
            rec = {**sp, "symbol": sym, "qty": 1, "debit": round(debit, 2),
                   "max_loss": round(debit * 100, 2), "max_profit": round((sp["width"] - debit) * 100, 2),
                   "opened": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "order_id": str(filled.id), "spot": spot,
                   "reason": (b.get("reasoning") or "")[:200], "confidence": b.get("confidence")}
            st["open"].append(rec)
            save(st)
            log.info(f"  🧩 OPEN {sym} {sp['long_strike']:g}/{sp['short_strike']:g}C {sp['expiry']} "
                     f"@ ${debit:.2f} (max loss ${rec['max_loss']:.0f}, max gain ${rec['max_profit']:.0f})")
            _notify(f"Bought {sym} {sp['long_strike']:g}/{sp['short_strike']:g} call spread, "
                    f"exp {sp['expiry']}, for ${rec['max_loss']:.0f} (most it can lose). "
                    f"Max gain ${rec['max_profit']:.0f}.")
        except Exception as e:
            log.error(f"  ❌ Options open {sym} failed: {e}")


def manage():
    """Every 15 min in market hours: close spreads that hit an exit rule."""
    if not OPTIONS["enabled"]:
        return
    st = load()
    if not st["open"]:
        return
    import alpaca_executor as ax
    import risk_manager as rm
    try:
        c = ax._client()
        if not c.get_clock().is_open:
            return
        held = {p.symbol: p for p in c.get_all_positions()}
        today = datetime.now(timezone.utc).date()
        quotes = _quotes([x for s in st["open"] for x in (s["long"], s["short"])])
    except Exception as e:
        log.error(f"  ❌ Options check failed: {e}")
        return
    for sp in list(st["open"]):
        if sp["long"] not in held and sp["short"] not in held:
            log.warning(f"  🧩 {sp['symbol']} spread no longer held at the broker — dropped from the sleeve")
            st["open"].remove(sp)
            st["closed"].append({**sp, "closed": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                 "exit_reason": "gone", "pnl": None})
            continue
        lm, sm = mid(quotes.get(sp["long"])), mid(quotes.get(sp["short"]))
        value = round(lm - sm, 2) if lm is not None and sm is not None else None
        sp["value"] = value
        reason = exit_reason(sp, value, today)
        if not reason:
            continue
        try:
            legs = [(sp["long"], "SELL", "SELL_TO_CLOSE"), (sp["short"], "BUY", "BUY_TO_CLOSE")]
            credit = max(0.01, value if value is not None else 0.01)
            filled = _fill_or_cancel(c, _submit(c, legs, -credit, f"close-{sp['symbol']}"))
            if not filled:     # retry at the natural price (long bid − short ask), floor 1c
                ql, qs = quotes.get(sp["long"]) or (0, 0), quotes.get(sp["short"]) or (0, 0)
                natural = max(0.01, round(ql[0] - qs[1], 2))
                filled = _fill_or_cancel(c, _submit(c, legs, -natural, f"close-{sp['symbol']}"))
            if not filled:
                log.warning(f"  🧩 {sp['symbol']} spread close ({reason}) didn't fill — will retry")
                continue
            exit_px = abs(float(filled.filled_avg_price or credit))
            pnl = round((exit_px - sp["debit"]) * 100 * sp["qty"], 2)
            rm.record_daily_pnl(pnl)
            st["open"].remove(sp)
            st["closed"].append({**sp, "exit": round(exit_px, 2), "pnl": pnl, "exit_reason": reason,
                                 "closed": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            log.info(f"  🧩 CLOSE {sp['symbol']} spread ({reason}) @ ${exit_px:.2f} P&L ${pnl:+.2f}")
            _notify(f"Closed {sp['symbol']} call spread ({reason.replace('_', ' ')}): P&L ${pnl:+.2f}.")
        except Exception as e:
            log.error(f"  ❌ Options close {sp['symbol']} failed: {e}")
    save(st)   # also keeps each open spread's latest value for the dashboard


def summary() -> dict:
    """For the dashboard and status: open spreads and closed results."""
    st = load()
    closed = [s for s in st["closed"] if s.get("pnl") is not None]
    wins = [s for s in closed if s["pnl"] > 0]
    return {"open": st["open"], "closed": st["closed"][-30:][::-1],
            "at_risk": round(sum(s["max_loss"] for s in st["open"]), 2),
            "realised_pnl": round(sum(s["pnl"] for s in closed), 2),
            "closed_n": len(closed), "win_pct": round(len(wins) / len(closed) * 100, 1) if closed else None,
            "settings": OPTIONS}
