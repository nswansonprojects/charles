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
