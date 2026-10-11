"""
shadow.py — simulated "shadow" portfolios that test strategy ideas on live data.

Each book starts with $1,000 of pretend cash. Every trading cycle it sees the
same AI decisions and prices as the live bot, applies its own rules and trades
on paper (no orders, ever). "baseline" copies the live rules; most other books
change exactly ONE thing, so comparing them with baseline shows what that one
change is worth ("swing" tests a package of rules together, on purpose).
Compare books with each other, not with the live account (simulated fills at
the cycle price, exits checked every 30 min).

State: data/shadow_books.json. Called from main.py after each trading cycle,
inside a try/except so a shadow problem can never affect live trading.
"""
import logging
import os
from datetime import datetime, timezone, timedelta

import config
import state_io
import timeofday

log = logging.getLogger(__name__)

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "shadow_books.json")
START_CASH = 1000.0

VARIANTS = {
    "baseline":       {"about": "live rules (the reference)",
                       "time_exit": True,  "ai_sells": True,  "sizing": "confidence", "entry_window": None},
    "no_time_exit":   {"about": "no 7-day time exit and no AI sells: hold until stop or take-profit",
                       "time_exit": False, "ai_sells": False, "sizing": "confidence", "entry_window": None},
    "risk_sized":     {"about": "size by volatility: risk 1% of the book per trade",
                       "time_exit": True,  "ai_sells": True,  "sizing": "risk",       "entry_window": None},
    "midday_entries": {"about": "buy only between 11:00 and 14:30 ET",
                       "time_exit": True,  "ai_sells": True,  "sizing": "confidence",
                       "entry_window": ("11:00", "14:30")},
    # swing changes several rules at once, on purpose (the 3–7 day hold package from the
    # 2026-10-01 review); compare it with baseline and no_time_exit
    "swing":          {"about": "swing package: buy only in the 11:00/14:00 cycles, no time exit or AI sells, "
                                "3×ATR trailing stop (max 8%), no take-profit",
                       "time_exit": False, "ai_sells": False, "sizing": "confidence", "entry_window": None,
                       "entry_cycles": ("11:00", "14:00"), "stop_atr_mult": 3.0, "stop_cap": 0.08,
                       "take_profit": False},
    # The owner made profit lock live on 2026-10-09 (all books inherit it); this book keeps the old
    # plain trailing stop so the lock's effect stays measured.
    "no_profit_lock": {"about": "old stop rule: plain trailing stop, no profit lock",
                       "time_exit": True,  "ai_sells": True,  "sizing": "confidence", "entry_window": None,
                       "lock": None},
    # owner, 2026-10-09: most give-back is small gains turning into losses (HPE best +$0.18 → −$7.40).
    # Hourly replay on 77 past trades was slightly negative (−$0.23 vs +$1.47) — measure it live.
    "breakeven":      {"about": "once a trade has been up 1.5%, its stop never goes below the buy price",
                       "time_exit": True,  "ai_sells": True,  "sizing": "confidence", "entry_window": None,
                       "breakeven": 0.015},
    "core_satellite": {"about": "70% buys and holds SPY from the first cycle; the other 30% runs the "
                                "baseline rules at 30% scale",
                       "time_exit": True,  "ai_sells": True,  "sizing": "confidence", "entry_window": None,
                       "core": ("SPY", 0.70)},
    # roadmap task 6 (owner said "build all of it", 2026-10-11): measured here before any live change
    "idle_cash":      {"about": "live rules, plus cash not in trades earns T-bill interest (SGOV/BIL, "
                                "assumed 4%/yr)",
                       "time_exit": True,  "ai_sells": True,  "sizing": "confidence", "entry_window": None,
                       "cash_yield": 0.04},
    # roadmap task 9 (AI as a news veto): a fixed rule picks the buys; the two books differ only in
    # whether the AI may block a pick on bad news. rules_only alone answers "do we need the AI at all?"
    "rules_only":     {"about": "a fixed trend rule picks the buys (no AI): above the 50-day average, "
                                "RSI 50–70, MACD rising, not at the top of today's range",
                       "time_exit": True,  "ai_sells": False, "sizing": "fixed", "entry_window": None,
                       "entry": "trend_rule"},
    "news_veto":      {"about": "same trend rule, but the AI can block a buy: it says SELL, or the "
                                "news sentiment is clearly negative",
                       "time_exit": True,  "ai_sells": False, "sizing": "fixed", "entry_window": None,
                       "entry": "trend_rule", "veto": True},
}
# Optional rule flags; books that leave them out get the live behaviour.
#   entry_cycles:  buy only in the cycles that start at these ET times (decisions arrive
#                  a few minutes after the cycle starts, so allow one scan interval)
#   stop_atr_mult: trailing-stop distance in ATRs (default config.TRAILING_STOP_ATR_MULT)
#   stop_cap:      max stop distance as a fraction of price (default config.STOP_LOSS_PCT)
#   take_profit:   False = no fixed take-profit
#   lock:          (trigger, keep): after the best gain reaches `trigger`, the stop is at least
#                  entry × (1 + keep × best gain). Default: the live config.PROFIT_LOCK_* values;
#                  None = no profit lock
#   breakeven:     after the best gain reaches this fraction, the stop is at least the entry price
#   core:          (symbol, fraction): buy and hold that share of the book in the symbol from
#                  the first cycle with a price; the rest (the satellite) trades the book's rules,
#                  sized and daily-loss-limited on its own slice. No satellite entries before
#                  the core is bought.
#   cash_yield:    annual interest on uninvested cash, accrued between cycles (T-bill ETF stand-in)
#   entry:         "trend_rule" = buys come from trend_rule() on the cycle's market data, not AI BUYs
#   veto:          with entry="trend_rule": skip a pick when the AI says SELL or sentiment is negative
RISK_PER_TRADE = 0.01       # risk_sized: 1% of book equity at risk per trade
MAX_RISK_SIZE_PCT = 0.20    # risk_sized: never more than 20% of the book in one position
FIXED_SIZE_PCT = 0.125      # sizing "fixed": midpoint of the live 10–15% range
VETO_SENTIMENT = -0.2       # news_veto: sentiment at or below this blocks a rule pick


def trend_rule(d: dict) -> bool:
    """Rule-only buy signal on one symbol's indicators (the cycle's market data): uptrend not
    stretched (above the 50-day average by at most 15%), RSI 50–70, daily MACD above its signal,
    and not buying at the top of today's hourly range."""
    rsi = d.get("rsi")
    return bool(d.get("above_ma50") and d.get("ma50_dist_pct") is not None and d["ma50_dist_pct"] <= 15
                and rsi is not None and 50 <= rsi <= 70
                and (d.get("macd") or 0) > (d.get("macd_signal") or 0)
                and (d.get("hourly_bb_pos") if d.get("hourly_bb_pos") is not None else 1) < 0.8)


def vetoed(decision: dict | None) -> bool:
    """news_veto: the AI blocks a rule pick on a SELL call or clearly negative news sentiment."""
    if not decision:
        return False
    return (str(decision.get("action")).upper() == "SELL"
            or (decision.get("sentiment") or 0) <= VETO_SENTIMENT)


# ── state ─────────────────────────────────────────────────────────────────────

def _new_book(now: datetime) -> dict:
    return {"cash": START_CASH, "peak": START_CASH, "positions": {}, "closed": [],
            "cooldowns": {}, "day": timeofday.market_date(now.isoformat()), "day_pnl": 0.0,
            "started": now.isoformat(timespec="seconds"), "equity": []}


def load(path: str | None = None) -> dict:
    import json
    try:
        with open(path or STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def save(books: dict, path: str | None = None):
    state_io.write_json(path or STATE_FILE, books, indent=1)


# ── market-wide checks (shared by all books, computed once per cycle) ─────────

def market_checks(regime: str) -> dict:
    """The live pre-trade checks that don't depend on account state."""
    import market_filter as mf
    import regime as reg
    checks = {"regime": regime, "mults": reg.regime_multipliers(regime),
              "spy_ok": True, "macro": False, "avoid": False, "earnings": mf.near_earnings}
    try:
        checks["spy_ok"] = mf.market_is_bullish()[0]
    except Exception:
        pass
    try:
        import macro_calendar
        checks["macro"] = macro_calendar.in_macro_blackout()[0]
    except Exception:
        pass
    try:
        checks["avoid"] = timeofday.in_avoid_window()[0]
    except Exception:
        pass
    return checks


def _price_now(symbol: str):
    try:
        import yfinance as yf
        return float(yf.Ticker(symbol).fast_info["last_price"])
    except Exception:
        return None


# ── one book ──────────────────────────────────────────────────────────────────

def _core_value(book: dict) -> float:
    c = book.get("core")
    return c["qty"] * c["last"] if c else 0.0


def _equity(book: dict, prices: dict) -> float:
    return (book["cash"] + _core_value(book)
            + sum(p["qty"] * prices.get(s, p["last"]) for s, p in book["positions"].items()))


def _sell(book: dict, symbol: str, price: float, reason: str, now: datetime):
    p = book["positions"].pop(symbol)
    pnl = (price - p["entry"]) * p["qty"]
    book["cash"] += p["qty"] * price
    book["day_pnl"] += pnl
    book["closed"].append({"symbol": symbol, "opened": p["opened"], "closed": now.isoformat(timespec="seconds"),
                           "entry": round(p["entry"], 4), "exit": round(price, 4),
                           "pnl": round(pnl, 2), "reason": reason,
                           "peak_pnl": round((p["highest"] - p["entry"]) * p["qty"], 2)})
    book["cooldowns"][symbol] = now.isoformat()


def _size_value(rules: dict, peak: float, equity: float, conf: float, price: float,
                stop_dist: float, mults: dict) -> float:
    import market_filter as mf
    import risk_manager as rm
    dd = rm.drawdown_size_multiplier(equity, peak_value=peak)
    if rules["sizing"] == "fixed":
        value = FIXED_SIZE_PCT * equity
    elif rules["sizing"] == "risk":
        value = min(RISK_PER_TRADE * equity / (stop_dist / price), MAX_RISK_SIZE_PCT * equity)
    else:
        value = mf.position_size_pct(conf) * equity
    return value * mults.get("size_mult", 1.0) * dd


def _update_book(name: str, book: dict, decisions: list, prices: dict, atrs: dict,
                 checks: dict, now: datetime, market: dict | None = None):
    rules = VARIANTS[name]
    if rules.get("cash_yield") and book.get("equity"):    # interest on idle cash since last cycle
        last = datetime.fromisoformat(book["equity"][-1][0])
        days = max(0.0, (now - last).total_seconds() / 86400)
        earned = book["cash"] * rules["cash_yield"] * days / 365
        book["cash"] += earned
        book["interest"] = round(book.get("interest", 0.0) + earned, 4)
    today = timeofday.market_date(now.isoformat())
    if book.get("day") != today:
        book["day"], book["day_pnl"] = today, 0.0
    by_symbol = {d["symbol"]: d for d in decisions if d.get("type", "stock") in ("stock", None)}

    core = rules.get("core")
    if core:                                         # buy-and-hold core, bought once
        csym, frac = core
        cprice = prices.get(csym)
        if cprice and not book.get("core"):
            value = frac * _equity(book, prices)
            book["cash"] -= value
            book["core"] = {"symbol": csym, "qty": value / cprice, "entry": cprice,
                            "opened": now.isoformat(timespec="seconds")}
        if cprice and book.get("core"):
            book["core"]["last"] = cprice

    # exits first (same order as live: stops, take-profit, time, earnings, AI sells)
    for sym in list(book["positions"]):
        p, price = book["positions"][sym], prices.get(sym)
        if not price:
            continue
        p["last"] = price
        p["highest"] = max(p["highest"], price)
        stop = max(p["stop"], p["highest"] - p["dist"])
        lock = rules.get("lock", (config.PROFIT_LOCK_TRIGGER, config.PROFIT_LOCK_KEEP))
        if lock and lock[0]:
            trigger, keep = lock
            best = p["highest"] / p["entry"] - 1
            if best >= trigger:
                stop = max(stop, p["entry"] * (1 + keep * best))
        if rules.get("breakeven") and p["highest"] / p["entry"] - 1 >= rules["breakeven"]:
            stop = max(stop, p["entry"])
        p["stop"] = round(stop, 4)
        gain = price / p["entry"] - 1
        held_days = (now - datetime.fromisoformat(p["opened"])).days
        d = by_symbol.get(sym)
        if price <= stop:
            _sell(book, sym, price, "stop", now)
        elif rules.get("take_profit", True) and gain >= config.TAKE_PROFIT_PCT:
            _sell(book, sym, price, "take_profit", now)
        elif rules["time_exit"] and held_days >= 7 and gain < 0.02:
            _sell(book, sym, price, "time_exit", now)
        elif checks["earnings"](sym)[0]:
            _sell(book, sym, price, "earnings", now)
        elif rules["ai_sells"] and d and str(d.get("action")).upper() == "SELL" \
                and d.get("confidence", 0) >= config.LLM_SELL_MIN_CONFIDENCE:
            _sell(book, sym, price, "ai_sell", now)

    equity = _equity(book, prices)
    book["peak"] = max(book["peak"], equity)
    size_equity, size_peak, scale = equity, book["peak"], 1.0
    if core and book.get("core"):                    # satellite: its own slice, its own peak
        size_equity = equity - _core_value(book)
        book["sat_peak"] = size_peak = max(book.get("sat_peak", size_equity), size_equity)
        scale = 1 - core[1]

    # entries
    blocked = (not checks["spy_ok"] or checks["macro"] or checks["avoid"]
               or not checks["mults"].get("allow_longs", True)
               or book["day_pnl"] <= -abs(config.MAX_DAILY_LOSS) * scale
               or bool(core and not book.get("core")))
    if rules["entry_window"]:
        hhmm = now.astimezone(timeofday.ET).strftime("%H:%M")
        lo, hi = rules["entry_window"]
        blocked = blocked or not (lo <= hhmm <= hi)
    if rules.get("entry_cycles"):
        et = now.astimezone(timeofday.ET)
        mins = et.hour * 60 + et.minute
        starts = [int(t[:2]) * 60 + int(t[3:]) for t in rules["entry_cycles"]]
        blocked = blocked or not any(s <= mins < s + config.STOCK_SCAN_INTERVAL_MIN for s in starts)
    need = config.MIN_CONFIDENCE + checks["mults"].get("conf_boost", 0.0)
    if rules.get("entry") == "trend_rule":
        candidates = {s: by_symbol.get(s, {}) for s, md in (market or {}).items()
                      if md.get("type", "stock") in ("stock", None) and trend_rule(md)
                      and not (rules.get("veto") and vetoed(by_symbol.get(s)))}
    else:
        candidates = by_symbol
    for sym, d in candidates.items():
        if blocked:
            break
        if rules.get("entry") != "trend_rule" and (
                str(d.get("action")).upper() != "BUY" or d.get("confidence", 0) < need - 1e-9):
            continue
        if sym in book["positions"] or len(book["positions"]) >= config.MAX_OPEN_POSITIONS:
            continue
        last = book["cooldowns"].get(sym)
        if last and now - datetime.fromisoformat(last) < timedelta(hours=config.TRADE_COOLDOWN_HOURS):
            continue
        price = prices.get(sym) or d.get("price")
        if not price or checks["earnings"](sym)[0]:
            continue
        atr = atrs.get(sym) or 0.0
        cap = rules.get("stop_cap", config.STOP_LOSS_PCT) * price
        mult = rules.get("stop_atr_mult", config.TRAILING_STOP_ATR_MULT)
        dist = min(atr * mult, cap) if atr > 0 else cap
        value = min(_size_value(rules, size_peak, size_equity, d.get("confidence", 0), price, dist,
                                checks["mults"]),
                    book["cash"] * 0.95)
        if value < 1.0:
            continue
        book["cash"] -= value
        book["positions"][sym] = {"qty": value / price, "entry": price, "highest": price, "last": price,
                                  "dist": round(dist, 4), "stop": round(price - dist, 4),
                                  "opened": now.isoformat(timespec="seconds"), "conf": d.get("confidence")}

    book["equity"].append([now.isoformat(timespec="minutes"), round(_equity(book, prices), 2)])
    book["equity"] = book["equity"][-2000:]


# ── entry points ──────────────────────────────────────────────────────────────

def on_cycle(decisions: list, market: dict, regime: str, now: datetime | None = None,
             checks: dict | None = None, price_lookup=_price_now, path: str | None = None) -> dict:
    """Update every shadow book with this cycle's decisions and prices, then save."""
    now = now or datetime.now(timezone.utc)
    books = load(path)
    for name in VARIANTS:
        books.setdefault(name, _new_book(now))
    checks = checks or market_checks(regime)
    prices = {s: d["price"] for s, d in (market or {}).items() if d.get("price")}
    atrs = {s: d.get("atr") for s, d in (market or {}).items()}
    for name, book in books.items():                 # held symbols outside today's watchlist
        held = list(book.get("positions", {}))
        if VARIANTS.get(name, {}).get("core"):
            held.append(VARIANTS[name]["core"][0])
        for sym in held:
            if sym not in prices:
                p = price_lookup(sym)
                if p:
                    prices[sym] = p
    for name in VARIANTS:
        _update_book(name, books[name], decisions, prices, atrs, checks, now, market)
    save(books, path)
    return books


def summary(books: dict | None = None) -> dict:
    """Per book: value, return, open positions, closed-trade stats and exits by reason."""
    books_arg_is_default = books is None
    books = books if books is not None else load()
    out = {}
    for name, b in books.items():
        closed = b.get("closed", [])
        value = b["equity"][-1][1] if b.get("equity") else START_CASH
        reasons = {}
        for t in closed:
            r = reasons.setdefault(t["reason"], {"n": 0, "pnl": 0.0})
            r["n"] += 1
            r["pnl"] = round(r["pnl"] + t["pnl"], 2)
        out[name] = {"about": VARIANTS.get(name, {}).get("about", ""), "since": b.get("started", "")[:10],
                     "value": value, "return_pct": round((value / START_CASH - 1) * 100, 2),
                     "max_drawdown_pct": _max_dd([v for _, v in b.get("equity", [])]),
                     "open": len(b.get("positions", {})), "closed": len(closed),
                     "win_pct": round(100 * sum(t["pnl"] > 0 for t in closed) / len(closed)) if closed else None,
                     "realized_pnl": round(sum(t["pnl"] for t in closed), 2), "exits": reasons}
    if books_arg_is_default:
        try:                                     # strategy books on daily data (shadow_daily.py)
            import shadow_daily
            out.update(shadow_daily.summary())
        except Exception as e:
            log.warning(f"Daily shadow summary failed: {e}")
    return out


def weekly(days: int = 7, now: datetime | None = None, books: dict | None = None,
           daily_books: dict | None = None, spy: list | None = None) -> dict:
    """Last `days` days per book (intraday and daily books): return, closed trades and worst
    drop, each vs baseline and vs SPY over the same days. spy: [[date, close], ...] oldest first."""
    import shadow_daily
    now = now or datetime.now(timezone.utc)
    cut = (now - timedelta(days=days)).date().isoformat()
    books = load() if books is None else books
    daily_books = shadow_daily.load() if daily_books is None else daily_books

    def window(curve, started=""):
        before = [v for t, v in curve if str(t)[:10] < cut]
        inside = [v for t, v in curve if str(t)[:10] >= cut]
        if before:
            start = [before[-1]]
        elif started[:10] >= cut:                        # book began inside the window
            start = [START_CASH]
        else:
            start = []
        return start + inside

    def pct(pts):
        return round((pts[-1] / pts[0] - 1) * 100, 2) if len(pts) >= 2 else None

    def diff(a, b):
        return round(a - b, 2) if a is not None and b is not None else None

    spy_pts = window(spy or [])
    out = {"from": cut, "days": days, "spy_return_pct": pct(spy_pts),
           "spy_max_drawdown_pct": shadow_daily.max_drawdown_pct(spy_pts) if spy_pts else None,
           "books": {}}
    for name, b in {**books, **daily_books}.items():
        pts = window(b.get("equity", []), b.get("started", ""))
        closed = [t for t in b.get("closed", []) if str(t.get("closed", ""))[:10] >= cut]
        out["books"][name] = {"return_pct": pct(pts), "closed": len(closed),
                              "pnl": round(sum(t["pnl"] for t in closed), 2),
                              "max_drawdown_pct": shadow_daily.max_drawdown_pct(pts)}
    base = out["books"].get("baseline", {})
    for r in out["books"].values():
        r["vs_baseline_pct"] = diff(r["return_pct"], base.get("return_pct"))
        r["vs_spy_pct"] = diff(r["return_pct"], out["spy_return_pct"])
        # positive = shallower worst drop than baseline / SPY
        r["drop_vs_baseline_pct"] = diff(r["max_drawdown_pct"], base.get("max_drawdown_pct"))
        r["drop_vs_spy_pct"] = diff(r["max_drawdown_pct"], out["spy_max_drawdown_pct"])
    return out


def _max_dd(curve: list) -> float:
    import shadow_daily
    return shadow_daily.max_drawdown_pct(curve)
