"""Leaderboard: the strict config check, signatures, submit/withdraw rules, the board's ranking, and charles-copy's checks."""
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone

from base import BotTestCase

import crypto_settings
from leaderboard import client, common, server, sim

NOW = datetime(2026, 10, 27, 20, 0, tzinfo=timezone.utc)


def signed(key, **fields):
    p = {"v": 1, "pubkey": common.public_b64(key), "ts": int(NOW.timestamp()), **fields}
    return {"payload": p, "sig": common.sign(key, p)}


def sub(key, settings=None, name=None, bot="stocks"):
    return signed(key, action="submit", bot=bot, settings=settings or {"CORE_PCT": 0.8, "PICKS": 4}, name=name)


class Schema(BotTestCase):
    def test_accepts_known_settings_in_range(self):
        s = common.validate_settings("stocks", {"CORE_PCT": 0.7, "PICKS": 4, "PICK_PCT": 0.075, "AGG_CORE": "UPRO", "AGGRESSIVE": False})
        self.assertEqual(s["PICKS"], 4)

    def test_rejects_rather_than_clamps(self):
        for bad in ({"CORE_PCT": 0.99}, {"NOPE": 1}, {"PICKS": 2.5}, {"PICKS": True}, {"AGG_CORE": "TQQQ"},
                    {"AGGRESSIVE": 1}, {"CORE_PCT": "0.8"}, {"CORE_PCT": 0.9, "PICKS": 4, "PICK_PCT": 0.05}, {}, []):
            with self.assertRaises(ValueError, msg=bad):
                common.validate_settings("stocks", bad)
        with self.assertRaises(ValueError):
            common.validate_settings("crypto", {"BTC_PCT": 0.7, "ETH_PCT": 0.5})
        with self.assertRaises(ValueError):
            common.validate_settings("kalshi", {"X": 1})

    def test_names(self):
        self.assertIsNone(common.check_name(""))
        self.assertEqual(common.check_name("safe_sally"), "safe_sally")
        for bad in ("ab", "<script>", "a" * 21, "Charles_Official", "admin1", "-dash", 7):
            with self.assertRaises(ValueError, msg=bad):
                common.check_name(bad)

    def test_innocent_words_that_contain_a_blocked_one(self):
        for ok in ("grapes", "skyscraper", "therapist_tom", "Scunthorpe_fc", "fire_retardant", "Ashkenazi_k"):
            self.assertEqual(common.check_name(ok), ok)
        for bad in ("grapesrape", "rape_grapes", "Retard"):                 # the word itself still fails
            with self.assertRaises(ValueError, msg=bad):
                common.check_name(bad)

    def test_crypto_flat_keys_round_trip(self):
        plan = crypto_settings.to_plan({"BTC_PCT": 0.7, "ETH_PCT": 0.3, "TREND_DAYS": 100})
        self.assertEqual(plan["WEIGHTS"], {"BTC-USD": 0.7, "ETH-USD": 0.3})
        self.assertEqual(crypto_settings.flat({**plan, "CHECK_DAYS": 7})["BTC_PCT"], 0.7)


class Signing(BotTestCase):
    def test_signature_covers_every_field(self):
        k = common.new_key()
        body = sub(k)
        self.assertTrue(common.verify(body["payload"]["pubkey"], body["payload"], body["sig"]))
        body["payload"]["settings"]["CORE_PCT"] = 0.7
        self.assertFalse(common.verify(body["payload"]["pubkey"], body["payload"], body["sig"]))

    def test_check_signed_refusals(self):
        k = common.new_key()
        fields = {"bot", "settings", "name"}
        good = sub(k)
        self.assertEqual(server.check_signed(good, "submit", fields, NOW.timestamp())["bot"], "stocks")
        with self.assertRaises(server.Reject):            # someone else's key
            other = sub(common.new_key())
            server.check_signed({"payload": {**other["payload"], "pubkey": good["payload"]["pubkey"]}, "sig": other["sig"]},
                                "submit", fields, NOW.timestamp())
        with self.assertRaises(server.Reject):            # replayed later
            server.check_signed(good, "submit", fields, NOW.timestamp() + 600)
        with self.assertRaises(server.Reject):            # an extra field
            p = {**good["payload"], "results": {"return_pct": 900}}
            server.check_signed({"payload": p, "sig": common.sign(k, p)}, "submit", fields, NOW.timestamp())
        with self.assertRaises(server.Reject):            # wrong action
            server.check_signed(good, "withdraw", {"id"}, NOW.timestamp())


class Writes(BotTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        self.db = sim.connect(os.path.join(self.tmp, "lb.db"))

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def test_submit_starts_tomorrow_and_resubmit_retires_the_old_entry(self):
        k = common.new_key()
        a = server.submit(self.db, sub(k), NOW)
        self.assertEqual(a["start_day"], "2026-10-28")
        self.assertEqual(server.submit(self.db, sub(k), NOW)["id"], a["id"])          # same config: same entry
        b = server.submit(self.db, sub(k, {"CORE_PCT": 0.7, "PICKS": 4}), NOW)
        self.assertEqual(b["replaced"], [a["id"]])
        rows = {r["id"]: r["status"] for r in self.db.execute("SELECT id, status FROM entries")}
        self.assertEqual(rows, {a["id"]: "retired", b["id"]: "active"})

    def test_duplicate_config_and_taken_name(self):
        server.submit(self.db, sub(common.new_key(), name="trendrider"), NOW)
        with self.assertRaises(server.Reject) as e:
            server.submit(self.db, sub(common.new_key()), NOW)
        self.assertEqual(e.exception.code, 409)
        with self.assertRaises(server.Reject):
            server.submit(self.db, sub(common.new_key(), {"CORE_PCT": 0.6}, name="TrendRider"), NOW)

    def test_only_the_owner_can_withdraw(self):
        k = common.new_key()
        eid = server.submit(self.db, sub(k), NOW)["id"]
        with self.assertRaises(server.Reject):
            server.withdraw(self.db, signed(common.new_key(), action="withdraw", id=eid), NOW)
        server.withdraw(self.db, signed(k, action="withdraw", id=eid), NOW)
        self.assertEqual(self.db.execute("SELECT status FROM entries").fetchone()[0], "retired")


def row(eid, curve, status="active", name=None, start="2026-09-01", retired=None):
    return {"id": eid, "bot": "stocks", "pubkey": "k" + eid, "name": name, "settings": "{}", "submitted": start + "T00:00:00",
            "start_day": start, "status": status, "retired": retired, "demo": 0, "copies": 0, "curve": json.dumps(curve)}


class Board(BotTestCase):
    def test_window_return_uses_the_value_before_the_window(self):
        curve = [["2026-10-01", 10000], ["2026-10-20", 11000], ["2026-10-27", 12100]]
        st = server.window_stats(curve, "2026-10-21", sim.START_CASH)
        self.assertEqual(st["return_pct"], 10.0)
        self.assertEqual(server.window_stats(curve, None, sim.START_CASH)["return_pct"], 21.0)

    def test_ranked_warming_and_retired(self):
        up = [[f"2026-10-{d:02d}", 10000 + d * 50] for d in range(1, 28)]
        down = [[f"2026-10-{d:02d}", 10000 - d * 20] for d in range(1, 28)]
        rows = [row("a", down, name="slow"), row("b", up), row("c", up[:5], start="2026-10-23"),
                row("d", up[:10], status="retired", retired="2026-10-11T00:00:00")]
        b = server.board(rows, [[d, 100 + i] for i, (d, _) in enumerate(up)], "week", "2026-10-27")
        self.assertEqual([e["id"] for e in b["ranked"]], ["b", "a"])
        self.assertEqual(b["ranked"][0]["rank"], 1)
        self.assertTrue(b["ranked"][0]["name"].startswith("Anonymous · "))
        self.assertEqual([e["id"] for e in b["warming"]], ["c"])
        self.assertEqual([e["id"] for e in b["retired"]], ["d"])
        self.assertGreater(b["bench_pct"], 0)


class Copy(BotTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        self.key = common.new_key()
        self.pub = common.public_b64(self.key)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def manifest(self, settings=None, key=None, **over):
        settings = settings or {"CORE_PCT": 0.7, "PICKS": 4, "PICK_PCT": 0.075}
        p = {"v": 1, "id": "lb-abc123", "bot": "stocks", "settings": settings, "hash": common.config_hash("stocks", settings),
             "name": "trendrider", "issued": "2026-10-27T20:00:00+00:00", **over}
        return {"payload": p, "sig": common.sign(key or self.key, p)}

    def fetch(self, m, eid="lb-abc123"):
        orig = client._get
        client._get = lambda url: m
        try:
            return client.fetch(eid, url="http://x", pubkey=self.pub)
        finally:
            client._get = orig

    def test_good_config_is_accepted(self):
        self.assertEqual(self.fetch(self.manifest())["settings"]["CORE_PCT"], 0.7)

    def test_refuses_wrong_key_tampering_swaps_and_bad_values(self):
        with self.assertRaises(ValueError):
            self.fetch(self.manifest(key=common.new_key()))            # not signed by the leaderboard
        m = self.manifest()
        m["payload"]["settings"]["CORE_PCT"] = 0.95                    # changed after signing
        with self.assertRaises(ValueError):
            self.fetch(m)
        with self.assertRaises(ValueError):
            self.fetch(self.manifest(id="lb-999999"))                  # a different config than asked for
        with self.assertRaises(ValueError):
            self.fetch(self.manifest({"CORE_PCT": 0.7, "CMD": "rm -rf /"}))   # signed, but not a valid config
        with self.assertRaises(ValueError):
            self.fetch(self.manifest(hash="0" * 64))
        with self.assertRaises(ValueError):
            self.fetch(self.manifest(), eid="../../etc/passwd")

    def test_install_backs_up_and_writes(self):
        path = os.path.join(self.tmp, "strategy.json")
        with open(path, "w") as f:
            json.dump({"settings": {"CORE_PCT": 0.8}}, f)
        cfg = {**self.manifest()["payload"]}
        backup = client.install(cfg, self.tmp, NOW)
        with open(backup) as f:
            self.assertEqual(json.load(f)["settings"]["CORE_PCT"], 0.8)
        with open(path) as f:
            new = json.load(f)
        self.assertEqual(new["settings"]["CORE_PCT"], 0.7)
        self.assertIn("lb-abc123", new["last_change"])
        self.assertEqual(client.current_settings("stocks", self.tmp)["PICK_PCT"], 0.075)

    def test_identity_is_made_once_and_private(self):
        k1 = client.identity(self.tmp)
        k2 = client.identity(self.tmp)
        self.assertEqual(common.public_b64(k1), common.public_b64(k2))
        self.assertEqual(os.stat(os.path.join(self.tmp, "leaderboard", "identity.pem")).st_mode & 0o777, 0o600)


class Simulation(BotTestCase):
    def test_warmup_covers_the_longest_trend_line_allowed(self):
        # The oldest entry gets WARMUP_DAYS calendar days of history before its start. With fewer trading
        # days than TREND_DAYS, its safety switch has no average yet and it starts in T-bills for a month.
        from datetime import date, timedelta
        import strategy_settings
        need = max(strategy_settings.LIMITS["TREND_DAYS"][1], 252 + 21) + 1
        weekdays = sum((date(2026, 1, 1) + timedelta(days=k)).weekday() < 5 for k in range(sim.WARMUP_DAYS))
        trading = weekdays - 10 * sim.WARMUP_DAYS / 365            # about 10 exchange holidays a year
        self.assertGreaterEqual(trading, need)


class HouseBots(BotTestCase):
    """leaderboard/house.py: labelled entries the board runs itself, which step aside for a real person."""
    def setUp(self):
        super().setUp()
        from leaderboard import house
        self.house = house
        self.tmp = tempfile.mkdtemp()
        self.db = sim.connect(os.path.join(self.tmp, "lb.db"))

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def rows(self, where="1"):
        return self.db.execute(f"SELECT * FROM entries WHERE {where} ORDER BY bot, pubkey").fetchall()

    def test_every_preset_is_added_once_with_valid_settings_and_a_name_that_says_what_it_is(self):
        added = self.house.ensure(self.db, NOW)
        self.assertEqual(len(added), sum(len(v) for v in self.house.HOUSE.values()))
        for r in self.rows():
            self.assertTrue(r["name"].startswith("House bot: "), r["name"])
            self.assertTrue(self.house.is_house(r["pubkey"]))
            self.assertEqual(json.loads(r["settings"]), common.validate_settings(r["bot"], json.loads(r["settings"])))
            self.assertEqual((r["status"], r["demo"], r["start_day"]), ("active", 0, "2026-10-28"))
        self.assertEqual(len({(r["bot"], r["hash"]) for r in self.rows()}), len(added))      # no two the same
        self.assertEqual(self.house.ensure(self.db, NOW), [])

    def test_a_preset_someone_already_shares_is_left_to_them(self):
        mine = server.submit(self.db, sub(common.new_key(), self.house.defaults("stocks"), name="first"), NOW)["id"]
        self.house.ensure(self.db, NOW)
        self.assertEqual([r["id"] for r in self.rows("bot='stocks' AND pubkey NOT LIKE 'house:%'")], [mine])
        self.assertEqual(self.rows("pubkey='house:stocks:default'"), [])

    def test_a_person_with_the_same_settings_takes_the_house_bots_place_for_good(self):
        self.house.ensure(self.db, NOW)
        steady = {**self.house.defaults("stocks"), "AGGRESSIVE": False}
        res = server.submit(self.db, sub(common.new_key(), steady, name="realperson"), NOW)       # no 409: the house bot isn't "someone"
        self.assertTrue(res["ok"])
        h = self.rows("pubkey='house:stocks:steady'")[0]
        self.assertEqual(h["status"], "retired")
        self.assertEqual(self.house.ensure(self.db, NOW), [])                                   # and it doesn't come back
        with self.assertRaises(server.Reject):                                                  # between people the rule is unchanged
            server.submit(self.db, sub(common.new_key(), steady), NOW)

    def test_nobody_can_withdraw_or_pose_as_one(self):
        self.house.ensure(self.db, NOW)
        eid = self.rows("pubkey='house:stocks:steady'")[0]["id"]
        with self.assertRaises(server.Reject):
            server.withdraw(self.db, signed(common.new_key(), action="withdraw", id=eid), NOW)
        for name in ("HouseBot", "house_bot_2", "House-Bot"):
            with self.assertRaises(ValueError):
                common.check_name(name)
        self.assertEqual(common.check_name("lighthouse"), "lighthouse")

    def test_the_board_flags_them(self):
        up = [[f"2026-10-{d:02d}", 10000 + d * 50] for d in range(1, 28)]
        b = server.board([{**row("a", up, name="House bot: Steady"), "pubkey": "house:stocks:steady"}, row("b", up, name="person")],
                         [[d, 100] for d, _ in up], "all", "2026-10-27")
        self.assertEqual({e["name"]: e["house"] for e in b["ranked"]}, {"House bot: Steady": True, "person": False})


class WhyCounts(BotTestCase):
    """The website's one-tap "What stopped you?": totals only, known answers only, three a day per address."""
    def setUp(self):
        super().setUp()
        import sqlite3
        from leaderboard import why
        self.why = why
        self.db = sqlite3.connect(os.path.join(self.tmp, "lb.db"))

    def test_counts_and_nothing_else(self):
        from datetime import datetime, timezone
        now = datetime(2026, 11, 13, 18, 0, tzinfo=timezone.utc)
        for r in ("tech", "tech", "price"):
            self.why.record(self.db, {"page": "guide", "reason": r}, now)
        self.why.record(self.db, {"page": "paid", "reason": "trust"}, now)
        self.assertEqual(self.why.totals(self.db), {"2026-11-13": {"guide": {"tech": 2, "price": 1}, "paid": {"trust": 1}}})
        cols = [c[1] for c in self.db.execute("PRAGMA table_info(why_counts)")]
        self.assertEqual(cols, ["day", "page", "reason", "n"])

    def test_unknown_answers_refused(self):
        for body in ({"page": "guide", "reason": "<script>"}, {"page": "x", "reason": "tech"}, [], {"page": "guide"}):
            with self.assertRaises(self.why.Bad):
                self.why.record(self.db, body)

    def test_over_http_with_the_limit(self):
        import json as _json
        import threading
        import urllib.error
        import urllib.request
        from http.server import ThreadingHTTPServer
        from leaderboard import server
        server._hits.clear()
        H = type("H", (server.Handler,), {"db_path": os.path.join(self.tmp, "http.db")})
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_address[1]}/api/why"

        def post(body):
            req = urllib.request.Request(url, data=_json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            try:
                return urllib.request.urlopen(req, timeout=5).status
            except urllib.error.HTTPError as e:
                return e.code
        try:
            self.assertEqual(post({"page": "paid", "reason": "nope"}), 400)
            self.assertEqual([post({"page": "guide", "reason": "later"}) for _ in range(3)], [200, 200, 429])   # 3 a day, the refused one too
        finally:
            srv.shutdown()
            server._hits.clear()


class WatchFeed(BotTestCase):
    """The website's "Watch Charles trade": a public cut of this server's Fetch, never its results."""
    def live(self, **kw):
        d = {"product": "BTC-USD", "now": 1000.0, "price": 70000.0, "bid": 69999.0, "ask": 70001.0, "bars": [[t, 1, 2, 0, 1, 5] for t in range(200)],
             "tape": [{"t": 999.0, "p": 70000.0, "q": 0.1, "side": "buy", "extra": 1}] * 40, "book": {"pos": None, "cash": 1012.0, "value": 1012.0},
             "pnl": {"total": 12.0}, "events": [{"kind": "sell", "ts": 900.0, "price": 70100.0, "pnl": 12.0, "say": "Sold for a gain of $12.00."}],
             "watching": "The trend is up. I am waiting for a dip of 0.2% more.", "watch": {"numbers": {"level": 69800.0}}}
        d.update(kw)
        return d

    def test_no_results_ever(self):
        from leaderboard import watch
        v = watch.public_view(self.live(), 1001.0)
        text = json.dumps(v)
        for leak in ("pnl", "cash", "value", "gain", "12.0", "say"):
            self.assertNotIn(leak, text)
        self.assertEqual((len(v["bars"]), len(v["tape"]), v["moves"]), (90, 30, [{"kind": "sell", "ts": 900.0, "price": 70100.0}]))
        self.assertEqual(v["watching"], "The trend is up. I am waiting for a dip of 0.2% more.")

    def test_holding_hides_the_open_trade(self):
        from leaderboard import watch
        v = watch.public_view(self.live(book={"pos": {"qty": 1}}, watching="Up 0.4% on this trade. Target 70,300."), 1001.0)
        self.assertEqual((v["holding"], v["watching"]), (True, "Charles is holding bitcoin and waiting for his target or his stop."))

    def test_stale_or_missing_is_off(self):
        from leaderboard import watch
        self.assertIsNone(watch.public_view(self.live(), 1000.0 + watch.STALE + 1))
        self.assertIsNone(watch.public_view({}, 1000.0))
        watch._cache["at"] = 0
        self.assertIsNone(watch.current(os.path.join(self.tmp, "none.json"), 5.0))
