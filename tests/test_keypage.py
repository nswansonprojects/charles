"""keypage: the paid setup's "Add your keys" page. Only a server the setup service made has it, the code works once and only
over HTTPS, root checks everything again, keys never land anywhere but .env, and the browser is logged in once."""
import hashlib
import json
import os
import stat
import threading

from base import BotTestCase

import auth
import control
import keypage
import webdash as wd

CODE = "Zq3vN8xYbR2mK7pLs9TdWc4eHj6uFa1oQi5gVn0yXk8"
HASH = hashlib.sha256(CODE.encode()).hexdigest()
KEY, SECRET = "PKABCDEFGHIJ34567890", "aZ8wR4tY7uI1oP3aS5dF6gH8jK0lZ2xC4vB6nM8q"
NOW = 1_800_000_000.0


def env_text(path):
    with open(path) as f:
        return f.read()


class Arming(BotTestCase):
    def setUp(self):
        super().setUp()
        with open(keypage.ENV_FILE, "w") as f:
            f.write("ALPACA_API_KEY=\nALPACA_SECRET_KEY=\nGROQ_API_KEY=\n")

    def test_only_an_armed_server_has_the_page_and_only_with_the_code(self):
        self.assertEqual(keypage.state(CODE, NOW), "closed")                       # a free install: no page at all
        keypage.arm(HASH, NOW)
        self.assertEqual(stat.S_IMODE(os.stat(keypage.STATE).st_mode), 0o600)
        self.assertNotIn(CODE, env_text(keypage.STATE))                            # only the hash is on the server
        self.assertEqual(keypage.state(CODE, NOW + 60), "open")
        self.assertEqual(keypage.state(CODE[:-1] + "Y", NOW + 60), "closed")
        self.assertEqual(keypage.state("", NOW + 60), "closed")
        self.assertEqual(keypage.state(CODE, NOW + keypage.KEY_HOURS * 3600 + 1), "closed")

    def test_arming_twice_or_with_keys_changes_nothing(self):
        keypage.arm(HASH, NOW)
        self.assertIn("already", keypage.arm("0" * 64, NOW + 5))
        self.assertEqual(keypage.read()["hash"], HASH)
        os.remove(keypage.STATE)
        with open(keypage.ENV_FILE, "w") as f:
            f.write(f"ALPACA_API_KEY={KEY}\n")
        self.assertIn("has keys already", keypage.arm(HASH, NOW))
        self.assertIsNone(keypage.read())
        with self.assertRaises(ValueError):
            keypage.arm("not-a-hash")

    def test_pasted_keys_are_tidied_or_refused(self):
        self.assertEqual(keypage.tidy(f'  "{KEY}" ', f"{SECRET}\n"), (KEY, SECRET))
        for key, secret, why in (("", SECRET, "both"), ("AKJ4D7QX2M9F3A8B1C6E", SECRET, "live-money"), ("pk lower", SECRET, "API Key"),
                                 (KEY, "short", "Secret Key"), (KEY + "AAAAAAAAAAAA", KEY + "AAAAAAAAAAAA", "same key")):
            with self.assertRaises(ValueError) as e:
                keypage.tidy(key, secret)
            self.assertIn(why, str(e.exception))


class RootSide(BotTestCase):
    """keypage.apply(), as control.py runs it: the code again, Alpaca, .env, start."""
    def setUp(self):
        super().setUp()
        with open(keypage.ENV_FILE, "w") as f:
            f.write("# Trading Bot Credentials\nALPACA_API_KEY=\nALPACA_SECRET_KEY=\nGROQ_API_KEY=gsk_keep_me\n")
        os.chmod(keypage.ENV_FILE, 0o600)
        keypage.arm(HASH, NOW)
        self.checked, self.started = [], []

    def run_one(self, attempt="a" * 16, said="ok", started=True, **r):
        def check(k, s):
            self.checked.append((k, s))
            return said

        def start():
            self.started.append(1)
            return started
        req = {"action": "set-keys", "code": CODE, "key": KEY, "secret": SECRET, "attempt": attempt, **r}
        return keypage.apply(req, NOW + 60, check=check, start=start)

    def test_good_keys_go_in_env_and_start_charles_once(self):
        said = self.run_one()
        self.assertEqual(said, "Your keys work and Charles is running.")
        self.assertEqual(self.checked, [(KEY, SECRET)])
        self.assertEqual(env_text(keypage.ENV_FILE), f"# Trading Bot Credentials\nALPACA_API_KEY={KEY}\nALPACA_SECRET_KEY={SECRET}\nGROQ_API_KEY=gsk_keep_me\n")
        self.assertEqual(stat.S_IMODE(os.stat(keypage.ENV_FILE).st_mode), 0o600)
        self.assertEqual(keypage.state(CODE, NOW + 61), "done")
        self.assertEqual(keypage.outcome("a" * 16)["ok"], True)
        self.assertNotIn(SECRET, env_text(keypage.STATE))
        self.assertNotIn(KEY, env_text(keypage.STATE))
        # closed for good: a second try (even with the code) changes nothing
        self.assertIn("already in", self.run_one(attempt="b" * 16, key="PKABCDEFGHIJZZZZZZZZ"))
        self.assertIn(KEY, env_text(keypage.ENV_FILE))
        self.assertEqual(self.started, [1])

    def test_refused_or_unreachable_keys_change_nothing_and_say_why(self):
        self.assertIn("didn't accept", self.run_one(said="rejected"))
        self.assertIn("Try again in a minute", self.run_one(attempt="b" * 16, said="unreachable"))
        self.assertIn("ALPACA_API_KEY=\n", env_text(keypage.ENV_FILE))
        self.assertEqual(self.started, [])
        self.assertEqual(keypage.state(CODE, NOW + 61), "open")                    # they can try again
        self.assertEqual(keypage.outcome("b" * 16)["ok"], False)
        self.assertIsNone(keypage.outcome("a" * 16))                               # only the last try is kept

    def test_wrong_code_late_or_keys_already_there(self):
        self.assertIn("closed", self.run_one(code="x" * 43))
        self.assertEqual(self.checked, [])
        with open(keypage.ENV_FILE, "a") as f:
            f.write(f"ALPACA_API_KEY={KEY}\n")
        self.assertIn("has Alpaca keys already", self.run_one())
        self.assertEqual(self.checked, [])
        self.assertEqual(keypage.state(CODE, NOW + 61), "done")

    def test_saved_but_not_started_still_logs_in(self):
        self.assertIn("didn't start yet", self.run_one(started=False))
        self.assertTrue(keypage.outcome("a" * 16)["ok"])

    def test_through_control_the_request_is_root_only_used_once_and_logged_without_keys(self):
        self.patch(keypage, "check_with_alpaca", lambda k, s: "ok")
        self.patch(keypage, "start_bot", lambda: True)
        name = control.request("set-keys", "the key page", code=CODE, key=KEY, secret=SECRET, attempt="c" * 16)
        path = os.path.join(control.REQ_DIR, name)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(control.run(), ["Your keys work and Charles is running."])
        self.assertEqual(os.listdir(control.REQ_DIR), [])
        log = env_text(control.LOG_FILE)
        self.assertIn('"set-keys"', log)
        self.assertNotIn(SECRET, log)
        self.assertNotIn(KEY, log)
        self.assertEqual([c[0] for c in self.alerts.calls], ["control"])
        self.assertNotIn(SECRET, json.dumps(self.alerts.calls))


class OverHttp(BotTestCase):
    """The page on the dashboard: check, add (root-only request), status, then the one-time login."""
    def setUp(self):
        super().setUp()
        from http.server import ThreadingHTTPServer
        self.auth = os.path.join(self.tmp, "auth.json")
        self.patch(auth, "AUTH_FILE", self.auth)
        self.patch(wd, "_cache", {})
        self.patch(wd, "_keys_tries", [])
        self.patch(wd, "_keys_logged_in", set())
        self.patch(wd, "STATE_FROM", None)
        with open(self.auth, "w") as f:
            json.dump({"access": "private", "secret": "s" * 64, "links": []}, f)
        with open(keypage.ENV_FILE, "w") as f:
            f.write("ALPACA_API_KEY=\nALPACA_SECRET_KEY=\n")
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), wd.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def req(self, method, path, body=None, https=True):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=10)
        headers = {"Content-Type": "application/json", **({"X-Forwarded-Proto": "https"} if https else {})}
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        r = c.getresponse()
        raw = r.read().decode()
        try:
            data = json.loads(raw)
        except ValueError:
            data = raw
        return r.status, data, r.headers.get_all("Set-Cookie") or []

    def test_a_free_install_has_no_key_page(self):
        self.assertEqual(self.req("GET", "/keys")[0], 404)
        self.assertEqual(self.req("POST", "/keys/check", {"t": CODE})[1], {"state": "closed", "https": True})
        self.assertEqual(self.req("POST", "/keys/add", {"t": CODE, "key": KEY, "secret": SECRET})[0], 410)

    def test_the_whole_way_from_code_to_logged_in(self):
        keypage.arm(HASH)
        code, page, _ = self.req("GET", "/keys")
        self.assertEqual(code, 200)
        self.assertIn("Start Charles", page)
        self.assertIn('name="referrer" content="no-referrer"', page)
        self.assertEqual(self.req("POST", "/keys/check", {"t": CODE})[1], {"state": "open", "https": True})
        self.assertEqual(self.req("POST", "/keys/add", {"t": CODE, "key": KEY, "secret": SECRET}, https=False)[0], 403)   # never over plain http
        self.assertEqual(self.req("POST", "/keys/add", {"t": "x" * 43, "key": KEY, "secret": SECRET})[0], 410)
        self.assertEqual(self.req("POST", "/keys/add", {"t": CODE, "key": "AK" + KEY[2:], "secret": SECRET})[0], 400)
        code, got, _ = self.req("POST", "/keys/add", {"t": CODE, "key": KEY, "secret": SECRET})
        self.assertEqual(code, 202)
        attempt = got["attempt"]
        self.assertEqual(self.req("POST", "/keys/status", {"t": CODE, "attempt": attempt})[1], {"state": "waiting"})
        names = os.listdir(control.REQ_DIR)
        self.assertEqual(len(names), 1)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(control.REQ_DIR, names[0])).st_mode), 0o600)
        # root's side
        self.patch(keypage, "check_with_alpaca", lambda k, s: "ok")
        self.patch(keypage, "start_bot", lambda: True)
        control.run()
        code, got, cookies = self.req("POST", "/keys/status", {"t": CODE, "attempt": attempt})
        self.assertEqual(got["state"], "done")
        self.assertEqual(got["go"], "/")
        self.assertTrue(any(c.startswith(wd.SESSION_COOKIE + "=") for c in cookies))
        self.assertTrue(any(c.startswith(wd.CSRF_COOKIE + "=") and "Secure" in c for c in cookies))
        jar = "; ".join(c.split(";")[0] for c in cookies)
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=10)
        c.request("GET", "/api/session", headers={"Cookie": jar})
        self.assertEqual(json.loads(c.getresponse().read())["logged_in"], True)
        # once only, and the page is closed now
        self.assertEqual(self.req("POST", "/keys/status", {"t": CODE, "attempt": attempt})[2], [])
        self.assertEqual(self.req("POST", "/keys/check", {"t": CODE})[1]["state"], "done")
        self.assertEqual(self.req("POST", "/keys/add", {"t": CODE, "key": KEY, "secret": SECRET})[0], 410)
        self.assertIn(f"ALPACA_SECRET_KEY={SECRET}", env_text(keypage.ENV_FILE))

    def test_a_refused_key_lets_them_try_again_with_no_login(self):
        keypage.arm(HASH)
        attempt = self.req("POST", "/keys/add", {"t": CODE, "key": KEY, "secret": SECRET})[1]["attempt"]
        self.patch(keypage, "check_with_alpaca", lambda k, s: "rejected")
        control.run()
        _, got, cookies = self.req("POST", "/keys/status", {"t": CODE, "attempt": attempt})
        self.assertEqual(got["state"], "bad")
        self.assertIn("didn't accept", got["message"])
        self.assertEqual(cookies, [])
        self.assertEqual(self.req("POST", "/keys/check", {"t": CODE})[1]["state"], "open")

    def test_tries_are_limited_and_bodies_small(self):
        keypage.arm(HASH)
        for _ in range(wd.KEYS_TRIES):
            self.req("POST", "/keys/add", {"t": CODE, "key": "bad", "secret": SECRET})
        self.assertEqual(self.req("POST", "/keys/add", {"t": CODE, "key": KEY, "secret": SECRET})[0], 429)
        self.assertEqual(self.req("POST", "/keys/check", {"t": CODE, "pad": "x" * 2000})[0], 413)

    def test_a_preview_never_takes_keys(self):
        keypage.arm(HASH)
        self.patch(wd, "STATE_FROM", "http://127.0.0.1:8502")
        self.assertEqual(self.req("GET", "/keys")[0], 404)
        self.assertEqual(self.req("POST", "/keys/add", {"t": CODE, "key": KEY, "secret": SECRET})[0], 409)


class Renewal(BotTestCase):
    def test_a_used_session_is_renewed_with_the_same_id(self):
        cfg = {"access": "private", "secret": "s" * 64, "links": []}
        v = auth.session(cfg, NOW)
        self.assertIsNone(auth.renew(cfg, v, NOW + 3600))                        # not due yet
        fresh = auth.renew(cfg, v, NOW + 20 * 86400)
        self.assertEqual(fresh.split(".")[0], v.split(".")[0])                   # so the page's CSRF token still works
        self.assertTrue(auth.check(cfg, fresh, NOW + 45 * 86400))
        self.assertIsNone(auth.renew(cfg, v, NOW + 31 * 86400))                  # an expired one stays expired
        self.assertIsNone(auth.renew(cfg, "junk", NOW))
