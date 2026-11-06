"""reports: report windows (scheduled = last complete period), the numbers at the same closes as SPY, SPY's inclusive end date."""
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

    def lines(self, spy=lambda s, e: []):
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
        spy = [("2026-10-16", 100.0), ("2026-10-19", 101.0), ("2026-10-30", 102.0), ("2026-11-02", 150.0)]
        got = self.lines(lambda s, e: spy_calls.append((s, e)) or spy)     # both from Friday 10-16's close, not Monday's
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
        got = self.lines(lambda s, e: [("2026-10-01", 100.0), ("2026-10-02", 102.0)])
        self.assertEqual(got[0], "Plan since Oct 1, 2026: -1.00% vs SPY +2.00% ⚠️")      # began inside: its first close
        self.assertTrue(got[-1].startswith("Mode changes: Oct 4 → invest"))
        self.assertTrue(got[-1].endswith("(+2 earlier)"))

    def test_report_message_includes_the_lines(self):
        self.write("plan.json", {"started": "2026-10-19", "mode": "invest", "picks": ["NVDA"]})
        stats = {"period": "monthly", "label": "October 2026", "plan_lines": self.lines()}
        rpt.post_report(stats)
        (name, args, _), = self.alerts.calls
        self.assertEqual(name, "report")
        self.assertIn("─\nPlan since Oct 19, 2026: not enough history yet\nPicks now:    NVDA", args[1])


class SpyEndDate(BotTestCase):
    def test_end_date_is_inclusive_and_the_close_before_comes_too(self):
        calls = []

        class Ticker:
            def __init__(self, sym): pass
            def history(self, start, end):
                calls.append((start, end))
                import pandas as pd
                return pd.DataFrame({"Close": [100.0, 110.0]}, index=pd.to_datetime(["2026-09-30", "2026-10-01"]))

        fake = types.ModuleType("yfinance"); fake.Ticker = Ticker
        real = sys.modules.get("yfinance")
        sys.modules["yfinance"] = fake
        try:
            self.assertEqual(rpt._spy_closes("2026-10-01", "2026-10-01"), [("2026-09-30", 100.0), ("2026-10-01", 110.0)])
        finally:
            if real is not None: sys.modules["yfinance"] = real
            else: sys.modules.pop("yfinance", None)
        self.assertEqual(calls, [("2026-09-21", "2026-10-02")])


def closes(values, first="2026-10-01"):
    """One in-session point a market day (15:30 ET) from `first`, plus a midnight mark that must not count."""
    from datetime import date, timedelta
    out, d = [], date.fromisoformat(first)
    for v in values:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out += [{"ts": f"{d}T04:00:00+00:00", "value": 1.0}, {"ts": f"{d}T19:30:00+00:00", "value": v}]
        d += timedelta(days=1)
    return out


class Numbers(BotTestCase):
    """The weekly/monthly numbers (bug hunt 2026-11-06: "-3.41% ($+17.54)" showed sales profit beside a loss)."""
    SELLS = [{"action": "SELL", "symbol": "WOLF", "pnl": 20.0}, {"action": "SELL", "symbol": "AMAT", "pnl": -2.46},
             {"action": "BUY", "symbol": "SPY"}]

    def test_the_dollar_change_is_the_accounts_not_just_sales(self):
        import plan_vs_spy
        curve = closes([1000.0, 990.0, 965.9])
        same = plan_vs_spy.over(curve, [], "2026-10-02", "2026-10-05")
        st = rpt._compute_stats(self.SELLS, curve[2:], same=same)
        self.assertEqual((st["start_value"], st["end_value"], st["period_return"], st["change"]), (1000.0, 965.9, -3.41, -34.1))
        self.assertEqual((st["sales_pnl"], st["closed_trades"], st["win_rate"]), (17.54, 2, 50.0))

    def test_sharpe_is_one_return_a_market_day_and_needs_enough_days(self):
        import statistics
        vals = [1000.0 * (1.01 if i % 2 else 0.995) ** i for i in range(rpt.SHARPE_DAYS + 1)]
        st = rpt._compute_stats([], closes(vals))
        rets = [b / a - 1 for a, b in zip(vals, vals[1:])]
        self.assertAlmostEqual(st["sharpe_ratio"], round(statistics.mean(rets) / statistics.stdev(rets) * 252 ** 0.5, 3))
        self.assertIsNone(rpt._compute_stats([], closes(vals[:6]))["sharpe_ratio"])           # a week: left out
        self.assertIsNone(rpt._compute_stats([], closes(vals))["win_rate"])                    # no sales: no win rate

    def test_drawdown_counts_from_the_close_before_and_skips_after_hours_marks(self):
        import plan_vs_spy
        curve = closes([1000.0, 950.0, 990.0])
        st = rpt._compute_stats([], curve[2:], same=plan_vs_spy.over(curve, [], "2026-10-02", "2026-10-05"))
        self.assertEqual(st["max_drawdown"], 5.0)                                              # not the $1 midnight marks

    def test_message(self):
        rpt.post_report({"period": "weekly", "label": "Week", "period_return": -1.07, "change": -10.32, "spy_return": -1.26,
                         "alpha": 0.19, "closed_trades": 0, "win_rate": None, "sharpe_ratio": None, "max_drawdown": 2.0})
        rpt.post_report({"period": "monthly", "label": "October 2026", "period_return": -3.41, "change": -34.1,
                         "spy_return": None, "alpha": None, "closed_trades": 11, "win_rate": 63.6, "sales_pnl": 17.54,
                         "sharpe_ratio": -1.2, "max_drawdown": 7.02, "best_symbol": "WOLF", "worst_symbol": "AMAT"})
        (_, (_, week), _), (_, (_, month), _) = self.alerts.calls
        self.assertIn("Return:       -1.07%  (-$10.32)\nvs SPY:       -1.26%  ✅ Alpha: +0.19%\nSales:        none\nMax drawdown: -2.00%", week)
        self.assertNotIn("Sharpe", week)
        self.assertNotIn("Best sym", week)
        self.assertIn("Return:       -3.41%  (-$34.10)\nvs SPY:       couldn't get SPY's prices", month)
        self.assertIn("Win rate:     63.6%  (11 sales, +$17.54)\nSharpe:       -1.200", month)
