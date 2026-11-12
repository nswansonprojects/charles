"""market_types: sorting past days into calm rise / fall / rebound / choppy, and scoring a plan in each."""
from base import BotTestCase

import market_types as mt


def line(start, end, n):
    return [start + (end - start) * i / (n - 1) for i in range(n)]


class Falls(BotTestCase):
    def test_a_drop_of_ten_percent_is_a_fall_from_its_high_to_its_low(self):
        closes = line(100, 120, 50) + line(119, 100, 20) + line(101, 130, 60)
        self.assertEqual(mt.falls(closes, 0.10), [(49, 69, True)])

    def test_a_smaller_dip_is_not(self):
        self.assertEqual(mt.falls(line(100, 120, 50) + line(119, 112, 10) + line(113, 130, 30), 0.10), [])

    def test_a_fall_still_running_is_marked(self):
        self.assertEqual(mt.falls(line(100, 120, 50) + line(119, 90, 20), 0.10), [(49, 69, False)])


class Label(BotTestCase):
    def test_the_four_kinds(self):
        zigzag = [130 + (3 if i % 2 else -3) for i in range(100)]
        closes = line(100, 120, 100) + line(119, 100, 20) + line(101, 130, 80) + zigzag
        got = mt.label(closes, drop=0.10, rebound_days=30, window=21, steady=0.10)["labels"]
        self.assertEqual(got[50], mt.CALM)
        self.assertEqual(set(got[100:120]), {mt.FALL})
        self.assertEqual(set(got[120:150]), {mt.REBOUND})
        self.assertEqual(got[170], mt.CALM)                 # the rise after the rebound window
        self.assertEqual(got[260], mt.CHOPPY)
        self.assertEqual(len(got), len(closes))
        self.assertNotIn(None, got)

    def test_it_says_how_many_days_can_still_change(self):
        closes = line(100, 150, 200) + line(149, 140, 10)
        self.assertEqual(mt.label(closes, window=21)["provisional"], 10)       # since the last high
        self.assertEqual(mt.label(line(100, 150, 200), window=21)["provisional"], 10)   # half a window

    def test_empty(self):
        self.assertEqual(mt.label([]), {"labels": [], "provisional": 0})


class Score(BotTestCase):
    def test_returns_are_counted_in_the_kind_of_the_day_they_happened(self):
        values = [100, 110, 99, 108.9]
        labels = [None, mt.CALM, mt.FALL, mt.CALM]
        s = mt.score(values, labels)
        self.assertEqual(s[mt.CALM]["days"], 2)
        self.assertEqual(s[mt.CALM]["total_pct"], 21.0)
        self.assertEqual(s[mt.FALL]["total_pct"], -10.0)
        self.assertEqual(s[mt.FALL]["worst_day_pct"], -10.0)
        self.assertEqual(s[mt.REBOUND]["days"], 0)
        self.assertIsNone(s[mt.REBOUND]["yearly_pct"])
        self.assertAlmostEqual(sum(s[k]["share_pct"] for k in mt.KINDS), 100.0, places=0)

    def test_a_month_is_a_twelfth_of_a_year_of_days(self):
        s = mt.score([100] + [100 * 1.001 ** i for i in range(1, 43)], [None] + [mt.CALM] * 42, year=252)
        self.assertAlmostEqual(s[mt.CALM]["monthly_pct"], (1.001 ** 21 - 1) * 100, places=2)
        self.assertIsNone(s[mt.FALL]["monthly_pct"])

    def test_weakest_is_where_the_plan_trails_the_market_most(self):
        row = lambda m, days=100: {"days": days, "monthly_pct": m}
        plan = {mt.CALM: row(3), mt.FALL: row(-5), mt.REBOUND: row(1), mt.CHOPPY: row(1)}
        market = {mt.CALM: row(2), mt.FALL: row(-9), mt.REBOUND: row(8), mt.CHOPPY: row(1.5)}
        self.assertEqual(mt.weakest(plan, market), mt.REBOUND)
        self.assertEqual(mt.weakest({**plan, mt.REBOUND: row(1, days=10)}, market), mt.CHOPPY)      # too few days to judge
        self.assertIsNone(mt.weakest(plan, plan))
