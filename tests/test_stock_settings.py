"""stock_settings: a change to the stock plan is checked, applied, noted and undone one at a time."""
import json
import os
from datetime import datetime, timezone

from base import BotTestCase

import stock_settings
import strategy_settings

NOW = datetime(2026, 10, 30, 20, 0, tzinfo=timezone.utc)
PLAN = {"CORE_PCT": 0.8, "PICKS": 4, "PICK_PCT": 0.05, "DRIFT_PCT": 0.03, "TREND_DAYS": 200, "SAFETY_SWITCH": True, "SWITCH_CHECK": "monthly",
        "AGGRESSIVE": True, "AGG_CORE": "SSO", "AGG_VOL_MAX": 0.2, "AGG_VOL_EXIT": 0.25}


class Changes(BotTestCase):
    def setUp(self):
        super().setUp()
        self.strategy = os.path.join(self.tmp, "strategy.json")
        with open(self.strategy, "w") as f:
            json.dump({"settings": PLAN, "last_change": "x"}, f)
        self.patch(strategy_settings, "PATH", self.strategy)
        self.patch(stock_settings, "DATA", self.tmp)

    def read(self):
        with open(self.strategy) as f:
            return json.load(f)["settings"]

    def test_bad_changes_are_refused_not_clamped(self):
        for bad in ({"MAX_DAILY_LOSS": 1000}, {"PICKS": 99}, {"CORE_PCT": 0.95, "PICKS": 8, "PICK_PCT": 0.1}, {"PICKS": "4"}, {}, None):
            with self.assertRaises(ValueError, msg=bad):
                stock_settings.check_settings(bad)
        self.assertEqual(self.read(), PLAN)

    def test_apply_writes_logs_tells_and_undoes(self):
        ok, said = stock_settings.apply_settings({"PICKS": 2, "PICK_PCT": 0.1}, "the dashboard", "fewer picks", NOW)
        self.assertTrue(ok)
        self.assertEqual(self.read()["PICKS"], 2)
        with open(self.strategy) as f:
            self.assertIn("from the dashboard", json.load(f)["last_change"])
        self.assertEqual([c[0] for c in self.alerts.calls], ["control"])
        self.assertEqual(stock_settings.last_change()["changes"], {"PICKS": 2, "PICK_PCT": 0.1})
        self.assertTrue(stock_settings.undo_settings("the dashboard", NOW)[0])
        self.assertEqual(self.read(), PLAN)
        self.assertIsNone(stock_settings.last_change())
        self.assertIn("no change to undo", stock_settings.undo_settings("the dashboard", NOW)[1])

    def test_apply_checks_again(self):
        with self.assertRaises(ValueError):
            stock_settings.apply_settings({"MAX_DAILY_LOSS": 1}, "the dashboard")
        self.assertEqual(self.read(), PLAN)

    def test_two_changes_undo_one_at_a_time(self):
        stock_settings.apply_settings({"TREND_DAYS": 150}, "the dashboard", now=NOW)
        stock_settings.apply_settings({"TREND_DAYS": 250}, "the dashboard", now=NOW)
        stock_settings.undo_settings("the dashboard", NOW)
        self.assertEqual(self.read()["TREND_DAYS"], 150)
        stock_settings.undo_settings("the dashboard", NOW)
        self.assertEqual(self.read()["TREND_DAYS"], 200)
