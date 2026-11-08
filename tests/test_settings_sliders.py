"""The Plan tab's sliders (owner 2026-10-31): one way to change a plan, checked strictly, undoable, and never reaching the daily loss limit."""
import json
import os
import tempfile
from datetime import datetime, timezone
from unittest import mock

import stock_settings
import crypto_settings
import updates
import webdash as wd
from base import BotTestCase

NOW = datetime(2026, 11, 2, 15, tzinfo=timezone.utc)
PLAN = {"CORE_PCT": 0.80, "PICKS": 4, "PICK_PCT": 0.05, "DRIFT_PCT": 0.03, "TREND_DAYS": 200, "SAFETY_SWITCH": True, "SWITCH_CHECK": "monthly",
        "AGGRESSIVE": True, "AGG_CORE": "SSO", "AGG_VOL_MAX": 0.20, "AGG_VOL_EXIT": 0.25}


class CryptoChange(BotTestCase):
    def setUp(self):
        super().setUp()
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.path, self.hist = os.path.join(self.d.name, "crypto.json"), os.path.join(self.d.name, "hist.jsonl")
        self.tells = []
        self.patch(crypto_settings, "_tell", self.tells.append)

    def apply(self, changes):
        return crypto_settings.apply_settings(changes, "the dashboard", now=NOW, path=self.path, history=self.hist)

    def read(self):
        with open(self.path) as f:
            return json.load(f)

    def test_a_new_install_has_no_file_and_gets_the_default_plan(self):
        self.assertEqual(crypto_settings.current(self.path), {"BTC_PCT": 0.6, "ETH_PCT": 0.4, "TREND_DAYS": 200, "CHECK_DAYS": 7, "MIN_TRADE": 0.05,
                                                              "INVESTED": 1.0, "BLOCKS": None})

    def test_the_change_lands_in_the_file_the_book_reads(self):
        ok, said = self.apply({"BTC_PCT": 0.5, "ETH_PCT": 0.3})
        self.assertTrue(ok)
        self.assertEqual(crypto_settings.load(self.path)["WEIGHTS"], {"BTC-USD": 0.5, "ETH-USD": 0.3})
        self.assertEqual(crypto_settings.load(self.path)["TREND_DAYS"], 200)
        self.assertIn("from the sliders", self.read()["last_change"])
        self.assertEqual(len(self.tells), 1)
        self.assertIn("Crypto settings changed", said)

    def test_undo_goes_back_one_change_at_a_time(self):
        self.apply({"TREND_DAYS": 100})
        self.apply({"TREND_DAYS": 150})
        crypto_settings.undo_settings("the dashboard", NOW, self.path, self.hist)
        self.assertEqual(crypto_settings.current(self.path)["TREND_DAYS"], 100)
        crypto_settings.undo_settings("the dashboard", NOW, self.path, self.hist)
        self.assertEqual(crypto_settings.current(self.path)["TREND_DAYS"], 200)
        self.assertIsNone(crypto_settings.last_change(self.hist))
        self.assertIn("no change to undo", crypto_settings.undo_settings("the dashboard", NOW, self.path, self.hist)[1])

    def test_a_bad_value_is_refused_not_clamped(self):
        for bad in ({"BTC_PCT": 0.8, "ETH_PCT": 0.8}, {"TREND_DAYS": 10}, {"TREND_DAYS": 150.5}, {"FEE": 0.0}, {"CHECK_DAYS": True}, {}, "x", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.apply(bad)
        self.assertFalse(os.path.exists(self.path))
        self.assertFalse(os.path.exists(self.hist))

    def test_the_same_values_change_nothing(self):
        self.assertIn("already", self.apply({"TREND_DAYS": 200})[1])
        self.assertFalse(os.path.exists(self.path))


class CryptoCopy(BotTestCase):
    def test_a_crypto_copy_can_be_undone_and_keeps_every_setting(self):
        with tempfile.TemporaryDirectory() as d:
            os.mkdir(os.path.join(d, "data"))
            self.patch(crypto_settings, "_tell", lambda t: None)
            cfg = {"id": "lb-1a2b3c", "name": "quiet", "bot": "crypto", "settings": {"BTC_PCT": 0.5, "ETH_PCT": 0.4, "TREND_DAYS": 200, "CHECK_DAYS": 7, "MIN_TRADE": 0.05}}
            ok, said = updates.copy_config("lb-1a2b3c", "the dashboard", NOW, fetch=lambda e: cfg, root=d)
            self.assertTrue(ok)
            self.assertIn("quiet", said)
            path, hist = os.path.join(d, "crypto.json"), os.path.join(d, "data", "crypto_settings_history.jsonl")
            self.assertEqual(crypto_settings.current(path)["BTC_PCT"], 0.5)
            self.assertEqual(crypto_settings.last_change(hist)["changes"], {"BTC_PCT": 0.5})
            crypto_settings.undo_settings("the dashboard", NOW, path, hist)
            self.assertEqual(crypto_settings.current(path)["BTC_PCT"], 0.6)


class Job(BotTestCase):
    """What the root service does with a slider's request."""
    def setUp(self):
        super().setUp()
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.patch(updates, "DATA", self.d.name)
        self.patch(updates, "check", lambda *a, **k: {})
        self.started = []

    def queue(self, action, **k):
        return updates.queue(action, "the dashboard", now=NOW, start=lambda: self.started.append(1), **k)

    def test_a_crypto_job_says_which_bot_and_runs_on_crypto(self):
        applied = []
        self.patch(crypto_settings, "apply_settings", lambda *a, **k: (applied.append((a, k)), (True, "done"))[1])
        self.patch(stock_settings, "apply_settings", lambda *a, **k: self.fail("the stock plan was changed"))
        self.queue("apply-settings", settings={"TREND_DAYS": 150}, bot="crypto", source="the sliders")
        self.assertEqual((updates.read_job()["bot"], updates.read_job()["source"]), ("crypto", "the sliders"))
        self.assertEqual(updates.run_job(NOW), "done")
        self.assertEqual(applied[0][0][0], {"TREND_DAYS": 150})
        self.assertEqual(applied[0][1]["source"], "the sliders")

    def test_a_stock_job_from_the_sliders_says_so(self):
        applied = []
        self.patch(stock_settings, "check_settings", lambda c, *a, **k: c)
        self.patch(stock_settings, "apply_settings", lambda *a, **k: (applied.append(k), (True, "done"))[1])
        self.queue("apply-settings", settings={"PICKS": 2}, source="the sliders")
        updates.run_job(NOW)
        self.assertEqual(applied[0]["source"], "the sliders")
        self.assertTrue(applied[0]["custom"])                  # only the sliders may change the custom plan

    def test_a_request_without_a_source_cant_touch_the_custom_plan(self):
        applied = []
        self.patch(stock_settings, "check_settings", lambda c, *a, **k: c)
        self.patch(stock_settings, "apply_settings", lambda *a, **k: (applied.append(k), (True, "done"))[1])
        self.queue("apply-settings", settings={"PICKS": 2})
        updates.run_job(NOW)
        self.assertEqual(applied[0]["source"], "the dashboard")
        self.assertFalse(applied[0]["custom"])

    def test_crypto_undo_stays_on_crypto(self):
        undone = []
        self.patch(crypto_settings, "undo_settings", lambda *a, **k: (undone.append(a), (True, "undone"))[1])
        self.patch(stock_settings, "undo_settings", lambda *a, **k: self.fail("the stock plan was undone"))
        self.queue("undo-settings", bot="crypto", source="the sliders")
        self.assertEqual(updates.run_job(NOW), "undone")
        self.assertTrue(undone)

    def test_what_the_queue_refuses(self):
        for k in ({"action": "apply-settings", "settings": {"PICKS": 2}, "bot": "kalshi"},
                  {"action": "apply-settings", "settings": {"INVESTED": 0.5}, "bot": "crypto", "source": "the dashboard"},     # only the sliders and self-tune set your own plan
                  {"action": "apply-settings", "settings": {"PICKS": 2}, "source": "someone"},
                  {"action": "apply-settings", "settings": {"MAX_DAILY_LOSS": 1}},
                  {"action": "apply-settings", "settings": {"FEE": 0}, "bot": "crypto"}):
            with self.subTest(k=k):
                self.queue(k.pop("action"), **k)
                self.assertEqual(updates.read_job()["status"], "refused")
        self.assertFalse(self.started)


class Dashboard(BotTestCase):
    def post(self, cfg=None, **body):
        return wd.settings_post(json.dumps(body).encode(), cfg)

    def test_it_only_leaves_a_request_and_only_with_the_login(self):
        with mock.patch.object(wd, "LEADERBOARD_BOT_DIR", wd.ROOT), mock.patch.object(wd, "STATE_FROM", ""), mock.patch.object(wd.control, "request") as asked, \
                mock.patch.object(wd.auth, "access", return_value="open"):
            self.assertEqual(self.post(bot="stocks", settings={"PICKS": 2, "PICK_PCT": 0.1})[0], 403)
            asked.assert_not_called()
        with mock.patch.object(wd, "LEADERBOARD_BOT_DIR", wd.ROOT), mock.patch.object(wd, "STATE_FROM", ""), mock.patch.object(wd.control, "request") as asked, \
                mock.patch.object(wd.auth, "access", return_value="public"):
            self.assertEqual(self.post({}, bot="stocks", settings={"PICKS": 2, "PICK_PCT": 0.1})[0], 202)
            asked.assert_called_once_with("apply-settings", "the dashboard", bot="stocks", source="the sliders", settings={"PICKS": 2, "PICK_PCT": 0.1})

    def test_bad_requests_never_reach_the_root_service(self):
        with mock.patch.object(wd, "LEADERBOARD_BOT_DIR", wd.ROOT), mock.patch.object(wd, "STATE_FROM", ""), mock.patch.object(wd.control, "request") as asked, \
                mock.patch.object(wd.auth, "access", return_value="public"):
            for body in ({"bot": "stocks", "settings": {"MAX_DAILY_LOSS": 1}}, {"bot": "stocks", "settings": {"CORE_PCT": 2}}, {"bot": "stocks", "settings": {"PICKS": 8, "PICK_PCT": 0.1}},
                         {"bot": "kalshi", "settings": {"PICKS": 2}}, {"bot": "crypto", "settings": {"BTC_PCT": 0.9, "ETH_PCT": 0.9}}, {"bot": "stocks", "settings": {}}, {"bot": "crypto", "settings": {"PICKS": 2}}):
                with self.subTest(body=body):
                    self.assertEqual(self.post({}, **body)[0], 400)
            self.assertEqual(wd.settings_post(b"not json", {})[0], 400)
            asked.assert_not_called()

    def test_both_plans_have_the_history_test(self):
        # owner 2026-11-03: the crypto plan gets everything the stock plan has, "Test it on past prices" too
        with mock.patch.object(wd, "LEADERBOARD_BOT_DIR", wd.ROOT), mock.patch.object(wd, "STATE_FROM", ""), mock.patch.object(wd.control, "request") as asked, \
                mock.patch.object(wd.auth, "access", return_value="public"):
            for bot in ("crypto", "stocks"):
                self.assertEqual(self.post({}, bot=bot, settings={"TREND_DAYS": 150}, backtest=True)[1]["action"], "backtest-settings")
            self.assertEqual([c.kwargs["bot"] for c in asked.call_args_list], ["crypto", "stocks"])

    def test_undo_asks_for_the_right_bot(self):
        with mock.patch.object(wd, "LEADERBOARD_BOT_DIR", wd.ROOT), mock.patch.object(wd, "STATE_FROM", ""), mock.patch.object(wd.control, "request") as asked, \
                mock.patch.object(wd.auth, "access", return_value="public"):
            self.assertEqual(self.post({}, bot="crypto", undo=True)[1]["action"], "undo-settings")
            asked.assert_called_once_with("undo-settings", "the dashboard", bot="crypto", source="the sliders")

    def test_a_preview_without_a_test_folder_refuses(self):
        with mock.patch.object(wd, "LEADERBOARD_BOT_DIR", wd.ROOT), mock.patch.object(wd, "STATE_FROM", "http://x"), mock.patch.object(wd.control, "request") as asked:
            self.assertEqual(self.post(bot="stocks", settings={"PICKS": 2, "PICK_PCT": 0.1})[0], 409)
            asked.assert_not_called()

    def test_a_preview_changes_only_its_test_folder_and_can_undo(self):
        state = {"rules": {"settings": {**PLAN, "PICKS": 3, "CASH_PCT": 0.0}}}
        with tempfile.TemporaryDirectory() as d, mock.patch.object(wd, "LEADERBOARD_BOT_DIR", d), mock.patch.object(wd, "STATE_FROM", "http://x"), \
                mock.patch.object(wd, "_cached", return_value=state), mock.patch.object(wd.control, "request") as asked, \
                mock.patch.object(stock_settings, "_tell", side_effect=AssertionError("told someone")):
            v = wd.settings_view("stocks", None, None)
            self.assertEqual((v["settings"]["PICKS"], v["can"], v["test"], v["can_undo"]), (3, True, True, False))
            self.assertNotIn("CASH_PCT", v["settings"])                      # the live bot's value, and only real settings
            code, res = self.post(bot="stocks", settings={"PICKS": 2})
            self.assertEqual((code, res["done"]), (200, True))
            self.assertEqual(wd.settings_view("stocks", None, None)["settings"]["PICKS"], 2)
            self.assertTrue(wd.settings_view("stocks", None, None)["can_undo"])
            self.post(bot="stocks", undo=True)
            self.assertEqual(wd.settings_view("stocks", None, None)["settings"]["PICKS"], 3)
            self.assertEqual(self.post(bot="stocks", settings={"TREND_DAYS": 150}, backtest=True)[0], 409)
            self.assertEqual(sorted(os.listdir(d)), ["stocks_settings_history.jsonl", "strategy.json"])
            asked.assert_not_called()

    def test_a_copy_on_a_preview_can_be_undone_too(self):
        from leaderboard import client
        cfg = {"id": "lb-1a2b3c", "name": "x", "bot": "crypto", "settings": {"BTC_PCT": 0.5, "ETH_PCT": 0.4, "TREND_DAYS": 200, "CHECK_DAYS": 7, "MIN_TRADE": 0.05}}
        with tempfile.TemporaryDirectory() as d, mock.patch.object(wd, "LEADERBOARD_BOT_DIR", d), mock.patch.object(client, "fetch", return_value=cfg):
            self.assertEqual(wd.leaderboard_copy(b'{"id": "lb-1a2b3c"}', None)[0], 200)
            self.assertEqual(wd.settings_view("crypto", None, None)["settings"]["BTC_PCT"], 0.5)
            self.post(bot="crypto", undo=True)
            self.assertEqual(wd.settings_view("crypto", None, None)["settings"]["BTC_PCT"], 0.6)

    def test_what_the_view_says_about_the_login(self):
        with mock.patch.object(wd, "LEADERBOARD_BOT_DIR", wd.ROOT), mock.patch.object(wd, "STATE_FROM", ""), mock.patch.object(stock_settings, "last_change", return_value=None), \
                mock.patch.object(wd.auth, "access", return_value="open"):
            v = wd.settings_view("stocks", None, None)
            self.assertFalse(v["can"])
            self.assertIn("charles-login", v["why"])
        with mock.patch.object(wd, "LEADERBOARD_BOT_DIR", wd.ROOT), mock.patch.object(wd, "STATE_FROM", ""), mock.patch.object(stock_settings, "last_change", return_value=None), \
                mock.patch.object(wd.auth, "access", return_value="public"), mock.patch.object(wd.auth, "check", return_value=None):
            self.assertIn("Log in", wd.settings_view("stocks", {}, None)["why"])
        with mock.patch.object(wd, "LEADERBOARD_BOT_DIR", wd.ROOT), mock.patch.object(wd, "STATE_FROM", ""), mock.patch.object(stock_settings, "last_change", return_value=None), \
                mock.patch.object(wd.auth, "access", return_value="public"), mock.patch.object(wd.auth, "check", return_value="sid"):
            v = wd.settings_view("stocks", {}, None)
            self.assertTrue(v["can"])
            self.assertIsNone(v["why"])

    def test_the_view_gives_real_limits_and_the_default_plan(self):
        with mock.patch.object(wd, "LEADERBOARD_BOT_DIR", wd.ROOT), mock.patch.object(wd, "STATE_FROM", ""), mock.patch.object(wd.auth, "access", return_value="open"), \
                mock.patch.object(stock_settings, "last_change", return_value=None), mock.patch.object(crypto_settings, "last_change", return_value=None):
            s = wd.settings_view("stocks", None, None)
            self.assertEqual(s["limits"]["CORE_PCT"], {"min": 0.5, "max": 0.95})
            self.assertEqual(s["defaults"]["PICKS"], 4)
            self.assertNotIn("MAX_DAILY_LOSS", {**s["limits"], **s["defaults"], **s["settings"]})
            self.assertNotIn("SAFETY_SWITCH", s["limits"])
            c = wd.settings_view("crypto", None, None)
            self.assertEqual(c["limits"]["TREND_DAYS"], {"min": 50, "max": 300})
            self.assertEqual(c["defaults"]["BTC_PCT"], 0.6)
            self.assertFalse(wd.settings_view("kalshi", None, None)["ok"])
