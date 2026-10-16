"""allocator: safety switch, momentum ranking, targets and orders (the plan's pure rules)."""
from base import BotTestCase

import allocator as al

S = al.DEFAULTS


def rising(n, start=100.0, step=0.5):
    return [start + step * i for i in range(n)]


class Trend(BotTestCase):
    def test_above_and_below_the_average(self):
        up = al.trend(rising(250))
        self.assertEqual(up["mode"], al.INVEST)
        self.assertGreater(up["gap_pct"], 0)
        down = al.trend(rising(250) + [50.0])
        self.assertEqual(down["mode"], al.SAFETY)
        self.assertLess(down["gap_pct"], 0)

    def test_needs_200_closes(self):
        self.assertIsNone(al.trend(rising(199)))


class Momentum(BotTestCase):
    def test_skips_the_latest_month(self):
        closes = [100.0] * 10 + [100.0 + i for i in range(243)] + [10.0] * 21
        # 252 days back = index -253, 21 days back = index -22
        m = al.momentum(closes)
        self.assertAlmostEqual(m, (closes[-22] / closes[-253] - 1) * 100, places=1)
        self.assertGreater(m, 0)              # the crash in the last 21 days doesn't count

    def test_short_history_is_none(self):
        self.assertIsNone(al.momentum(rising(252)))
        self.assertIsNotNone(al.momentum(rising(253)))


class RankAndPick(BotTestCase):
    def setUp(self):
        super().setUp()
        self.closes = {
            "FAST": rising(300, step=1.0),
            "SLOW": rising(300, step=0.1),
            "FELL": rising(299, step=1.0) + [10.0],      # strong year, now below its average
            "DOWN": [400 - i for i in range(300)],        # negative momentum
            "NEW": rising(100),                           # not enough history
        }

    def test_order_and_eligibility(self):
        r = al.rank(self.closes)
        self.assertEqual(sorted(x["symbol"] for x in r[:2]), ["FAST", "FELL"])
        self.assertNotIn("NEW", [x["symbol"] for x in r])
        by = {x["symbol"]: x for x in r}
        self.assertFalse(by["FELL"]["eligible"])     # below its 200-day average
        self.assertFalse(by["DOWN"]["eligible"])     # momentum ≤ 0
        self.assertTrue(by["SLOW"]["eligible"])

    def test_pick_skips_ineligible_and_vetoed(self):
        r = al.rank(self.closes)
        self.assertEqual(al.pick(r, 4), ["FAST", "SLOW"])
        self.assertEqual(al.pick(r, 4, vetoed={"FAST"}), ["SLOW"])
        self.assertEqual(al.pick(r, 1), ["FAST"])


class Targets(BotTestCase):
    def test_invest_with_all_picks(self):
        t = al.targets(al.INVEST, ["A", "B", "C", "D"])
        self.assertEqual(t, {"SPY": 0.80, "A": 0.05, "B": 0.05, "C": 0.05, "D": 0.05})
        self.assertNotIn("SGOV", t)

    def test_empty_slots_go_to_tbills(self):
        t = al.targets(al.INVEST, ["A"])
        self.assertAlmostEqual(t["SGOV"], 0.15)
        self.assertAlmostEqual(sum(t.values()), 1.0)

    def test_safety_is_all_tbills(self):
        self.assertEqual(al.targets(al.SAFETY, ["A", "B"]), {"SGOV": 1.0})

    def test_extra_picks_ignored(self):
        self.assertEqual(len(al.targets(al.INVEST, list("ABCDEFG"))), 5)


class Orders(BotTestCase):
    def test_first_rebalance_from_old_positions(self):
        held = {"XOM": 100.0, "ONON": 70.0}
        o = al.orders({"SPY": 0.8, "A": 0.05, "SGOV": 0.15}, held, 1000.0, full=True)
        self.assertEqual([(x["side"], x["symbol"]) for x in o],
                         [("sell", "XOM"), ("sell", "ONON"), ("buy", "SPY"), ("buy", "SGOV"), ("buy", "A")])
        self.assertTrue(o[0]["all"])
        self.assertEqual(o[2]["amount"], 800.0)

    def test_small_drift_left_alone_on_ordinary_days(self):
        held = {"SPY": 820.0, "A": 45.0}
        self.assertEqual(al.orders({"SPY": 0.8, "A": 0.05}, held, 1000.0, full=False), [])

    def test_big_drift_fixed_on_ordinary_days(self):
        held = {"SPY": 850.0, "A": 50.0}
        o = al.orders({"SPY": 0.8, "A": 0.05}, held, 1000.0, full=False)
        self.assertEqual(o, [{"symbol": "SPY", "side": "sell", "amount": 50.0, "all": False}])

    def test_full_rebalance_trims_small_drifts(self):
        held = {"SPY": 820.0, "A": 45.0}
        o = al.orders({"SPY": 0.8, "A": 0.05}, held, 1000.0, full=True)
        self.assertEqual([(x["side"], x["symbol"], x["amount"]) for x in o], [("sell", "SPY", 20.0), ("buy", "A", 5.0)])

    def test_tiny_differences_never_trade(self):
        self.assertEqual(al.orders({"SPY": 0.8}, {"SPY": 799.5}, 1000.0, full=True), [])

    def test_symbol_not_held_is_bought_even_on_ordinary_days(self):
        o = al.orders({"SPY": 0.8, "A": 0.05}, {"SPY": 800.0}, 1000.0, full=False)
        self.assertEqual(o, [{"symbol": "A", "side": "buy", "amount": 50.0, "all": False}])


class Rebalance(BotTestCase):
    def test_first_run_of_a_month(self):
        self.assertTrue(al.is_rebalance_day("2026-11-02", "2026-10-19"))
        self.assertFalse(al.is_rebalance_day("2026-10-20", "2026-10-19"))
        self.assertTrue(al.is_rebalance_day("2026-10-20", None))


class Describe(BotTestCase):
    def test_sentences(self):
        self.assertIn("Invest mode", al.describe({"mode": al.INVEST, "gap_pct": 3}, ["NVDA"]))
        self.assertIn("NVDA", al.describe({"mode": al.INVEST, "gap_pct": 3}, ["NVDA"]))
        self.assertIn("T-bills", al.describe({"mode": al.SAFETY, "gap_pct": -2}, []))
        self.assertIn("Waiting", al.describe(None, []))
