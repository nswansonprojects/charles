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
