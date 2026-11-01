"""ops/droplet_test.py: a stand-in DigitalOcean. The droplet and key always go, the token only goes in the header."""
import importlib.util
import json
import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import SkipTest

from base import BotTestCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "ops", "droplet_test.py")


def load():
    if not os.path.exists(PATH):
        raise SkipTest("ops/ isn't in a public copy")
    spec = importlib.util.spec_from_file_location("droplet_test", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeDO(BaseHTTPRequestHandler):
    calls, auth, fail_on = [], [], None

    def _reply(self, code, body=None):
        raw = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _any(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        FakeDO.calls.append((self.command, self.path, body))
        FakeDO.auth.append(self.headers.get("Authorization"))
        if FakeDO.fail_on == (self.command, self.path):
            return self._reply(403, {"message": "not allowed"})
        if self.command == "DELETE":
            return self._reply(204)
        if self.path == "/v2/account/keys":
            return self._reply(201, {"ssh_key": {"id": 7}})
        if self.path == "/v2/droplets":
            return self._reply(202, {"droplet": {"id": 42}})
        self._reply(200, {"droplet": {"status": "active", "networks": {"v4": [{"type": "private", "ip_address": "10.0.0.1"}, {"type": "public", "ip_address": "203.0.113.9"}]}}})

    do_GET = do_POST = do_DELETE = _any

    def log_message(self, *a):
        pass


class Droplet(BotTestCase):
    def setUp(self):
        super().setUp()
        self.mod = load()
        FakeDO.calls, FakeDO.auth, FakeDO.fail_on = [], [], None
        self.srv = HTTPServer(("127.0.0.1", 0), FakeDO)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.do = self.mod.DO("secret-token", f"http://127.0.0.1:{self.srv.server_port}")
        self.report = os.path.join(self.tmp, "report.json")
        self.site = os.path.join(self.tmp, "site")
        os.makedirs(self.site)
        with open(os.path.join(self.site, "install.sh"), "w") as f:
            f.write('#!/bin/bash\nREPO="https://github.com/nswansonprojects/charles.git"\necho hi\n')
        self.repo = os.path.join(self.tmp, "repo")
        self.patch(self.mod, "build_release", lambda *a: ("/tmp/a.tgz", "/tmp/b.bundle"))

    def run_it(self, tester, *args):
        return self.mod.main(["--report", self.report, "--site", self.site, *args], do=self.do, make_ssh=lambda *a: "ssh", tester=tester)

    def methods(self):
        return [(m, p) for m, p, _ in FakeDO.calls]

    def test_everything_is_destroyed_after_a_good_run(self):
        seen = []
        def tester(ssh, tar, later, installer, results):
            seen.append(installer)
            with open(installer) as f:
                self.assertIn('REPO="/srv/charles.git"', f.read())
            results.append({"check": "fine", "ok": True})
        self.assertEqual(self.run_it(tester), 0)
        self.assertTrue(seen)
        self.assertEqual(self.methods()[-2:], [("DELETE", "/v2/droplets/42"), ("DELETE", "/v2/account/keys/7")])

    def test_everything_is_destroyed_when_the_test_blows_up(self):
        def tester(*a):
            raise OSError("ssh died")
        self.assertEqual(self.run_it(tester), 1)
        self.assertEqual(self.methods()[-2:], [("DELETE", "/v2/droplets/42"), ("DELETE", "/v2/account/keys/7")])
        with open(self.report) as f:
            self.assertIn("ssh died", f.read())

    def test_the_key_goes_even_if_the_droplet_was_never_made(self):
        FakeDO.fail_on = ("POST", "/v2/droplets")
        self.assertEqual(self.run_it(lambda *a: None), 1)
        self.assertEqual(self.methods()[-1], ("DELETE", "/v2/account/keys/7"))
        self.assertNotIn(("DELETE", "/v2/droplets/42"), self.methods())

    def test_keep_leaves_them(self):
        self.assertEqual(self.run_it(lambda s, t, l, i, r: r.append({"check": "x", "ok": True}), "--keep"), 0)
        self.assertFalse([c for c in self.methods() if c[0] == "DELETE"])

    def test_a_failed_check_fails_the_run(self):
        self.assertEqual(self.run_it(lambda s, t, l, i, r: r.append({"check": "x", "ok": False})), 1)

    def test_the_token_is_only_in_the_header(self):
        self.run_it(lambda s, t, l, i, r: r.append({"check": "x", "ok": True}))
        self.assertTrue(all(a == "Bearer secret-token" for a in FakeDO.auth))
        self.assertNotIn("secret-token", json.dumps(FakeDO.calls))
        with open(self.report) as f:
            self.assertNotIn("secret-token", f.read())

    def test_no_token_stops_before_anything_starts(self):
        self.patch(self.mod.os, "environ", {})
        self.assertEqual(self.mod.main(["--report", self.report]), 2)

    def test_the_public_ip_is_the_one_used(self):
        self.assertEqual(self.do.wait_ip(42), "203.0.113.9")


class Release(BotTestCase):
    def test_the_release_is_like_a_public_one(self):
        mod = load()
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as out:
            def git(*a, cwd=src):
                return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout
            git("init", "-q", "-b", "dashboard-next")
            git("config", "user.email", "t@example.com")
            git("config", "user.name", "t")
            os.makedirs(os.path.join(src, "ops"))
            os.makedirs(os.path.join(src, "deploy"))
            for name, text in (("ops/x", "x"), ("deploy/y", "y"), ("CLAUDE.md", "rules"), ("README.md", "hi"), ("strategy.json", '{"settings": {"PICKS": 4}}'),
                               ("CHANGELOG.json", '{"releases": [{"version": "v1.0.0", "date": null, "kind": "fix", "notes": ["first"]}]}')):
                with open(os.path.join(src, name), "w") as f:
                    f.write(text)
            git("add", "-A")
            git("commit", "-qm", "base")
            tar, later = mod.build_release(src, "dashboard-next", out)
            bare = os.path.join(out, "charles.git")
            self.assertEqual(git("tag", cwd=bare).split(), ["v1.0.0"])                  # only the first, at install
            self.assertEqual(git("ls-tree", "--name-only", "v1.0.0", cwd=bare).split(), ["CHANGELOG.json", "README.md", "strategy.json"])
            git("fetch", "-q", later, "refs/heads/main:refs/heads/main", "refs/tags/*:refs/tags/*", cwd=bare)
            self.assertEqual(git("tag", cwd=bare).split(), ["v1.0.0", "v1.0.1", "v1.1.0"])
            self.assertEqual(json.loads(git("show", "v1.1.0:strategy.json", cwd=bare))["settings"]["PICKS"], 3)
            self.assertEqual(json.loads(git("show", "v1.0.1:strategy.json", cwd=bare))["settings"]["PICKS"], 4)
            kinds = {r["version"]: r["kind"] for r in json.loads(git("show", "v1.1.0:CHANGELOG.json", cwd=bare))["releases"]}
            self.assertEqual(kinds, {"v1.1.0": "rule", "v1.0.1": "fix", "v1.0.0": "fix"})
