"""backtest_core: the live rules on made-up history — fills at the next open, the switch, the stats."""
from datetime import date, timedelta

from base import BotTestCase

import allocator as al
import backtest_core as bc


def days(n, start=date(2006, 1, 2)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


class Simulate(BotTestCase):
    def setUp(self):
        super().setUp()
        self.d = days(700)
        n = len(self.d)
        spy = [100 * 1.0004 ** i for i in range(n)]
        self.closes = {"SPY": spy, "BIL": [50.0] * n,
                       "UP": [20 * 1.001 ** i for i in range(n)], "FLAT": [30.0] * n}
        self.opens = {k: list(v) for k, v in self.closes.items()}
        self.s = dict(al.DEFAULTS, SAFE="BIL", AGGRESSIVE=False)
        self.start = self.d[400]

    def test_rising_market_stays_invested(self):
        r = bc.simulate(self.d, self.opens, self.closes, self.s, start=self.start, universe=["UP", "FLAT"])
        self.assertEqual(r["switches"], 0)
        self.assertEqual(r["picks_log"][0][1:], ["invest", ["UP"]])        # FLAT has no momentum
        self.assertGreater(r["values"][-1], 1000)
        # 80% SPY at +0.04%/day + 5% UP + 14% BIL flat: about +0.04% × 0.8 + 0.1% × 0.05 a day
        self.assertLess(r["values"][-1], 1000 * 1.0004 ** 300)

    def test_crash_moves_to_tbills_at_the_month_start(self):
        n = len(self.d)
        crash_at = 520
        spy = [100 * 1.0004 ** i if i < crash_at else 100 * 1.0004 ** crash_at * 0.995 ** (i - crash_at) for i in range(n)]
        self.closes["SPY"] = self.opens["SPY"] = spy
        r = bc.simulate(self.d, self.opens, self.closes, self.s, start=self.start, universe=["UP"])
        self.assertGreaterEqual(r["switches"], 1)
        modes = [m for _, m, _ in r["picks_log"]]
        self.assertEqual(modes[-1], "safety")
        sw = next(day for day, m, _ in r["picks_log"] if m == "safety")
        k = self.d.index(sw)
        self.assertNotEqual(self.d[k - 1][:7], sw[:7])         # the first trading day of a month
        self.assertGreater(sw, self.d[crash_at])
        # once in T-bills the value stops falling with SPY
        k = r["dates"].index(sw)
        self.assertAlmostEqual(r["values"][-1], r["values"][k + 1], delta=1.0)

    def test_buy_hold(self):
        bh = bc.buy_hold(self.d, self.closes["SPY"], start=self.start)
        self.assertAlmostEqual(bh["values"][0], 1000 * 1.0004, places=1)


class Stats(BotTestCase):
    def test_drawdown_and_years(self):
        d = ["2020-06-30", "2020-12-31", "2021-06-30", "2021-12-31"]
        st = bc.stats(d, [1100.0, 1200.0, 900.0, 1080.0])
        self.assertEqual(st["max_drawdown_pct"], -25.0)
        self.assertEqual(st["yearly"], {"2020": 20.0, "2021": -10.0})
        self.assertEqual(st["worst_year"], ("2021", -10.0))
        self.assertEqual(st["total_pct"], 8.0)

    def test_weekly_keeps_the_last_point(self):
        pts = bc.weekly([str(i) for i in range(12)], list(range(12)))
        self.assertEqual(pts[-1], ["11", 11])
        self.assertEqual(len(pts), 4)


class DailySwitchCheck(BotTestCase):
    """SWITCH_CHECK 'daily': a mid-month return from safety re-picks the stocks, as the live bot does
    (the backtest used to give decide() only SPY's history on such a day, so no stock qualified)."""
    def test_mid_month_return_from_safety_picks_stocks(self):
        d = days(120, start=date(2021, 1, 4))
        start = 50
        first = next(i for i in range(start + 1, len(d)) if d[i][:7] != d[i - 1][:7])   # a month's first day
        spy = [100.0 + i for i in range(len(d))]
        for i in (first + 5, first + 6, first + 7):
            spy[i] = 50.0                                   # three closes far below the 20-day average
        closes = {"SPY": spy, "BIL": [100.0] * len(d), "AAA": [50 + 0.5 * i for i in range(len(d))]}
        opens = {k: list(v) for k, v in closes.items()}
        s = dict(al.DEFAULTS, SAFE="BIL", AGGRESSIVE=False, SWITCH_CHECK="daily", TREND_DAYS=20,
                 MOM_DAYS=40, MOM_SKIP=5, PICKS=1, PICK_PCT=0.2)
        r = bc.simulate(d, opens, closes, s, start=d[start], universe=["AAA"])
        log = {day: (mode, picks) for day, mode, picks in r["picks_log"]}
        self.assertEqual(log[d[first + 6]], ("safety", []))
        self.assertEqual(log[d[first + 9]], ("invest", ["AAA"]))       # was ("invest", []): 20% idle in T-bills
