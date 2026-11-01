"""updates: releases are real git tags in temp repos; only the restart, the health watch and pip are stubbed."""
import json
import os
import subprocess
import types
from datetime import datetime, timezone

from base import BotTestCase

import updates

NOON_SAT = datetime(2026, 10, 31, 16, 0, tzinfo=timezone.utc)


def sh(cwd, *cmd):
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)


def write(root, name, body):
    os.makedirs(os.path.dirname(os.path.join(root, name)), exist_ok=True)
    with open(os.path.join(root, name), "w") as f:
        f.write(body if isinstance(body, str) else json.dumps(body))


class Base(BotTestCase):
    """A bare origin, a publisher's clone that makes releases, and the user's clone under test."""
    def setUp(self):
        super().setUp()
        self.origin = os.path.join(self.tmp, "origin.git")
        self.pub = os.path.join(self.tmp, "pub")
        self.user = os.path.join(self.tmp, "user")
        sh(self.tmp, "git", "init", "-q", "--bare", "-b", "main", self.origin)
        sh(self.tmp, "git", "clone", "-q", self.origin, self.pub)
        for k, v in (("user.email", "t@example.com"), ("user.name", "t")):
            sh(self.pub, "git", "config", k, v)
        write(self.pub, ".gitignore", "data/\n")
        self.log = []
        self.release("v1.0.0", "fix", {"settings": {"CORE_PCT": 0.8, "PICKS": 4}}, "code one\n", "first")
        sh(self.tmp, "git", "clone", "-q", self.origin, self.user)
        self.data = os.path.join(self.user, "data")
        os.makedirs(self.data)
        self.patch(updates, "ROOT", self.user)
        self.patch(updates, "DATA", self.data)
        self.patch(updates, "RELEASE_REPO", self.origin)
        self.moved = []

    def release(self, tag, kind, strategy, code, note, bt=None, rule_file=None):
        self.log.insert(0, {"version": tag, "date": "2026-11-01", "kind": kind, "notes": [note]})
        write(self.pub, "strategy.json", strategy)
        write(self.pub, "webdash.py", code)
        write(self.pub, "CHANGELOG.json", {"releases": self.log})
        if bt:
            write(self.pub, "docs/backtest.json", {"runs": {"charles": {"stats": bt}}})
        if rule_file:
            write(self.pub, rule_file, f"# {tag}\n")
        sh(self.pub, "git", "add", "-A")
        sh(self.pub, "git", "commit", "-q", "-m", tag)
        sh(self.pub, "git", "tag", tag)
        sh(self.pub, "git", "push", "-q", "origin", "main", tag)

    def steps(self, **over):
        calls = {"install": lambda: self.moved.append("install"), "tests": lambda: self.moved.append("tests"),
                 "restart_": lambda: self.moved.append("restart"), "health": lambda: True}
        return {**calls, **over}

    def code(self):
        with open(os.path.join(self.user, "webdash.py")) as f:
            return f.read()


class Check(Base):
    def test_up_to_date(self):
        sh(self.user, "git", "fetch", "-q", "--tags")
        sh(self.user, "git", "checkout", "-q", "--detach", "v1.0.0")
        v = updates.check(NOON_SAT)
        self.assertEqual((v["state"], v["current"]), ("up_to_date", "v1.0.0"))
        with open(os.path.join(self.data, "updates.json")) as f:
            self.assertEqual(json.load(f)["state"], "up_to_date")

    def test_fixes_and_rule_changes_are_counted(self):
        sh(self.user, "git", "checkout", "-q", "--detach", "v1.0.0")
        self.release("v1.1.0", "fix", {"settings": {"CORE_PCT": 0.8, "PICKS": 4}}, "code two\n", "a fix")
        self.release("v1.2.0", "rule", {"settings": {"CORE_PCT": 0.7, "PICKS": 4}}, "code three\n", "a rule")
        v = updates.check(NOON_SAT)
        self.assertEqual(v["state"], "available")
        self.assertEqual(v["latest"], "v1.2.0")
        self.assertEqual(updates.summary(v["pending"]), "1 fix and 1 rule change")
        by = {e["version"]: e for e in v["versions"]}
        self.assertFalse(by["v1.1.0"]["needs_confirm"])
        self.assertTrue(by["v1.2.0"]["needs_confirm"])
        self.assertEqual(by["v1.2.0"]["diff"]["settings"],
                         [{"name": "CORE_PCT", "label": updates.LABELS["CORE_PCT"], "old": 0.8, "new": 0.7}])

    def test_a_private_copy_is_never_offered_a_release(self):
        self.patch(updates, "RELEASE_REPO", "https://github.com/nswansonprojects/charles.git")
        v = updates.check(NOON_SAT)
        self.assertEqual((v["state"], v["versions"]), ("unmanaged", []))
        ok = updates.queue("update", "the dashboard", version="v1.0.0", now=NOON_SAT, start=lambda: self.fail("started"))
        self.assertIn("updates through git", ok)
        self.assertEqual(updates.read_job()["status"], "refused")

    def test_a_fix_that_touches_trading_code_still_needs_the_confirm(self):
        sh(self.user, "git", "checkout", "-q", "--detach", "v1.0.0")
        self.release("v1.1.0", "fix", {"settings": {"CORE_PCT": 0.8, "PICKS": 4}}, "code two\n", "only a fix", rule_file="rebalancer.py")
        e = updates.check(NOON_SAT)["versions"][0]
        self.assertTrue(e["needs_confirm"])
        self.assertEqual(e["diff"]["files"], ["rebalancer.py"])

    def test_a_release_without_notes_counts_as_a_rule_change(self):
        sh(self.user, "git", "checkout", "-q", "--detach", "v1.0.0")
        write(self.pub, "webdash.py", "code two\n")
        sh(self.pub, "git", "add", "-A"); sh(self.pub, "git", "commit", "-q", "-m", "x"); sh(self.pub, "git", "tag", "v1.1.0")
        sh(self.pub, "git", "push", "-q", "origin", "main", "v1.1.0")
        e = updates.check(NOON_SAT)["versions"][0]
        self.assertEqual((e["kind"], e["needs_confirm"]), ("rule", True))

    def test_the_backtest_numbers_show_when_they_differ(self):
        sh(self.user, "git", "checkout", "-q", "--detach", "v1.0.0")
        self.release("v1.1.0", "rule", {"settings": {"CORE_PCT": 0.8, "PICKS": 4}}, "x\n", "n",
                     bt={"cagr_pct": 18.0, "max_drawdown_pct": -35.0})
        self.assertIsNone(updates.check(NOON_SAT)["versions"][0]["diff"]["backtest"])     # the old version has none to compare
        write(self.user, "unused", "")
        self.release("v1.2.0", "rule", {"settings": {"CORE_PCT": 0.8, "PICKS": 4}}, "y\n", "n",
                     bt={"cagr_pct": 19.0, "max_drawdown_pct": -39.0})
        sh(self.user, "git", "fetch", "-q", "--tags")
        sh(self.user, "git", "checkout", "-q", "--detach", "v1.1.0")
        d = updates.check(NOON_SAT)["versions"][0]["diff"]["backtest"]
        self.assertEqual((d["old"]["cagr_pct"], d["new"]["cagr_pct"]), (18.0, 19.0))


class Move(Base):
    def setUp(self):
        super().setUp()
        sh(self.user, "git", "checkout", "-q", "--detach", "v1.0.0")
        self.release("v1.1.0", "fix", {"settings": {"CORE_PCT": 0.8, "PICKS": 4}}, "code two\n", "a fix")
        self.release("v1.2.0", "rule", {"settings": {"CORE_PCT": 0.7, "PICKS": 4}}, "code three\n", "a rule")
        updates.check(NOON_SAT)

    def test_update_runs_the_steps_in_order_and_is_recorded(self):
        ok, said = updates.move("v1.1.0", "SSH", False, NOON_SAT, **self.steps())
        self.assertTrue(ok, said)
        self.assertEqual(self.moved, ["install", "tests", "restart"])
        self.assertEqual(self.code(), "code two\n")
        self.assertEqual(updates.current(), "v1.1.0")
        with open(os.path.join(self.data, "versions.json")) as f:
            row = json.load(f)[0]
        self.assertEqual((row["from"], row["to"], row["ok"]), ("v1.0.0", "v1.1.0", True))
        self.assertEqual([c[0] for c in self.alerts.calls], ["control"])

    def test_a_rule_change_needs_the_box_ticked(self):
        with self.assertRaises(updates.UpdateError):
            updates.move("v1.2.0", "SSH", False, NOON_SAT, **self.steps())
        self.assertEqual(self.code(), "code one\n")
        self.assertTrue(updates.move("v1.2.0", "SSH", True, NOON_SAT, **self.steps())[0])
        self.assertEqual(self.code(), "code three\n")

    def test_failing_tests_go_back_without_restarting(self):
        def bad():
            raise updates.UpdateError("The tests failed on this version: boom")
        ok, said = updates.move("v1.1.0", "SSH", False, NOON_SAT, **self.steps(tests=bad))
        self.assertFalse(ok)
        self.assertIn("back on v1.0.0", said)
        self.assertEqual(self.code(), "code one\n")
        self.assertNotIn("restart", self.moved)

    def test_an_unhealthy_bot_goes_back_and_restarts_again(self):
        ok, said = updates.move("v1.1.0", "SSH", False, NOON_SAT, **self.steps(health=lambda: False))
        self.assertFalse(ok)
        self.assertIn("wasn't healthy", said)
        self.assertEqual(self.code(), "code one\n")
        self.assertEqual(self.moved.count("restart"), 2)

    def test_restore_goes_back_and_data_and_settings_stay(self):
        write(self.user, "strategy.json", {"settings": {"CORE_PCT": 0.6, "PICKS": 2}})     # the user's own change
        write(self.data, "plan.json", {"keep": "me"})
        write(self.user, "my-notes.txt", "untracked\n")
        self.assertTrue(updates.move("v1.2.0", "SSH", True, NOON_SAT, **self.steps())[0])
        self.assertEqual(self.code(), "code three\n")
        with open(os.path.join(self.user, "strategy.json")) as f:
            self.assertEqual(json.load(f)["settings"]["CORE_PCT"], 0.6)
        v = updates.check(NOON_SAT, fetch=False)
        self.assertEqual(v["local_settings"], ["strategy.json"])
        self.assertTrue(updates.move("v1.0.0", "SSH", True, NOON_SAT, **self.steps())[0])     # back across a rule change: confirmed
        self.assertEqual(self.code(), "code one\n")
        with open(os.path.join(self.user, "strategy.json")) as f:
            self.assertEqual(json.load(f)["settings"]["CORE_PCT"], 0.6)
        with open(os.path.join(self.data, "plan.json")) as f:
            self.assertEqual(json.load(f), {"keep": "me"})
        with open(os.path.join(self.user, "my-notes.txt")) as f:
            self.assertEqual(f.read(), "untracked\n")

    def test_going_back_across_a_rule_change_needs_the_confirm(self):
        self.assertTrue(updates.move("v1.2.0", "SSH", True, NOON_SAT, **self.steps())[0])
        self.release("v1.3.0", "fix", {"settings": {"CORE_PCT": 0.7, "PICKS": 4}}, "code four\n", "a fix")
        self.assertTrue(updates.move("v1.3.0", "SSH", False, NOON_SAT, **self.steps())[0] if updates.check(NOON_SAT) else False)
        by = {e["version"]: e for e in updates.view(NOON_SAT)["versions"]}
        self.assertFalse(by["v1.2.0"].get("needs_confirm"))
        self.assertTrue(by["v1.1.0"]["needs_confirm"])

    def test_code_changes_of_the_users_own_are_not_overwritten(self):
        write(self.user, "webdash.py", "my own edit\n")
        with self.assertRaises(updates.UpdateError) as e:
            updates.move("v1.1.0", "SSH", False, NOON_SAT, **self.steps())
        self.assertIn("code changes of its own", str(e.exception))
        self.assertEqual(self.code(), "my own edit\n")

    def test_a_failed_way_back_says_so(self):
        real = updates.switch
        calls = []

        def switch(rev):
            calls.append(rev)
            if len(calls) == 2:
                raise updates.UpdateError("disk full")
            real(rev)
        self.patch(updates, "switch", switch)
        ok, said = updates.move("v1.1.0", "SSH", False, NOON_SAT, **self.steps(health=lambda: False))
        self.assertFalse(ok)
        self.assertIn("failed too", said)
        self.assertIn("charles-restore", said)


class Queue(Base):
    def setUp(self):
        super().setUp()
        sh(self.user, "git", "checkout", "-q", "--detach", "v1.0.0")
        self.release("v1.1.0", "fix", {"settings": {"CORE_PCT": 0.8, "PICKS": 4}}, "code two\n", "a fix")
        updates.check(NOON_SAT)
        self.started = []

    def queue(self, *a, **k):
        return updates.queue(*a, now=k.pop("now", NOON_SAT), start=lambda: self.started.append(1), **k)

    def test_a_good_request_is_queued_and_the_service_started(self):
        self.assertIn("Queued", self.queue("update", "the dashboard", version="v1.1.0"))
        self.assertEqual(self.started, [1])
        self.assertEqual(updates.read_job()["status"], "queued")
        said = updates.run_job(NOON_SAT, **self.steps())
        self.assertIn("now on v1.1.0", said)
        self.assertEqual(updates.read_job()["status"], "done")
        self.assertEqual(self.code(), "code two\n")
        with open(os.path.join(self.data, "updates.json")) as f:
            self.assertEqual(json.load(f)["state"], "up_to_date")

    def test_junk_versions_and_second_jobs_are_refused(self):
        for bad in ("v9.9.9", "main", "--upload-pack=x", "v1.1.0;rm -rf /", None, 7):
            self.assertNotIn("Queued", self.queue("update", "the dashboard", version=bad), bad)
        self.assertEqual(self.started, [])
        self.assertIn("Queued", self.queue("update", "the dashboard", version="v1.1.0"))
        self.assertIn("still running", self.queue("restore", "the dashboard", version="v1.0.0"))
        self.assertEqual(self.started, [1])

    def test_a_stale_job_does_not_block_forever(self):
        write(self.data, "update_job.json", {"status": "running", "at_ts": NOON_SAT.timestamp() - 3600})
        self.assertIn("Queued", self.queue("update", "the dashboard", version="v1.1.0"))

    def test_no_updates_during_the_daily_check(self):
        busy = datetime(2026, 10, 30, 14, 5, tzinfo=timezone.utc)          # Friday 10:05 AM ET
        self.assertTrue(updates.busy_now(busy))
        self.assertFalse(updates.busy_now(datetime(2026, 10, 30, 15, 0, tzinfo=timezone.utc)))
        self.assertFalse(updates.busy_now(datetime(2026, 10, 31, 14, 5, tzinfo=timezone.utc)))   # Saturday
        self.assertIn("daily check", self.queue("update", "the dashboard", version="v1.1.0", now=busy))
        self.assertEqual(self.started, [])

    def test_with_no_job_the_service_only_looks(self):
        self.assertIsNone(updates.run_job(NOON_SAT, **self.steps()))
        self.assertEqual(self.moved, [])
        self.assertEqual(self.code(), "code one\n")


class Health(BotTestCase):
    def run_it(self, answers):
        t = {"now": 0.0}
        it = iter(answers)

        def probe():
            return next(it, answers[-1])
        return updates.healthy(probe, wait=60, settle=20, sleep=lambda s: t.__setitem__("now", t["now"] + s), clock=lambda: t["now"])

    def test_steady_is_healthy(self):
        self.assertTrue(self.run_it([(True, 0)]))

    def test_down_is_not(self):
        self.assertFalse(self.run_it([(False, 0)]))

    def test_a_crash_loop_is_not_even_when_each_look_finds_it_up(self):
        self.assertFalse(self.run_it([(True, n) for n in range(30)]))



class Restart(BotTestCase):
    def test_no_kalshi_add_on_stays_quiet(self):
        calls = []

        def run(cmd, **kw):
            calls.append((cmd, kw))
            return types.SimpleNamespace(returncode=5, stdout="", stderr="")
        self.patch(updates.subprocess, "run", run)
        updates.restart()
        kalshi = [kw for cmd, kw in calls if "kalshi-web" in cmd]
        self.assertTrue(kalshi)
        self.assertTrue(kalshi[0].get("capture_output"))     # "Unit kalshi-web.service not found" isn't for the user

class CopyConfig(BotTestCase):
    """The Copy button's root job: it fetches and checks the config itself, whatever the dashboard saw."""
    CFG = {"id": "lb-1a2b3c", "name": "quiet", "bot": "stocks", "settings": {"PICKS": 2, "CORE_PCT": 0.8}}

    def test_stocks_change_only_what_differs_and_can_be_undone(self):
        import chat
        applied = []
        self.patch(chat, "current_settings", lambda *a: {"PICKS": 4, "CORE_PCT": 0.8})
        self.patch(chat, "apply_settings", lambda *a, **k: (applied.append((a, k)), (True, "done"))[1])
        self.assertEqual(updates.copy_config("lb-1a2b3c", "the dashboard", fetch=lambda e: self.CFG), (True, "done"))
        self.assertEqual(applied[0][0][0], {"PICKS": 2})
        self.assertEqual(applied[0][1]["source"], "the leaderboard")

    def test_settings_already_in_place_change_nothing(self):
        import chat
        self.patch(chat, "current_settings", lambda *a: {"PICKS": 2, "CORE_PCT": 0.8})
        self.patch(chat, "apply_settings", lambda *a, **k: self.fail("applied"))
        ok, said = updates.copy_config("lb-1a2b3c", "the dashboard", fetch=lambda e: self.CFG)
        self.assertTrue(ok)
        self.assertIn("already", said)

    def test_crypto_writes_the_file_the_book_reads(self):
        import crypto_settings
        self.patch(crypto_settings, "_tell", lambda t: None)
        os.makedirs(os.path.join(self.tmp, "data"), exist_ok=True)
        write(self.tmp, "crypto.json", {"settings": {"BTC_PCT": 0.6}})
        cfg = {"id": "lb-1a2b3c", "name": "quiet", "bot": "crypto", "settings": {"BTC_PCT": 0.5}}
        ok, _ = updates.copy_config("lb-1a2b3c", "the dashboard", datetime(2026, 11, 1, tzinfo=timezone.utc), fetch=lambda e: cfg, root=self.tmp)
        self.assertTrue(ok)
        with open(os.path.join(self.tmp, "crypto.json")) as f:
            self.assertEqual(json.load(f)["settings"]["BTC_PCT"], 0.5)

    def test_a_config_that_fails_its_checks_changes_nothing(self):
        def bad(entry):
            raise ValueError("the config's signature doesn't match")
        with self.assertRaises(ValueError):
            updates.copy_config("lb-1a2b3c", "the dashboard", fetch=bad)

    def test_the_queue_only_takes_a_leaderboard_id(self):
        self.patch(updates, "DATA", self.tmp)
        self.patch(updates, "ROOT", self.tmp)
        for bad in ("../../etc", "lb-XYZ", None, 7):
            self.assertNotIn("Queued", updates.queue("copy-config", "the dashboard", config=bad, now=NOON_SAT, start=lambda: self.fail("started")), bad)
