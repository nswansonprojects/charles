"""shadow_daily: strategy signals on made-up prices, the rebalance engine, once-a-day runs."""
import os
from datetime import datetime, timezone

from base import BotTestCase

import shadow_daily as sd


def up(n, start=100.0, step=0.5):
    return [start + i * step for i in range(n)]


def down(n, start=200.0, step=0.5):
    return [start - i * step for i in range(n)]


def book(**positions):
    b = sd._new_book("2026-10-01")
    b["positions"] = {s: {"qty": 1.0, "entry": 100.0, "last": 100.0, "opened": "2026-10-01", "days": d}
                      for s, d in positions.items()}
    return b


class Signals(BotTestCase):
    def test_mean_reversion_buys_dip_in_uptrend_only(self):
        dip = up(250) + [210.0, 200.0]                   # sharp 2-day drop, still above the 200-day avg
        self.assertEqual(sd.etf_mean_reversion({"SPY": dip, "QQQ": down(252)}, book(), "d"), {"SPY": 0.45})

    def test_mean_reversion_exits_on_bounce_or_after_10_days(self):
        bounce = up(250) + [200.0, 230.0]                # back above the 5-day average
        self.assertEqual(sd.etf_mean_reversion({"SPY": bounce}, book(SPY=1), "d")["SPY"], 0)
        flat = up(250) + [200.0, 199.0]
        self.assertEqual(sd.etf_mean_reversion({"SPY": flat}, book(SPY=3), "d")["SPY"], sd.KEEP)
        self.assertEqual(sd.etf_mean_reversion({"SPY": flat}, book(SPY=10), "d")["SPY"], 0)

    def test_crypto_trend(self):
        self.assertEqual(sd.crypto_trend({"BTC-USD": up(60), "ETH-USD": down(60)}, book(), "d"),
                         {"BTC-USD": 0.5, "ETH-USD": 0})

    def test_dual_momentum(self):
        c = {"SPY": up(300, step=0.1), "EFA": up(300, step=0.3), "GLD": up(300, step=0.2),
             "BIL": up(300, step=0.01), "AGG": up(300, step=0.01)}
        self.assertEqual(sd.dual_momentum(c, book(), "d"), {"EFA": 1.0})
        c.update(SPY=down(300, step=0.1), EFA=down(300), GLD=down(300))
        self.assertEqual(sd.dual_momentum(c, book(), "d"), {"BIL": 1.0})
        c["BIL"] = down(300, step=0.01)
        self.assertEqual(sd.dual_momentum(c, book(), "d"), {"AGG": 1.0})

    def test_sector_momentum_top3_in_uptrend_else_bil(self):
        c = {s: up(260, step=0.1 * (i + 1)) for i, s in enumerate(sd.SECTORS)}
        c["XLC"] = down(260, step=0.9)                   # fastest mover is falling: never picked
        out = sd.sector_momentum(c, book(), "d")
        self.assertEqual(set(out), {"XLRE", "XLB", "XLU"})
        c = {s: down(260) for s in sd.SECTORS}
        self.assertEqual(sd.sector_momentum(c, book(), "d"), {"BIL": 1.0})

    def test_trend_following(self):
        c = {"SPY": up(210), "QQQ": down(210), "EFA": up(210), "GLD": [], "TLT": up(50)}
        self.assertEqual(sd.trend_following(c, book(), "d"),
                         {"SPY": 0.2, "QQQ": 0, "EFA": 0.2, "GLD": 0, "TLT": 0})


def bars(closes, opens=None, days=None):
    """Made-up daily bars: high/low 1 around the close, dates d00, d01, ..."""
    n = len(closes)
    return {"date": days or [f"d{i:02d}" for i in range(n)], "open": opens or list(closes),
            "high": [c + 1 for c in closes], "low": [c - 1 for c in closes], "close": list(closes)}


class EarningsDrift(BotTestCase):
    def gapper(self, today_close=108.0):
        c = [100.0] * 18 + [107.0, today_close]          # gap day d18 opens at 105 (+5%)
        o = [100.0] * 18 + [105.0, 107.0]
        return bars(c, o)

    def test_buys_day_after_earnings_gap_that_holds(self):
        on_d18 = lambda s: {"d18"}
        self.assertEqual(sd.earnings_drift({"A": self.gapper()}, book(), "d19", on_d18), {"A": 0.2})
        self.assertEqual(sd.earnings_drift({"A": self.gapper()}, book(), "d19", lambda s: {"d17"}), {"A": 0.2})
        # no earnings, gap faded below the gap day's open, or stale data (no bar for today): no trade
        self.assertEqual(sd.earnings_drift({"A": self.gapper()}, book(), "d19", lambda s: {"d10"}), {})
        self.assertEqual(sd.earnings_drift({"A": self.gapper(104.0)}, book(), "d19", on_d18), {})
        self.assertEqual(sd.earnings_drift({"A": self.gapper()}, book(), "d20", on_d18), {})
        small = bars([100.0] * 18 + [103.0, 104.0], [100.0] * 18 + [103.0, 103.0])   # +3% gap
        self.assertEqual(sd.earnings_drift({"A": small}, book(), "d19", on_d18), {})

    def test_max_four_positions_biggest_gaps_first(self):
        held = book(H1=1, H2=1, H3=1)
        for p in held["positions"].values():
            p["opened"] = "d19"
        b = {s: self.gapper() for s in ("H1", "H2", "H3")}
        small, big = self.gapper(), self.gapper()
        big["open"][-2] = 106.0
        b.update(SMALL=small, BIG=big)
        out = sd.earnings_drift(b, held, "d19", lambda s: {"d18"})
        self.assertEqual(out, {"H1": sd.KEEP, "H2": sd.KEEP, "H3": sd.KEEP, "BIG": 0.2})

    def test_exits_after_10_days_or_on_2atr_trailing_stop(self):
        b = book(A=3)
        b["positions"]["A"]["opened"] = "d10"
        rise = [100.0] * 10 + [100.0 + i for i in range(10)]       # peak close 109, ATR ≈ 2
        self.assertEqual(sd.earnings_drift({"A": bars(rise)}, b, "d19", set)["A"], sd.KEEP)
        drop = rise + [104.0]                                        # 109 - 2×ATR ≈ 104.4
        self.assertEqual(sd.earnings_drift({"A": bars(drop)}, b, "d19", set)["A"], 0)
        b["positions"]["A"]["days"] = 10
        self.assertEqual(sd.earnings_drift({"A": bars(rise)}, b, "d19", set)["A"], 0)
        self.assertEqual(sd.earnings_drift({}, book(A=3), "d19", set), {"A": sd.KEEP})   # no data: hold


class PreEarningsRunup(BotTestCase):
    TODAY = "2026-10-09"                                  # a Friday
    E = "2026-10-20"                                      # 7 trading days later

    def stock(self, rising=True):
        c = up(60) if rising else down(60)
        return bars(c, days=[f"x{i:02d}" for i in range(59)] + [self.TODAY])

    def test_trading_days_between(self):
        self.assertEqual(sd.trading_days_between(self.TODAY, self.E), 7)
        self.assertEqual(sd.trading_days_between(self.TODAY, "2026-10-12"), 1)   # over the weekend
        self.assertEqual(sd.trading_days_between(self.E, self.TODAY), -7)

    def test_buys_7_trading_days_before_earnings_in_uptrend_only(self):
        on = lambda d: (lambda s: {"2026-07-20", d})
        self.assertEqual(sd.pre_earnings_runup({"A": self.stock()}, book(), self.TODAY, on(self.E)), {"A": 0.2})
        self.assertEqual(sd.pre_earnings_runup({"A": self.stock(False)}, book(), self.TODAY, on(self.E)), {})
        self.assertEqual(sd.pre_earnings_runup({"A": self.stock()}, book(), self.TODAY, on("2026-10-21")), {})
        self.assertEqual(sd.pre_earnings_runup({"A": self.stock()}, book(), self.TODAY, lambda s: set()), {})

    def test_max_four_positions(self):
        held = book(H1=1, H2=1, H3=1)
        for p in held["positions"].values():
            p["opened"] = "2026-10-08"
        b = {"A": self.stock(), "B": self.stock()}
        b["B"]["close"][-1] = 200.0                       # furthest above its 50-day average
        out = sd.pre_earnings_runup(b, held, self.TODAY, lambda s: {"2026-10-19"} if s[0] == "H" else {self.E})
        self.assertEqual(out, {"H1": sd.KEEP, "H2": sd.KEEP, "H3": sd.KEEP, "B": 0.2})

    def test_sells_day_before_earnings_or_at_once_if_date_missing_or_moved(self):
        b = book(A=5)
        b["positions"]["A"]["opened"] = "2026-10-02"     # entered 7 trading days before 10-13
        run = lambda today, dates: sd.pre_earnings_runup({}, b, today, lambda s: dates)["A"]
        self.assertEqual(run("2026-10-09", {"2026-10-13"}), sd.KEEP)
        self.assertEqual(run("2026-10-12", {"2026-10-13"}), 0)         # the trading day before
        self.assertEqual(run("2026-10-09", set()), 0)                  # date gone
        self.assertEqual(run("2026-10-09", {"2026-10-20"}), 0)         # moved later
        self.assertEqual(run("2026-10-09", {"2026-10-14", "2026-07-14"}), 0)   # moved, old date only in the past

    def test_earnings_cache_looks_up_once_a_day_near_the_date(self):
        calls = []
        lookup = lambda s: calls.append(s) or ({"2026-12-01"} if s == "FAR" else {self.E})
        cache = {}
        get = sd.cached_earnings(lookup, self.TODAY, cache)
        self.assertEqual(get("FAR"), {"2026-12-01"})
        get("FAR"), get("NEAR"), get("NEAR")
        self.assertEqual(calls, ["FAR", "NEAR"])                       # same day: cached
        get = sd.cached_earnings(lookup, "2026-10-12", cache)
        get("FAR"), get("NEAR")
        self.assertEqual(calls, ["FAR", "NEAR", "NEAR"])               # far date: weekly; near: daily
        get = sd.cached_earnings(lookup, "2026-10-16", cache, max_lookups=0)
        self.assertEqual(get("NEAR"), {self.E})                        # over the cap: cache answers
        self.assertEqual(get("NEW"), set())
        self.assertEqual(len(calls), 3)


class Engine(BotTestCase):
    def test_rebalance_buys_sells_and_records_pnl(self):
        b = sd._new_book("d1")
        sd.rebalance(b, {"A": 0.5, "B": 0.5}, {"A": 10.0, "B": 20.0}, "d1", False)
        self.assertAlmostEqual(b["positions"]["A"]["qty"], 50.0)
        self.assertAlmostEqual(b["cash"], 0.0)
        sd.rebalance(b, {"A": 0.5, "B": 0}, {"A": 12.0, "B": 20.0}, "d2", False)
        self.assertNotIn("B", b["positions"])
        self.assertEqual(b["closed"][0]["pnl"], 0.0)
        self.assertAlmostEqual(sd._value(b, {"A": 12.0}), 1100.0)

    def test_steady_signal_does_not_churn(self):
        b = sd._new_book("d1")
        sd.rebalance(b, {"A": 0.5}, {"A": 10.0}, "d1", False)
        qty = b["positions"]["A"]["qty"]
        sd.rebalance(b, {"A": 0.5}, {"A": 15.0}, "d2", False)   # same holdings: no trade
        self.assertEqual(b["positions"]["A"]["qty"], qty)
        self.assertEqual(b["positions"]["A"]["days"], 1)

    def test_monthly_strategy_trades_once_a_month(self):
        c = {"SPY": up(300, step=0.3), "EFA": up(300, step=0.1), "GLD": up(300, step=0.1),
             "BIL": up(300, step=0.01), "AGG": up(300)}
        books = sd.run(c, "2026-10-05", {})
        self.assertIn("SPY", books["dual_momentum"]["positions"])
        c["EFA"] = up(300, step=0.9)                    # EFA now leads, but it's mid-month
        sd.run(c, "2026-10-06", books)
        self.assertIn("SPY", books["dual_momentum"]["positions"])
        sd.run(c, "2026-11-02", books)
        self.assertEqual(set(books["dual_momentum"]["positions"]), {"EFA"})

    def test_max_drawdown(self):
        self.assertEqual(sd.max_drawdown_pct([1000, 1100, 990, 1200]), -10.0)
        self.assertEqual(sd.max_drawdown_pct([]), 0.0)


class Schedule(BotTestCase):
    def test_runs_once_per_weekday_after_1530_et(self):
        path = os.path.join(self.tmp, "daily.json")
        calls = []
        fetch = lambda: calls.append(1) or {"SPY": up(300)}
        go = lambda now: sd.maybe_run(now, fetch, path, fetch_bars=dict, earnings=lambda s: set())
        at = lambda h, m, d=2: datetime(2026, 10, d, h, m, tzinfo=timezone.utc)   # EDT = UTC-4
        self.assertFalse(go(at(19, 0)))                                 # 15:00 ET: too early
        self.assertTrue(go(at(19, 32)))                                 # 15:32 ET
        self.assertFalse(go(at(19, 45)))                                # already ran today
        self.assertFalse(go(at(19, 32, d=3)))                           # Saturday
        self.assertEqual(len(calls), 1)
        self.assertIn("trend_following", sd.summary(sd.load(path)))
        self.assertIn("earnings_drift", sd.summary(sd.load(path)))

    def test_earnings_drift_book_trades_stock_bars(self):
        days = [f"2026-09-{i:02d}" for i in range(10, 30)]
        c = [100.0] * 18 + [107.0, 108.0]
        b = {"A": bars(c, [100.0] * 18 + [105.0, 107.0], days)}
        books = sd.run({"SPY": up(300)}, "2026-09-29", {}, b, lambda s: {"2026-09-28"})
        pos = books["earnings_drift"]["positions"]["A"]
        self.assertAlmostEqual(pos["qty"] * 108.0, 200.0)
