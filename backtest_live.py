"""
backtest_live.py — Replays the live bot's own code over past prices.

Everything that decides and manages trades is the live code, fed data "as of"
each step through a fake yfinance and a fake clock:
  screener (top 8 of the 99-stock universe, 8:45 and 13:00 ET), collector
  indicators (90d daily + 5d hourly, with a partial bar for today), regime and
  size multipliers, pre-trade filters (earnings, cooldown, SPY trend, macro,
  time of day), conviction sizing, drawdown sizing, trailing stops (capped at
  STOP_LOSS_PCT), take-profit, earnings exit, 7-day time exit, daily-loss
  kill switch, MAX_OPEN_POSITIONS.

What is NOT the live bot:
  * The AI. brain.technical_signal stands in for it (see proxy_decision). The
    live rule fallback is stamped 0.55 confidence and can never pass the 0.60
    gate, so replaying it verbatim would trade nothing.
  * Hourly granularity. Scans run at each hourly bar (10:30-15:30 ET); live
    scans every 30 min, but 9:30/10:00 buys are blocked by the time filter and
    30-min history isn't available this far back. Stops trigger on bar lows,
    trails ratchet on bar closes.
  * Universe is today's STOCK_UNIVERSE (survivorship bias: it was picked with
    hindsight). Prices are split/dividend adjusted. Macro events exist only
    from 2025-09 (as hardcoded in macro_calendar). Confidence auto-tuner off.

Run:  python backtest_live.py [--refresh] [--start YYYY-MM-DD] [--end YYYY-MM-DD]
Data is cached in backtest_cache/; results go to reports/backtest_live_*.json.
Nothing is written to data/ (guarded) and no alerts are sent (stubbed).
"""

import argparse
import builtins
import json
import logging
import os
import pickle
import random
import sys
import tempfile
import time
import types
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

HERE      = os.path.dirname(os.path.abspath(__file__))
LIVE_DATA = os.path.join(HERE, "data")
CACHE_DIR = os.path.join(HERE, "backtest_cache")
REPORTS   = os.path.join(HERE, "reports")
ET        = ZoneInfo("America/New_York")
sys.path.insert(0, HERE)

log = logging.getLogger("backtest_live")


# ── Safety: stub alerts, refuse writes to the live data/ dir ─────────────────

class _Stub(types.ModuleType):
    def __getattr__(self, name):
        return lambda *a, **k: None

sys.modules["alerts"]            = _Stub("alerts")
sys.modules["finbert_sentiment"] = _Stub("finbert_sentiment")

_real_open = builtins.open

def _guarded_open(file, mode="r", *a, **k):
    if any(c in mode for c in "wax+"):
        p = os.path.realpath(str(file))
        if p.startswith(os.path.realpath(LIVE_DATA) + os.sep):
            raise PermissionError(f"backtest refused to write live file {p}")
    return _real_open(file, mode, *a, **k)

builtins.open = _guarded_open


# ── Fake clock ────────────────────────────────────────────────────────────────

_NOW = [datetime(2020, 1, 1, tzinfo=timezone.utc)]

# The server runs on UTC, so naive now() and date.today() are UTC, as live.
class FakeDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        n = _NOW[0]
        return n.astimezone(tz) if tz else n.astimezone(timezone.utc).replace(tzinfo=None)

    @classmethod
    def utcnow(cls):
        return _NOW[0].astimezone(timezone.utc).replace(tzinfo=None)

class FakeDate(date):
    @classmethod
    def today(cls):
        return _NOW[0].astimezone(timezone.utc).date()

def set_now(dt_et: datetime):
    _NOW[0] = dt_et


# ── Historical data ───────────────────────────────────────────────────────────

def _download(symbols: list[str]) -> dict:
    import yfinance as yf
    daily, hourly = {}, {}
    for i, s in enumerate(symbols):
        t = yf.Ticker(s)
        try:
            d = t.history(period="4y", interval="1d").dropna(subset=["Close", "High", "Low"])
            h = t.history(period="729d", interval="1h").dropna(subset=["Close", "High", "Low"])
            if len(d) and len(h):
                d.index = d.index.tz_convert(ET)
                h.index = h.index.tz_convert(ET)
                daily[s], hourly[s] = d, h
        except Exception as e:
            log.warning(f"  {s}: price download failed: {e}")
        if i % 10 == 9:
            log.info(f"  downloaded {i + 1}/{len(symbols)}")
    # No earnings dates: live market_filter.near_earnings crashes on the dict that
    # yfinance's .calendar returns, so the live blackout never fires. FakeTicker
    # returns a dict too, which reproduces that.
    return {"daily": daily, "hourly": hourly, "earnings": {},
            "fetched_at": datetime.now(timezone.utc).isoformat()}


def load_data(refresh: bool) -> dict:
    import screener, sector_rotation
    path = os.path.join(CACHE_DIR, "prices.pkl")
    if os.path.exists(path) and not refresh:
        with _real_open(path, "rb") as f:
            return pickle.load(f)
    syms = sorted(set(screener.STOCK_UNIVERSE) | {"SPY"} | set(sector_rotation.SECTOR_ETFS))
    log.info(f"Downloading {len(syms)} symbols (4y daily, 729d hourly)...")
    data = _download(syms)
    os.makedirs(CACHE_DIR, exist_ok=True)
    with _real_open(path, "wb") as f:
        pickle.dump(data, f)
    return data


class History:
    """As-of views over the cached bars for one symbol."""

    def __init__(self, daily: pd.DataFrame, hourly: pd.DataFrame):
        self.d = daily
        self.h = hourly
        self.d_dates = daily.index.date
        # hourly bar end = start + 1h, but the last bar of the day ends at 16:00
        ends = hourly.index + pd.Timedelta(hours=1)
        close = pd.DatetimeIndex([ts.replace(hour=16, minute=0) for ts in hourly.index])
        self.h_end = ends.where(ends <= close, close)
        self.h_dates = hourly.index.date

    def hourly_done(self, now: datetime) -> pd.DataFrame:
        """Hourly bars completed by `now`."""
        return self.h[self.h_end <= now]

    def daily(self, now: datetime, bars: int) -> pd.DataFrame:
        """Last `bars` trading days as yfinance returns them (period="Nd" counts
        trading days), with today's bar built from the hours completed so far."""
        today = now.date()
        hb = self.h[(self.h_dates == today) & (self.h_end <= now)]
        done = self.d[self.d_dates < today][["Open", "High", "Low", "Close", "Volume"]]
        if not len(hb):
            return done.iloc[-bars:]
        part = pd.DataFrame({
            "Open": [hb["Open"].iloc[0]], "High": [hb["High"].max()],
            "Low": [hb["Low"].min()], "Close": [hb["Close"].iloc[-1]],
            "Volume": [hb["Volume"].sum()],
        }, index=[pd.Timestamp(today, tz=ET)])
        return pd.concat([done.iloc[-(bars - 1):] if bars > 1 else done.iloc[:0], part])

    def hourly(self, now: datetime, sessions: int) -> pd.DataFrame:
        hb = self.hourly_done(now)
        if hb.empty:
            return hb
        keep = sorted(set(hb.index.date))[-sessions:]
        return hb[hb.index.date >= keep[0]]


class FakeTicker:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.fast_info = types.SimpleNamespace(market_cap=None)

    def history(self, period: str = "1mo", interval: str = "1d", **_):
        h = HIST.get(self.symbol)
        if h is None:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        n   = int(period.rstrip("d"))
        now = _NOW[0].astimezone(ET)
        return h.hourly(now, n) if interval == "1h" else h.daily(now, n)

    @property
    def calendar(self):
        today = FakeDate.today()
        return {"Earnings Date": [d for d in EARNINGS.get(self.symbol, [])
                                  if d >= today - timedelta(days=1)][:2]}


HIST: dict[str, History] = {}
EARNINGS: dict[str, list] = {}


# ── Wire the live modules to the fakes ────────────────────────────────────────

def install_fakes(tmp: str):
    import yfinance as yf
    import config, collector, screener, sector_rotation, regime
    import market_filter, macro_calendar, timeofday, risk_manager, brain

    yf.Ticker  = FakeTicker
    time.time  = lambda: _NOW[0].timestamp()   # module caches (regime, SPY, sectors) age in sim time
    time.sleep = lambda s: None                # screener throttling
    random.seed(0)                             # screener shuffles the universe
    for m in (collector, screener, sector_rotation, market_filter,
              macro_calendar, timeofday, risk_manager, brain):
        if hasattr(m, "datetime"):
            m.datetime = FakeDatetime
    market_filter.dt = FakeDatetime
    risk_manager.date = FakeDate

    risk_manager.DATA_DIR        = tmp
    risk_manager.TRAILING_FILE   = os.path.join(tmp, "trailing_stops.json")
    risk_manager.DAILY_LOSS_FILE = os.path.join(tmp, "daily_loss.json")
    market_filter.COOLDOWN_FILE  = os.path.join(tmp, "cooldowns.json")
    screener.DATA_DIR            = tmp
    screener.WATCHLIST_FILE      = os.path.join(tmp, "watchlist.json")
    screener.PERFORMANCE_FILE    = os.path.join(tmp, "performance.json")
    collector.DATA_DIR           = tmp
    brain.DECISIONS_FILE         = os.path.join(tmp, "decisions.jsonl")
    config.MIN_CONFIDENCE        = config.MIN_CONFIDENCE_FLOOR   # tuner off


# ── The AI stand-in ───────────────────────────────────────────────────────────

def proxy_decision(d: dict, held: bool) -> tuple[str, float]:
    """
    Rule signal in place of the AI. Confidence is scaled so a bare BUY (score
    0.40) lands on the 0.60 buy floor and a bare SELL (-0.35) on the 0.75 LLM
    sell bar. Held positions are never added to (the AI's prompt defaults to HOLD).
    """
    import brain
    sig, score = brain.technical_signal(d)
    if sig == "BUY" and not held:
        return "BUY", round(min(1.0, 0.60 + (score - 0.40) / 0.60 * 0.40), 3)
    if sig == "SELL" and held:
        return "SELL", round(min(1.0, 0.75 + (-score - 0.35) / 0.65 * 0.25), 3)
    return "HOLD", 0.0


# ── Simulated account ─────────────────────────────────────────────────────────

class Account:
    def __init__(self, cash: float):
        self.cash   = cash
        self.pos    = {}     # symbol -> {qty, avg, opened}
        self.trades = []
        self.equity = []
        self.peak   = cash

    def value(self, prices: dict) -> float:
        return self.cash + sum(p["qty"] * prices.get(s, p["avg"]) for s, p in self.pos.items())

    def buy(self, sym, spend, fill, now, conf, reason):
        qty = spend / fill
        self.cash -= spend
        self.pos[sym] = {"qty": qty, "avg": fill, "opened": now}
        self.trades.append({"ts": now.isoformat(), "symbol": sym, "action": "BUY",
                            "price": round(fill, 4), "value": round(spend, 2),
                            "confidence": conf, "reason": reason})

    def sell(self, sym, fill, now, reason):
        import risk_manager as rm, market_filter as mf
        p = self.pos.pop(sym)
        proceeds = p["qty"] * fill
        pnl = proceeds - p["qty"] * p["avg"]
        self.cash += proceeds
        rm.remove_trailing_stop(sym)
        rm.record_daily_pnl(pnl)
        mf.record_trade_time(sym)
        self.trades.append({"ts": now.isoformat(), "symbol": sym, "action": "SELL",
                            "price": round(fill, 4), "value": round(proceeds, 2),
                            "pnl": round(pnl, 2), "pnl_pct": round((fill / p["avg"] - 1) * 100, 2),
                            "hold_days": round((now - p["opened"]).total_seconds() / 86400, 2),
                            "reason": reason})


def bar_at(sym: str, start: datetime):
    h = HIST.get(sym)
    if h is None:
        return None
    try:
        return h.h.loc[start]
    except KeyError:
        return None


def last_close(sym: str, now: datetime):
    h = HIST.get(sym)
    if h is None:
        return None
    hb = h.hourly_done(now)
    return float(hb["Close"].iloc[-1]) if len(hb) else None


def execute(acct: Account, sym: str, action: str, conf: float, d: dict,
            now: datetime, fill_bar, reason: str):
    """Mirror of alpaca_executor.execute_decision for BUY/SELL."""
    import config, risk_manager as rm, market_filter as mf, regime as reg

    if rm.is_trading_halted():
        return
    if conf < config.MIN_CONFIDENCE:
        return
    if fill_bar is None:
        return
    fill = float(fill_bar["Open"])

    if action == "BUY":
        ok, why = mf.should_trade({"symbol": sym, "action": "BUY"})
        if not ok:
            return
        prices   = {s: last_close(s, now) or p["avg"] for s, p in acct.pos.items()}
        port_val = acct.value(prices)
        acct.peak = max(acct.peak, port_val)
        dd_mult  = rm.drawdown_size_multiplier(port_val, acct.peak)
        regime_name, _ = reg.detect_regime()
        r_mult   = reg.regime_multipliers(regime_name).get("size_mult", 1.0)
        size_pct = mf.position_size_pct(conf) * dd_mult * r_mult
        spend    = min(port_val * size_pct, acct.cash * 0.95)
        if spend < 1.0 or len(acct.pos) >= config.MAX_OPEN_POSITIONS:
            return
        acct.buy(sym, spend, fill, now, conf, reason)
        if d.get("price"):
            rm.register_trailing_stop(sym, d["price"])
        mf.record_trade_time(sym)

    elif action == "SELL" and sym in acct.pos:
        acct.sell(sym, fill, now, reason)


def manage_stops(acct: Account, bar_start: datetime):
    """One hourly bar of check_stops + the resting broker stop."""
    import config, risk_manager as rm
    for sym in list(acct.pos):
        b = bar_at(sym, bar_start)
        if b is None:
            continue
        ts = rm.get_trailing_stops().get(sym)
        if ts and float(b["Low"]) <= ts["stop"]:
            acct.sell(sym, min(float(b["Open"]), ts["stop"]), bar_start, "trailing_stop")
            continue
        rm.update_trailing_stops({sym: float(b["Close"])})
        if float(b["Close"]) / acct.pos[sym]["avg"] - 1 >= config.TAKE_PROFIT_PCT:
            acct.sell(sym, float(b["Close"]), bar_start, "take_profit")


def morning(acct: Account, day: date, first_bar: datetime):
    """8:45 ET: screener, earnings exit, time-based exit (fills at the open)."""
    import config, screener, market_filter as mf
    set_now(datetime(day.year, day.month, day.day, 8, 45, tzinfo=ET))
    mf._earnings_cache.clear()
    screener.run_screener()
    config.STOCK_WATCHLIST = screener.get_current_watchlist()[0]

    for sym in mf.get_positions_near_earnings({s: {} for s in acct.pos}):
        execute(acct, sym, "SELL", 0.95, {}, first_bar, bar_at(sym, first_bar), "earnings_exit")

    for sym, p in list(acct.pos.items()):
        px = last_close(sym, _NOW[0]) or p["avg"]
        hold_days = (_NOW[0] - p["opened"]).days
        if hold_days >= 7 and (px / p["avg"] - 1) * 100 < 2.0:
            execute(acct, sym, "SELL", 0.90, {}, first_bar, bar_at(sym, first_bar), "time_exit")


def scan(acct: Account, now: datetime):
    """One stock cycle: live indicators for the watchlist, proxy decision, execute."""
    import config, collector, risk_manager as rm
    set_now(now)
    if rm.is_trading_halted():
        return
    for sym in list(config.STOCK_WATCHLIST):
        d = collector.fetch_stock_data(sym)
        if not d:
            continue
        action, conf = proxy_decision(d, sym in acct.pos)
        if action != "HOLD":
            execute(acct, sym, action, conf, d, now, bar_at(sym, now), f"proxy_{action.lower()}")


def run(start: date, end: date, capital: float) -> dict:
    import config, screener
    spy = HIST["SPY"]
    days = sorted({d for d in spy.h_dates if start <= d <= end})
    acct = Account(capital)

    for i, day in enumerate(days):
        bars = spy.h[spy.h_dates == day].index
        if len(bars) == 0:
            continue
        morning(acct, day, bars[0].to_pydatetime())
        for b in bars:
            b = b.to_pydatetime()
            if b.hour == 13 and b.minute == 30:          # 13:00 ET screener refresh
                set_now(b.replace(minute=0))
                screener.run_screener()
                config.STOCK_WATCHLIST = screener.get_current_watchlist()[0]
            if (b.hour, b.minute) >= (10, 30):
                scan(acct, b)
            set_now(b)
            manage_stops(acct, b)
        eod = datetime(day.year, day.month, day.day, 16, 0, tzinfo=ET)
        prices = {s: last_close(s, eod) for s in acct.pos}
        acct.peak = max(acct.peak, acct.value(prices))
        acct.equity.append({"date": day.isoformat(), "value": round(acct.value(prices), 2),
                            "positions": len(acct.pos)})
        if i % 20 == 19:
            log.info(f"  {day}  equity ${acct.equity[-1]['value']:,.2f}  "
                     f"trades {len(acct.trades)}  open {len(acct.pos)}")
    return summarize(acct, days, capital)


def summarize(acct: Account, days: list, capital: float) -> dict:
    eq    = acct.equity
    final = eq[-1]["value"] if eq else capital
    peak, max_dd = capital, 0.0
    for e in eq:
        peak = max(peak, e["value"])
        max_dd = max(max_dd, (peak - e["value"]) / peak * 100)
    sells  = [t for t in acct.trades if t["action"] == "SELL"]
    wins   = [t for t in sells if t["pnl"] > 0]
    losses = [t for t in sells if t["pnl"] <= 0]
    spy_c  = HIST["SPY"].d
    spy_c  = spy_c[(spy_c.index.date >= days[0]) & (spy_c.index.date <= days[-1])]["Close"]
    months = max(len(days) / 21, 1e-9)
    by_reason = {}
    for t in sells:
        r = by_reason.setdefault(t["reason"], {"n": 0, "pnl": 0.0})
        r["n"] += 1
        r["pnl"] = round(r["pnl"] + t["pnl"], 2)
    monthly = {}
    for e in eq:
        monthly.setdefault(e["date"][:7], []).append(e["value"])
    prev, monthly_ret = capital, {}
    for m, vals in monthly.items():
        monthly_ret[m] = round((vals[-1] / prev - 1) * 100, 2)
        prev = vals[-1]
    return {
        "start": days[0].isoformat(), "end": days[-1].isoformat(),
        "trading_days": len(days), "start_capital": capital, "end_value": final,
        "total_return_pct": round((final / capital - 1) * 100, 2),
        "avg_monthly_pct": round(((final / capital) ** (1 / months) - 1) * 100, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "spy_return_pct": round((spy_c.iloc[-1] / spy_c.iloc[0] - 1) * 100, 2),
        "closed_trades": len(sells),
        "win_rate_pct": round(len(wins) / len(sells) * 100, 1) if sells else 0,
        "avg_win": round(sum(t["pnl"] for t in wins) / len(wins), 2) if wins else 0,
        "avg_loss": round(sum(t["pnl"] for t in losses) / len(losses), 2) if losses else 0,
        "median_hold_days": sorted(t["hold_days"] for t in sells)[len(sells) // 2] if sells else 0,
        "exits_by_reason": by_reason,
        "monthly_return_pct": monthly_ret,
        "open_at_end": list(acct.pos),
        "trades": acct.trades,
        "equity": eq,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-download price history")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("screener", "collector", "market_filter", "risk_manager", "regime",
                  "sector_rotation", "macro_calendar", "timeofday", "brain", "yfinance"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    data = load_data(args.refresh)
    for s, d in data["daily"].items():
        if s in data["hourly"]:
            HIST[s] = History(d, data["hourly"][s])
    EARNINGS.update(data["earnings"])

    tmp = tempfile.mkdtemp(prefix="bt_live_")
    install_fakes(tmp)
    import config

    first_h = HIST["SPY"].h.index[0].date()
    start = date.fromisoformat(args.start) if args.start else first_h + timedelta(days=10)
    end   = date.fromisoformat(args.end) if args.end else HIST["SPY"].h.index[-1].date()
    log.info(f"Replaying {start} → {end} with ${config.VIRTUAL_CASH:,.0f} "
             f"({len(HIST)} symbols, data fetched {data['fetched_at'][:10]})")

    r = run(start, end, config.VIRTUAL_CASH)
    r["config"] = {k: getattr(config, k) for k in (
        "MIN_CONFIDENCE", "MIN_POSITION_SIZE_PCT", "MAX_POSITION_SIZE_PCT", "STOP_LOSS_PCT",
        "TAKE_PROFIT_PCT", "TRAILING_STOP_ATR_MULT", "MAX_OPEN_POSITIONS", "MAX_DAILY_LOSS",
        "TRADE_COOLDOWN_HOURS")}
    r["note"] = "Rule signal stands in for the AI; see module docstring."

    os.makedirs(REPORTS, exist_ok=True)
    out = args.out or os.path.join(REPORTS, f"backtest_live_{start}_{end}.json")
    with _real_open(out, "w") as f:
        json.dump(r, f, indent=1, default=str)

    log.info("=" * 60)
    for k in ("start", "end", "end_value", "total_return_pct", "avg_monthly_pct",
              "max_drawdown_pct", "spy_return_pct", "closed_trades", "win_rate_pct",
              "avg_win", "avg_loss", "median_hold_days"):
        log.info(f"  {k:18s} {r[k]}")
    log.info(f"  exits_by_reason    {r['exits_by_reason']}")
    log.info(f"  saved → {out}")


if __name__ == "__main__":
    main()
