"""Stop logic: capped trailing distance, trailing stops, daily-loss kill switch."""
import json
import os

from base import BotTestCase, config

import risk_manager as rm
import state_io
import timeofday


class CappedDistance(BotTestCase):
    def test_atr_wider_than_fixed_stop_is_capped(self):
        self.assertAlmostEqual(rm._capped_dist(10.0, 100.0), 5.0)   # 5% of entry

    def test_narrow_atr_kept(self):
        self.assertAlmostEqual(rm._capped_dist(2.0, 100.0), 2.0)

    def test_no_atr_falls_back_to_fixed_stop(self):
        self.assertAlmostEqual(rm._capped_dist(0.0, 100.0), 5.0)


class TrailingStops(BotTestCase):
    def setUp(self):
        super().setUp()
        self.patch(rm, "calc_atr", lambda sym, period=14: 1.5)   # 2×ATR = 3.0, under the 5.0 cap

    def test_register(self):
        rm.register_trailing_stop("XYZ", 100.0)
        ts = rm.get_trailing_stops()["XYZ"]
        self.assertEqual((ts["entry"], ts["stop"], ts["dist"]), (100.0, 97.0, 3.0))

    def test_register_without_atr_uses_fixed_stop(self):
        self.patch(rm, "calc_atr", lambda sym, period=14: 0.0)
        rm.register_trailing_stop("XYZ", 100.0)
        self.assertEqual(rm.get_trailing_stops()["XYZ"]["stop"], 95.0)

    def test_profit_lock_keeps_half_the_best_gain(self):
        self.patch(rm, "calc_atr", lambda sym, period=14: 5.0)    # 2×ATR = 10, capped at 5
        rm.register_trailing_stop("XYZ", 100.0)
        self.assertEqual(rm.update_trailing_stops({"XYZ": 108.0}), [])
        ts = rm.get_trailing_stops()["XYZ"]
        self.assertEqual(ts["stop"], 104.0)          # plain trailing would be 103
        self.assertTrue(ts["locked"])
        self.assertEqual(rm.update_trailing_stops({"XYZ": 103.9}), ["XYZ"])

    def test_profit_lock_waits_for_the_trigger_and_can_be_off(self):
        self.patch(rm, "calc_atr", lambda sym, period=14: 5.0)
        rm.register_trailing_stop("XYZ", 100.0)
        rm.update_trailing_stops({"XYZ": 103.0})                   # best +3% < 4%
        self.assertEqual(rm.get_trailing_stops()["XYZ"]["stop"], 98.0)
        config.PROFIT_LOCK_TRIGGER = 0
        rm.update_trailing_stops({"XYZ": 110.0})
        self.assertEqual(rm.get_trailing_stops()["XYZ"]["stop"], 105.0)   # trailing only

    def test_profit_lock_uses_broker_entry_price(self):
        self.patch(rm, "calc_atr", lambda sym, period=14: 5.0)
        rm.register_trailing_stop("XYZ", 100.0)
        rm.set_trailing_field("XYZ", "avg_entry", 102.0)          # actual fill was higher
        rm.update_trailing_stops({"XYZ": 110.16})                  # +8% on 102
        self.assertAlmostEqual(rm.get_trailing_stops()["XYZ"]["stop"], 106.08, places=2)

    def test_stop_rises_with_price_and_never_falls(self):
        rm.register_trailing_stop("XYZ", 100.0)
        self.assertEqual(rm.update_trailing_stops({"XYZ": 110.0}), [])
        self.assertEqual(rm.get_trailing_stops()["XYZ"]["stop"], 107.0)
        self.assertEqual(rm.update_trailing_stops({"XYZ": 108.0}), [])
        self.assertEqual(rm.get_trailing_stops()["XYZ"]["stop"], 107.0)
        self.assertTrue(any(c[0] == "trailing_stop_raised" for c in self.alerts.calls))

    def test_triggers_at_stop_and_is_removed(self):
        rm.register_trailing_stop("XYZ", 100.0)
        self.assertEqual(rm.update_trailing_stops({"XYZ": 97.0}), ["XYZ"])
        self.assertNotIn("XYZ", rm.get_trailing_stops())

    def test_missing_price_is_ignored(self):
        rm.register_trailing_stop("XYZ", 100.0)
        self.assertEqual(rm.update_trailing_stops({}), [])
        self.assertIn("XYZ", rm.get_trailing_stops())

    def test_old_wide_distance_is_capped(self):
        state_io.write_json(rm.TRAILING_FILE, {"OLD": {"entry": 100.0, "highest": 100.0,
                                                       "stop": 88.0, "atr": 6.0, "dist": 12.0}})
        self.assertEqual(rm.update_trailing_stops({"OLD": 99.0}), [])
        ts = rm.get_trailing_stops()["OLD"]
        self.assertEqual((ts["dist"], ts["stop"]), (5.0, 95.0))

    def test_remove(self):
        rm.register_trailing_stop("XYZ", 100.0)
        rm.remove_trailing_stop("XYZ")
        self.assertEqual(rm.get_trailing_stops(), {})


class DailyLossKillSwitch(BotTestCase):
    def test_accumulates_and_halts_at_limit(self):
        rm.record_daily_pnl(-8.0)
        rm.record_daily_pnl(-7.0)
        self.assertFalse(rm.is_trading_halted())
        rm.record_daily_pnl(-5.0)            # -20 = limit
        self.assertTrue(rm.is_trading_halted())
        self.assertEqual(rm.get_daily_stats()["loss"], -20.0)

    def test_alert_sent_once(self):
        rm.record_daily_pnl(-25.0)
        rm.record_daily_pnl(-1.0)
        self.assertEqual([c[0] for c in self.alerts.calls].count("daily_loss_halted"), 1)

    def test_wins_offset_losses(self):
        rm.record_daily_pnl(-15.0)
        rm.record_daily_pnl(+10.0)
        rm.record_daily_pnl(-10.0)
        self.assertFalse(rm.is_trading_halted())

    def test_resets_on_new_et_trading_day(self):
        self.patch(timeofday, "market_date", lambda ts=None: "2026-10-01")
        rm.record_daily_pnl(-25.0)
        self.assertTrue(rm.is_trading_halted())
        self.patch(timeofday, "market_date", lambda ts=None: "2026-10-02")
        self.assertFalse(rm.is_trading_halted())
        self.assertEqual(rm.get_daily_stats()["loss"], 0.0)


class AtomicWrites(BotTestCase):
    def test_failed_write_keeps_old_file(self):
        path = os.path.join(self.tmp, "x.json")
        state_io.write_json(path, {"a": 1})
        with self.assertRaises(TypeError):
            state_io.write_json(path, {"a": object()})
        with open(path) as f:
            self.assertEqual(json.load(f), {"a": 1})
        self.assertEqual([f for f in os.listdir(self.tmp) if f.endswith(".tmp")], [])

    def test_keeps_permissions(self):
        path = os.path.join(self.tmp, "y.json")
        state_io.write_json(path, [])
        os.chmod(path, 0o600)
        state_io.write_json(path, [1])
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)


class MarketDate(BotTestCase):
    def test_et_day_boundaries(self):
        cases = [("2026-09-30T19:59:00+00:00", "2026-09-30"),   # 3:59 PM EDT
                 ("2026-10-01T00:30:00+00:00", "2026-09-30"),   # 8:30 PM EDT, UTC already next day
                 ("2026-12-15T04:30:00+00:00", "2026-12-14"),   # 11:30 PM EST
                 ("2026-09-30T17:31:44", "2026-09-30"),          # naive = UTC
                 ("", ""), ("garbage", "")]
        for ts, want in cases:
            with self.subTest(ts=ts):
                self.assertEqual(timeofday.market_date(ts), want)
