"""Position sizing: confidence scaling, drawdown multiplier, strategy.json limits."""
import json
import os

from base import BotTestCase, config

import market_filter as mf
import risk_manager as rm
import strategy_settings as ss


class PositionSize(BotTestCase):
    def test_min_size_at_or_below_min_confidence(self):
        self.assertEqual(mf.position_size_pct(0.60), 0.10)
        self.assertEqual(mf.position_size_pct(0.40), 0.10)

    def test_max_size_at_full_confidence(self):
        self.assertEqual(mf.position_size_pct(1.0), 0.15)
        self.assertEqual(mf.position_size_pct(1.2), 0.15)

    def test_linear_in_between(self):
        self.assertAlmostEqual(mf.position_size_pct(0.80), 0.125, places=4)

    def test_follows_config_changes(self):
        config.MIN_POSITION_SIZE_PCT, config.MAX_POSITION_SIZE_PCT = 0.05, 0.20
        self.assertAlmostEqual(mf.position_size_pct(0.80), 0.125, places=4)
        self.assertEqual(mf.position_size_pct(0.99) <= 0.20, True)


class DrawdownMultiplier(BotTestCase):
    def test_thresholds(self):
        cases = [(1000, 1.0), (950, 1.0), (949, 0.8), (900, 0.8), (899, 0.6),
                 (850, 0.6), (849, 0.4), (800, 0.4), (799, 0.2), (500, 0.2)]
        for value, mult in cases:
            with self.subTest(value=value):
                self.assertEqual(rm.drawdown_size_multiplier(value, peak_value=1000), mult)

    def test_new_high_is_no_drawdown(self):
        self.assertEqual(rm.drawdown_size_multiplier(1100, peak_value=1000), 1.0)


class StrategySettings(BotTestCase):
    def write(self, settings):
        path = os.path.join(self.tmp, "strategy.json")
        with open(path, "w") as f:
            json.dump({"settings": settings}, f)
        self.patch(ss, "PATH", path)

    def test_values_are_applied(self):
        self.write({"MAX_OPEN_POSITIONS": 5, "STOP_LOSS_PCT": 0.04})
        ss.apply()
        self.assertEqual((config.MAX_OPEN_POSITIONS, config.STOP_LOSS_PCT), (5, 0.04))

    def test_out_of_range_values_are_clamped(self):
        self.write({"MAX_POSITION_SIZE_PCT": 0.9, "MAX_OPEN_POSITIONS": 50, "STOP_LOSS_PCT": 0.001})
        got = ss.load()
        self.assertEqual(got["MAX_POSITION_SIZE_PCT"], 0.20)
        self.assertEqual(got["MAX_OPEN_POSITIONS"], 6)
        self.assertEqual(got["STOP_LOSS_PCT"], 0.03)

    def test_daily_loss_and_unknown_keys_ignored(self):
        self.write({"MAX_DAILY_LOSS": 500, "SOMETHING": 1})
        ss.apply()
        self.assertEqual(config.MAX_DAILY_LOSS, 20.0)

    def test_max_never_below_min(self):
        self.write({"MIN_POSITION_SIZE_PCT": 0.15, "MAX_POSITION_SIZE_PCT": 0.08})
        got = ss.load()
        self.assertGreaterEqual(got["MAX_POSITION_SIZE_PCT"], got["MIN_POSITION_SIZE_PCT"])

    def test_confidence_floor_raises_min_confidence(self):
        self.write({"MIN_CONFIDENCE_FLOOR": 0.70})
        ss.apply()
        self.assertEqual(config.MIN_CONFIDENCE, 0.70)

    def test_broken_or_missing_file_changes_nothing(self):
        path = os.path.join(self.tmp, "strategy.json")
        with open(path, "w") as f:
            f.write("{broken")
        self.patch(ss, "PATH", path)
        self.assertEqual(ss.apply(), {})
        self.patch(ss, "PATH", os.path.join(self.tmp, "missing.json"))
        self.assertEqual(ss.apply(), {})
        self.assertEqual(config.MAX_OPEN_POSITIONS, 4)

    def test_repo_strategy_json_is_valid_and_within_limits(self):
        with open(os.path.join(os.path.dirname(ss.__file__), "strategy.json")) as f:
            raw = json.load(f)["settings"]
        for name, value in raw.items():
            with self.subTest(name=name):
                self.assertIn(name, ss.LIMITS)
                lo, hi, _, _ = ss.LIMITS[name]
                self.assertTrue(lo <= value <= hi, f"{name}={value} outside {lo}..{hi}")
