"""attribution.py: each closed trade's return vs SPY over the same days, grouped."""
from datetime import datetime, timezone

from base import BotTestCase

import attribution as at

SPY = [["2026-10-01", 100.0], ["2026-10-02", 101.0], ["2026-10-05", 102.0], ["2026-10-06", 99.0]]
NOW = datetime(2026, 10, 7, tzinfo=timezone.utc)


def pos(sym, opened, closed, pnl, reason="stop"):
    return {"symbol": sym, "opened": opened, "closed": closed, "exit_pnl": pnl, "exit_reason": reason}


def buy(sym, ts, value=100.0, conf=0.7):
    return {"symbol": sym, "timestamp": ts, "action": "BUY", "value": value, "confidence": conf}


class Attribution(BotTestCase):
    def test_excess_maths(self):
        rows = at.excess_rows([pos("AAA", "2026-10-01T15:00", "2026-10-05T15:00+00:00", 5.0)], SPY,
                              [buy("AAA", "2026-10-01T15:00:00+00:00")])
        r = rows[0]
        self.assertAlmostEqual(r["return_pct"], 5.0)
        self.assertAlmostEqual(r["spy_pct"], 2.0)             # 100 → 102
        self.assertAlmostEqual(r["excess_pct"], 3.0)
        self.assertAlmostEqual(r["excess_usd"], 3.0)          # $5 vs $2 SPY would have made
        self.assertEqual((r["hold"], r["spy_move"], r["confidence"]), ("4–7d", "SPY up", "65–75%"))

    def test_weekend_close_uses_last_trading_day(self):
        rows = at.excess_rows([pos("AAA", "2026-10-03T15:00", "2026-10-04T15:00", 1.0)], SPY,
                              [buy("AAA", "2026-10-03T15:00")])
        self.assertAlmostEqual(rows[0]["spy_pct"], 0.0)       # both map to Oct 2

    def test_grouping_and_too_few_flag(self):
        positions = [pos(f"S{i}", "2026-10-01T15:00", "2026-10-06T15:00", 1.0) for i in range(10)]
        positions.append(pos("LOSS", "2026-10-01T15:00", "2026-10-01T18:00", -3.0, "take_profit"))
        trades = [buy(p["symbol"], "2026-10-01T15:00") for p in positions]
        g = at.group(at.excess_rows(positions, SPY, trades))
        stop = g["exit"]["stop"]
        self.assertEqual(stop["trades"], 10)
        self.assertNotIn("too_few", stop)
        self.assertAlmostEqual(stop["avg_excess_pct"], 2.0)    # +1% vs SPY −1%
        self.assertEqual(stop["beat_spy_pct"], 100)
        self.assertTrue(g["exit"]["take_profit"]["too_few"])
        self.assertEqual(list(g["exit"])[0], "take_profit")    # worst total excess first

    def test_no_decision_row_is_unknown_regime(self):
        decisions = [{"symbol": "AAA", "ts": "2026-10-01T14:00:00+00:00", "regime": "RANGING"},
                     {"symbol": "AAA", "ts": "2026-10-02T14:00:00+00:00", "regime": "VOLATILE"}]
        positions = [pos("AAA", "2026-10-01T15:00", "2026-10-05T15:00", 1.0),
                     pos("BBB", "2026-10-01T15:00", "2026-10-05T15:00", 1.0)]
        trades = [buy("AAA", "2026-10-01T15:00"), buy("BBB", "2026-10-01T15:00")]
        rows = at.excess_rows(positions, SPY, trades, decisions)
        self.assertEqual([r["regime"] for r in rows], ["RANGING", "unknown"])   # latest row BEFORE entry

    def test_untraceable_position_skipped(self):
        self.assertEqual(at.excess_rows([pos("AAA", "2026-10-01T15:00", "2026-10-05T15:00", 1.0)], SPY), [])

    def test_vs_spy_windows_and_overall(self):
        positions = [pos("AAA", "2026-10-01T15:00", "2026-10-05T15:00", 5.0)]
        equity = [["2026-10-01T15:00", 1000.0], ["2026-10-06T15:00", 1010.0]]
        r = at.vs_spy(positions, SPY, [buy("AAA", "2026-10-01T15:00")], equity=equity, now=NOW, weeks=4)
        self.assertEqual(r["all_time"]["trades"], 1)
        self.assertAlmostEqual(r["all_time"]["bot_return_pct"], 1.0)
        self.assertAlmostEqual(r["all_time"]["spy_return_pct"], -1.0)
        self.assertAlmostEqual(r["all_time"]["bot_minus_spy_pct"], 2.0)
        self.assertEqual(r["last_4_weeks"]["trades"], 1)
