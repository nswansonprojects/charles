"""alerts.py: notices go to the dashboard's file, nothing leaves the server, and old Telegram code is gone."""
import importlib
import json
import os
import re
from datetime import datetime, timedelta, timezone

from base import BotTestCase

import webdash as wd

alerts = importlib.import_module("alerts")      # the real module (BotTestCase swaps in a fake for the others)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Notices(BotTestCase):
    def setUp(self):
        super().setUp()
        self.file = os.path.join(self.tmp, "notifications.json")
        self.patch(alerts, "_NOTIF_FILE", self.file)

    def rows(self):
        with open(self.file) as f:
            return json.load(f)

    def test_daily_summary_names_aggressive_mode(self):
        alerts.daily_summary({"total_value": 1070.55, "day_change": 2.0, "total_return_pct": 7.0},
                             {"mode": "aggressive", "next_rebalance": "2026-11-02"})
        self.assertIn("Aggressive mode", self.rows()[-1]["message"])
        self.assertIn("next re-pick 2026-11-02", self.rows()[-1]["message"])

    def test_problems_ask_for_a_look_and_routine_notes_dont(self):
        alerts.error_alert("Rebalance error: boom")
        alerts.daily_loss_halted(-2100.0, 2042.0)
        alerts.bot_started()
        self.assertEqual([r["level"] for r in self.rows()], ["error", "error", "info"])

    def test_ops_notes_set_their_own_level(self):
        for text, level in (("✅ Charles: all good\nValue $1,000", "info"), ("⚠️ Charles: needs you\n- a key expired", "error"),
                            ("⚠️ Charles daily check-up failed to run (exit 1).", "error")):
            alerts.notice(text)
            self.assertEqual(self.rows()[-1]["level"], level)
        self.assertEqual(self.rows()[0]["title"], "Charles: all good")
        self.assertEqual(self.rows()[1]["message"], "- a key expired")

    def test_only_the_last_50_are_kept(self):
        for i in range(55):
            alerts.bot_started()
        self.assertEqual(len(self.rows()), 50)

    def test_the_dashboard_shows_the_last_week_newest_first(self):
        now = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
        with open(self.file, "w") as f:
            json.dump([{"ts": (now - timedelta(days=9)).isoformat(), "title": "old", "message": "", "level": "info"},
                       {"ts": "not a date", "title": "broken"},
                       {"ts": (now - timedelta(days=2)).isoformat(), "title": "first", "message": "m", "level": "error"},
                       {"ts": (now - timedelta(hours=1)).isoformat(), "title": "second", "level": "strange"}], f)
        got = wd.notices(self.file, now)["notices"]
        self.assertEqual([n["title"] for n in got], ["second", "first"])
        self.assertEqual([n["level"] for n in got], ["info", "error"])

    def test_no_file_is_no_notices(self):
        self.assertEqual(wd.notices(self.file + ".missing")["notices"], [])


class NoTelegram(BotTestCase):
    def test_no_code_or_setup_mentions_telegram(self):
        found = []
        for folder, _, files in os.walk(ROOT):
            if any(p in folder for p in ("/.git", "__pycache__", "/kalshi", "/journal", "/data", "/logs", "/backtest_cache")):
                continue
            for name in files:
                path = os.path.join(folder, name)
                rel = os.path.relpath(path, ROOT)
                if name.endswith((".png", ".db")) or name.startswith(".env") or rel in ("docs/redesign-plan.md", "ops/roadmap.md", "tests/test_alerts.py", "ops/droplet_test.py") or rel.startswith("docs/roadmap-"):
                    continue          # the old plans are history
                try:
                    if re.search("telegram", open(path, errors="ignore").read(), re.I):
                        found.append(rel)
                except OSError:
                    pass
        self.assertEqual(found, [])
