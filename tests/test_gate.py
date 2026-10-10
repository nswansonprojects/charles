"""Pre-trade gate (market_filter.should_trade) and the executor's early refusals."""
import datetime as d
from datetime import datetime, timezone, timedelta

from base import BotTestCase, config

import alpaca_executor as ax
import market_filter as mf
import regime


class Gate(BotTestCase):
    def setUp(self):
        super().setUp()
        # all filters open by default; each test closes one
        self.patch(mf, "near_earnings", lambda s: (False, 999))
        self.patch(mf, "market_is_bullish", lambda: (True, 1.0))
        self.patch(mf, "macro_calendar", None)
        self.patch(mf, "timeofday", None)
        self.patch(regime, "detect_regime", lambda: ("TRENDING_BULL", {}))

    def buy(self, conf=0.80, symbol="XYZ"):
        return mf.should_trade({"symbol": symbol, "action": "BUY", "confidence": conf})

    def test_open_gate_allows_buy(self):
        self.assertEqual(self.buy(), (True, ""))

    def test_sells_are_never_filtered(self):
        self.patch(regime, "detect_regime", lambda: ("TRENDING_BEAR", {}))
        self.patch(mf, "market_is_bullish", lambda: (False, -3.0))
        self.assertEqual(mf.should_trade({"symbol": "XYZ", "action": "SELL", "confidence": 0.1}),
                         (True, ""))

    def test_earnings_blocks(self):
        self.patch(mf, "near_earnings", lambda s: (True, 1))
        ok, why = self.buy()
        self.assertFalse(ok)
        self.assertIn("earnings", why)

    def test_cooldown_blocks_then_expires(self):
        mf.record_trade_time("XYZ")
        self.assertEqual(self.buy()[1], "cooldown active")
        old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        mf._save_cooldowns({"XYZ": old})
        self.assertTrue(self.buy()[0])

    def test_spy_downtrend_blocks(self):
        self.patch(mf, "market_is_bullish", lambda: (False, -2.5))
        ok, why = self.buy()
        self.assertFalse(ok)
        self.assertIn("downtrend", why)

    def test_bear_regime_blocks_new_buys(self):
        self.patch(regime, "detect_regime", lambda: ("TRENDING_BEAR", {}))
        ok, why = self.buy(conf=0.99)
        self.assertFalse(ok)
        self.assertIn("suppressed", why)

    def test_regime_raises_confidence_bar(self):
        for reg, below, at in [("RANGING", 0.64, 0.65), ("VOLATILE", 0.74, 0.75),
                               ("TRENDING_BULL", 0.59, 0.60)]:
            self.patch(regime, "detect_regime", lambda r=reg: (r, {}))
            with self.subTest(regime=reg):
                self.assertFalse(self.buy(conf=below)[0])
                self.assertTrue(self.buy(conf=at)[0])

    def test_failed_regime_check_does_not_block(self):
        def boom():
            raise RuntimeError("yahoo down")
        self.patch(regime, "detect_regime", boom)
        self.assertTrue(self.buy()[0])

    def test_macro_and_time_windows_block(self):
        class Macro:
            @staticmethod
            def in_macro_blackout():
                return True, "FOMC"
        self.patch(mf, "macro_calendar", Macro)
        self.assertIn("macro", self.buy()[1])
        self.patch(mf, "macro_calendar", None)

        class Tod:
            @staticmethod
            def in_avoid_window():
                return True, "Opening noise window"
        self.patch(mf, "timeofday", Tod)
        self.assertIn("time filter", self.buy()[1])


class Earnings(BotTestCase):
    def test_reads_all_calendar_formats(self):
        import pandas as pd
        self.assertEqual(mf._earnings_dates({"Earnings Date": [d.date(2026, 10, 2)]}), [d.date(2026, 10, 2)])
        self.assertEqual(mf._earnings_dates({"Earnings Date": d.datetime(2026, 10, 3, 16)}), [d.date(2026, 10, 3)])
        self.assertEqual(mf._earnings_dates(None), [])
        self.assertEqual(mf._earnings_dates({}), [])
        df = pd.DataFrame({0: [pd.Timestamp("2026-10-02")]}, index=["Earnings Date"])
        self.assertEqual(mf._earnings_dates(df), [d.date(2026, 10, 2)])

    def test_window_and_cache(self):
        today = datetime.now(timezone.utc).astimezone(mf._ET).date()
        calls = []

        class Ticker:
            def __init__(self, sym):
                calls.append(sym)
                offset = {"SOON": 2, "FAR": 10, "PAST": -2}[sym]
                self.calendar = {"Earnings Date": [today + timedelta(days=offset)]}
        self.patch(mf.yf, "Ticker", Ticker)
        self.patch(mf, "_earnings_cache", {})
        self.assertEqual(mf.near_earnings("SOON"), (True, 2))
        self.assertEqual(mf.near_earnings("FAR"), (False, 999))
        self.assertEqual(mf.near_earnings("PAST"), (False, 999))
        mf.near_earnings("SOON")
        self.assertEqual(calls.count("SOON"), 1, "second lookup the same day should be cached")

    def test_lookup_failure_does_not_block(self):
        def boom(sym):
            raise RuntimeError("no data")
        self.patch(mf.yf, "Ticker", boom)
        self.patch(mf, "_earnings_cache", {})
        self.assertEqual(mf.near_earnings("ANY"), (False, 999))


class ExecutorRefusals(BotTestCase):
    """execute_decision must refuse these before it ever talks to the broker."""
    def setUp(self):
        super().setUp()

        def no_broker():
            raise AssertionError("broker was contacted")
        self.patch(ax, "_client", no_broker)

    def test_hold_does_nothing(self):
        self.assertIsNone(ax.execute_decision({"symbol": "XYZ", "action": "HOLD", "confidence": 0.9}))

    def test_low_confidence_refused(self):
        self.assertIsNone(ax.execute_decision({"symbol": "XYZ", "action": "BUY", "confidence": 0.55}))

    def test_rule_fallback_confidence_never_trades(self):
        # brain stamps rule fallbacks 0.55 on purpose (#21)
        self.assertLess(0.55, config.MIN_CONFIDENCE)
        self.assertIsNone(ax.execute_decision({"symbol": "XYZ", "action": "SELL", "confidence": 0.55}))

    def test_halted_refuses_everything(self):
        import risk_manager as rm
        rm.record_daily_pnl(-25.0)
        self.assertIsNone(ax.execute_decision({"symbol": "XYZ", "action": "BUY", "confidence": 0.95}))

    def test_gate_block_stops_buy_before_broker(self):
        self.patch(mf, "should_trade", lambda dec: (False, "test block"))
        self.assertIsNone(ax.execute_decision({"symbol": "XYZ", "action": "BUY", "confidence": 0.95}))
