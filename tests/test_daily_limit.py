"""The daily loss limit (owner's kill switch), atomic state writes, ET trading-day dates."""
import json
import os

from base import BotTestCase, config  # noqa: F401

import risk_manager as rm
import state_io
import timeofday


class DailyLossKillSwitch(BotTestCase):
    def test_accumulates_and_halts_at_limit(self):
        rm.record_daily_pnl(-8.0)
        rm.record_daily_pnl(-7.0)
        self.assertFalse(rm.is_trading_halted())
        rm.record_daily_pnl(-5.0)            # -20 = limit
        self.assertTrue(rm.is_trading_halted())
        self.assertEqual(rm.get_daily_stats()["loss"], -20.0)

    def test_open_losses_count_too(self):
        rm.check_day_change(-12.0)           # positions down, nothing closed yet
        self.assertFalse(rm.is_trading_halted())
        rm.check_day_change(-20.5)
        self.assertTrue(rm.is_trading_halted())
        self.assertEqual(rm.get_daily_stats()["loss"], 0.0)   # closed-trade counter untouched
        rm.check_day_change(-30.0)
        self.assertEqual([c[0] for c in self.alerts.calls].count("daily_loss_halted"), 1)

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
