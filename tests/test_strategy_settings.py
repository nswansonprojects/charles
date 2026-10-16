"""strategy_settings: strategy.json is clamped to hard limits, and the picks always fit next to the core."""
import json
import os

from base import BotTestCase

import strategy_settings as ss

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Settings(BotTestCase):
    def write(self, settings):
        p = os.path.join(self.tmp, "strategy.json")
        with open(p, "w") as f:
            json.dump({"settings": settings}, f)
        return p

    def test_clamped_and_unknown_ignored(self):
        got = ss.load(self.write({"CORE_PCT": 2, "PICKS": -1, "STOP_LOSS_PCT": 0.05}))
        self.assertEqual(got, {"CORE_PCT": 0.95, "PICKS": 0})

    def test_bool_and_choice(self):
        got = ss.load(self.write({"SAFETY_SWITCH": "yes", "SWITCH_CHECK": "hourly"}))
        self.assertEqual(got, {})
        got = ss.load(self.write({"SAFETY_SWITCH": False, "SWITCH_CHECK": "daily"}))
        self.assertEqual(got, {"SAFETY_SWITCH": False, "SWITCH_CHECK": "daily"})

    def test_picks_must_fit(self):
        got = ss.load(self.write({"CORE_PCT": 0.9, "PICKS": 4, "PICK_PCT": 0.05}))
        self.assertAlmostEqual(got["PICK_PCT"], 0.025)

    def test_the_real_file_is_valid(self):
        with open(os.path.join(ROOT, "strategy.json")) as f:
            raw = json.load(f)["settings"]
        self.assertEqual(ss.load(os.path.join(ROOT, "strategy.json")), raw)
