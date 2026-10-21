"""reports: report windows (scheduled = last complete period) and SPY's inclusive end date."""
import sys
import types
from datetime import datetime, timezone

from base import BotTestCase

import reports as rpt

UTC = timezone.utc


class Windows(BotTestCase):
    def test_scheduled_monthly_covers_last_month(self):
        # The job runs on the 1st at 7 PM ET (23:00 UTC)
        s, e, label, fname = rpt.report_window("monthly", datetime(2026, 10, 1, 23, tzinfo=UTC), True)
        self.assertEqual(s, datetime(2026, 9, 1, tzinfo=UTC))
        self.assertEqual(e, datetime(2026, 9, 30, 23, 59, 59, tzinfo=UTC))
        self.assertEqual((label, fname), ("September 2026", "monthly_2026_09"))

    def test_scheduled_monthly_in_january_is_december(self):
        s, e, label, _ = rpt.report_window("monthly", datetime(2027, 1, 2, 0, tzinfo=UTC), True)
        self.assertEqual((s.year, s.month, e.day, label), (2026, 12, 31, "December 2026"))

    def test_scheduled_yearly_covers_last_year(self):
        # Jan 1, 8 PM ET is already Jan 2 in UTC
        s, e, label, fname = rpt.report_window("yearly", datetime(2027, 1, 2, 1, tzinfo=UTC), True)
        self.assertEqual((s, e.date().isoformat(), label, fname),
                         (datetime(2026, 1, 1, tzinfo=UTC), "2026-12-31", "2026", "yearly_2026"))

    def test_on_demand_monthly_is_month_to_date(self):
        now = datetime(2026, 10, 15, 12, tzinfo=UTC)
        s, e, label, _ = rpt.report_window("monthly", now)
        self.assertEqual((s, e, label), (datetime(2026, 10, 1, tzinfo=UTC), now, "October 2026"))


class PlanLines(BotTestCase):
    """The monthly report's plan section: plan vs SPY since the start, picks, vetoes, mode changes."""
    OCT = (datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 31, 23, 59, 59, tzinfo=UTC))

    def write(self, name, obj):
        import json, os
        with open(os.path.join(self.tmp, name), "w") as f:
            if name.endswith(".jsonl"):
                f.write("".join(json.dumps(r) + "\n" for r in obj) + "not json\n")
            else:
                json.dump(obj, f)

    def lines(self, spy=lambda s, e: 2.0):
        return rpt.plan_lines(*self.OCT, data_dir=self.tmp, spy=spy)

    def test_nothing_before_the_plan_starts(self):
        self.assertEqual(self.lines(), [])
        self.write("plan.json", {"mode": "invest"})
        self.assertEqual(self.lines(), [])

    def test_full_month(self):
        self.write("plan.json", {"started": "2026-10-19", "mode": "invest", "picks": ["NVDA", "AAPL"],
                                 "switches": [{"date": "2026-09-30", "to": "safety"},
                                              {"date": "2026-10-20", "to": "aggressive"},
                                              {"date": "2026-10-27", "to": "invest"}]})
        self.write("equity_curve.json", [{"ts": "2026-10-16T20:00:00", "value": 1000.0},
                                         {"ts": "2026-10-30T20:00:00", "value": 1030.0},
                                         {"ts": "2026-11-02T20:00:00", "value": 9999.0}])
        self.write("vetoes.jsonl", [{"ts": "2026-10-19T14:00:00", "symbol": "XYZ", "veto": True, "reason": "SEC probe."},
                                    {"ts": "2026-10-19T14:00:00", "symbol": "NVDA", "veto": False, "reason": "Fine."},
                                    {"ts": "2026-09-01T14:00:00", "symbol": "OLD", "veto": True, "reason": "Old."}])
        spy_calls = []
        got = self.lines(lambda s, e: spy_calls.append((s, e)) or 2.0)
        self.assertEqual(got, [
            "Plan since Oct 19, 2026: +3.00% vs SPY +2.00% ✅",
            "Picks now:    NVDA, AAPL",
            "AI veto:      XYZ — SEC probe.",
            "Mode changes: Oct 20 → aggressive (SSO), Oct 27 → invest",
        ])
        self.assertEqual(spy_calls, [("2026-10-19", "2026-10-31")])

    def test_quiet_month_and_no_history(self):
        self.write("plan.json", {"started": "2026-10-30", "mode": "safety", "picks": []})
        self.write("equity_curve.json", [{"ts": "2026-10-30T20:00:00", "value": 1000.0}])
        self.assertEqual(self.lines(), [
            "Plan since Oct 30, 2026: not enough history yet",
            "Picks now:    none (all in the core or T-bills)",
            "AI veto:      none this month",
            "Mode changes: none this month (now safety (T-bills))",
        ])

    def test_behind_spy_and_many_switches(self):
        self.write("plan.json", {"started": "2026-10-01", "mode": "invest", "picks": ["NVDA"],
                                 "switches": [{"date": f"2026-10-{d:02d}", "to": "invest"} for d in range(2, 8)]})
        self.write("equity_curve.json", [{"ts": "2026-10-01T20:00:00", "value": 1000.0},
                                         {"ts": "2026-10-02T20:00:00", "value": 990.0}])
        got = self.lines()
        self.assertEqual(got[0], "Plan since Oct 1, 2026: -1.00% vs SPY +2.00% ⚠️")
        self.assertTrue(got[-1].startswith("Mode changes: Oct 4 → invest"))
        self.assertTrue(got[-1].endswith("(+2 earlier)"))

    def test_report_message_includes_the_lines(self):
        self.write("plan.json", {"started": "2026-10-19", "mode": "invest", "picks": ["NVDA"]})
        stats = {"period": "monthly", "label": "October 2026", "plan_lines": self.lines()}
        rpt.send_report_telegram(stats)
        (name, args, _), = self.alerts.calls
        self.assertEqual(name, "_send")
        self.assertIn("─\nPlan since Oct 19, 2026: not enough history yet\nPicks now:    NVDA", args[0])


class SpyEndDate(BotTestCase):
    def test_end_date_is_inclusive(self):
        calls = []

        class Ticker:
            def __init__(self, sym): pass
            def history(self, start, end):
                calls.append((start, end))
                import pandas as pd
                return pd.DataFrame({"Close": [100.0, 110.0]})

        fake = types.ModuleType("yfinance"); fake.Ticker = Ticker
        real = sys.modules.get("yfinance")
        sys.modules["yfinance"] = fake
        try:
            self.assertEqual(rpt._spy_return("2026-10-01", "2026-10-01"), 10.0)
        finally:
            if real is not None: sys.modules["yfinance"] = real
            else: sys.modules.pop("yfinance", None)
        self.assertEqual(calls, [("2026-10-01", "2026-10-02")])
