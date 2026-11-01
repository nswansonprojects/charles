"""addons.py: crypto and Kalshi are add-ons, off on a new install; on = its timer is enabled in systemd."""
import os
import tempfile

from base import BotTestCase

import addons


class Addons(BotTestCase):
    def setUp(self):
        super().setUp()
        self.sd = tempfile.mkdtemp(dir=self.tmp)                       # a stand-in for /etc/systemd/system
        self.calls = []
        self.run = lambda cmd: self.calls.append(cmd)

    def enable(self, timer):
        os.makedirs(os.path.join(self.sd, "timers.target.wants"), exist_ok=True)
        open(os.path.join(self.sd, "timers.target.wants", timer), "w").close()

    def test_off_until_the_timer_is_enabled(self):
        self.assertEqual(addons.enabled(self.sd), {"crypto": False, "kalshi": False})
        self.enable("charles-crypto.timer")
        self.assertEqual(addons.enabled(self.sd), {"crypto": True, "kalshi": False})

    def test_turning_crypto_on_installs_its_units_and_starts_it(self):
        wrote = addons.on("crypto", run=self.run, systemd_dir=self.sd)
        self.assertEqual(sorted(wrote), ["charles-crypto.service", "charles-crypto.timer"])
        with open(os.path.join(self.sd, "charles-crypto.service")) as f:
            self.assertIn(os.path.join(addons.ROOT, "crypto_paper.py"), f.read())
        self.assertEqual(self.calls, [["systemctl", "daemon-reload"],
                                      ["systemctl", "enable", "--now", "charles-crypto.timer"],
                                      ["systemctl", "start", "--no-block", "charles-crypto.service"]])

    def test_a_unit_already_there_is_never_replaced(self):
        # the owner's server has its own (sandboxed) units
        with open(os.path.join(self.sd, "charles-crypto.service"), "w") as f:
            f.write("mine")
        self.assertEqual(addons.on("crypto", run=self.run, systemd_dir=self.sd), ["charles-crypto.timer"])
        with open(os.path.join(self.sd, "charles-crypto.service")) as f:
            self.assertEqual(f.read(), "mine")

    def test_turning_kalshi_off_stops_both_its_services(self):
        addons.off("kalshi", run=self.run)
        self.assertEqual(self.calls, [["systemctl", "disable", "--now", "kalshi-net.timer", "kalshi-web.service"]])

    def test_kalshi_needs_its_folder(self):
        self.patch(addons, "ROOT", self.tmp)
        with self.assertRaises(addons.AddonError):
            addons.on("kalshi", run=self.run, systemd_dir=self.sd)
        self.assertEqual(self.calls, [])

    def test_status_for_the_accounts_page(self):
        self.enable("kalshi-net.timer")
        rows = {r["id"]: r for r in addons.status(self.sd)}
        self.assertEqual((rows["crypto"]["on"], rows["kalshi"]["on"]), (False, True))
        self.assertEqual(rows["crypto"]["command"], "charles-addon on crypto")
        self.assertEqual(rows["kalshi"]["command"], "charles-addon off kalshi")
