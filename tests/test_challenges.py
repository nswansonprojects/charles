"""Challenges: rules are enforced, one entry per key, limits per address, a ranking that discounts luck, open data with no names."""
import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone

from base import BotTestCase

from leaderboard import challenges, common, server, sim

NOW = datetime(2026, 10, 30, 20, 0, tzinfo=timezone.utc)      # entries are open for November (Two picks only)
PLAN = {"CORE_PCT": 0.8, "PICKS": 2, "PICK_PCT": 0.05, "AGGRESSIVE": False}


def signed(key, ts=NOW, **fields):
    p = {"v": 1, "pubkey": common.public_b64(key), "ts": int(ts.timestamp()), **fields}
    return {"payload": p, "sig": common.sign(key, p)}


def entry(key, settings=None, name=None, challenge="2026-11", ts=NOW):
    return signed(key, ts, action="enter", challenge=challenge, settings=settings or PLAN, name=name)


class Base(BotTestCase):
    def setUp(self):
        super().setUp()
        self.db = sim.connect(os.path.join(self.tmp, "lb.db"))
        challenges.ensure(self.db)
        challenges.ensure_months(self.db, NOW)

    def enter(self, key=None, addr="1.1.1.1", now=NOW, **kw):
        return challenges.enter(self.db, entry(key or common.new_key(), ts=now, **kw), addr, now)


class Calendar(Base):
    def test_the_rotation_and_bounds(self):
        self.assertEqual(challenges.theme_for("2026-11")["key"], "two-picks")
        self.assertEqual(challenges.theme_for("2026-12")["key"], "no-leverage")
        self.assertEqual(challenges.theme_for("2027-03")["key"], "one-sector")
        self.assertEqual(challenges.theme_for("2027-04")["key"], "two-picks")
        self.assertEqual(challenges.month_bounds("2026-12"), ("2026-12-01", "2026-12-31"))
        self.assertEqual(challenges.next_month("2026-12"), "2027-01")

    def test_the_page_shows_next_month_open(self):
        v = challenges.view(self.db, NOW)
        self.assertEqual(v["open"]["id"], "2026-11")
        self.assertEqual(v["running"]["id"], "2026-10")
        self.assertEqual(v["finished"], [])


class Enter(Base):
    def test_the_rules_are_enforced(self):
        for bad in ({**PLAN, "PICKS": 3}, {"CORE_PCT": 0.8}):
            with self.assertRaises(server.Reject) as e:
                self.enter(settings=bad)
            self.assertIn("rules", e.exception.message)
        self.assertTrue(self.enter()["ok"])

    def test_results_and_junk_are_refused(self):
        with self.assertRaises(server.Reject):
            self.enter(settings={**PLAN, "MAX_DAILY_LOSS": 1})
        key = common.new_key()
        body = entry(key)
        body["payload"]["return_pct"] = 500
        with self.assertRaises(server.Reject):
            challenges.enter(self.db, body, "1.1.1.1", NOW)

    def test_a_bad_signature_is_refused(self):
        body = entry(common.new_key())
        body["payload"]["settings"] = {**PLAN, "PICKS": 1}
        with self.assertRaises(server.Reject) as e:
            challenges.enter(self.db, body, "1.1.1.1", NOW)
        self.assertEqual(e.exception.code, 403)

    def test_one_entry_per_key(self):
        key = common.new_key()
        first = self.enter(key)
        again = self.enter(key, settings={**PLAN, "PICKS": 1})
        self.assertEqual(first["id"], again["id"])
        self.assertTrue(again["replaced"])
        rows = self.db.execute("SELECT * FROM challenge_entries").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0]["settings"])["PICKS"], 1)

    def test_at_most_three_per_address(self):
        for _ in range(3):
            self.enter(addr="9.9.9.9")
        with self.assertRaises(server.Reject) as e:
            self.enter(addr="9.9.9.9")
        self.assertEqual(e.exception.code, 429)
        self.assertTrue(self.enter(addr="8.8.8.8")["ok"])

    def test_entries_close_when_the_month_starts(self):
        late = datetime(2026, 11, 1, 0, 5, tzinfo=timezone.utc)
        with self.assertRaises(server.Reject) as e:
            self.enter(now=late)
        self.assertEqual(e.exception.code, 409)

    def test_names_stay_with_their_owner(self):
        self.enter(name="trendrider")
        with self.assertRaises(server.Reject):
            self.enter(name="TrendRider")

    def test_fit_rules_only_changes_what_the_rules_need(self):
        s = challenges.fit_rules({"PICKS": 4, "CORE_PCT": 0.8}, {"max": {"PICKS": 2}, "fixed": {"AGGRESSIVE": False}})
        self.assertEqual(s, {"PICKS": 2, "CORE_PCT": 0.8, "AGGRESSIVE": False})
        self.assertEqual(challenges.check_rules(s, {"max": {"PICKS": 2}, "fixed": {"AGGRESSIVE": False}}), [])

    def test_withdraw_only_before_the_start(self):
        key = common.new_key()
        self.enter(key)
        late = datetime(2026, 11, 2, tzinfo=timezone.utc)
        with self.assertRaises(server.Reject):
            challenges.withdraw(self.db, signed(key, late, action="withdraw_entry", challenge="2026-11"), late)
        self.assertTrue(challenges.withdraw(self.db, signed(key, NOW, action="withdraw_entry", challenge="2026-11"), NOW)["ok"])
        self.assertEqual(self.db.execute("SELECT count(*) FROM challenge_entries").fetchone()[0], 0)
        with self.assertRaises(server.Reject) as e:       # nothing left to withdraw
            challenges.withdraw(self.db, signed(key, NOW, action="withdraw_entry", challenge="2026-11"), NOW)
        self.assertEqual(e.exception.code, 404)


def month_prices(month="2026-10"):
    """Two weeks of made-up closes: AAA doubles, BBB halves, the rest are flat."""
    days = [f"{month}-{d:02d}" for d in range(1, 15)]
    n = len(days)
    grow = [100 * (1 + i / (n - 1)) for i in range(n)]
    fall = [100 * (1 - 0.5 * i / (n - 1)) for i in range(n)]
    flat = [100.0] * n
    import allocator
    names = list(allocator.UNIVERSE)
    series = {k: list(flat) for k in names}
    series[names[0]], series[names[1]] = grow, fall
    series.update(SPY=list(flat), BIL=list(flat), SSO=list(flat))
    return days, series


class Referee(Base):
    def setUp(self):
        super().setUp()
        # Rules of its own: month_prices() moves two stocks that a sector theme might leave out
        self.db.execute("UPDATE challenges SET rules='{\"max\": {\"PICKS\": 8}}' WHERE id='2026-10'")
        self.c = self.db.execute("SELECT * FROM challenges WHERE id='2026-10'").fetchone()
        self.days, self.series = month_prices()

    def test_stats(self):
        self.assertEqual(challenges.month_stats([["a", 10000], ["b", 11000], ["c", 9900]]), {"return_pct": -1.0, "max_drop_pct": -10.0, "days": 3})
        self.assertIsNone(challenges.month_stats([]))

    def test_the_random_portfolios_are_repeatable_and_keep_the_rules(self):
        a = challenges.random_scores(self.c, self.days, self.series, n=50)
        self.assertEqual(a, challenges.random_scores(self.c, self.days, self.series, n=50))
        self.assertEqual(len(a[0]), 50)
        import numpy as np
        rng = np.random.default_rng(1)
        for _ in range(100):
            w = challenges.random_weights("stocks", {"max": {"PICKS": 2}, "fixed": {"AGGRESSIVE": False}}, rng, set(self.series))
            self.assertNotIn("SSO", w)
            self.assertLessEqual(len(w), 3)
            self.assertLessEqual(sum(w.values()), 1.0 + 1e-9)
            w = challenges.random_weights("crypto", {}, rng, {"BTC-USD", "ETH-USD"})
            self.assertLessEqual(sum(w.values()), 1.0 + 1e-9)

    def test_a_one_sector_challenge_only_picks_inside_the_sector(self):
        import allocator
        import numpy as np
        rules = next(t for t in challenges.THEMES if t["key"] == "one-sector")["rules"]
        self.assertTrue(set(rules["universe"]) <= set(allocator.UNIVERSE))
        rng = np.random.default_rng(1)
        seen = set()
        for _ in range(200):
            w = challenges.random_weights("stocks", rules, rng, {"SPY", "SSO", "AAPL", "NVDA", *rules["universe"]})
            seen |= set(w) - {"SPY", "SSO"}
        self.assertTrue(seen)
        self.assertTrue(seen <= set(rules["universe"]))

    def test_settling_runs_the_entry_on_the_challenges_sector(self):
        c = dict(self.c)
        c["rules"] = json.dumps({"universe": ["LLY", "PFE"]})
        self.add("x", 1)
        seen = {}

        def run(settings, start, prices, end, universe=None):
            seen["universe"] = universe
            return [["2026-10-01", 10000.0], ["2026-10-14", 10100.0]]
        challenges.settle(self.db, c, self.days, self.series, run, None)
        self.assertEqual(seen["universe"], ["LLY", "PFE"])

    def test_the_sim_passes_the_sector_on(self):
        import backtest_core
        seen = {}

        def simulate(dates, opens, closes, s, start_cash, start, universe):
            seen["universe"] = universe
            return {"dates": dates, "values": [start_cash] * len(dates)}
        self.patch(backtest_core, "simulate", simulate)
        days = ["2026-10-01"]
        closes = {"LLY": [1.0], "PFE": [1.0], "AAPL": [1.0], "SPY": [1.0]}
        sim.run_stocks({}, "2026-10-01", (days, closes, closes), universe=["LLY", "NOT-PRICED"])
        self.assertEqual(seen["universe"], ["LLY"])
        sim.run_stocks({}, "2026-10-01", (days, closes, closes))
        self.assertEqual(set(seen["universe"]), {"LLY", "PFE", "AAPL"})

    def test_beat_share(self):
        self.assertEqual(challenges.beat_share(5, [1, 2, 3, 9]), 75.0)
        self.assertEqual(challenges.beat_share(0, []), 0.0)
        self.assertEqual(challenges.beat_share(-2, [-10, -5, -1, -3]), 75.0)      # a shallower fall beats a deeper one

    def add(self, name, value):
        key = common.new_key()
        self.db.execute("INSERT INTO challenge_entries (id, challenge, pubkey, name, settings, hash, submitted) VALUES (?,?,?,?,?,?,?)",
                        (f"ch-{name}", "2026-10", common.public_b64(key), name, json.dumps({"V": value}), name, "2026-09-20T00:00:00+00:00"))

    def settle(self):
        def run(settings, start, prices, end, universe=None):
            return [["2026-10-01", 10000.0], ["2026-10-14", 10000.0 * (1 + settings["V"] / 100)]]
        challenges.settle(self.db, self.c, self.days, self.series, run, None)
        self.db.commit()

    def test_settling_ranks_by_the_share_beaten(self):
        self.add("loser", -60)
        self.add("winner", 60)
        self.add("middle", 1)
        self.settle()
        rows = challenges._standing(self.c, self.db.execute("SELECT * FROM challenge_entries").fetchall(), {}, NOW)
        self.assertEqual([r["name"] for r in rows], ["winner", "middle", "loser"])
        self.assertEqual(rows[0]["rank"], 1)
        self.assertGreater(rows[0]["beat_pct"], rows[1]["beat_pct"])
        self.assertLess(rows[2]["beat_pct"], 5)
        self.assertGreater(rows[0]["beat_pct"], 95)

    def test_new_keys_are_marked(self):
        self.add("fresh", 1)
        self.settle()
        row = self.db.execute("SELECT * FROM challenge_entries").fetchone()
        fresh = {row["pubkey"]: (NOW - timedelta(days=5)).isoformat()}
        self.assertTrue(challenges._standing(self.c, [row], fresh, NOW)[0]["new"])
        old = {row["pubkey"]: (NOW - timedelta(days=90)).isoformat()}
        self.assertFalse(challenges._standing(self.c, [row], old, NOW)[0]["new"])

    def test_addresses_are_forgotten_when_the_month_starts(self):
        self.enter()
        self.assertIsNotNone(self.db.execute("SELECT ip_tag FROM challenge_entries").fetchone()[0])
        self.patch(sim, "stock_prices", lambda first: self.fail("no prices needed"))
        self.patch(challenges, "backtest_entries", lambda db, bot: None)
        self.db.execute("DELETE FROM challenge_entries WHERE challenge='2026-11' AND 0")
        later = datetime(2026, 11, 1, 1, 0, tzinfo=timezone.utc)
        self.patch(challenges, "settle", lambda *a: None)
        self.patch(sim, "stock_prices", lambda first: (["2026-11-01"], {}, {}))
        challenges.update(self.db, later)
        self.assertIsNone(self.db.execute("SELECT ip_tag FROM challenge_entries").fetchone()[0])
        self.assertIsNone(self.db.execute("SELECT salt FROM challenges WHERE id='2026-11'").fetchone()[0])

    def test_open_data_has_no_names_or_keys(self):
        self.add("secretname", 1)
        self.settle()
        text = challenges.results_csv(self.db, "2026-10")
        self.assertIn("beat_random_pct", text)
        self.assertNotIn("secretname", text)
        key = self.db.execute("SELECT pubkey FROM challenge_entries").fetchone()[0]
        self.assertNotIn(key, text)
        self.assertIsNone(challenges.results_csv(self.db, "1999-01"))
