"""What a public install does with no data/launch_authorized.json: that file is only this server's, from an old wizard.
Found by reading main.py while testing a real install: the preview code refused to start, and only a hand patch to the public
repo had been hiding that."""
import ast
import io
import json
import os
import subprocess
import sys
from unittest import mock

from base import BotTestCase

import state_io
import update_keys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FlagRequired(BotTestCase):
    def test_only_this_servers_own_copy_needs_it(self):
        self.assertFalse(state_io.private_copy(self.tmp))
        os.makedirs(os.path.join(self.tmp, "ops"))
        self.assertTrue(state_io.private_copy(self.tmp))

    def test_main_stops_only_when_the_copy_needs_the_flag(self):
        with open(os.path.join(ROOT, "main.py")) as f:
            tree = ast.parse(f.read())
        guard = next(n for n in tree.body if isinstance(n, ast.If) and "__main__" in ast.dump(n.test))
        first = guard.body[0]
        self.assertIsInstance(first, ast.If)
        self.assertIn("LAUNCH_FLAG", ast.dump(first.test))
        stop = first.body[0]                         # no flag: the first thing done is to ask if this copy needs it
        self.assertIsInstance(stop, ast.If)
        self.assertIn("private_copy", ast.dump(stop.test))
        self.assertIn("exit", ast.dump(stop))


class Keys(BotTestCase):
    def run_keys(self, private: bool, *argv, stdin="abc\n"):
        flag, calls = os.path.join(self.tmp, "launch_authorized.json"), []
        self.patch(update_keys, "FLAG", flag)
        self.patch(update_keys, "HERE", self.tmp)
        self.patch(update_keys, "set_key", lambda k, v: calls.append((k, v)))
        self.patch(update_keys.subprocess, "run", lambda *a, **k: calls.append(a[0]))
        self.patch(update_keys, "sync_launch_flag", lambda *a, **k: self.fail("there's no flag to sync"))
        self.patch(update_keys.server_confirm, "_load_env", lambda: {})
        if private:
            os.makedirs(os.path.join(self.tmp, "ops"))
        with mock.patch.object(sys, "argv", ["update_keys.py", *argv]), mock.patch.object(sys, "stdin", io.StringIO(stdin)):
            update_keys.main()
        return calls

    def test_a_public_install_saves_a_key_with_no_flag(self):
        calls = self.run_keys(False, "--set", "ALPACA_API_KEY")
        self.assertEqual(calls[0], ("ALPACA_API_KEY", "abc"))
        self.assertIn(["/usr/local/bin/charles-restart"], calls)

    def test_this_servers_copy_still_refuses_without_the_flag(self):
        with self.assertRaises(SystemExit):
            self.run_keys(True, "--set", "ALPACA_API_KEY")

    def test_the_menu_offers_the_chats_key(self):
        """The guide's chat needs this key, and the menu was the first place the owner looked for it."""
        import server_confirm
        self.patch(server_confirm, "_load_env", lambda: {"ALPACA_API_KEY": "a", "ALPACA_SECRET_KEY": "b"})
        saved = {}
        self.patch(server_confirm, "_save_env", saved.update)
        answers = iter(["2", "4", "sk-test", "4"])
        with mock.patch("builtins.input", lambda *a: next(answers)), mock.patch("sys.stdout", io.StringIO()):
            server_confirm.run_confirmation()
        self.assertEqual(saved.get("ANTHROPIC_API_KEY"), "sk-test")

