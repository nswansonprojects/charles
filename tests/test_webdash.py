"""webdash: equity thinning, market hours, SPY-since-date and the experiments table."""
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

    def test_experiments_sorted_with_baseline_gap_and_progress(self):
        now = datetime(2026, 10, 7, tzinfo=timezone.utc)
        summary = {"baseline": {"since": "2026-10-02", "return_pct": 1.0, "closed": 15},
                   "swing": {"since": "2026-10-02", "return_pct": 3.0, "closed": 45},
                   "trend_following": {"since": "2026-10-05", "return_pct": 0.5, "closed": 0}}
        rows = wd.experiments(summary, SPY, now)
        self.assertEqual([r["name"] for r in rows], ["swing", "baseline", "trend_following"])
        self.assertEqual(rows[0]["vs_baseline"], 2.0)
        self.assertEqual(rows[0]["progress"], 1.0)                      # capped
        self.assertEqual(rows[1]["progress"], 0.5)                      # 15 of 30 trades
        self.assertIsNone(rows[1]["vs_baseline"])
        self.assertEqual(rows[2]["kind"], "daily")
        self.assertEqual(rows[2]["progress"], round(2 / 91, 2))         # days of the 3 months

    def test_ai_check_pairs_horizons(self):
        sc = {"groups": {"AI BUY": {"1d": {"n": 3, "mean_pct": 1.0, "up_pct": 60}},
                         "all candidates (baseline)": {"1d": {"n": 9, "mean_pct": 2.0, "up_pct": 70},
                                                       "3d": {"n": 4, "mean_pct": 1.0, "up_pct": 50}}}}
        self.assertEqual(wd.ai_check(sc), [{"horizon": "1d", "ai_mean": 1.0, "ai_n": 3, "ai_up": 60,
                                            "all_mean": 2.0, "all_n": 9, "all_up": 70}])


class OrbSwitchEndpoint(BotTestCase):
    """POST /api/orb is the dashboard's one write: it flips data/control/orb_switch.json, nothing else."""

    def setUp(self):
        super().setUp()
        import os
        import threading
        from http.server import ThreadingHTTPServer
        import orb
        self.orb = orb
        self.patch(orb, "SWITCH_FILE", os.path.join(self.tmp, "control", "orb_switch.json"))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), wd.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post(self, path, body, ctype="application/json"):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        conn.request("POST", path, body=body, headers={"Content-Type": ctype})
        r = conn.getresponse()
        r.read()
        conn.close()
        return r.status

    def test_switches_off_and_on(self):
        self.assertEqual(self.post("/api/orb", b'{"on": false}'), 200)
        self.assertFalse(self.orb.switched_on())
        self.assertEqual(self.post("/api/orb", b'{"on": true}'), 200)
        self.assertTrue(self.orb.switched_on())

    def test_rejects_anything_else(self):
        self.assertEqual(self.post("/api/orb", b'{"on": "no"}'), 400)
        self.assertEqual(self.post("/api/orb", b'on=false', "application/x-www-form-urlencoded"), 400)
        self.assertEqual(self.post("/api/orb", b'{}'), 400)
        self.assertEqual(self.post("/api/state", b'{"on": false}'), 404)
        self.assertTrue(self.orb.switched_on())


class ClaudeTab(BotTestCase):
    def test_latest_journal_picks_newest_dated_report(self):
        folder = os.path.join(self.tmp, "journal")
        os.makedirs(folder)
        for name, text in (("2026-10-09.md", "old"), ("2026-10-12.md", "new"), ("notes.md", "x")):
            with open(os.path.join(folder, name), "w") as f:
                f.write(text)
        self.assertEqual(wd.latest_journal(folder), {"date": "2026-10-12", "text": "new", "count": 2})
        self.assertIsNone(wd.latest_journal(os.path.join(self.tmp, "none")))

    def test_milestones(self):
        now = datetime(2026, 10, 17, tzinfo=timezone.utc)
        card = {"groups": {"model B · all candidates (baseline)": {"5d": {"n": 25}}}}
        summary = {"baseline": {"closed": 40, "return_pct": 0, "realized_pnl": 0, "max_drawdown_pct": 0},
                   "swing": {"closed": 31, "return_pct": 1, "realized_pnl": 1, "max_drawdown_pct": 0},
                   "midday_entries": {"closed": 3, "return_pct": 1, "realized_pnl": 1, "max_drawdown_pct": 0}}
        m = {x["name"]: x for x in wd.milestones(now, card, {"labelled_weeks": 3, "needs_weeks": 12,
                                                              "progress": 0.25}, summary)}
        self.assertAlmostEqual(m["First promotion review"]["progress"], 0.5)      # Oct 1 → Nov 2, day 16
        self.assertIn("16 days to go · 1 of 2 books have enough data", m["First promotion review"]["detail"])
        self.assertEqual((m["Second AI model"]["progress"], m["Second AI model"]["detail"]), (0.25, "25 graded"))
        self.assertEqual(m["Learning model"]["detail"], "3 of 12 weeks")
        self.assertEqual(wd.milestones(now, {}, {}, {})[1]["progress"], 0.0)       # nothing yet
