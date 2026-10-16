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
