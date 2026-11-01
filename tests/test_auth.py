"""auth: the dashboard login. Links work once, within 10 minutes; sessions are signed and end on --logout-all."""
import contextlib
import io
import json
import os
import stat

from base import BotTestCase

import auth

NOW = 1_800_000_000.0


class Links(BotTestCase):
    def setUp(self):
        super().setUp()
        self.cfg = {"access": "private", "secret": "s" * 64, "links": []}

    def test_a_link_works_once(self):
        token, used = auth.new_link(self.cfg, NOW), set()
        self.assertTrue(auth.link_ok(self.cfg, token, used, NOW + 60))          # looking doesn't use it up
        self.assertTrue(auth.take_link(self.cfg, token, used, NOW + 60))
        self.assertFalse(auth.take_link(self.cfg, token, used, NOW + 61))
        self.assertNotIn(token, json.dumps(self.cfg))                             # only its hash is kept

    def test_a_link_expires_and_strangers_fail(self):
        token = auth.new_link(self.cfg, NOW)
        self.assertFalse(auth.take_link(self.cfg, token, set(), NOW + 601))
        self.assertFalse(auth.take_link(self.cfg, "made-up", set(), NOW))
        self.assertFalse(auth.take_link(self.cfg, "", set(), NOW))
        self.assertFalse(auth.take_link(None, token, set(), NOW))

    def test_old_links_are_dropped_when_a_new_one_is_made(self):
        auth.new_link(self.cfg, NOW)
        auth.new_link(self.cfg, NOW + 700)
        self.assertEqual(len(self.cfg["links"]), 1)


class Sessions(BotTestCase):
    def setUp(self):
        super().setUp()
        self.cfg = {"access": "private", "secret": "s" * 64, "links": []}

    def test_a_session_lasts_30_days(self):
        v = auth.session(self.cfg, NOW)
        sid = auth.check(self.cfg, v, NOW + 29 * 86400)
        self.assertTrue(sid)
        self.assertIsNone(auth.check(self.cfg, v, NOW + 31 * 86400))

    def test_a_changed_cookie_or_secret_fails(self):
        v = auth.session(self.cfg, NOW)
        sid, exp, sig = v.split(".")
        self.assertIsNone(auth.check(self.cfg, f"{sid}.{int(exp) + 86400 * 365}.{sig}", NOW))  # a longer expiry
        self.assertIsNone(auth.check(self.cfg, "junk", NOW))
        self.assertIsNone(auth.check({**self.cfg, "secret": "t" * 64}, v, NOW))                 # after --logout-all
        self.assertIsNone(auth.check({"access": "private", "secret": None}, v, NOW))           # a damaged file

    def test_csrf_belongs_to_one_session(self):
        a, b = (auth.check(self.cfg, auth.session(self.cfg, NOW), NOW) for _ in range(2))
        self.assertEqual(auth.csrf(self.cfg, a), auth.csrf(self.cfg, a))
        self.assertNotEqual(auth.csrf(self.cfg, a), auth.csrf(self.cfg, b))

    def test_cookies(self):
        self.assertEqual(auth.cookies("a=1; charles_session_8502=x.1.y"), {"a": "1", "charles_session_8502": "x.1.y"})
        self.assertEqual(auth.cookies(None), {})


class Command(BotTestCase):
    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.tmp, "auth.json")
        self.patch(auth, "AUTH_FILE", self.path)
        os.environ["CHARLES_URL"] = "https://203.0.113.5"
        self.addCleanup(os.environ.pop, "CHARLES_URL", None)

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(auth.main(list(args)), 0)
        return out.getvalue()

    def test_no_file_means_open(self):
        self.assertIsNone(auth.load())
        self.assertEqual(auth.access(auth.load()), "open")

    def test_the_first_run_turns_on_public_with_a_link(self):
        out = self.run_cli()
        cfg = auth.load()
        self.assertEqual(cfg["access"], "public")
        self.assertIn("https://203.0.113.5/login?t=", out)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        token = out.split("login?t=")[1].split()[0]
        self.assertTrue(auth.take_link(cfg, token, set()))

    def test_modes_and_logout_all(self):
        self.run_cli("--private")
        secret = auth.load()["secret"]
        self.assertEqual(auth.load()["access"], "private")
        out = self.run_cli("--logout-all")
        self.assertNotEqual(auth.load()["secret"], secret)
        self.assertEqual(auth.load()["links"], [])
        self.assertNotIn("login?t=", out)
        self.assertIn("no login", self.run_cli("--off"))
        self.assertEqual(auth.access(auth.load()), "open")

    def test_a_damaged_file_locks_instead_of_opening(self):
        with open(self.path, "w") as f:
            f.write("{not json")
        self.assertEqual(auth.access(auth.load()), "private")
        self.run_cli("--status")
        self.run_cli()                                                            # writes a good file again
        self.assertEqual(auth.load()["access"], "public")
