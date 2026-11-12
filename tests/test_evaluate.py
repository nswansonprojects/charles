"""evaluate: one-step variants, keeping the account invested, and the 'better everywhere' bar."""
from base import BotTestCase

import allocator as al
import evaluate as ev


def res(first, second, dd):
    return {"first": {"cagr_pct": first}, "second": {"cagr_pct": second}, "all": {"max_drawdown_pct": dd}}


class Evaluate(BotTestCase):
    def test_variants_are_one_step_and_in_bounds(self):
        v = ev.variants(dict(al.DEFAULTS))
        self.assertIn(("PICKS", 4, 5), v)
        self.assertIn(("CORE_PCT", 0.8, 0.75), v)
        self.assertIn(("SAFETY_SWITCH", True, False), v)
        self.assertIn(("SWITCH_CHECK", "monthly", "daily"), v)
        self.assertIn(("AGGRESSIVE", True, False), v)
        self.assertIn(("AGG_VOL_MAX", 0.2, 0.23), v)
        self.assertNotIn(("CORE_PCT", 0.9, 0.95), ev.variants(dict(al.DEFAULTS, CORE_PCT=0.9)))

    def test_fit_keeps_the_account_invested(self):
        s = ev.fit(dict(al.DEFAULTS, CORE_PCT=0.75, PICKS=5))
        self.assertAlmostEqual(s["CORE_PCT"] + s["PICKS"] * s["PICK_PCT"], 1.0, places=3)

    def test_passes_only_when_better_everywhere(self):
        base = {"stocks": res(10, 12, -30), "sectors": res(6, 8, -25)}
        ok, why = ev.judge(base, {"stocks": res(11, 13, -31), "sectors": res(7, 9, -26)})
        self.assertTrue(ok, why)
        ok, why = ev.judge(base, {"stocks": res(11, 13, -31), "sectors": res(6.2, 9, -26)})
        self.assertFalse(ok)
        self.assertIn("sectors 2007-16", why)
        ok, why = ev.judge(base, {"stocks": res(12, 14, -40), "sectors": res(7, 9, -26)})
        self.assertFalse(ok)
        self.assertIn("worst drop", why)


class ByKind(BotTestCase):
    """The plan scored by kind of market, and the rule that a change mustn't make its weakest kind worse."""
    # A market that rises in a line, falls 20% and comes back; a plan that keeps half of every day's move.
    market = [100 + i for i in range(100)] + [199 - 2 * i for i in range(1, 21)] + [159 + i for i in range(1, 101)]
    cfg = {"drop": 0.10, "rebound_days": 30, "window": 21, "steady": 0.10}

    def plan(self, share):
        v = [1000.0]
        for a, b in zip(self.market, self.market[1:]):
            v.append(v[-1] * (1 + share * (b / a - 1)))
        return v

    def days(self):
        return [f"d{i:03d}" for i in range(len(self.market))]

    def test_scores_the_plan_beside_the_market_and_names_the_weakest_kind(self):
        k = ev.by_kind(self.days(), [self.plan(0.5)], self.market, self.cfg)
        self.assertLess(k["kinds"]["fall"]["market_pct"], k["kinds"]["fall"]["plan_pct"])        # half the fall
        self.assertLess(k["kinds"]["rebound"]["plan_pct"], k["kinds"]["rebound"]["market_pct"])  # and half the rebound
        self.assertIn(k["weak"], ("rebound", "calm"))
        self.assertEqual((k["from"], k["to"]), ("d000", "d219"))
        self.assertAlmostEqual(sum(r["share_pct"] for r in k["kinds"].values()), 100, delta=1)

    def test_several_runs_are_averaged(self):
        one = ev.by_kind(self.days(), [self.plan(0.5)], self.market, self.cfg)["kinds"]["fall"]["plan_pct"]
        two = ev.by_kind(self.days(), [self.plan(0.5), self.plan(0.5)[3:]], self.market, self.cfg)["kinds"]["fall"]["plan_pct"]
        self.assertAlmostEqual(one, two, places=1)

    def test_weak_gain_is_the_change_in_the_weakest_kind(self):
        base = ev.by_kind(self.days(), [self.plan(0.5)], self.market, self.cfg)
        self.assertEqual(ev.weak_gain(base, [self.plan(0.5)], self.market, self.cfg), 0.0)
        self.assertGreater(ev.weak_gain(base, [self.plan(0.8)], self.market, self.cfg), 0)
        self.assertLess(ev.weak_gain(base, [self.plan(0.2)], self.market, self.cfg), 0)
        self.assertIsNone(ev.weak_gain({**base, "weak": None}, [self.plan(0.2)], self.market, self.cfg))

    def test_a_passing_change_that_hurts_the_weakest_kind_is_not_proposed(self):
        rows = [{"label": "a", "passes": True, "why": "Better.", "weak_gain": -2.0, "g": 3},
                {"label": "b", "passes": True, "why": "Better.", "weak_gain": 0.1, "g": 1},
                {"label": "c", "passes": False, "why": "No.", "weak_gain": 5.0, "g": 9},
                {"label": "d", "passes": True, "why": "Better.", "weak_gain": -0.05, "g": 2}]
        self.assertEqual(ev.pick(rows, lambda r: r["g"])["label"], "d")          # within the slack, and the best of the rest
        self.assertIn("isn't proposed", rows[0]["why"])
        self.assertEqual(rows[1]["why"], "Better.")
        self.assertIsNone(ev.pick([rows[0], rows[2]], lambda r: r["g"]))
        self.assertEqual(ev.pick([{"label": "e", "passes": True, "why": "", "g": 1}], lambda r: r["g"])["label"], "e")   # no score yet

    def test_the_real_book_by_kind_counts_from_the_last_close_before_it_started(self):
        account = {"2026-01-01": 1000, "2026-01-02": 1000, "2026-01-05": 990, "2026-01-06": 1010, "2026-01-07": 1000}
        market = {"2026-01-01": 50, "2026-01-02": 100, "2026-01-05": 98, "2026-01-06": 99, "2026-01-07": 99, "2026-01-08": 120}
        kind_of = {"2026-01-02": "calm", "2026-01-05": "fall", "2026-01-06": "choppy", "2026-01-07": "choppy"}
        got = ev.live_by_kind(account, market, kind_of, "2026-01-05T10:00:00")
        self.assertEqual(got["fall"], {"days": 1, "plan_pct": -1.0, "market_pct": -2.0})
        self.assertEqual(got["choppy"]["days"], 2)
        self.assertEqual(got["choppy"]["plan_pct"], 1.0)
        self.assertNotIn("calm", got)
        self.assertEqual(ev.live_by_kind({"2026-01-05": 1}, market, kind_of, "2026-01-05"), {})
