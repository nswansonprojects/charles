"""connections: key format checks, saving privately, status without secrets, the terminal key prompt."""
import json
import os
import stat

from base import BotTestCase

import connections as cn

PEM = "-----BEGIN EC PRIVATE KEY-----\nMHcCAQEEIabc\n-----END EC PRIVATE KEY-----"
ALPACA = {"key_id": "PKABCDEFGHIJKLMNOP12", "secret": "a" * 40}


class Connections(BotTestCase):
    def setUp(self):
        super().setUp()
        self.store = os.path.join(self.tmp, "connections.json")
        self.alpaca_ok = lambda k, s: (True, "Paper account found, $1,000.00.")

    def test_formats_are_checked(self):
        self.assertEqual(cn.validate("charles", {"key_id": " PKABCDEFGHIJKLMNOP12 ", "secret": "a" * 40})["key_id"],
                         "PKABCDEFGHIJKLMNOP12")
        for bot, fields in [("charles", {"key_id": "PK1", "secret": "x"}),
                            ("charles", {"key_id": "AKABCDEFGHIJKLMNOP12", "secret": "a" * 40}),   # live key
                            ("kalshi", {"key_id": "not-a-uuid", "private_key": PEM}),
                            ("crypto", {"key_id": "organizations/1/apiKeys/2", "private_key": "hello"}),
                            ("nope", {})]:
            with self.assertRaises(cn.ConnectError):
                cn.validate(bot, fields)
        ok = cn.validate("crypto", {"key_id": "organizations/ab-1/apiKeys/cd-2", "private_key": PEM.replace("\n", "\\n")})
        self.assertIn("\n", ok["private_key"])                  # pasted with literal \n still works

    def test_connect_saves_privately_and_status_never_shows_secrets(self):
        cn.connect("charles", dict(ALPACA), self.store, alpaca=self.alpaca_ok)
        self.assertEqual(stat.S_IMODE(os.stat(self.store).st_mode), 0o600)
        with open(self.store) as f:
            saved = json.load(f)
        rows = {r["id"]: r for r in cn.public_status(saved)}
        self.assertTrue(rows["charles"]["connected"])
        self.assertEqual(rows["charles"]["tail"], "OP12")
        self.assertNotIn(ALPACA["secret"], json.dumps(rows))
        self.assertNotIn(ALPACA["key_id"], json.dumps(rows))
        self.assertFalse(rows["kalshi"]["connected"])
        cn.disconnect("charles", self.store)
        with open(self.store) as f:
            self.assertEqual(json.load(f), {})

    def test_rejected_keys_are_not_saved(self):
        with self.assertRaises(cn.ConnectError):
            cn.connect("charles", dict(ALPACA), self.store,
                       alpaca=lambda k, s: (False, "Alpaca turned these keys down."))
        self.assertFalse(os.path.exists(self.store))

    def test_server_keys_show_charles_connected(self):
        row = cn.public_status({}, server_alpaca=True)[0]
        self.assertEqual((row["connected"], row["source"], row["tail"]), (True, "server", None))

    def test_status_carries_each_accounts_terminal_command(self):
        rows = {r["id"]: r["command"] for r in cn.public_status({})}
        self.assertEqual(rows, {"charles": "charles-keys", "kalshi": "charles-connect kalshi", "crypto": "charles-connect crypto"})

    def test_pasted_private_key_is_read_up_to_the_end_line(self):
        import io
        from unittest import mock
        with mock.patch("sys.stdin", io.StringIO(PEM + "\nnext answer\n")), mock.patch("builtins.print"):
            self.assertEqual(cn._ask_pem("Private key (PEM)"), PEM)
