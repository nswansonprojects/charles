"""plan_vs_spy: Charles and SPY measured at the same close, never Charles's after-hours marks."""
from base import BotTestCase

import plan_vs_spy as pv


def pt(ts, value):
    return {"ts": ts, "value": value}


# Plan starts Monday 2026-10-19; base = Friday 10-16's last session point (15:30 ET = 19:30 UTC, EDT).
CURVE = [
    pt("2026-10-16T14:00:00+00:00", 990.0),
    pt("2026-10-16T19:30:00+00:00", 1000.0),
    pt("2026-10-16T21:31:00+00:00", 940.0),    # 17:31 ET after-hours mark: ignored
    pt("2026-10-19T04:00:00+00:00", 930.0),    # midnight rollover: ignored
    pt("2026-10-19T19:30:00+00:00", 1010.0),
    pt("2026-10-23T19:00:00+00:00", 1018.0),
    pt("2026-10-23T19:30:00+00:00", 1020.0),   # Friday's close point
    pt("2026-10-24T04:19:00+00:00", 990.0),    # Saturday 00:19 ET: ignored
]
SPY = [("2026-10-15", 99.0), ("2026-10-16", 100.0), ("2026-10-19", 101.0), ("2026-10-23", 103.0)]


class PlanVsSpy(BotTestCase):
    def test_both_sides_at_the_same_close(self):
        r = pv.since_started(CURVE, "2026-10-19", SPY)
        self.assertEqual(r, {"charles_pct": 2.0, "spy_pct": 3.0, "as_of_close": "2026-10-23", "base_close": "2026-10-16"})

    def test_after_hours_and_weekend_points_are_ignored(self):
        self.assertEqual(pv.close_point(CURVE, "2026-10-16")["value"], 1000.0)
        self.assertEqual(pv.close_point(CURVE, "2026-10-23")["value"], 1020.0)
        self.assertIsNone(pv.close_point(CURVE, "2026-10-24"))

    def test_latest_close_both_have(self):
        # SPY has a newer close than the curve (bot down that day): both sides use 10-23.
        r = pv.since_started(CURVE, "2026-10-19", SPY + [("2026-10-26", 104.0)])
        self.assertEqual((r["as_of_close"], r["spy_pct"]), ("2026-10-23", 3.0))

    def test_until_caps_the_end(self):
        r = pv.since_started(CURVE, "2026-10-19", SPY, until="2026-10-20")
        self.assertEqual((r["as_of_close"], r["charles_pct"], r["spy_pct"]), ("2026-10-19", 1.0, 1.0))

    def test_dst_change_uses_et_session_hours(self):
        # After DST ends (2026-11-01) 15:30 ET is 20:30 UTC, and 19:30 UTC is 14:30 ET.
        curve = [pt("2026-11-02T19:30:00+00:00", 1000.0), pt("2026-11-02T20:30:00+00:00", 1005.0),
                 pt("2026-11-02T21:30:00+00:00", 960.0), pt("2026-11-03T01:00:00+00:00", 950.0)]
        self.assertEqual(pv.close_point(curve, "2026-11-02")["value"], 1005.0)
        self.assertTrue(pv.in_session("2026-11-02T14:30:00+00:00"))       # 9:30 ET
        self.assertFalse(pv.in_session("2026-11-02T13:30:00+00:00"))      # 8:30 ET

    def test_empty_or_short_inputs(self):
        self.assertIsNone(pv.since_started([], "2026-10-19", SPY))
        self.assertIsNone(pv.since_started(CURVE, "", SPY))
        self.assertIsNone(pv.since_started(CURVE, "2026-10-19", []))
        self.assertIsNone(pv.since_started(CURVE[4:], "2026-10-19", SPY))   # no close before the start
