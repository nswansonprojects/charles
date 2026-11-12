"""webdash: equity thinning, market hours, SPY-since-date, holdings by role, milestones, the backtest view."""
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from unittest import mock

from base import BotTestCase

import auth
import control
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
        with open(os.path.join(wd.ROOT, "web", "stocks.html")) as f:
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


class SpyHistory(BotTestCase):
    def test_hourly_spy_stays_inside_yahoos_window(self):
        # Yahoo serves 60-minute bars only for the last 730 days: asking from 2026-05-01 would fail from ~May 2028
        from datetime import timedelta
        self.assertEqual(wd.hourly_start(datetime(2026, 10, 29, tzinfo=timezone.utc)), wd.SPY_FROM)
        later = datetime(2028, 6, 1, tzinfo=timezone.utc)
        self.assertEqual(wd.hourly_start(later), (later - timedelta(days=wd.HOURLY_DAYS)).date().isoformat())
        self.assertLess(wd.HOURLY_DAYS, 730)


class SelfTuneCopy(BotTestCase):
    """Public installs have the monthly rule check (evaluate.py) but not Claude's daily check-up, so the
    page must not tell them Claude is changing their plan."""
    def test_checkup_is_detected_by_its_runner(self):
        self.assertFalse(wd.checkup_installed(os.path.join(self.tmp, "ops", "run-checkup")))
        os.makedirs(os.path.join(self.tmp, "ops"))
        with open(os.path.join(self.tmp, "ops", "run-checkup"), "w") as f:
            f.write("#!/bin/bash\n")
        self.assertTrue(wd.checkup_installed(os.path.join(self.tmp, "ops", "run-checkup")))

    def test_rule_check_milestone_does_not_claim_claude(self):
        m = wd.milestones({"started": "2026-10-16", "next_rebalance": "2026-11-02", "last_rebalance": "2026-10-16"},
                          datetime(2026, 10, 26, 15, tzinfo=timezone.utc))
        self.assertNotIn("Claude", m[1]["about"])

    def test_page_picks_the_copy_by_install(self):
        with open(os.path.join(wd.ROOT, "web", "stocks.html")) as f:
            page = f.read()
        self.assertIn("C.checkup", page)
        self.assertNotIn("Claude will test small changes", page)
        self.assertNotIn("Claude re-tests it every month", page)


class AddonTabs(BotTestCase):
    """Crypto and Kalshi are add-ons (addons.py): a tab shows only while its add-on is on."""
    PAGES = ("web/home.html", "web/stocks.html", "web/crypto.html", "web/leaderboard.html", "web/settings.html", "kalshi/web/index.html")

    def page(self, rel):
        with open(os.path.join(wd.ROOT, rel), "rb") as f:
            return f.read()

    def nav(self, html: bytes) -> str:
        s = html.decode()
        i = s.find('<nav class="topnav"')
        return s[i:s.index("</nav>", i)]

    def test_every_page_drops_the_tab_of_an_add_on_that_is_off(self):
        for rel in self.PAGES:
            with self.subTest(page=rel):
                html = self.page(rel)
                nav = self.nav(wd.shell(html, {"crypto": True, "kalshi": True}, "home"))
                self.assertIn('href="/crypto/"', nav)
                self.assertIn('href="/kalshi/"', nav)
                nav = self.nav(wd.shell(html, {"crypto": False, "kalshi": True}, "home"))
                self.assertIn('href="/kalshi/"', nav)
                self.assertNotIn('href="/crypto/"', nav)
                nav = self.nav(wd.shell(html, {"crypto": False, "kalshi": False}, "home"))
                self.assertNotIn('href="/kalshi/"', nav)
                self.assertIn('href="/leaderboard"', nav)

    def test_every_page_has_one_header_whose_logo_goes_home(self):
        for rel in self.PAGES:
            with self.subTest(page=rel):
                out = wd.shell(self.page(rel), {"crypto": True, "kalshi": True}, "stocks").decode()
                self.assertEqual(out.count("<header"), 1)
                self.assertIn('<a class="brand" href="/"', out)
                self.assertNotIn('chat-open', out)
                self.assertNotIn('chat.js', out)
                self.assertIn('href="/settings"', out)
                self.assertIn('class=on aria-current=page', out)
                self.assertIn('src="/shell.js"', out)
                self.assertIn('src="/tour.js"', out)

    def test_old_addresses_lead_to_their_new_pages(self):
        self.assertEqual(wd.PAGES["/"], "home.html")
        self.assertEqual(wd.PAGES["/stocks"], "stocks.html")
        for page in wd.PAGES.values():
            self.assertTrue(os.path.isfile(os.path.join(wd.ROOT, "web", page)), page)

    def test_an_add_on_page_says_how_to_turn_it_on(self):
        page = wd.addon_off_page("crypto").decode()
        self.assertIn("charles-addon on crypto", page)
        self.assertIn("is off", page)

    def test_accounts_lists_only_the_bots_that_are_on(self):
        rows = [{"id": "charles"}, {"id": "kalshi"}, {"id": "crypto"}]
        v = wd.accounts_view(rows, {"crypto": True, "kalshi": False}, [{"id": "crypto", "on": True}])
        self.assertEqual([b["id"] for b in v["bots"]], ["charles", "crypto"])
        self.assertEqual(v["addons"], [{"id": "crypto", "on": True}])


class Lessons(BotTestCase):
    def setUp(self):
        super().setUp()
        self.patch(wd, "_cache", {})

    def test_state_gets_its_lessons_and_each_holding_its_why(self):
        s = wd.with_lessons({"plan": {"mode": "invest", "next_rebalance": "2026-11-02"},
                             "positions": [{"symbol": "SPY", "role": "core"}, {"symbol": "MU", "role": "pick"}]})
        self.assertEqual(s["lessons"][0]["id"], "paper")
        self.assertIn("19-year backtest", next(x for x in s["lessons"] if x["id"] == "safety")["body"])   # docs/backtest.json
        self.assertIn("The core of the plan", s["positions"][0]["why"])
        self.assertIn("on Nov 2", s["positions"][1]["why"])
        self.assertEqual(wd.with_lessons({"error": "unavailable"}), {"error": "unavailable"})

    def test_news_is_for_the_stocks_held(self):
        self.patch(wd, "_stocks_state", lambda: {"positions": [{"symbol": "MU", "role": "pick"}], "names": {"MU": "Micron"}})
        seen = []
        self.patch(wd.news_feed, "holdings_news", lambda pos, names: seen.append((pos, names)) or {"items": []})
        self.assertEqual(wd.holdings_news(), {"items": []})
        self.assertEqual(seen, [([{"symbol": "MU", "role": "pick"}], {"MU": "Micron"})])


class Login(BotTestCase):
    """The login in front of the dashboard (auth.py): private, public, or open as before."""
    def setUp(self):
        super().setUp()
        self.cfg = {"access": "public", "secret": "s" * 64, "links": []}
        self.sid_cookie = auth.session(self.cfg)
        self.sid = auth.check(self.cfg, self.sid_cookie)

    def cookie(self, port=None):
        return f"{wd.SESSION_COOKIE if port is None else f'charles_session_{port}'}={self.sid_cookie}"

    def test_open_lets_everything_through(self):
        self.assertIsNone(wd.gate("POST", "/kalshi/api/mode", None, None, None))
        self.assertIsNone(wd.gate("GET", "/", None, None, None))

    def test_public_looks_free_changes_need_the_login_and_csrf(self):
        self.assertIsNone(wd.gate("GET", "/", self.cfg, None, None))
        self.assertIsNone(wd.gate("GET", "/api/state", self.cfg, None, None))
        self.assertEqual(wd.gate("POST", "/kalshi/api/mode", self.cfg, None, None), (401, "login"))
        self.assertEqual(wd.gate("POST", "/kalshi/api/mode", self.cfg, self.cookie(), None), (403, "csrf"))
        self.assertEqual(wd.gate("POST", "/kalshi/api/mode", self.cfg, self.cookie(), "wrong"), (403, "csrf"))
        self.assertIsNone(wd.gate("POST", "/kalshi/api/mode", self.cfg, self.cookie(), auth.csrf(self.cfg, self.sid)))

    def test_private_needs_the_login_to_look(self):
        cfg = {**self.cfg, "access": "private"}
        self.assertEqual(wd.gate("GET", "/", cfg, None, None), (401, "login"))
        self.assertEqual(wd.gate("GET", "/kalshi/api/state", cfg, None, None), (401, "login"))
        self.assertIsNone(wd.gate("GET", "/style.css", cfg, None, None))           # the locked page needs its style
        self.assertIsNone(wd.gate("GET", "/", cfg, self.cookie(), None))
        self.assertEqual(wd.gate("GET", "/", cfg, self.cookie(port=8504 if wd.PORT != 8504 else 8502), None), (401, "login"))

    def test_pages_carry_the_csrf_header_script(self):
        page = wd.with_csrf(b"<html><head><title>x</title></head><body></body></html>")
        self.assertEqual(page.count(b"X-CSRF-Token"), 1)
        self.assertIn(wd.CSRF_COOKIE.encode(), page)
        self.assertLess(page.index(b"X-CSRF-Token"), page.index(b"</head>"))

    def test_the_login_pages(self):
        confirm = wd.auth_page("confirm", 'abc"<x>').decode()
        self.assertIn('name="t" value="abc&quot;&lt;x&gt;"', confirm)
        self.assertIn('content="no-referrer"', confirm)
        self.assertIn("charles-login", wd.auth_page("locked").decode())
        self.assertIn("expired", wd.auth_page("expired").decode())


class LoginOverHttp(BotTestCase):
    """The whole flow against a real server on a spare local port: link, tap, cookie, locked without it."""
    def setUp(self):
        super().setUp()
        import threading
        from http.server import ThreadingHTTPServer
        self.path = os.path.join(self.tmp, "auth.json")
        self.patch(auth, "AUTH_FILE", self.path)
        self.patch(wd, "_cache", {})
        self.patch(wd, "_links_used", set())
        with open(self.path, "w") as f:
            json.dump({"access": "private", "secret": "s" * 64, "links": []}, f)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), wd.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def req(self, method, path, body=None, cookie=None):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=10)
        headers = {"Cookie": cookie} if cookie else {}
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        c.request(method, path, body=body, headers=headers)
        r = c.getresponse()
        return r.status, r.getheader("Location"), r.headers.get_all("Set-Cookie") or [], r.read().decode()

    def test_link_then_tap_then_in(self):
        cfg = auth.load()
        token = auth.new_link(cfg)
        with open(self.path, "w") as f:
            json.dump(cfg, f)
        self.assertEqual(self.req("GET", "/")[0], 401)
        code, _, _, page = self.req("GET", f"/login?t={token}")
        self.assertEqual(code, 200)
        self.assertIn("Log in on this phone or computer?", page)
        code, where, cookies, _ = self.req("POST", "/login", body=f"t={token}")
        self.assertEqual((code, where), (303, "/"))
        session = next(c for c in cookies if c.startswith(wd.SESSION_COOKIE + "="))
        self.assertIn("HttpOnly", session)
        self.assertIn("SameSite=Lax", session)
        csrf = next(c for c in cookies if c.startswith(wd.CSRF_COOKIE + "="))
        self.assertNotIn("HttpOnly", csrf)                                        # the page's script reads it
        jar = "; ".join(c.split(";")[0] for c in cookies)
        code, _, _, page = self.req("GET", "/", cookie=jar)
        self.assertEqual(code, 200)
        self.assertIn("X-CSRF-Token", page)
        self.assertEqual(self.req("POST", "/login", body=f"t={token}")[0], 410)    # used up
        self.assertEqual(self.req("GET", "/api/session", cookie=jar)[3], '{"access": "private", "logged_in": true}')
        _, _, cookies, _ = self.req("GET", "/logout", cookie=jar)
        self.assertTrue(all("Max-Age=0" in c for c in cookies))

    def test_pause_needs_the_page_token(self):
        self.patch(wd, "STATE_FROM", None)
        cfg = auth.load()
        session = auth.session(cfg)
        jar = f"{wd.SESSION_COOKIE}={session}"
        import http.client
        for token, want in ((None, 403), ("wrong", 403), (auth.csrf(cfg, session.split(".")[0]), 202)):
            c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=10)
            headers = {"Cookie": jar, "Content-Type": "application/json", **({"X-CSRF-Token": token} if token else {})}
            c.request("POST", "/api/control", body='{"action": "pause"}', headers=headers)
            self.assertEqual(c.getresponse().status, want)
        self.assertEqual(self.req("POST", "/api/control", body='{"action": "pause"}')[0], 401)  # no session
        self.assertEqual(len(os.listdir(control.REQ_DIR)), 1)


class LeaderboardPostsNeedTheLogin(BotTestCase):
    """Share, Withdraw and challenge Enter/Withdraw: refused with no login set up, refused without a session, through with one.
    The leaderboard's client is a fake, so nothing leaves the test."""
    ADDRS = (("/api/leaderboard/submit", '{"bot": "stocks", "name": "abc"}'), ("/api/leaderboard/withdraw", '{"id": "lb-1a2b3c"}'),
             ("/api/challenge/enter", '{"bot": "stocks", "name": "abc"}'), ("/api/challenge/withdraw", '{"bot": "stocks"}'))

    def setUp(self):
        super().setUp()
        import threading
        from http.server import ThreadingHTTPServer
        from leaderboard import client
        self.path = os.path.join(self.tmp, "auth.json")
        self.patch(auth, "AUTH_FILE", self.path)
        self.patch(wd, "_cache", {})
        self.cfg = {"access": "public", "secret": "s" * 64, "links": []}
        self.calls = []
        def fake(name):
            def call(*a, **k):
                self.calls.append(name)
                return {"open": {"id": "c1", "bot": "stocks"}} if name == "challenges" else (200, {"ok": True})
            return call
        for name in ("submit", "withdraw", "withdraw_challenge", "enter_challenge", "challenges", "_get"):
            self.patch(client, name, fake(name))
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), wd.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def state(self, access):
        if access == "open":
            if os.path.exists(self.path):
                os.remove(self.path)
        else:
            with open(self.path, "w") as f:
                json.dump({**self.cfg, "access": access}, f)
        wd._cache.clear()

    def post(self, path, body, cookie=None, csrf=None):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=10)
        c.request("POST", path, body=body, headers={"Content-Type": "application/json", **({"Cookie": cookie} if cookie else {}), **({"X-CSRF-Token": csrf} if csrf else {})})
        r = c.getresponse()
        return r.status, json.loads(r.read().decode() or "{}")

    def test_open_refuses_and_sends_nothing(self):
        self.state("open")
        for path, body in self.ADDRS:
            code, out = self.post(path, body)
            self.assertEqual(code, 403, path)
            self.assertIn("charles-login", out["message"])
            self.assertFalse(out["ok"])
        self.assertEqual(self.calls, [])

    def test_a_preview_with_its_own_test_board_may_share_without_a_login(self):
        self.state("open")
        self.patch(wd, "LEADERBOARD_BOT_DIR", os.path.join(self.tmp, "copy-test"))
        self.assertEqual(self.post(*self.ADDRS[0])[0], 403)           # a test copy alone, still pointed at the real board: refused
        self.assertEqual(self.calls, [])
        self.patch(wd, "LEADERBOARD_URL", "http://127.0.0.1:1")
        code, out = self.post(*self.ADDRS[0])
        self.assertEqual((code, out.get("ok")), (200, True), out)
        self.assertTrue(self.calls)

    def test_public_without_a_session_is_refused(self):
        self.state("public")
        for path, body in self.ADDRS:
            self.assertEqual(self.post(path, body)[0], 401, path)
            sid = auth.session(self.cfg)
            self.assertEqual(self.post(path, body, cookie=f"{wd.SESSION_COOKIE}={sid}")[0], 403, path)      # a session but no page token
        self.assertEqual(self.calls, [])

    def test_public_with_a_session_and_token_goes_through(self):
        self.state("public")
        sid = auth.session(self.cfg)
        for path, body in self.ADDRS:
            self.calls.clear()
            code, out = self.post(path, body, cookie=f"{wd.SESSION_COOKIE}={sid}", csrf=auth.csrf(self.cfg, sid.split(".")[0]))
            self.assertEqual((code, out.get("ok")), (200, True), (path, out))
            self.assertTrue(self.calls, path)


class Control(BotTestCase):
    """POST /api/control: pause and resume need the login; the root service (control.py) carries them out."""
    CFG = {"access": "public", "secret": "s" * 64, "links": []}

    def test_only_with_the_login_turned_on_and_never_from_a_preview(self):
        self.patch(wd, "STATE_FROM", None)
        self.assertEqual(wd.control_post(b'{"action": "pause"}', None)[0], 403)              # no login at all
        self.assertEqual(wd.control_post(b'{"action": "sell_all"}', self.CFG)[0], 400)
        self.assertEqual(wd.control_post(b"junk", self.CFG)[0], 400)
        self.assertEqual(wd.control_post(b'{"action": "pause"}', self.CFG), (202, {"ok": True, "action": "pause"}))
        self.assertEqual(len(os.listdir(control.REQ_DIR)), 1)
        self.patch(wd, "STATE_FROM", "http://127.0.0.1:8502")
        self.assertEqual(wd.control_post(b'{"action": "pause"}', self.CFG)[0], 409)

    def test_a_read_only_server_says_so(self):
        self.patch(wd, "STATE_FROM", None)

        def boom(*a):
            raise OSError("Read-only file system")
        self.patch(control, "request", boom)
        self.assertEqual(wd.control_post(b'{"action": "pause"}', self.CFG)[0], 503)


class HomeFeed(BotTestCase):
    """GET /api/home: every bot's earnings on one page. Rewards never count as profit."""
    NOW = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)

    def stocks(self, **kw):
        return {"account": {"total_value": 1030.15, "day_change": -44.58}, "start_cash": 1000, "bot_running": True, "paused": None,
                "today": {"halted": False, "limit": 50}, "since_start": {"spy_pct": 9.1},
                "equity": [["2026-05-28T00:00:00+00:00", 1000.0], ["2026-10-31T03:27:46.170760+00:00", 1030.15]],
                "plan": {"mode": "aggressive", "next_rebalance": "2026-11-02"}, "trades": [], **kw}

    def kalshi(self):
        return {"account": {"account_value": 5000, "start_cash": 1000},           # the arbitrage book, with estimated rewards
                "betting": {"account": {"account_value": 560.69, "start_cash": 1000.0, "day_pnl": 0.03, "open_bets": 9},
                            "equity": [["2026-10-09T23:45:01+00:00", 971.87], ["2026-10-31T03:15:00+00:00", 560.69]],
                            "mode": {"evil": False}, "closed": [{"closed": "2026-10-30T10:00:00+00:00", "sub": "Rain in Miami", "pnl": -3.5, "how": "lost"}], "open": []},
                "scanner": {"healthy": True}}

    def crypto(self):
        return {"book": {"value": 944.5, "start_cash": 1000.0, "day_change": -12.0, "btc_pct": -2.0,
                         "curve": [["2026-10-26T00:00:00+00:00", 1000.0], ["2026-10-31T00:00:00+00:00", 944.5]],
                         "coins": [{"name": "Bitcoin", "holding": True}, {"name": "Ether", "holding": False}], "trades": []}, "names": {}}

    def view(self, stocks=None, crypto=None, kalshi=None, on=None, noted=()):
        return wd.home_view(stocks, crypto, kalshi, list(noted), on or {"crypto": True, "kalshi": True}, self.NOW)

    def test_the_week_counts_from_seven_days_ago_or_from_a_young_books_start(self):
        st = self.stocks(equity=[["2026-05-28T00:00:00+00:00", 1000.0], ["2026-10-23T20:00:00+00:00", 1010.0], ["2026-10-24T11:00:00+00:00", 1020.0],
                                 ["2026-10-30T20:00:00+00:00", 1040.0]])
        v = self.view(st, self.crypto(), self.kalshi())
        by = {b["id"]: b for b in v["bots"]}
        self.assertEqual(by["stocks"]["week"], {"change": 10.15, "pct": 1.0, "days": 7})          # from $1,020: the last value 7 days ago or earlier
        self.assertEqual(by["crypto"]["week"], {"change": -55.5, "pct": -5.55, "days": 5})         # 5 days old: from its $1,000 start
        self.assertEqual(by["kalshi"]["week"]["days"], 7)
        self.assertEqual(v["total"]["week"], round(10.15 - 55.5 + by["kalshi"]["week"]["change"], 2))
        self.assertEqual(v["total"]["week_days"], 7)
        self.assertIsNone(wd.week_change([], None, 1000, self.NOW))
        self.assertEqual(wd.week_change([], 1000.0, 1000, self.NOW), {"change": 0.0, "pct": 0.0, "days": 0})
        self.assertEqual(self.view(None, None, None, on={})["total"]["week"], None)

    def test_adds_up_every_bot_and_uses_kalshi_betting_book_only(self):
        v = self.view(self.stocks(), self.crypto(), self.kalshi())
        by = {b["id"]: b for b in v["bots"]}
        self.assertEqual(by["kalshi"]["value"], 560.69)
        self.assertEqual(by["kalshi"]["earned"], -439.31)
        self.assertEqual(by["stocks"]["earned"], 30.15)
        self.assertEqual(by["crypto"]["earned"], -55.5)
        self.assertEqual(v["total"]["earned"], round(30.15 - 55.5 - 439.31, 2))
        self.assertEqual(v["total"]["start"], 3000)
        self.assertEqual(v["series"][-1][1], v["total"]["earned"])          # the chart ends on the big number
        self.assertEqual(by["crypto"]["note"], "Holding bitcoin")
        self.assertEqual(by["kalshi"]["note"], "Following Charles · 9 open bets")       # who the account copies (2026-11-03)
        k = self.kalshi()
        k["betting"]["mode"] = {"evil": True}
        k["betting"]["equity"] = [r + ["evil"] for r in k["betting"]["equity"]]         # Kalshi's points now say who it followed
        by = {b["id"]: b for b in self.view(self.stocks(), self.crypto(), k)["bots"]}
        self.assertEqual(by["kalshi"]["note"], "Following Evil Charles · 9 open bets")
        self.assertEqual(by["kalshi"]["curve"][-1], ["2026-10-31T03:15:00+00:00", 560.69])

    def test_an_add_on_that_is_off_or_silent_counts_for_nothing(self):
        v = self.view(self.stocks(), None, None, on={"crypto": False, "kalshi": False})
        self.assertEqual([b["id"] for b in v["bots"]], ["stocks"])
        self.assertEqual(v["total"]["earned"], 30.15)
        v = self.view(self.stocks(), None, None)
        self.assertEqual([b["id"] for b in v["bots"]], ["stocks", "crypto", "kalshi"])
        self.assertIsNone(v["bots"][2]["earned"])
        self.assertEqual(v["total"]["start"], 1000)

    def test_nothing_at_all_still_answers(self):
        v = self.view()
        self.assertEqual(v["total"]["earned"], 0)
        self.assertEqual(v["series"], [])

    def test_a_late_bot_counts_zero_until_it_starts_and_keeps_its_last_value(self):
        rows = wd.earnings_series([([["2026-10-29T10:30:00+00:00", 1010.0], ["2026-10-30T10:30:00+00:00", 1020.0]], 1000),
                                   ([["2026-10-30T11:00:00Z", 990.0]], 1000)], self.NOW)
        self.assertEqual([r[1] for r in rows], [10.0, 20.0, 10.0])

    def test_times_with_different_offsets_line_up_and_old_days_shrink_to_one_point(self):
        rows = wd.earnings_series([([["2026-09-01T01:00:00+00:00", 1001.0], ["2026-09-01T20:00:00+00:00", 1002.0],
                                     ["2026-10-30T22:30:00-04:00", 1003.0]], 1000)], self.NOW)
        self.assertEqual(rows, [["2026-09-01T00:00:00+00:00", 2.0], ["2026-10-31T02:00:00+00:00", 3.0]])

    def test_a_bot_thats_off_can_be_added(self):
        v = self.view(self.stocks(), None, None, on={"crypto": False, "kalshi": False})
        self.assertEqual([b["id"] for b in v["bots"]], ["stocks"])
        self.assertEqual([a["id"] for a in v["can_add"]], ["crypto", "kalshi"])
        self.assertEqual(self.view(self.stocks(), self.crypto(), self.kalshi())["can_add"], [])

    def test_thinning_keeps_the_ends(self):
        rows = list(range(1000))
        out = wd._thin(rows, 90)
        self.assertEqual((len(out), out[0], out[-1]), (90, 0, 999))
        self.assertEqual(wd._thin(rows[:5]), rows[:5])


class CopyFromTheBoard(BotTestCase):
    """POST /api/leaderboard/copy: the dashboard only passes on an id; the root job fetches and checks the config itself."""

    def test_a_preview_writes_only_its_test_folder(self):
        from leaderboard import client
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(wd, "LEADERBOARD_BOT_DIR", d), mock.patch.object(wd.control, "request") as asked, \
                mock.patch.object(client, "fetch", return_value={"id": "lb-1a2b3c", "name": "x", "bot": "crypto", "settings": {"BTC_PCT": .5}}):
            code, res = wd.leaderboard_copy(b'{"id": "lb-1a2b3c"}', None)
            self.assertEqual((code, res["test"]), (200, True))
            self.assertTrue(os.path.isfile(os.path.join(d, "crypto.json")))
            asked.assert_not_called()

    def test_the_real_copy_needs_the_login_and_only_leaves_a_request(self):
        from leaderboard import client
        with mock.patch.object(wd, "LEADERBOARD_BOT_DIR", wd.ROOT), mock.patch.object(wd.control, "request") as asked, \
                mock.patch.object(client, "fetch", return_value={"id": "lb-1a2b3c"}), mock.patch.object(wd.auth, "access", return_value="open"):
            self.assertEqual(wd.leaderboard_copy(b'{"id": "lb-1a2b3c"}', None)[0], 403)
            asked.assert_not_called()
        with mock.patch.object(wd, "LEADERBOARD_BOT_DIR", wd.ROOT), mock.patch.object(wd.control, "request") as asked, \
                mock.patch.object(client, "fetch", return_value={"id": "lb-1a2b3c"}), mock.patch.object(wd.auth, "access", return_value="public"):
            self.assertEqual(wd.leaderboard_copy(b'{"id": "lb-1a2b3c"}', {})[0], 202)
            asked.assert_called_once_with("copy-config", "the dashboard", config="lb-1a2b3c")

    def test_a_config_that_fails_its_checks_is_refused_with_the_reason(self):
        from leaderboard import client
        with mock.patch.object(wd, "LEADERBOARD_BOT_DIR", wd.ROOT), mock.patch.object(wd.control, "request") as asked, mock.patch.object(wd.auth, "access", return_value="public"), \
                mock.patch.object(client, "fetch", side_effect=ValueError("the config's signature doesn't match")):
            code, res = wd.leaderboard_copy(b'{"id": "lb-1a2b3c"}', {})
            self.assertEqual(code, 400)
            self.assertIn("signature", res["message"])
            asked.assert_not_called()

    def test_control_route_cannot_ask_for_a_copy(self):
        with mock.patch.object(wd, "STATE_FROM", None), mock.patch.object(wd.auth, "access", return_value="public"), mock.patch.object(wd.control, "request") as asked:
            code, _ = wd.control_post(b'{"action": "copy-config", "config": "lb-1a2b3c"}', {})
            self.assertEqual(code, 400)
            asked.assert_not_called()


class PauseButton(BotTestCase):
    def test_the_button_is_redrawn_once_the_pause_took_effect(self):
        """load() skips the button while busy, so without this it stayed on a disabled "Asking…" until the next minute's refresh.
        Found by a real browser on a real install; the preview refuses Pause, so it had never run."""
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "home.html")) as f:
            page = f.read()
        i = page.index('if (!!D.paused === (action === "pause"))')
        self.assertIn("renderRun()", page[i:page.index("return;", i)])

    def test_the_tour_survives_its_own_end_and_a_part_that_isnt_there(self):
        """Pressing Done left two delayed place() calls with nothing to place: a page error on every first visit. A part of the page
        that isn't there (no leaderboard, nobody to copy) is skipped after a short wait, never pointed at."""
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "tour.js")) as f:
            js = f.read()
        i = js.index("function place()")
        self.assertIn("if (!cur || cur.dlg || ended) return;", js[i:i + 120])
        self.assertIn("if (!ok) return go(st.miss || after(st));", js)

    def test_the_tour_starts_by_itself_only_on_a_new_install(self):
        """Owner 2026-11-03: the tour came back on every new network, phone or browser. It starts by itself only while Home's
        Get started is there (a new install); one under way or replayed from Settings (which sets the step) goes on."""
        web = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
        with open(os.path.join(web, "tour.js")) as f:
            js = f.read()
        with open(os.path.join(web, "home.html")) as f:
            home = f.read()
        with open(os.path.join(web, "settings.html")) as f:
            settings = f.read()
        i = js.index("const begin = () => {")
        self.assertIn('if (!at && !window.homeNew) { put(DONE, "1");', js[i:i + 200])
        self.assertIn("window.homeNew = !!D.setup; window.homeReady = true;", home)
        self.assertIn('localStorage.setItem("charles-tour-at", "hi")', settings)
        self.assertIn('{id: "hi", page: "home"', js)
        old = {"equity": [["2026-06-03T18:56:23+00:00", 1042.6]], "account": {"total_value": 1000}}
        self.assertIsNone(wd.setup_view(old, False, datetime(2026, 11, 3, tzinfo=timezone.utc)))   # months old: no Get started, no tour
        self.assertIsNotNone(wd.setup_view({}, False, datetime(2026, 11, 3, tzinfo=timezone.utc), new=True))

    def test_the_tour_only_asks_for_real_changes_where_a_refusal_has_a_way_on(self):
        """Every step that waits on the leaderboard has somewhere to go when the board says no, or the dialog is closed."""
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "tour.js")) as f:
            js = f.read()
        steps = js[js.index("const STEPS = ["):js.index("const byId")]
        for line in [x for x in steps.split("{id: ")[1:] if "dlg: true" in x.split("say:")[0]]:
            self.assertIn("closed:", line, line[:40])
        for line in [x for x in steps.split("{id: ")[1:] if "do: true" in x.split("say:")[0]]:
            self.assertIn("skip:", line, line[:40])
            self.assertIn("miss:", line, line[:40])


class Alerts(BotTestCase):
    """What pops up and what the bell lists: one shape, newest first, only what really happened."""
    STOCKS = {"trades": [{"timestamp": "2026-10-16T14:00:35+00:00", "symbol": "STX", "action": "BUY", "value": 51.38, "pnl": None, "reasoning": "New momentum pick"},
                         {"timestamp": "2026-10-16T14:00:30+00:00", "symbol": "ETSY", "action": "SELL", "value": 90.32, "pnl": 1.19, "reasoning": "Not part of the plan"}],
              "plan": {"last_rebalance": "2026-10-16", "vetoes": [{"symbol": "MRNA", "reason": "Fraud probe", "rank": 2}]}}
    CRYPTO = {"names": {"ETH-USD": "Ether"}, "book": {"trades": [{"timestamp": "2026-10-24T05:12:04+00:00", "product": "ETH-USD", "side": "BUY", "value": 400.0, "reason": "Above its average"}]}}
    KALSHI = {"betting": {"closed": [{"closed": "2026-10-17T20:00:00+00:00", "how": "won", "sub": "55° to 56°", "title": "Low in San Antonio?", "pnl": 2.09},
                                     {"closed": "2026-10-17T19:00:00+00:00", "how": "sold", "sub": "Early", "title": "A bet", "pnl": 0.5}],
                          "open": [{"opened": "2026-10-14T13:15:00+00:00", "side": "yes", "sub": "Above 0.3%", "title": "CPI in October", "cost": 4.26}]}}

    def test_every_bot_in_one_shape_newest_first(self):
        out = wd.alert_events(self.STOCKS, self.CRYPTO, self.KALSHI, [])
        self.assertEqual([a["ts"][:10] for a in out], sorted((a["ts"][:10] for a in out), reverse=True))
        by = {a["title"]: a for a in out}
        self.assertEqual((by["Bought STX"]["kind"], by["Bought STX"]["amount"], by["Bought STX"]["pnl"], by["Bought STX"]["sub"]), ("buy", 51.38, None, "New momentum pick"))
        self.assertEqual((by["Sold ETSY"]["kind"], by["Sold ETSY"]["pnl"]), ("sell", 1.19))
        self.assertEqual((by["Bought Ether"]["bot"], by["Bought Ether"]["href"]), ("Crypto", "/crypto/"))
        self.assertEqual((by["Won: 55° to 56°"]["kind"], by["Won: 55° to 56°"]["pnl"]), ("won", 2.09))
        self.assertEqual(by["Sold: Early"]["kind"], "note")
        self.assertEqual((by["Bet YES: Above 0.3%"]["kind"], by["Bet YES: Above 0.3%"]["amount"]), ("bet", 4.26))
        self.assertEqual(by["AI skipped MRNA"]["sub"], "Fraud probe")
        for a in out:
            self.assertEqual(set(a), {"ts", "bot", "kind", "title", "sub", "amount", "pnl", "href"})

    def test_home_drops_a_daily_pause_once_its_market_day_is_over(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(wd.__file__)), "web", "home.html")) as f:
            page = f.read()
        self.assertIn('n.title.startsWith("Buys paused") && marketDay(n.ts) !== marketDay(Date.now())', page)
        self.assertIn("!overToday(n)", page)

    def test_a_restart_alarm_goes_once_a_later_notice_clears_it(self):
        now = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
        rows = [{"ts": "2026-11-01T04:00:00+00:00", "title": "Buys paused", "level": "error"},
                {"ts": "2026-11-01T05:00:00+00:00", "title": "Charles restart needs a look", "level": "error"},
                {"ts": "2026-11-01T06:00:00+00:00", "title": "Charles restart checked: all clear", "level": "info"},
                {"ts": "2026-11-01T07:00:00+00:00", "title": "Charles restart needs a look", "level": "error"}]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "n.json")
            with open(path, "w") as f:
                json.dump(rows, f)
            got = wd.notices(path, now)["notices"]
        self.assertEqual([n["ts"][11:13] for n in got], ["07", "06", "04"])

    def test_notices_lose_their_emoji_and_errors_stand_out(self):
        out = wd.alert_events({}, {}, {}, [{"ts": "2026-10-20T10:00:00+00:00", "title": "🎛️ Settings changed: Picks 4 → 6", "message": "", "level": "info"},
                                           {"ts": "2026-10-20T11:00:00+00:00", "title": "Charles hit an error", "message": "Rebalance error", "level": "error"}])
        self.assertEqual(out[0]["kind"], "error")
        self.assertEqual(out[0]["href"], "/")
        self.assertEqual(out[1]["title"], "Settings changed: Picks 4 → 6")
        self.assertEqual((out[1]["kind"], out[1]["href"]), ("note", None))

    def test_a_rebalance_note_only_shows_what_its_orders_dont(self):
        note = lambda msg: {"ts": "2026-10-20T14:00:00+00:00", "title": "Rebalanced", "message": msg, "level": "info"}
        self.assertEqual(wd.alert_events({}, {}, {}, [note("Bought STX $51.38; Sold ETSY $90.32 Aggressive mode: the market is rising")]), [])
        self.assertEqual(wd.alert_events({}, {}, {}, [note("🛡️ Safety mode on: the money moves to T-bills.; Sold SSO $800")])[0]["title"], "Safety switch: into T-bills")
        self.assertEqual(wd.alert_events({}, {}, {}, [note("🚀 Aggressive mode on: calm, rising market")])[0]["title"], "Aggressive mode on")
        self.assertEqual(wd.alert_events({}, {}, {}, [note("📈 Invest mode: plain SPY core.")])[0]["title"], "Back to plain SPY")

    def test_nothing_to_report_is_an_empty_list(self):
        self.assertEqual(wd.alert_events({}, {}, {}, []), [])
        self.assertEqual(wd.alert_events({"trades": [{"symbol": "X", "action": "BUY"}]}, {}, {}, []), [])      # no time: not an event

    def test_it_stops_at_the_limit(self):
        many = {"trades": [{"timestamp": f"2026-10-{d:02d}T14:00:00+00:00", "symbol": "X", "action": "BUY", "value": 1} for d in range(1, 29)]}
        self.assertEqual(len(wd.alert_events(many, {}, {}, [], keep=5)), 5)

    def test_every_page_gets_the_bell_and_the_script(self):
        out = wd.shell(b"<html><head></head><body><header></header></body></html>", {"crypto": True, "kalshi": True}, "home").decode()
        self.assertIn('id="bell"', out)
        self.assertIn('/alerts.js', out)
        self.assertIn("/alerts.js", wd.STATIC)

    def test_the_login_covers_the_alerts(self):
        self.assertEqual(wd.gate("GET", "/api/alerts", {"access": "private", "secret": "x", "sessions": {}}, None, None), (401, "login"))

    def test_a_card_only_links_inside_the_dashboard(self):
        """The hrefs are the server's own, but the page checks anyway: a bad one in a notice shouldn't become a link."""
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "alerts.js")) as f:
            js = f.read()
        self.assertIn("safeHref", js)
        self.assertIn('/^\\/(?!\\/)/', js)

    def test_a_failed_first_look_at_kalshi_is_tried_again_soon(self):
        """It used to keep "no word from it" for two minutes, leaving Kalshi out of Home's total."""
        calls, now = [], [1000.0]

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise OSError("not answering")
            return {"betting": {}}
        self.patch(wd, "_kalshi_state", flaky)
        self.patch(wd.time, "time", lambda: now[0])
        wd._cache.pop("kalshi_state", None)
        self.addCleanup(wd._cache.pop, "kalshi_state", None)
        self.assertIsNone(wd.kalshi_for_home())
        now[0] += 5
        self.assertIsNone(wd.kalshi_for_home())
        self.assertEqual(len(calls), 1)                # not on every look
        now[0] += 11
        self.assertEqual(wd.kalshi_for_home(), {"betting": {}})


class RuleCheckTiles(BotTestCase):
    """The Self-Tune tab's grid: a change has to win every test, so a tile shows its worst one."""
    EV = {"current": {"stocks": {"all": {"cagr_pct": 18.0}, "first": {"cagr_pct": 18.0}}, "sectors": {"all": {"cagr_pct": 11.0}, "first": {"cagr_pct": 12.0}}},
          "variants": [{"setting": "PICKS", "label": "Momentum picks", "from": 4, "to": 3, "passes": False, "why": "No.",
                        "results": {"stocks": {"all": {"cagr_pct": 20.0}, "first": {"cagr_pct": 19.0}}, "sectors": {"all": {"cagr_pct": 10.5}, "first": {"cagr_pct": 12.5}}}},
                       {"setting": "CORE_PCT", "label": "Core", "from": .8, "to": .75, "passes": True, "why": "Yes."}]}

    def test_the_worst_test_against_the_current_rules(self):
        rows = wd.evaluation_tests(self.EV)
        self.assertEqual(rows[0]["worst"], -0.5)
        self.assertNotIn("results", rows[0])
        self.assertIsNone(rows[1]["worst"])

    def test_no_file_no_tiles(self):
        self.assertEqual(wd.evaluation_tests(None), [])


class SoonCache(BotTestCase):
    """_soon: a page never waits for a slow news site."""
    def test_nothing_the_first_time_then_the_answer(self):
        import threading
        release, done = threading.Event(), threading.Event()

        def slow():
            release.wait(2)
            done.set()
            return {"items": [1]}
        key = f"soon-test-{id(self)}"
        self.assertIsNone(wd._soon(key, 60, slow))          # answers at once, while the work goes on behind it
        self.assertIsNone(wd._soon(key, 60, slow))          # and a second page doesn't start a second fetch
        release.set()
        done.wait(2)
        for _ in range(100):
            if wd._soon(key, 60, slow):
                break
            threading.Event().wait(.01)
        self.assertEqual(wd._soon(key, 60, slow), {"items": [1]})


class PreviewAsNew(BotTestCase):
    def on(self, cookie, state_from):
        class Fake:
            headers = {"Cookie": cookie}
        old = wd.STATE_FROM, wd.addons.enabled
        wd.STATE_FROM, wd.addons.enabled = state_from, lambda: {"crypto": True, "kalshi": True}
        try:
            return wd.Handler._on(Fake())
        finally:
            wd.STATE_FROM, wd.addons.enabled = old

    def test_every_addon_off_only_on_the_preview_with_the_cookie(self):
        self.assertEqual(self.on("a=b; preview_new=1", "http://127.0.0.1:8502"), {"crypto": False, "kalshi": False})
        self.assertEqual(self.on("a=b", "http://127.0.0.1:8502"), {"crypto": True, "kalshi": True})
        self.assertEqual(self.on("preview_new=1", ""), {"crypto": True, "kalshi": True})        # a real install ignores it


class FirstSteps(BotTestCase):
    """Home's Get started card: only on a new install, never on one that's been running, and never for a moment when the state can't be read."""
    NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)
    TODAY = [["2026-10-31T14:00:00+00:00", 1000.0]]
    LONG_AGO = [["2026-10-01T14:00:00+00:00", 1000.0]]

    def test_no_keys_yet(self):
        self.assertEqual(wd.setup_view({"account": None, "equity": []}, False, self.NOW), {"keys": False, "plan": False})

    def test_keys_in_and_no_plan_chosen(self):
        self.assertEqual(wd.setup_view({"account": {"total_value": 1000.0}, "equity": self.TODAY}, False, self.NOW), {"keys": True, "plan": False})

    def test_a_chosen_plan_ends_it(self):
        self.assertIsNone(wd.setup_view({"account": {"total_value": 1000.0}, "equity": self.TODAY}, True, self.NOW))

    def test_a_week_of_running_ends_it(self):
        self.assertIsNone(wd.setup_view({"account": {"total_value": 1000.0}, "equity": self.LONG_AGO}, False, self.NOW))

    def test_an_old_install_whose_broker_is_down_isnt_new(self):
        self.assertIsNone(wd.setup_view({"account": None, "equity": self.LONG_AGO}, False, self.NOW))

    def test_a_state_that_cant_be_read_isnt_new(self):
        self.assertIsNone(wd.setup_view({}, False, self.NOW))

    def test_the_header_says_not_started_only_without_keys(self):
        self.patch(wd, "_cached", lambda key, ttl, fn: {"bot_running": True, "account": None, "equity": []})
        self.assertTrue(wd.status_view()["waiting"])
        self.patch(wd, "_cached", lambda key, ttl, fn: {"bot_running": True, "account": {"total_value": 1.0}, "equity": []})
        self.assertFalse(wd.status_view()["waiting"])
        self.patch(wd, "_cached", lambda key, ttl, fn: None)                         # can't read it: "…", not "Not started"
        self.assertFalse(wd.status_view()["waiting"])

    def test_the_preview_as_a_new_install_has_nothing_of_the_account(self):
        st = {"account": {"total_value": 9.0}, "positions": [{"symbol": "SPY"}], "equity": [[1, 2]], "trades": [1], "plan": {"mode": "invest"}, "names": {"SPY": "S&P"},
              "backtest": {"x": 1}, "today": {"halted": True, "limit": 20}, "claude": {"report": {"a": 1}}, "evaluation": {"a": 1}}
        new = wd.new_install_state(st)
        for k in ("account", "plan", "evaluation"):
            self.assertIsNone(new[k])
        for k in ("positions", "equity", "trades"):
            self.assertEqual(new[k], [])
        self.assertFalse(new["today"]["halted"])
        self.assertIsNone(new["claude"]["report"])
        self.assertEqual((new["names"], new["backtest"]), (st["names"], st["backtest"]))   # what ships with the code stays
        self.assertEqual(st["account"], {"total_value": 9.0})                                # and the cached state isn't touched


class FeedRoutes(BotTestCase):
    """GET /api/feed: what each page's feed gets."""
    STOCKS = {"trades": [{"timestamp": "2026-10-30T14:00:00+00:00", "symbol": "SPY", "action": "BUY", "value": 10.0}]}
    NEWS = {"items": [{"symbol": "SPY", "title": "SPY news", "link": "", "source": "", "published": "2026-10-31T10:00:00+00:00"}]}

    def state(self, news):
        self.patch(wd, "notices", lambda: {"notices": []})
        self.patch(wd, "_stocks_state", lambda: self.STOCKS)
        self.patch(wd, "_soon", lambda key, ttl, fn: news)
        wd._cache.pop("state", None)

    def test_a_bots_feed_is_its_posts_and_the_news(self):
        self.state(self.NEWS)
        v = wd.feed_state("stocks", {"crypto": False, "kalshi": False})
        self.assertEqual([p["who"] for p in v["posts"]], ["news", "charles"])
        self.assertFalse(v["waiting"])
        self.assertIn("not advice", v["note"])

    def test_the_page_doesnt_wait_for_the_news(self):
        self.state(None)
        v = wd.feed_state("stocks", {"crypto": False, "kalshi": False})
        self.assertEqual([p["who"] for p in v["posts"]], ["charles"])
        self.assertTrue(v["waiting"])

    def test_a_bot_thats_off_or_unknown_has_no_feed(self):
        self.state(self.NEWS)
        for bot in ("crypto", "kalshi", "nonsense"):
            self.assertEqual(wd.feed_state(bot, {"crypto": False, "kalshi": False}), {"posts": []})

    def test_findings_for_a_bot_thats_off_are_empty(self):
        self.state(None)
        self.assertEqual(wd.findings_state("kalshi", {"crypto": False, "kalshi": False})["items"], [])
        self.assertEqual(wd.findings_state("nonsense", {"crypto": False, "kalshi": False})["items"], [])

    def test_a_stocks_line_is_dates_and_closes(self):
        import requests

        class Reply:
            def raise_for_status(self):
                pass

            def json(self):
                return {"chart": {"result": [{"timestamp": [1793367000, 1793453400], "indicators": {"quote": [{"close": [644.5812, None]}]}}]}}
        self.patch(requests, "get", lambda *a, **k: Reply())
        self.patch(wd, "_soon", lambda key, ttl, fn: fn())
        rows = wd._stock_line("STX")
        self.assertEqual(len(rows), 1)                      # a day with no close is left out
        self.assertEqual(rows[0][1], 644.58)
        self.assertRegex(rows[0][0], r"^\d{4}-\d\d-\d\d$")


class FeedLogin(BotTestCase):
    def test_the_new_addresses_are_behind_the_login(self):
        private = {"access": "private", "secret": "s" * 32}
        for path in ("/api/feed", "/api/findings"):
            stop = wd.gate("GET", path, private, None, None)
            self.assertIsNotNone(stop, path)
            self.assertEqual(stop[0], 401, path)


def css_rules(css: str) -> dict:
    """{(media, selector): {property: value}}: enough to compare two copies of a stylesheet."""
    out = {}

    def walk(text, media):
        i = 0
        while True:
            head = re.search(r"([^{}]+)\{", text[i:])
            if not head:
                return
            start = j = i + head.end()
            depth = 1
            while depth:
                depth += {"{": 1, "}": -1}.get(text[j], 0)
                j += 1
            selector = " ".join(head.group(1).split())
            if selector.startswith("@"):
                walk(text[start:j - 1], selector)
            else:
                for line in text[start:j - 1].split(";"):
                    if ":" in line:
                        name, value = line.split(":", 1)
                        out.setdefault((media, selector), {})[name.strip()] = " ".join(value.split())
            i = j
    walk(re.sub(r"/\*.*?\*/", "", css, flags=re.S), "")
    return out


class PagesOpenOnOverview(BotTestCase):
    """Owner 2026-10-31: tapping Stocks, Crypto or Kalshi always opens Overview, and the tab bar has no scrollbar arrows."""
    def read(self, *rel):
        for r in rel:
            path = os.path.join(wd.ROOT, r)
            if os.path.exists(path):
                with open(path) as f:
                    return f.read()
        self.fail(f"none of {rel}")

    def test_no_page_remembers_its_tab(self):
        for page in (self.read("web/stocks.html"), self.read("web/crypto.html"), self.read("../kalshi-next/web/index.html", "kalshi/web/index.html")):
            self.assertNotRegex(page, r"(stocks|crypto|kn)-tab")
            self.assertIn('tab = "home"', page)

    def test_a_link_can_ask_for_a_tab_only_once(self):
        for page in (self.read("web/stocks.html"), self.read("web/crypto.html"), self.read("../kalshi-next/web/index.html", "kalshi/web/index.html")):
            self.assertIn("location.hash.slice(1)", page)
            self.assertIn("history.replaceState", page)

    def test_the_tab_bar_cannot_grow_a_scrollbar(self):
        import re
        for css in (self.read("web/style.css"), self.read("../kalshi-next/web/index.html", "kalshi/web/index.html")):
            rule = re.search(r"\.seg \{[^}]*\}", css).group(0)
            self.assertNotIn("overflow", rule)
            self.assertIn("::-webkit-scrollbar-button { display: none; }", css)


class KalshiKeepsTheLook(BotTestCase):
    """The Kalshi page keeps a copy of web/style.css's rules, because it must also work alone on 8503. Where both have a rule it must
    say the same thing, or the bots stop looking like one product (owner 2026-10-31). Page-only rules can differ: those have a page-only name."""

    def page(self):
        for rel in ("../kalshi-next/web/index.html", "kalshi/web/index.html"):       # the preview's own copy first: kalshi/ catches up at deploy
            path = os.path.join(wd.ROOT, rel)
            if os.path.exists(path):
                with open(path) as f:
                    return f.read()
        self.skipTest("no Kalshi page here")

    def test_a_rule_in_both_says_the_same_thing(self):
        with open(os.path.join(wd.ROOT, "web/style.css")) as f:
            shared = css_rules(f.read())
        kalshi = css_rules("".join(re.findall(r"<style[^>]*>(.*?)</style>", self.page(), flags=re.S)))
        common = set(shared) & set(kalshi)
        self.assertGreater(len(common), 100)                                         # the check is really reading both
        apart = {f"{key[1]} {name}": (shared[key][name], kalshi[key][name])
                 for key in common for name in shared[key] if name in kalshi[key] and shared[key][name] != kalshi[key][name]}
        self.assertEqual(apart, {})

    def test_the_two_copies_use_the_same_colors(self):
        with open(os.path.join(wd.ROOT, "web/style.css")) as f:
            shared = css_rules(f.read())
        kalshi = css_rules("".join(re.findall(r"<style[^>]*>(.*?)</style>", self.page(), flags=re.S)))
        for key in [("", ":root"), ("", ':root[data-theme="dark"]')]:
            for name in ("--bg", "--surface", "--text", "--good", "--critical", "--display"):
                if name in shared[key]:
                    self.assertEqual(kalshi[key].get(name), shared[key][name], (key, name))
