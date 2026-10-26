"""webdash: equity thinning, market hours, SPY-since-date, holdings by role, milestones, the backtest view."""
import os
from datetime import datetime, timezone

from base import BotTestCase

import webdash as wd

SPY = [["2026-10-01", 100.0], ["2026-10-02", 101.0], ["2026-10-05", 99.0], ["2026-10-06", 102.0]]


class WebDash(BotTestCase):
    def test_thin_hourly_keeps_last_point_per_hour(self):
        curve = [{"ts": "2026-10-01T14:00:01", "value": 1}, {"ts": "2026-10-01T14:30:00", "value": 2},
                 {"ts": "2026-10-01T15:00:00", "value": 3}]
        self.assertEqual(wd.thin_hourly(curve), [["2026-10-01T14:30:00", 2], ["2026-10-01T15:00:00", 3]])

    def test_live_point_ends_chart_at_header_value(self):
        now = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)
        pts = [["2026-10-14T14:32:18+00:00", 1073.04]]
        self.assertEqual(wd.with_live_point(pts, 1073.2, now), pts + [[now.isoformat(), 1073.2]])
        self.assertEqual(wd.with_live_point(pts, None, now), pts)          # Alpaca unreachable
        self.assertEqual(wd.with_live_point([], 1000, now), [[now.isoformat(), 1000.0]])

    def test_daily_history_starts_at_first_funded_day_and_fills_the_gap(self):
        ts = [datetime(2026, 5, d, tzinfo=timezone.utc).timestamp() for d in (27, 28, 29)]
        daily = wd.daily_history(ts, [0.0, 1000.0, 1011.07])
        self.assertEqual(daily, [["2026-05-28T00:00:00+00:00", 1000.0], ["2026-05-29T00:00:00+00:00", 1011.07]])
        curve = [["2026-05-29T00:00:00+00:00", 1011.0], ["2026-06-03T18:56:00+00:00", 1042.61]]
        self.assertEqual(wd.with_history(curve, daily), [daily[0]] + curve)   # only days before the curve
        self.assertEqual(wd.with_history([], daily), daily)

    def test_hourly_spy_gets_its_days_dividend_factor(self):
        h = [["2026-06-17T19:00+00:00", 600.0], ["2026-06-18T19:00+00:00", 600.0]]
        self.assertEqual(wd.dividend_adjust(h, {"2026-06-17": 0.995}), [["2026-06-17T19:00+00:00", 597.0], h[1]])

    def test_market_status(self):
        at = lambda h, m, day=7: wd.market_status(datetime(2026, 10, day, h, m, tzinfo=timezone.utc))
        self.assertEqual(at(13, 0), "pre")      # 9:00 ET
        self.assertEqual(at(13, 30), "open")    # 9:30 ET
        self.assertEqual(at(20, 0), "after")    # 16:00 ET
        self.assertEqual(at(15, 0, day=10), "closed")   # Saturday

    def test_missing_closes_dropped_and_state_made_json_safe(self):
        import json
        nan = float("nan")
        pairs = [(datetime(2026, 10, 7), 100.0), (datetime(2026, 10, 8), nan)]
        self.assertEqual(wd.clean_closes(pairs), [["2026-10-07", 100.0]])
        state = {"spy": [["2026-10-08", nan]], "a": {"b": float("inf"), "c": 1.5}, "why": "NaN in text"}
        out = json.dumps(wd.json_safe(state), allow_nan=False)
        self.assertEqual(json.loads(out), {"spy": [["2026-10-08", None]], "a": {"b": None, "c": 1.5},
                                           "why": "NaN in text"})

    def test_hourly_closes_are_stamped_when_the_bar_ends(self):
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        rows = [(datetime(2026, 10, 9, 9, 30, tzinfo=et), 100.0),    # ends 10:30 ET = 14:30 UTC
                (datetime(2026, 10, 9, 15, 30, tzinfo=et), 101.0),   # last bar ends at the 4 PM close
                (datetime(2026, 10, 9, 14, 30, tzinfo=et), float("nan"))]
        now = datetime(2026, 10, 9, 20, 30, tzinfo=timezone.utc)
        self.assertEqual(wd.hourly_closes(rows, now), [["2026-10-09T14:30+00:00", 100.0],
                                                       ["2026-10-09T20:00+00:00", 101.0]])
        early = datetime(2026, 10, 9, 19, 0, tzinfo=timezone.utc)   # 3 PM ET: the 3:30 bar isn't done
        self.assertEqual(len(wd.hourly_closes(rows, early)), 1)

    def test_manifest_icons_are_served_files(self):
        import json
        m = json.loads(wd.MANIFEST)
        self.assertEqual(m["display"], "standalone")
        for icon in m["icons"]:
            self.assertIn(icon["src"], wd.STATIC)
        for path in wd.STATIC:
            self.assertTrue(os.path.isfile(os.path.join(wd.ROOT, "web", path[1:])), path)

    def test_kalshi_proxy_only_passes_known_paths_and_reports_a_down_dashboard(self):
        self.assertEqual(set(wd.KALSHI_PATHS), {"/kalshi/", "/kalshi/index.html", "/kalshi/api/state"})
        old = wd.KALSHI_URL
        wd.KALSHI_URL = "http://127.0.0.1:9"   # nothing listens here
        try:
            code, body, ctype = wd.kalshi_proxy("/kalshi/api/state")
        finally:
            wd.KALSHI_URL = old
        self.assertEqual(code, 502)
        self.assertIn(b"kalshi-web", body)

    def test_spy_return_since_uses_close_before_the_day(self):
        self.assertEqual(wd.spy_return_since(SPY, "2026-10-02T15:00:00"), 2.0)   # from 100 to 102
        self.assertEqual(wd.spy_return_since(SPY, "2026-09-01"), 2.0)            # before data: first close
        self.assertIsNone(wd.spy_return_since([], "2026-10-02"))


class ClaudeTab(BotTestCase):
    def test_latest_journal_picks_newest_dated_report(self):
        folder = os.path.join(self.tmp, "journal")
        os.makedirs(folder)
        for name, text in (("2026-10-09.md", "old"), ("2026-10-12.md", "new"), ("notes.md", "x")):
            with open(os.path.join(folder, name), "w") as f:
                f.write(text)
        self.assertEqual(wd.latest_journal(folder), {"date": "2026-10-12", "text": "new", "count": 2})
        self.assertIsNone(wd.latest_journal(os.path.join(self.tmp, "none")))


class PlanViews(BotTestCase):
    PLAN = {"settings": {"CORE": "SPY", "SAFE": "SGOV"}, "targets": {"SPY": 0.8, "NVDA": 0.05, "SGOV": 0.15},
            "picks": ["NVDA"], "ranked": [{"symbol": "NVDA", "momentum": 80.0, "rank": 1}],
            "started": "2026-10-19", "last_rebalance": "2026-10-19", "next_rebalance": "2026-11-02"}

    def test_holdings_get_roles_in_plan_order(self):
        pos = [{"symbol": "XOM", "market_value": 100.0}, {"symbol": "SGOV", "market_value": 150.0},
               {"symbol": "NVDA", "market_value": 50.0}, {"symbol": "SPY", "market_value": 700.0}]
        h = wd.holdings(pos, self.PLAN, 1000.0)
        self.assertEqual([(x["symbol"], x["role"]) for x in h],
                         [("SPY", "core"), ("NVDA", "pick"), ("SGOV", "safety"), ("XOM", "leaving")])
        self.assertEqual((h[0]["target_pct"], h[0]["actual_pct"]), (80.0, 70.0))
        self.assertEqual((h[1]["momentum"], h[1]["rank"]), (80.0, 1))
        self.assertEqual(h[3]["target_pct"], 0.0)

    def test_holdings_before_the_first_check(self):
        h = wd.holdings([{"symbol": "XOM", "market_value": 100.0}], {}, 1000.0)
        self.assertEqual((h[0]["role"], h[0]["target_pct"]), ("leaving", None))

    def test_milestones(self):
        now = datetime(2026, 10, 26, 15, tzinfo=timezone.utc)
        m = wd.milestones(self.PLAN, now)
        self.assertEqual([x["name"] for x in m], ["Next re-pick", "Next rule check", "Six-month check vs SPY"])
        self.assertEqual(m[1]["needs"], "Nov 1")              # first Sunday of November
        self.assertEqual(m[0]["detail"], "in 7 days")
        self.assertEqual(m[0]["progress"], 0.5)
        self.assertEqual(m[2]["needs"], "Apr 19, 2027")
        self.assertEqual(wd.milestones({}, now)[0]["name"], "First check")

    def test_backtest_view_keeps_the_shown_runs(self):
        bt = {"start": "2007-07-02", "end": "2026-10-15", "runs": {
            "charles": {"about": "a", "curve": [["2007-07-02", 1000]], "stats": {"cagr_pct": 15.5, "max_drawdown_pct": -29.1, "yearly": {}}},
            "charles_no_switch": {"about": "b", "curve": [], "stats": {}}}}
        v = wd.backtest_view(bt)
        self.assertEqual(list(v["runs"]), ["charles"])
        self.assertEqual(v["runs"]["charles"]["cagr_pct"], 15.5)
        self.assertIsNone(wd.backtest_view({}))

    def test_the_real_backtest_file_has_what_the_page_draws(self):
        import json
        with open(wd.BACKTEST) as f:
            v = wd.backtest_view(json.load(f))
        self.assertEqual(set(v["runs"]), {"charles", "charles_sectors", "spy"})
        for r in v["runs"].values():
            self.assertGreater(len(r["curve"]), 500)
            self.assertLess(r["max_drawdown_pct"], 0)

    def test_page_has_a_since_the_plan_range(self):
        with open(os.path.join(wd.ROOT, "web", "index.html")) as f:
            page = f.read()
        self.assertIn('["Plan", "plan"]', page)                 # the fifth range button
        self.assertIn('days === "plan"', page)                  # series() starts it at plan.json "started"
        self.assertIn("P().started", page)

    def test_next_evaluation_is_the_first_sunday(self):
        at = lambda y, mo, d: wd.next_evaluation(datetime(y, mo, d, 15, tzinfo=timezone.utc)).date().isoformat()
        self.assertEqual(at(2026, 10, 16), "2026-11-01")
        self.assertEqual(at(2026, 11, 1), "2026-11-01")
        self.assertEqual(at(2026, 11, 2), "2026-12-06")

    def test_evaluation_view(self):
        ev = {"generated": "2026-11-01T08:00", "variants": [{"passes": False}, {"passes": True}],
              "candidate": {"setting": "PICKS", "from": 4, "to": 5}, "live": {"days": 13}}
        v = wd.evaluation_view(ev)
        self.assertEqual((v["tested"], v["passing"], v["candidate"]["to"]), (2, 1, 5))
        self.assertIsNone(wd.evaluation_view(None))


class KalshiPreviewPage(BotTestCase):
    def test_preview_serves_a_branch_copy_of_the_kalshi_page(self):
        page = os.path.join(self.tmp, "kalshi.html")
        with open(page, "w") as f:
            f.write("<p>branch copy</p>")
        self.patch(wd, "KALSHI_PAGE", page)
        self.assertEqual(wd.kalshi_proxy("/kalshi/")[:2], (200, b"<p>branch copy</p>"))
