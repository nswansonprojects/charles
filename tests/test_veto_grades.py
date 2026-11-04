"""veto_grades: a vetoed stock's next month vs the picks the AI let through that day."""
from base import BotTestCase

import veto_grades as vg


def row(day, sym, veto):
    return {"ts": f"{day}T14:00:12+00:00", "symbol": sym, "veto": veto, "reason": "r", "by": "ai"}


def closes(start, step, n=30, first_day=1):
    """n daily closes from 2026-12-01 on, growing by `step` each day."""
    return [(f"2026-12-{first_day + i:02d}" if first_day + i <= 31 else f"2027-01-{first_day + i - 31:02d}",
             start + step * i) for i in range(n)]


class VetoGrades(BotTestCase):
    def test_no_vetoes_is_empty(self):
        rows = [row("2026-12-01", "AAA", False), row("2026-12-01", "BBB", False)]
        self.assertEqual(vg.symbols_needed(rows), [])
        g = vg.grade(rows, {})
        self.assertEqual((g["vetoes"], g["graded"], g["complete"], g["helped"], g["grades"]), (0, 0, 0, 0, []))
        self.assertIsNone(g["avg_vs_picks_pts"])

    def test_veto_that_helped(self):
        rows = [row("2026-12-01", "BAD", True), row("2026-12-01", "AAA", False),
                row("2026-12-01", "BBB", False), row("2026-11-02", "OLD", False)]
        self.assertEqual(vg.symbols_needed(rows), ["AAA", "BAD", "BBB"])
        px = {"BAD": closes(100, -0.5), "AAA": closes(100, 0.5), "BBB": closes(100, 0.0)}
        g = vg.grade(rows, px)
        one = g["grades"][0]
        self.assertEqual(one["picks"], ["AAA", "BBB"])
        self.assertEqual(one["days"], 21)
        self.assertTrue(one["complete"])
        self.assertEqual(one["stock_pct"], -10.5)        # 100 -> 89.5 over 21 days
        self.assertEqual(one["picks_avg_pct"], 5.25)     # (10.5 + 0) / 2
        self.assertEqual(one["vs_picks_pts"], -15.75)
        self.assertTrue(one["helped"])
        self.assertEqual((g["vetoes"], g["complete"], g["helped"], g["avg_vs_picks_pts"]), (1, 1, 1, -15.75))

    def test_month_not_over_is_partial(self):
        rows = [row("2026-12-01", "BAD", True), row("2026-12-01", "AAA", False)]
        px = {"BAD": closes(100, 1.0, n=6), "AAA": closes(100, 0.0, n=6)}
        one = vg.grade(rows, px)["grades"][0]
        self.assertEqual(one["days"], 5)
        self.assertFalse(one["complete"])
        self.assertFalse(one["helped"])                  # it rose while the pick stayed flat: cost so far
        g = vg.grade(rows, px)
        self.assertEqual((g["graded"], g["complete"], g["avg_vs_picks_pts"]), (1, 0, None))

    def test_no_prices_yet(self):
        rows = [row("2026-12-01", "BAD", True), row("2026-12-01", "AAA", False)]
        one = vg.grade(rows, {"BAD": [("2026-12-01", 100.0)]})["grades"][0]
        self.assertEqual(one["note"], "no prices yet")
        self.assertNotIn("vs_picks_pts", one)

    def test_veto_on_a_day_without_a_close_uses_the_last_close_before(self):
        self.assertEqual(vg.forward_return([("2026-12-04", 100.0), ("2026-12-07", 110.0)], "2026-12-05"), (10.0, 1))
        self.assertEqual(vg.forward_return([("2026-12-04", 100.0), ("2026-12-07", 110.0)], "2026-12-04"), (10.0, 1))
        self.assertIsNone(vg.forward_return([("2026-12-07", 110.0)], "2026-12-05"))

    def test_read_rows_skips_bad_lines(self):
        rows = vg.read_rows(['{"ts": "2026-12-01T14:00:00+00:00", "symbol": "A", "veto": true}', "not json",
                             "[]", '{"symbol": "B"}', ""])
        self.assertEqual([r["symbol"] for r in rows], ["A"])
