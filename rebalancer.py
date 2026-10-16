"""
rebalancer.py — once a day (10:00 ET, retry 14:00), make the account match the plan (allocator.py).

Signals use the previous close; on the first trading day of a month Charles re-picks the momentum
stocks and looks at the safety switch, on other days it only fixes big drifts. Sales go first, then
buys with the cash they freed. The daily loss limit blocks buys (not sales); a rebalance cut short
that way is finished the next day.

`python rebalancer.py --dry-run [--veto]` prints what it would do now, without placing anything
(--veto also asks the AI about new picks).
State: data/plan.json (what the dashboard shows). Orders: data/trades.json.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone, date, timedelta

import allocator as al
import config
import state_io
import timeofday

log = logging.getLogger(__name__)
ROOT = os.path.dirname(os.path.abspath(__file__))
PLAN_FILE = os.path.join(ROOT, "data", "plan.json")
RANKED_SHOWN = 15
CASH_RESERVE = 0.002                # a few dollars never spent, so the last buy can't overdraw
HISTORY_DAYS = "2y"                 # enough for 252-day momentum + 200-day average


def settings() -> dict:
    import strategy_settings
    return {**al.DEFAULTS, **strategy_settings.load()}


def load_state(path: str = PLAN_FILE) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def next_rebalance(today: str) -> str:
    """First weekday of next month (exchange holidays aren't known here; Charles then goes a day later)."""
    d = date.fromisoformat(today)
    d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.isoformat()


def decide(today: str, closes: dict[str, list[float]], state: dict, s: dict, veto=None,
           universe: list[str] | None = None, rank_always: bool = True) -> dict:
    """What Charles should hold today, from closes before `today`. Pure apart from
    `veto(symbol, momentum) -> {"veto", "reason"}`. The backtest runs this same function
    (rank_always=False: it only ranks on rebalance days, for speed).
    Modes: safety (T-bills), invest (SPY core), aggressive (2x S&P core while calm and rising).
    Returns {"mode", "prev", "trend", "vol", "full", "switched", "picks", "targets", "ranked", "vetoes"}."""
    spy = closes.get(s["CORE"], [])
    info = al.trend(spy, s["TREND_DAYS"])
    vol = al.volatility(spy, s.get("VOL_DAYS", 20))
    prev = state.get("mode")
    reb = al.is_rebalance_day(today, state.get("last_rebalance"))
    base = info["mode"] if info else al.SAFETY                    # the safety switch's reading
    if not s.get("SAFETY_SWITCH", True):
        base = al.INVEST
    calm = vol is not None and vol < s.get("AGG_VOL_MAX", 0)
    choppy = vol is None or vol > s.get("AGG_VOL_EXIT", 0) or (info is not None and info["mode"] == al.SAFETY)
    if s.get("SWITCH_CHECK") == "monthly" and prev and not reb:
        mode = prev                                   # the switch is only looked at once a month...
        if mode == al.AGGRESSIVE and (choppy or not s.get("AGGRESSIVE")):
            mode = al.INVEST                          # ...but leverage is dropped on any choppy day
    else:
        mode = base
        if mode == al.INVEST and s.get("AGGRESSIVE"):
            stay = prev == al.AGGRESSIVE and not choppy and s.get("SWITCH_CHECK") != "monthly"
            if calm or stay:
                mode = al.AGGRESSIVE
    switched = bool(prev) and mode != prev
    full = bool(reb or switched or not prev or state.get("pending"))
    # stocks are re-picked monthly, or when coming out of / going into safety; a swap between
    # invest and aggressive only changes the core fund
    repick = bool(reb or not prev or state.get("pending") or (switched and al.SAFETY in (prev, mode)))
    ranked = al.rank({k: closes[k] for k in (universe or al.UNIVERSE) if k in closes}, s) \
        if repick or rank_always else []
    for i, r in enumerate(ranked):
        r["rank"] = i + 1
    picks, vetoes = list(state.get("picks", [])), []
    if mode == al.SAFETY:
        picks = []
    elif repick:
        kept_before = set(state.get("picks", []))
        picks = []
        if mode in (al.INVEST, al.AGGRESSIVE):
            for r in ranked:
                if len(picks) >= s["PICKS"]:
                    break
                if not r["eligible"]:
                    continue
                if veto and r["symbol"] not in kept_before:     # only new buys get the news check
                    v = veto(r["symbol"], r["momentum"])
                    if v and v.get("veto"):
                        vetoes.append({"symbol": r["symbol"], "reason": v.get("reason", ""), "rank": r["rank"]})
                        continue
                picks.append(r["symbol"])
    return {"mode": mode, "prev": prev, "trend": info, "vol": vol, "full": full, "switched": switched,
            "picks": picks, "targets": al.targets(mode, picks, s), "ranked": ranked, "vetoes": vetoes}


def reason(order: dict, d: dict, state: dict, s: dict, first_run: bool) -> str:
    """Why this order, in one plain sentence (Activity tab, Telegram)."""
    sym, side = order["symbol"], order["side"]
    rank = {r["symbol"]: r for r in d["ranked"]}
    tw = round(d["targets"].get(sym, 0) * 100)
    vol = f"{d['vol'] * 100:.0f}%" if d.get("vol") is not None else "unknown"
    if sym == s.get("AGG_CORE"):
        if side == "buy" and d["mode"] == al.AGGRESSIVE and (d["switched"] or first_run):
            return (f"Aggressive mode: the market is rising calmly (swings {vol} a year, under "
                    f"{s['AGG_VOL_MAX'] * 100:.0f}%), so the core uses a 2x S&P 500 fund.")
        if side == "sell" and order["all"]:
            why = ("SPY closed below its 200-day average" if (d["trend"] or {}).get("mode") == al.SAFETY
                   else f"the market got choppy (swings {vol} a year)")
            return f"Leaving aggressive mode: {why}, so the core goes back to plain SPY."
    if sym == s["CORE"] and side == "sell" and order["all"] and d["mode"] == al.AGGRESSIVE:
        return "Swapped for the 2x S&P 500 fund: aggressive mode is on."
    if side == "sell" and order["all"]:
        if d["switched"] and d["mode"] == al.SAFETY:
            return "Safety mode: SPY ended the month below its 200-day average, so Charles moved the money to T-bills."
        if sym in state.get("picks", []):
            r = rank.get(sym)
            why = ("it fell below its 200-day average" if r and not r["above_trend"] else
                   f"it's now ranked {r['rank']}" if r else "it no longer qualifies")
            return f"Monthly re-pick: {sym} left the top {s['PICKS']} momentum stocks ({why})."
        if sym == s["SAFE"]:
            return "Back to investing: SPY ended the month above its 200-day average." if d["switched"] \
                else "The T-bill slot is needed for a new pick."
        return "Not part of the new plan: bought by Charles's old trading rules."
    if sym == s["CORE"]:
        if d["switched"] and d.get("prev") == al.AGGRESSIVE:
            return f"Back to plain SPY for the {tw}% core: aggressive mode is off."
        if first_run or d["switched"]:
            return f"Core holding: {tw}% of the account follows the whole market (S&P 500)."
        return f"Rebalance: SPY back to its {tw}% target."
    if sym == s.get("AGG_CORE"):
        return f"Rebalance: {sym} {'topped up' if side == 'buy' else 'trimmed'} to its {tw}% target."
    if sym == s["SAFE"]:
        if d["mode"] == al.SAFETY:
            return "Safety mode: waiting in T-bills (about 4% a year) until SPY is back above its 200-day average."
        return "No stock qualified for a pick slot, so that money waits in T-bills."
    r = rank.get(sym)
    if r and sym not in state.get("picks", []):
        if order.get("held"):                       # an old holding that is now a pick
            return (f"Kept as a momentum pick (rank {r['rank']} of {len(d['ranked'])}, up {r['momentum']:.0f}% over "
                    f"the past year): {'topped up' if side == 'buy' else 'trimmed'} to its {tw}% target.")
        return (f"New momentum pick: up {r['momentum']:.0f}% over the past year, skipping the latest month "
                f"(rank {r['rank']} of {len(d['ranked'])}), and above its 200-day average.")
    return f"Rebalance: {sym} {'topped up' if side == 'buy' else 'trimmed'} to its {tw}% target."


def closes_for(symbols: list[str], today: str) -> tuple[dict[str, list[float]], str | None]:
    """Daily dividend-adjusted closes before `today` (ET), so a partial day never counts."""
    import yfinance as yf
    df = yf.download(symbols, period=HISTORY_DAYS, auto_adjust=True, progress=False, threads=True)["Close"]
    df = df[[str(i.date()) < today for i in df.index]]
    out = {}
    for sym in symbols:
        if sym in df:
            col = [float(v) for v in df[sym].tolist() if v == v]
            if col:
                out[sym] = col
    return out, (str(df.index[-1].date()) if len(df.index) else None)


def run(dry_run: bool = False, plan_path: str = PLAN_FILE, now: datetime | None = None,
        use_veto: bool | None = None) -> dict:
    """The daily check. dry_run: decide and return the orders without placing or saving anything
    (use_veto=True also asks the AI about new picks, as the real run does)."""
    import alpaca_executor as ex
    import risk_manager as rm
    import news_veto
    now = now or datetime.now(timezone.utc)
    today = timeofday.market_date(now.isoformat())
    s = settings()
    c = ex._client()
    if not dry_run and not ex.market_open(c):
        log.info("  🕐 Market closed — no rebalance")
        return {"skipped": "market closed"}
    acct = ex.get_account_summary()
    if not acct:
        log.error("  ❌ Account unreadable — no rebalance")
        return {"skipped": "account unreadable"}
    state = load_state(plan_path)
    closes, as_of = closes_for(sorted(set(al.UNIVERSE) | {s["CORE"], s["SAFE"], s.get("AGG_CORE", s["CORE"])}), today)
    if s["CORE"] not in closes or len(closes[s["CORE"]]) < s["TREND_DAYS"]:
        log.error(f"  ❌ No {s['CORE']} history — no rebalance")
        return {"skipped": "no price history"}
    use_veto = (not dry_run) if use_veto is None else use_veto
    d = decide(today, closes, state, s, veto=news_veto.check if use_veto else None)
    held = {p["symbol"]: p["market_value"] for p in acct["positions"]}
    equity = acct["total_value"]
    todo = al.orders(d["targets"], held, equity, d["full"], s)
    if not dry_run:
        rm.check_day_change(acct.get("day_change") or 0)
    halted = rm.is_trading_halted()
    blocked = [o for o in todo if o["side"] == "buy"] if halted else []
    todo = [o for o in todo if o not in blocked]
    first = not state.get("mode")
    for o in todo + blocked:
        o["held"] = held.get(o["symbol"], 0) > 0
        o["reason"] = reason(o, d, state, s, first)

    done = []
    if not dry_run:
        for o in [o for o in todo if o["side"] == "sell"]:
            try:
                r = ex.sell(c, o["symbol"], None if o["all"] else o["amount"])
            except Exception as e:
                log.error(f"  ❌ SELL {o['symbol']} failed: {e}")
                continue
            if not r:
                continue
            rm.record_daily_pnl(r["pnl"])
            ex.log_trade({"symbol": o["symbol"], "action": "SELL", "value": r["value"], "price": r["price"],
                          "qty": r["qty"], "pnl": r["pnl"], "pnl_pct": r["pnl_pct"], "order_id": r["order_id"],
                          "status": r["status"], "reasoning": o["reason"], "source": "rebalance"})
            done.append({**o, **r})
            log.info(f"  ✅ SELL {o['symbol']} ${r['value']:.2f} P&L ${r['pnl']:+.2f} — {o['reason']}")
        cash = (ex.get_account_summary() or acct)["cash"]
        spendable = cash - max(0.50, CASH_RESERVE * equity) - s["CASH_PCT"] * equity
        for o in [o for o in todo if o["side"] == "buy"]:
            amt = round(min(o["amount"], spendable), 2)
            if amt < s["MIN_ORDER"]:
                log.info(f"  💸 {o['symbol']}: not enough cash left (${spendable:.2f})")
                continue
            try:
                r = ex.buy(c, o["symbol"], amt)
            except Exception as e:
                log.error(f"  ❌ BUY {o['symbol']} failed: {e}")
                continue
            spendable -= amt
            ex.log_trade({"symbol": o["symbol"], "action": "BUY", "value": amt, "price": r["price"], "qty": r["qty"],
                          "order_id": r["order_id"], "status": r["status"], "reasoning": o["reason"],
                          "source": "rebalance"})
            done.append({**o, **r, "amount": amt})
            log.info(f"  ✅ BUY  {o['symbol']} ${amt:.2f} — {o['reason']}")

    new_state = {
        "updated": now.isoformat(), "as_of": as_of, "mode": d["mode"], "trend": d["trend"], "vol": d["vol"],
        "picks": d["picks"], "targets": d["targets"],
        "last_rebalance": today if d["full"] and not blocked else state.get("last_rebalance"),
        "pending": bool(blocked),
        "next_rebalance": next_rebalance(today),
        "summary": al.describe({**d["trend"], "mode": d["mode"]} if d["trend"] else None, d["picks"], s),
        "ranked": d["ranked"][:RANKED_SHOWN], "universe_n": len(d["ranked"]),
        "vetoes": d["vetoes"] or (state.get("vetoes", []) if not d["full"] else []),
        "switches": state.get("switches", []) + ([{"date": today, "to": d["mode"]}] if d["switched"] else []),
        "started": state.get("started") or today,
        "last_orders": (done if not dry_run else todo) or state.get("last_orders", []),
        "last_orders_date": today if (done or dry_run) else state.get("last_orders_date"),
        "halted_buys": bool(blocked),
        "settings": {k: s[k] for k in ("CORE", "SAFE", "CORE_PCT", "PICKS", "PICK_PCT", "TREND_DAYS",
                                       "SWITCH_CHECK", "SAFETY_SWITCH", "AGGRESSIVE", "AGG_CORE", "AGG_VOL_MAX",
                                       "AGG_VOL_EXIT", "VOL_DAYS") if k in s},
    }
    if dry_run:
        return {"state": new_state, "orders": todo, "blocked": blocked, "equity": equity, "held": held}
    state_io.write_json(plan_path, new_state, indent=1)
    if done or d["switched"]:
        _tell(new_state, done, d, blocked)
    return {"state": new_state, "done": done, "blocked": blocked}


def _tell(st: dict, done: list, d: dict, blocked: list):
    try:
        import alerts
        lines = []
        if d["switched"]:
            lines.append({al.SAFETY: "🛡️ Safety mode on: the money moves to T-bills.",
                          al.AGGRESSIVE: "🚀 Aggressive mode on: calm, rising market, so the core uses a 2x S&P fund.",
                          al.INVEST: "📈 Invest mode: plain SPY core."}[d["mode"]])
        for o in done:
            pnl = f" ({'+' if o.get('pnl', 0) >= 0 else '−'}${abs(o['pnl']):.2f})" if o["side"] == "sell" and o.get("pnl") is not None else ""
            lines.append(f"{'Bought' if o['side'] == 'buy' else 'Sold'} {o['symbol']} ${o.get('value') or o['amount']:.2f}{pnl}")
        if blocked:
            lines.append("⏸ Buys wait until tomorrow: the daily loss limit was hit.")
        alerts.rebalanced(st["summary"], lines)
    except Exception as e:
        log.warning(f"Rebalance alert failed: {e}")


def _print(res: dict):
    st = res.get("state")
    if not st:
        print(res)
        return
    print(f"As of the {st['as_of']} close: {st['summary']}")
    t = st["trend"] or {}
    print(f"SPY {t.get('price')} vs 200-day average {t.get('average')} ({t.get('gap_pct')}%) → {st['mode']}")
    print("Targets: " + ", ".join(f"{k} {v:.0%}" for k, v in st["targets"].items()))
    print(f"Account ${res['equity']:,.2f}; held: " + (", ".join(f"{k} ${v:,.2f}" for k, v in res["held"].items()) or "nothing"))
    for o in res["orders"] + res["blocked"]:
        print(f"  {'BLOCKED ' if o in res['blocked'] else ''}{o['side'].upper():4s} {o['symbol']:5s} "
              f"{'all' if o['all'] else ''} ${o['amount']:,.2f} — {o['reason']}")
    print("Top of the ranking: " + ", ".join(f"{r['symbol']} {r['momentum']:+.0f}%{'' if r['eligible'] else ' (no)'}"
                                             for r in st["ranked"][:10]))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if "--dry-run" in sys.argv:
        _print(run(dry_run=True, use_veto="--veto" in sys.argv))
    else:
        print("Use --dry-run (the bot runs the real rebalance on its schedule).")
