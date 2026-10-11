"""promotion.py: the task 8 evidence rules per book."""
from datetime import datetime, timezone

from base import BotTestCase

import promotion as pr

NOW = datetime(2027, 1, 15, tzinfo=timezone.utc)
SPY = [["2026-10-01", 100.0], ["2026-11-01", 95.0], ["2027-01-14", 102.0]]   # +2%, worst drop −5%


def book(ret, closed, pnl, dd, since="2026-10-01"):
    return {"return_pct": ret, "closed": closed, "realized_pnl": pnl, "max_drawdown_pct": dd, "since": since}


class Promotion(BotTestCase):
    def test_intraday_book_needs_every_rule(self):
        base_trades = [{"pnl": 2.0}, {"pnl": -2.0}]                   # average trade $2
        summary = {"baseline": book(1.0, 40, 10.0, -6.0), "good": book(3.0, 30, 15.0, -4.0),
                   "few": book(5.0, 10, 30.0, -1.0), "thin_edge": book(1.5, 40, 11.0, -4.0),
                   "deep": book(3.0, 30, 15.0, -9.0)}
        r = pr.review(summary, SPY, base_trades, NOW)
        self.assertEqual(r["ready"], ["good"])
        self.assertEqual(r["promote_first"], "good")
        self.assertIn("30 closed trades (10 so far)", r["books"]["few"]["why"])
        self.assertIn("more than one average trade", r["books"]["thin_edge"]["why"])   # +$1 < $2
        self.assertIn("worst drop", r["books"]["deep"]["why"])                         # −9 vs −6 / −5
        self.assertEqual(r["books"]["baseline"]["why"], "reference book")
        self.assertAlmostEqual(r["books"]["few"]["progress"], 0.33)

    def test_daily_book_needs_three_months_and_beat_spy(self):
        summary = {"baseline": book(0, 0, 0, 0), "trend_following": book(3.0, 2, 30.0, -4.0),
                   "dual_momentum": book(1.0, 1, 10.0, -1.0),
                   "crypto_trend": book(9.0, 1, 90.0, -2.0, since="2026-12-01")}
        r = pr.review(summary, SPY, [], NOW)
        self.assertTrue(r["books"]["trend_following"]["ready"])          # +3% > SPY +2%, −4% vs −5%
        self.assertIn("return above SPY", r["books"]["dual_momentum"]["why"])
        self.assertIn("91 days of data", r["books"]["crypto_trend"]["why"])
        self.assertTrue(r["review_open"])
