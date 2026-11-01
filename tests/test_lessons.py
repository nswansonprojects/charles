"""lessons.py: short lessons shown when the thing they explain happens. Education, never advice."""
import re

from base import BotTestCase

import lessons


SETTINGS = {"AGG_CORE": "SSO", "CORE": "SPY", "SAFE": "SGOV", "TREND_DAYS": 200, "PICKS": 4, "CORE_PCT": 0.8}
BACKTEST = {"start": "2007-07-02", "end": "2026-10-15", "slippage_bps": 5.0, "runs": {
    "charles": {"stats": {"cagr_pct": 18.89, "max_drawdown_pct": -38.9}},
    "charles_no_switch": {"stats": {"cagr_pct": 22.98, "max_drawdown_pct": -56.5}},
    "spy": {"stats": {"cagr_pct": 10.85, "max_drawdown_pct": -55.2}}, "charles_steady": {"curve": []}}}


def state(mode="invest", roles=(("SPY", "core"), ("MU", "pick")), halted=False, day_change=0.0, total=1000.0,
          vetoes=(), last_rebalance="2026-10-01", today="2026-10-20", trades=(), **settings):
    return {"generated_at": f"{today}T15:00:00+00:00", "today": {"halted": halted, "limit": 20.0},
            "account": {"total_value": total, "day_change": day_change},
            "plan": {"mode": mode, "vetoes": list(vetoes), "last_rebalance": last_rebalance,
                     "settings": {**SETTINGS, **settings}},
            "positions": [{"symbol": s, "role": r} for s, r in roles], "trades": list(trades)}


def lesson(lid, s, numbers=None):
    return next(x for x in lessons.due(s, numbers) if x["id"] == lid)


class Due(BotTestCase):
    def ids(self, s):
        return [x["id"] for x in lessons.due(s)]

    def test_a_quiet_day_starts_with_paper_money_then_what_it_holds(self):
        self.assertEqual(self.ids(state())[:3], ["paper", "index-fund", "momentum"])

    def test_aggressive_mode_explains_the_2x_fund_early(self):
        ids = self.ids(state(mode="aggressive", roles=(("SSO", "core"), ("MU", "pick"))))
        self.assertEqual(ids[:2], ["paper", "aggressive"])

    def test_events_come_first(self):
        self.assertEqual(self.ids(state(halted=True))[0], "loss-limit")
        self.assertEqual(self.ids(state(mode="safety", roles=(("SGOV", "safety"),)))[0], "safety")
        self.assertEqual(self.ids(state(day_change=-30.0, total=1000.0))[0], "drawdown")          # −2.9% today
        self.assertEqual(self.ids(state(vetoes=[{"symbol": "XYZ", "reason": "probe", "rank": 3}]))[0], "veto")
        self.assertEqual(self.ids(state(last_rebalance="2026-10-19"))[0], "repick")              # yesterday

    def test_every_lesson_once_the_ones_that_fit_first(self):
        d = lessons.due(state())
        self.assertEqual(sorted(x["id"] for x in d), sorted(x["id"] for x in lessons.LESSONS))
        fits = [x["why"] is not None for x in d]
        self.assertEqual(fits, sorted(fits, reverse=True))           # all the "why now" ones come first
        self.assertIsNone(next(x for x in d if x["id"] == "safety")["why"])

    def test_why_now_names_the_reason(self):
        self.assertIn("$20", lesson("loss-limit", state(halted=True))["why"])
        self.assertIn("MU", lesson("momentum", state())["why"])
        trades = [{"timestamp": "2026-10-19T14:00:35+00:00"}, {"timestamp": "2026-10-19T14:00:34+00:00"},
                  {"timestamp": "2026-09-01T14:00:00+00:00"}]
        self.assertEqual(lesson("costs", state(trades=trades))["why"], "Charles placed 2 orders on Oct 19.")
        self.assertIsNone(lesson("costs", state(trades=trades[2:]))["why"])                   # an old trade

    def test_numbers_come_from_the_backtest_and_settings(self):
        n = lessons.backtest_numbers(BACKTEST)
        self.assertEqual((n["years"], n["plan_dd"], n["nosw_dd"], n["nosw_cagr"], n["slip"]), (19, 39, 57, 23, "0.05%"))
        self.assertNotIn("steady_cagr", n)                                                   # that run has no stats
        self.assertIn("from 57% to 39%", lesson("safety", state(), n)["body"])
        self.assertNotIn("backtest", lesson("safety", state(), {})["body"])                   # no file: no numbers
        self.assertNotIn("backtest", lesson("aggressive", state(), n)["body"])                # a missing run, the same
        self.assertEqual(lessons.backtest_numbers(None), {})
        self.assertIn("ended a month below its 220-day", lesson("safety", state(TREND_DAYS=220))["body"])
        self.assertIn("closed a day below", lesson("safety", state(SWITCH_CHECK="daily"))["body"])
        self.assertIn("In Charles's plan, picks must be above their own line, and if SPY ends a month",
                      lesson("trend-line", state(AGGRESSIVE=False))["body"])
        self.assertIn("UPRO is down about 9%", lesson("aggressive", state(AGG_CORE="UPRO"))["body"])

    def test_every_lesson_is_short_plain_and_not_advice(self):
        self.assertEqual(len({x["id"] for x in lessons.LESSONS}), len(lessons.LESSONS))
        for x in lessons.due(state(), lessons.backtest_numbers(BACKTEST)):
            with self.subTest(lesson=x["id"]):
                self.assertTrue(x["title"] and x["body"])
                self.assertNotIn("{", x["title"] + x["body"])                                  # every number filled
                self.assertLess(len(x["body"]), 900)
                self.assertIsNone(re.search(r"you should|should buy|buy now|guarantee|can't lose|risk-free", x["body"], re.I))


class HoldingWhy(BotTestCase):
    PLAN = {"settings": SETTINGS, "next_rebalance": "2026-11-02", "as_of": "2026-10-28", "universe_n": 99,
            "trend": {"gap_pct": 6.49}, "ranked": [{"symbol": "MU", "momentum": 384.38, "above_trend": True, "rank": 2},
                                                   {"symbol": "DELL", "momentum": 236.0, "above_trend": False, "rank": 5}]}

    def why(self, sym, role):
        return lessons.holding_why({"symbol": sym, "role": role}, self.PLAN)

    def test_a_pick_says_its_rank_gain_and_next_check(self):
        self.assertEqual(self.why("MU", "pick"), "Momentum pick #2 of 99: up 384% over the past year (not counting the "
                         "latest month) and above its 200-day average, as of Oct 28. Charles checks it again on Nov 2: "
                         "it stays if it's still in the top 4.")
        self.assertIn("now below its 200-day average", self.why("DELL", "pick"))
        self.assertIn("and back above that line", self.why("DELL", "pick"))
        self.assertEqual(self.why("XYZ", "pick"), "One of this month's 4 momentum picks. Charles checks it again on Nov 2.")

    def test_the_funds(self):
        self.assertIn("SPY is 6.5% above its 200-day average now", self.why("SPY", "core"))
        self.assertIn("twice as much as the S&P 500", self.why("SSO", "core"))
        self.assertIn("waiting room", self.why("SGOV", "safety"))
        self.assertIsNone(self.why("ABC", "mystery"))
