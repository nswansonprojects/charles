"""Shadow portfolios: each variant differs from baseline in exactly one rule."""
import os
from datetime import datetime, timezone, timedelta

from base import BotTestCase, config

import shadow

T0 = datetime(2026, 10, 1, 16, 0, tzinfo=timezone.utc)        # 12:00 ET, inside midday window
EARLY = datetime(2026, 10, 1, 14, 0, tzinfo=timezone.utc)     # 10:00 ET, outside it


def checks(regime="TRENDING_BULL", **kw):
    import regime as reg
    c = {"regime": regime, "mults": reg.regime_multipliers(regime), "spy_ok": True,
         "macro": False, "avoid": False, "earnings": lambda s: (False, 999)}
    c.update(kw)
    return c


def buy(sym="XYZ", conf=0.80, price=100.0):
    return {"symbol": sym, "type": "stock", "action": "BUY", "confidence": conf, "price": price}


def mkt(price=100.0, atr=1.0, sym="XYZ"):
    return {sym: {"price": price, "atr": atr}}


class Shadow(BotTestCase):
    def setUp(self):
        super().setUp()
        config.TAKE_PROFIT_PCT = 0.15
        config.LLM_SELL_MIN_CONFIDENCE = 0.75
        config.PROFIT_LOCK_TRIGGER, config.PROFIT_LOCK_KEEP = 0.04, 0.5
        self.path = os.path.join(self.tmp, "shadow.json")

    def cycle(self, decisions, market, now=T0, c=None, lookup=lambda s: None):
        return shadow.on_cycle(decisions, market, "TRENDING_BULL", now=now,
                               checks=c or checks(), price_lookup=lookup, path=self.path)

    def test_buy_opens_in_every_book_with_different_sizes(self):
        books = self.cycle([buy(conf=0.80)], mkt(atr=1.0), lookup={"SPY": 500.0}.get)
        for name in shadow.VARIANTS:
            if name != "swing":                               # 12:00 is not a swing entry cycle
                self.assertIn("XYZ", books[name]["positions"], name)
        conf_value = books["baseline"]["positions"]["XYZ"]["qty"] * 100
        risk_value = books["risk_sized"]["positions"]["XYZ"]["qty"] * 100
        self.assertAlmostEqual(conf_value, 125.0)            # 12.5% at 80% confidence
        self.assertAlmostEqual(risk_value, 200.0)            # 1% risk / 2% stop = 50% → capped at 20%
        self.assertEqual(books["baseline"]["positions"]["XYZ"]["stop"], 98.0)   # 2×ATR

    def test_midday_book_skips_early_entries(self):
        books = self.cycle([buy()], mkt(), now=EARLY)
        self.assertNotIn("XYZ", books["midday_entries"]["positions"])
        self.assertIn("XYZ", books["baseline"]["positions"])

    def test_swing_buys_only_in_11_and_14_cycles(self):
        et = lambda h, m: datetime(2026, 10, 1, h + 4, m, tzinfo=timezone.utc)   # EDT = UTC-4
        cases = {(10, 59): False, (11, 0): True, (11, 4): True, (11, 29): True, (11, 30): False,
                 (12, 0): False, (13, 59): False, (14, 3): True, (14, 30): False, (15, 30): False}
        for (h, m), opens in cases.items():
            with self.subTest(f"{h:02d}:{m:02d}"):
                if os.path.exists(self.path):
                    os.remove(self.path)
                books = self.cycle([buy()], mkt(), now=et(h, m))
                self.assertEqual("XYZ" in books["swing"]["positions"], opens)

    def test_swing_wider_stop(self):
        swing_at = datetime(2026, 10, 1, 15, 5, tzinfo=timezone.utc)            # 11:05 ET
        books = self.cycle([buy()], mkt(100.0, atr=1.0), now=swing_at)
        self.assertEqual(books["swing"]["positions"]["XYZ"]["stop"], 97.0)       # 3×ATR
        self.assertEqual(books["baseline"]["positions"]["XYZ"]["stop"], 98.0)    # 2×ATR
        os.remove(self.path)
        books = self.cycle([buy()], mkt(100.0, atr=5.0), now=swing_at)
        self.assertEqual(books["swing"]["positions"]["XYZ"]["stop"], 92.0)       # 15% capped at 8%
        self.assertEqual(books["baseline"]["positions"]["XYZ"]["stop"], 95.0)    # capped at 5%
        books = self.cycle([], mkt(94.0, atr=5.0), now=swing_at + timedelta(hours=1))
        self.assertIn("XYZ", books["swing"]["positions"])
        self.assertEqual(books["baseline"]["closed"][0]["reason"], "stop")

    def test_swing_no_take_profit_no_time_exit_no_ai_sell(self):
        swing_at = datetime(2026, 10, 1, 15, 5, tzinfo=timezone.utc)            # 11:05 ET
        self.cycle([buy()], mkt(100.0, atr=1.0), now=swing_at)
        books = self.cycle([], mkt(120.0, atr=1.0), now=swing_at + timedelta(days=1))
        self.assertEqual(books["baseline"]["closed"][0]["reason"], "take_profit")
        self.assertIn("XYZ", books["swing"]["positions"])
        self.assertEqual(books["swing"]["positions"]["XYZ"]["stop"], 117.0)      # trails 3 below the high
        sell = {"symbol": "XYZ", "type": "stock", "action": "SELL", "confidence": 0.9, "price": 120.0}
        books = self.cycle([sell], mkt(120.0, atr=1.0), now=swing_at + timedelta(days=8))
        self.assertIn("XYZ", books["swing"]["positions"])

    def test_entry_blocks(self):
        cases = {"low confidence": ([buy(conf=0.59)], checks()),
                 "ranging bar 65%": ([buy(conf=0.62)], checks("RANGING")),
                 "bear market": ([buy(conf=0.99)], checks("TRENDING_BEAR")),
                 "earnings": ([buy()], checks(earnings=lambda s: (True, 1))),
                 "spy downtrend": ([buy()], checks(spy_ok=False)),
                 "macro blackout": ([buy()], checks(macro=True))}
        for label, (decs, c) in cases.items():
            with self.subTest(label):
                if os.path.exists(self.path):
                    os.remove(self.path)
                books = self.cycle(decs, mkt(), c=c)
                self.assertEqual(books["baseline"]["positions"], {}, label)

    def test_trailing_stop_raises_then_sells(self):
        self.cycle([buy()], mkt(100.0))
        books = self.cycle([], mkt(110.0), now=T0 + timedelta(hours=1))
        self.assertEqual(books["baseline"]["positions"]["XYZ"]["stop"], 108.0)
        books = self.cycle([], mkt(107.5), now=T0 + timedelta(hours=2))
        self.assertEqual(books["baseline"]["positions"], {})
        self.assertEqual(books["baseline"]["closed"][0]["reason"], "stop")
        self.assertGreater(books["baseline"]["closed"][0]["pnl"], 0)

    def test_profit_lock_keeps_half_the_best_gain(self):
        self.cycle([buy()], mkt(100.0, atr=20.0))                 # stop capped at 5% below: 95
        books = self.cycle([], mkt(108.0, atr=20.0), now=T0 + timedelta(hours=1))
        self.assertEqual(books["no_profit_lock"]["positions"]["XYZ"]["stop"], 103.0)  # 108 − 5
        self.assertEqual(books["baseline"]["positions"]["XYZ"]["stop"], 104.0)        # keeps half of +8%
        books = self.cycle([], mkt(103.5, atr=20.0), now=T0 + timedelta(hours=2))
        self.assertIn("XYZ", books["no_profit_lock"]["positions"])
        closed = books["baseline"]["closed"][0]
        self.assertEqual(closed["reason"], "stop")
        self.assertGreater(closed["pnl"], 0)
        self.assertGreater(closed["peak_pnl"], closed["pnl"])                       # peak recorded

    def test_profit_lock_waits_for_the_trigger(self):
        self.cycle([buy()], mkt(100.0, atr=20.0))
        books = self.cycle([], mkt(103.0, atr=20.0), now=T0 + timedelta(hours=1))   # best +3% < 4%
        self.assertEqual(books["no_profit_lock"]["positions"]["XYZ"]["stop"],
                         books["baseline"]["positions"]["XYZ"]["stop"])

    def test_breakeven_stop_after_small_gain(self):
        self.cycle([buy()], mkt(100.0, atr=20.0))                 # stop 95
        books = self.cycle([], mkt(101.6, atr=20.0), now=T0 + timedelta(hours=1))   # best +1.6%
        self.assertEqual(books["breakeven"]["positions"]["XYZ"]["stop"], 100.0)
        self.assertEqual(books["baseline"]["positions"]["XYZ"]["stop"], 96.6)      # 101.6 − 5
        books = self.cycle([], mkt(99.9, atr=20.0), now=T0 + timedelta(hours=2))
        self.assertEqual(books["breakeven"]["closed"][0]["reason"], "stop")
        self.assertIn("XYZ", books["baseline"]["positions"])

    def test_take_profit(self):
        self.cycle([buy()], mkt(100.0, atr=20.0))         # wide ATR → stop capped at 5%
        books = self.cycle([], mkt(116.0, atr=20.0), now=T0 + timedelta(days=1))
        self.assertEqual(books["no_time_exit"]["closed"][0]["reason"], "take_profit")

    def test_time_exit_only_where_enabled(self):
        self.cycle([buy()], mkt(100.0))
        books = self.cycle([], mkt(100.5), now=T0 + timedelta(days=7, hours=1))
        self.assertEqual(books["baseline"]["closed"][0]["reason"], "time_exit")
        self.assertIn("XYZ", books["no_time_exit"]["positions"])

    def test_ai_sell_only_where_enabled(self):
        self.cycle([buy()], mkt(100.0))
        sell = {"symbol": "XYZ", "type": "stock", "action": "SELL", "confidence": 0.8, "price": 100.2}
        books = self.cycle([sell], mkt(100.2), now=T0 + timedelta(hours=5))
        self.assertEqual(books["baseline"]["closed"][0]["reason"], "ai_sell")
        self.assertIn("XYZ", books["no_time_exit"]["positions"])
        weak = dict(sell, symbol="ABC", confidence=0.7)
        self.cycle([buy("ABC")], mkt(50.0, sym="ABC"), now=T0 + timedelta(hours=6))
        books = self.cycle([weak], mkt(50.0, sym="ABC"), now=T0 + timedelta(hours=7))
        self.assertIn("ABC", books["baseline"]["positions"])   # below the 75% sell bar

    def test_cooldown_and_max_positions(self):
        self.cycle([buy()], mkt(100.0))
        self.cycle([], mkt(97.0), now=T0 + timedelta(minutes=30))       # stopped out
        books = self.cycle([buy()], mkt(97.0), now=T0 + timedelta(hours=1))
        self.assertNotIn("XYZ", books["baseline"]["positions"])         # 4h cooldown
        decs = [buy(s) for s in "ABCDE"]
        m = {s: {"price": 10.0, "atr": 0.1} for s in "ABCDE"}
        books = self.cycle(decs, m, now=T0 + timedelta(hours=2))
        self.assertEqual(len(books["baseline"]["positions"]), config.MAX_OPEN_POSITIONS)

    def test_daily_loss_stops_new_entries(self):
        books = shadow.load(self.path) or {}
        self.cycle([], {})
        books = shadow.load(self.path)
        books["baseline"]["day_pnl"] = -25.0
        shadow.save(books, self.path)
        books = self.cycle([buy()], mkt())
        self.assertEqual(books["baseline"]["positions"], {})
        books = self.cycle([buy()], mkt(), now=T0 + timedelta(days=1))   # new day resets
        self.assertIn("XYZ", books["baseline"]["positions"])

    def test_held_symbol_priced_via_lookup(self):
        self.cycle([buy("OLD")], mkt(sym="OLD"))
        books = self.cycle([], mkt(sym="NEW"), now=T0 + timedelta(hours=1), lookup=lambda s: 90.0)
        self.assertEqual(books["baseline"]["closed"][0]["symbol"], "OLD")   # 90 < 98 stop

    def test_core_satellite_holds_spy_core(self):
        spy = {"SPY": 500.0}
        books = self.cycle([], mkt(), lookup=spy.get)
        b = books["core_satellite"]
        self.assertAlmostEqual(b["core"]["qty"] * 500.0, 700.0)              # 70% in SPY
        self.assertAlmostEqual(b["cash"], 300.0)
        spy["SPY"] = 550.0                                                   # +10%, never sold
        books = self.cycle([], mkt(), now=T0 + timedelta(days=30), lookup=spy.get)
        b = books["core_satellite"]
        self.assertEqual(b["core"]["entry"], 500.0)
        self.assertAlmostEqual(b["equity"][-1][1], 1070.0)
        self.assertEqual(b["closed"], [])

    def test_core_satellite_scaled_satellite(self):
        books = self.cycle([buy(conf=0.80)], mkt(), lookup=lambda s: None)  # no SPY price yet
        self.assertEqual(books["core_satellite"]["positions"], {})          # no satellite before core
        self.assertNotIn("core", books["core_satellite"])
        books = self.cycle([buy(conf=0.80)], mkt(), now=T0 + timedelta(minutes=30),
                           lookup={"SPY": 500.0}.get)
        sat = books["core_satellite"]["positions"]["XYZ"]["qty"] * 100
        base = books["baseline"]["positions"]["XYZ"]["qty"] * 100
        self.assertAlmostEqual(sat, 37.5)                                   # 12.5% of the $300 slice
        self.assertAlmostEqual(sat, 0.3 * base)
        books = shadow.load(self.path)
        books["core_satellite"]["day_pnl"] = -7.0                          # past 30% of the $20 limit
        books["core_satellite"]["positions"] = {}
        shadow.save(books, self.path)
        books = self.cycle([buy("ABC")], mkt(sym="ABC"), now=T0 + timedelta(hours=1),
                           lookup={"SPY": 500.0}.get)
        self.assertEqual(books["core_satellite"]["positions"], {})
        self.assertIn("ABC", books["baseline"]["positions"])

    def test_summary(self):
        self.cycle([buy()], mkt(100.0))
        self.cycle([], mkt(95.0), now=T0 + timedelta(hours=1))
        s = shadow.summary(shadow.load(self.path))
        self.assertEqual(set(s), set(shadow.VARIANTS))
        b = s["baseline"]
        self.assertEqual((b["closed"], b["open"], b["win_pct"]), (1, 0, 0))
        self.assertLess(b["return_pct"], 0)
        self.assertEqual(b["exits"]["stop"]["n"], 1)

    def test_weekly_vs_baseline_and_spy(self):
        now = datetime(2026, 10, 12, 20, 0, tzinfo=timezone.utc)      # window from 2026-10-05
        books = {
            "baseline": {"started": "2026-09-01T14:00:00+00:00",
                         "equity": [["2026-10-02T19:30+00:00", 1000.0], ["2026-10-06T19:30+00:00", 1100.0],
                                    ["2026-10-09T19:30+00:00", 990.0]],
                         "closed": [{"closed": "2026-10-01T15:00:00+00:00", "pnl": 50.0},
                                    {"closed": "2026-10-08T15:00:00+00:00", "pnl": -10.0}]},
            "swing": {"started": "2026-10-07T14:00:00+00:00",        # began inside the window
                      "equity": [["2026-10-07T19:30+00:00", 1020.0]], "closed": []},
        }
        daily = {"trend_following": {"started": "2026-09-01", "closed": [],
                                     "equity": [["2026-10-02", 1000.0], ["2026-10-09", 1030.0]]}}
        spy = [["2026-10-02", 500.0], ["2026-10-06", 510.0], ["2026-10-09", 505.0]]
        w = shadow.weekly(now=now, books=books, daily_books=daily, spy=spy)
        self.assertEqual((w["from"], w["spy_return_pct"]), ("2026-10-05", 1.0))
        self.assertEqual(w["spy_max_drawdown_pct"], -0.98)
        b, s, t = w["books"]["baseline"], w["books"]["swing"], w["books"]["trend_following"]
        self.assertEqual((b["return_pct"], b["closed"], b["pnl"]), (-1.0, 1, -10.0))
        self.assertEqual(b["max_drawdown_pct"], -10.0)
        self.assertEqual((b["vs_spy_pct"], b["vs_baseline_pct"]), (-2.0, 0.0))
        self.assertEqual(s["return_pct"], 2.0)                       # from the $1,000 start
        self.assertEqual((t["return_pct"], t["vs_baseline_pct"], t["vs_spy_pct"]), (3.0, 4.0, 2.0))
        self.assertEqual((t["drop_vs_baseline_pct"], t["drop_vs_spy_pct"]), (10.0, 0.98))

    def test_weekly_without_data(self):
        w = shadow.weekly(now=T0, books={}, daily_books={}, spy=None)
        self.assertEqual((w["books"], w["spy_return_pct"], w["spy_max_drawdown_pct"]), ({}, None, None))
