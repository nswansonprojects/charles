"""terms.py and live.py: the License Agreement on file, and live trading only after every step (owner 2026-11-03)."""
import io
import json
import os
import sys
import types
import urllib.error
from datetime import datetime, timezone

from base import BotTestCase

import alpaca_executor as ex
import live
import terms


class Agreement(BotTestCase):
    def test_nothing_on_file_is_not_accepted(self):
        self.assertIsNone(terms.accepted())
        self.assertFalse(terms.current())
        self.assertIn("Not accepted yet", terms.status_line())

    def test_recorded_is_current_and_logged(self):
        terms.record("installer", now=datetime(2026, 11, 3, tzinfo=timezone.utc))
        self.assertTrue(terms.current())
        self.assertIn("accepted on 2026-11-03 (installer)", terms.status_line())
        with open(os.path.join(self.tmp, "terms_log.jsonl")) as f:
            rows = [json.loads(x) for x in f]
        self.assertEqual([r["how"] for r in rows], ["installer"])

    def test_an_older_version_is_not_current(self):
        with open(os.path.join(self.tmp, "terms_accepted.json"), "w") as f:
            json.dump({"version": "0.9", "at": "2026-10-01T00:00:00+00:00", "how": "installer", "age_confirmed": True}, f)
        self.assertFalse(terms.current())
        self.assertIn("is new", terms.status_line())

    def test_unknown_way_is_refused(self):
        with self.assertRaises(ValueError):
            terms.record("a web form")

    def test_check_and_record_for_the_installer(self):
        self.assertEqual(terms.main(["--check"]), 1)
        self.assertEqual(terms.main(["--record", "installer"]), 0)
        self.assertEqual(terms.main(["--check"]), 0)

    def test_the_installer_shows_the_same_short_version(self):
        # install.sh (the website's repo) asks before anything is installed; its bullets must say what terms.SHORT says
        site = os.environ.get("CHARLES_SITE_DIR", "/root/site-next")
        path = os.path.join(site, "install.sh")
        if not os.path.exists(path):
            self.skipTest("no website checkout here")
        with open(path) as f:
            text = f.read()
        for line in terms.SHORT:
            self.assertIn(line, text)
        self.assertIn(f'TERMS_VERSION="{terms.VERSION}"', text)

    # The website's terms.html (and the other legal pages) are checked in test_legal.py: made by ops/legal/render.py.

    def _ask(self, answers):
        said = []
        ok = terms.ask(read=lambda q: answers.pop(0), say=said.append)
        return ok, " ".join(said)

    def test_under_age_or_no_agreement_changes_nothing(self):
        self.assertEqual(self._ask(["n"])[0], False)
        self.assertEqual(self._ask(["y", "maybe"])[0], False)
        self.assertFalse(terms.current())

    def test_yes_and_i_agree_records_it(self):
        ok, said = self._ask(["yes", "I agree"])
        self.assertTrue(ok)
        self.assertTrue(terms.current())
        self.assertIn(terms.URL, said)
        self.assertEqual(terms.accepted()["how"], "charles-terms")

    def test_the_short_version_matches_the_agreement(self):
        with open(os.path.join(os.path.dirname(terms.__file__), "LICENSE.md")) as f:
            text = f.read()
        for line in terms.SHORT:
            self.assertIn(line, text)
        self.assertIn(f"Version {terms.VERSION}, effective {terms.EFFECTIVE}", text)


class Mode(BotTestCase):
    ENV = {"ALPACA_LIVE_API_KEY": "AKTESTKEY123456", "ALPACA_LIVE_SECRET_KEY": "s" * 40}

    def test_paper_by_default_and_when_unreadable(self):
        self.assertEqual(live.state(), {"mode": "paper"})
        with open(os.path.join(self.tmp, "trading_mode.json"), "w") as f:
            f.write("{not json")
        self.assertFalse(live.is_live())
        self.assertIsNone(live.problem(env={}))

    def test_live_needs_the_agreement_then_the_keys(self):
        live.set_mode("live", "test")
        self.assertTrue(live.is_live())
        self.assertIn("charles-terms", live.problem(env=self.ENV))
        terms.record("charles-live")
        self.assertIn("keys are missing", live.problem(env={}))
        self.assertIsNone(live.problem(env=self.ENV))
        d = live.dashboard()
        self.assertEqual(d["mode"], "live")
        self.assertNotIn("AKTEST", json.dumps(d))           # never a key on the dashboard

    def test_a_new_agreement_version_puts_live_on_hold(self):
        live.set_mode("live", "test")
        terms.record("charles-live")
        self.patch(terms, "VERSION", terms.VERSION + ".1")          # whatever the next version is
        self.assertIn("charles-terms", live.problem(env=self.ENV))


class Keys(BotTestCase):
    @staticmethod
    def opener(body=None, code=None):
        def open_(req, timeout=None):
            if code:
                raise urllib.error.HTTPError(req.full_url, code, "no", {}, None)
            return io.BytesIO(json.dumps(body).encode())
        return open_

    def test_paper_keys_are_not_live_keys(self):
        ok, said, _ = live.check_keys("PKPAPERKEY1234", "x" * 40, self.opener({}))
        self.assertFalse(ok)
        self.assertIn("start with AK", said)

    def test_an_active_live_account(self):
        ok, said, acct = live.check_keys("AKLIVEKEY12345", "x" * 40, self.opener({"status": "ACTIVE", "equity": "1100", "cash": "1100"}))
        self.assertTrue(ok)
        self.assertIn("$1,100.00", said)

    def test_refused_or_blocked(self):
        self.assertFalse(live.check_keys("AKLIVEKEY12345", "x" * 40, self.opener(code=401))[0])
        self.assertFalse(live.check_keys("AKLIVEKEY12345", "x" * 40, self.opener({"status": "ACTIVE", "trading_blocked": True}))[0])


class Switching(BotTestCase):
    def setUp(self):
        super().setUp()
        for n in ("start.json", "equity_curve.json", "plan.json", "trades.json"):
            with open(os.path.join(self.tmp, n), "w") as f:
                f.write(json.dumps({"paper": n}))
        self.services = []
        self.saved = {}

    def _on(self, answers, env=None, body=None):
        said = []
        ok = live.turn_on(read=lambda q: answers.pop(0), secret_read=lambda q: "x" * 40, say=said.append,
                          opener=Keys.opener(body or {"status": "ACTIVE", "equity": "500", "cash": "500"}),
                          service=self.services.append, env=env if env is not None else {},
                          save_keys=lambda k, s: self.saved.update(key=k, secret=s))
        return ok, " ".join(said)

    def test_every_step_then_live_with_the_paper_history_kept(self):
        ok, said = self._on(["y", "I agree", "AKLIVEKEY12345", "LIVE"])
        self.assertTrue(ok, said)
        self.assertTrue(live.is_live())
        self.assertTrue(terms.current())
        self.assertEqual(self.saved["key"], "AKLIVEKEY12345")
        self.assertEqual(self.services, ["stop", "start", "web"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "trades.json")))      # a new account, a new history
        kept = [n for n in os.listdir(self.tmp) if n.startswith("paper-")]
        self.assertEqual(len(kept), 1)
        self.assertEqual(sorted(os.listdir(os.path.join(self.tmp, kept[0]))), ["equity_curve.json", "plan.json", "start.json", "trades.json"])

    def test_any_no_changes_nothing(self):
        for answers in (["n"], ["y", "I agree", "PKPAPER123456"], ["y", "I agree", "AKLIVEKEY12345", "live please"]):
            self.assertFalse(self._on(list(answers))[0])
            self.assertFalse(live.is_live())
            self.assertTrue(os.path.exists(os.path.join(self.tmp, "trades.json")))
        self.assertEqual(self.services, [])

    def test_off_brings_the_paper_history_back(self):
        self._on(["y", "I agree", "AKLIVEKEY12345", "LIVE"])
        with open(os.path.join(self.tmp, "trades.json"), "w") as f:
            f.write('{"live": 1}')
        ok = live.turn_off(read=lambda q: "y", say=lambda s: None, service=self.services.append)
        self.assertTrue(ok)
        self.assertFalse(live.is_live())
        with open(os.path.join(self.tmp, "trades.json")) as f:
            self.assertEqual(json.load(f), {"paper": "trades.json"})
        live_kept = [n for n in os.listdir(self.tmp) if n.startswith("live-")]
        self.assertEqual(len(live_kept), 1)

    def test_bring_back_never_overwrites(self):
        live.put_aside("paper", now=datetime(2026, 11, 3, 1, tzinfo=timezone.utc))
        with open(os.path.join(self.tmp, "plan.json"), "w") as f:
            f.write('{"new": 1}')
        live.bring_back("paper")
        with open(os.path.join(self.tmp, "plan.json")) as f:
            self.assertEqual(json.load(f), {"new": 1})


class Client(BotTestCase):
    def setUp(self):
        super().setUp()
        made = self.made = []

        class FakeTrading:
            def __init__(self, key, secret, paper):
                made.append((key, secret, paper))
        mod = types.ModuleType("alpaca.trading.client")
        mod.TradingClient = FakeTrading
        self._mods = {k: sys.modules.get(k) for k in ("alpaca.trading.client",)}
        sys.modules["alpaca.trading.client"] = mod
        self.patch(ex.sec, "alpaca_key", lambda: "PKPAPER")
        self.patch(ex.sec, "alpaca_secret", lambda: "paper-secret")
        self.patch(live, "keys", lambda env=None: ("AKLIVE", "live-secret"))

    def tearDown(self):
        for k, v in self._mods.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        super().tearDown()

    def test_paper_keys_on_paper(self):
        ex._client()
        self.assertEqual(self.made, [("PKPAPER", "paper-secret", True)])

    def test_live_keys_only_when_live(self):
        live.set_mode("live", "test")
        ex._client()
        self.assertEqual(self.made, [("AKLIVE", "live-secret", False)])
