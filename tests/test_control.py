"""control: the dashboard's requests (pause, resume) carried out by the root service, logged and told."""
import json
import os
from datetime import datetime, timezone

from base import BotTestCase

import control

NOW = datetime(2026, 10, 29, 20, 0, tzinfo=timezone.utc)


class Requests(BotTestCase):
    def setUp(self):
        super().setUp()
        os.makedirs(control.REQ_DIR)

    def test_a_dashboard_request_is_carried_out_once_and_told(self):
        control.request("pause", "the dashboard", NOW)
        self.assertEqual(len(os.listdir(control.REQ_DIR)), 1)
        said = control.run()
        self.assertEqual(len(said), 1)
        self.assertIn("paused", said[0])
        self.assertEqual(os.listdir(control.REQ_DIR), [])                          # used up
        self.assertEqual(control.paused()["by"], "the dashboard")
        self.assertEqual([c[0] for c in self.alerts.calls], ["control"])
        self.assertEqual(control.run(), [])

    def test_resume_and_repeats(self):
        self.assertIn("wasn't paused", control.apply("resume", "SSH", NOW))
        control.apply("pause", "SSH", NOW)
        self.assertIn("already paused", control.apply("pause", "SSH", NOW))
        self.assertIn("resumed", control.apply("resume", "SSH", NOW))
        self.assertIsNone(control.paused())
        with open(control.LOG_FILE) as f:
            self.assertEqual([json.loads(x)["action"] for x in f], ["resume", "pause", "pause", "resume"])
        self.assertEqual(len(self.alerts.calls), 2)                                 # only real changes are told

    def test_junk_is_dropped_not_obeyed(self):
        for name, body in (("1-sell.json", '{"action": "sell_everything"}'), ("2-bad.json", "{oops"),
                           ("3-big.json", json.dumps({"action": "pause", "pad": "x" * 5000}))):
            with open(os.path.join(control.REQ_DIR, name), "w") as f:
                f.write(body)
        self.assertEqual(control.run(), [])
        self.assertEqual(os.listdir(control.REQ_DIR), [])
        self.assertIsNone(control.paused())
        with self.assertRaises(ValueError):
            control.request("sell_everything", "the dashboard")

    def test_leftovers_are_cleared_and_a_write_in_progress_is_waited_for(self):
        old = os.path.join(control.REQ_DIR, ".x.json.abc.tmp")
        with open(old, "w") as f:
            f.write("{")
        os.utime(old, (0, 0))                                                      # an hour-old crash leftover
        fresh = os.path.join(control.REQ_DIR, ".y.json.def.tmp")
        with open(fresh, "w") as f:
            f.write("{")
        self.assertEqual(control.run(wait=0), [])
        self.assertEqual(os.listdir(control.REQ_DIR), [".y.json.def.tmp"])         # still being written: kept

    def test_a_damaged_pause_file_pauses_until_resume(self):
        with open(control.PAUSE_FILE, "w") as f:
            f.write("{bad")
        self.assertIn("damaged", control.paused()["by"])
        self.assertIn("resumed", control.apply("resume", "SSH", NOW))
        self.assertIsNone(control.paused())
