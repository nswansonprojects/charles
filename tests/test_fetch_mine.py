"""Fetch's own-rule hook (RULE "mine", fetch_my_rule.py) and fetch_test.py's replay, without the network."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fetch_plan as rt                      # noqa: E402
import fetch_test as ft                      # noqa: E402

T0 = 20000 * 86400


def bars(prices):
    return [rt.Bar(T0 + i * 60, p, p * 1.0005, p * 0.9995, p, 1.0) for i, p in enumerate(prices)]


class Mine(unittest.TestCase):
    def setUp(self):
        self.saved = dict(rt._my)

    def tearDown(self):
        rt._my.clear()
        rt._my.update(self.saved)

    def use(self, mod, why=None):
        rt._my.clear()
        rt._my.update(mod=mod, why=why)

    def test_the_example_rule_buys_and_says_why(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("ex", os.path.join(os.path.dirname(rt.__file__), "fetch_my_rule.example.py"))
        ex = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ex)
        self.use(ex)
        s = rt.settings({"RULE": "mine", "FEE": 0.0})
        st, ev = rt.new_state(1000.0), []
        for b in bars([100.0] * 30 + [100.1, 100.2, 100.3, 100.4, 100.5, 100.6]):
            ev += rt.step(st, b, s)
        self.assertEqual([e.kind for e in ev], ["buy"])
        self.assertIn("Three rising minutes", rt.say(ev[0]))
        flat = rt.new_state(1000.0)
        flat.c = [100.0] * 40
        self.assertIn("30 minute average", rt.say(rt.watch(flat, 100.5, s)))

    def test_a_broken_or_missing_rule_buys_nothing_and_says_so(self):
        boom = types.SimpleNamespace(NEED=2, entry=lambda c, h, l, s: 1 / 0)
        self.use(boom)
        s = rt.settings({"RULE": "mine"})
        st = rt.new_state(1000.0)
        for b in bars([100.0 + i for i in range(10)]):
            self.assertEqual(rt.step(st, b, s), [])
        self.use(None, "there is no fetch_my_rule.py yet")
        w = rt.watch(rt.new_state(1000.0), 100.0, s)
        self.assertIn("can't run", rt.say(w))

    def test_mine_exits_by_target_stop_and_time_only(self):
        self.assertIsNone(rt.signal_exit("mine", 5, 90.0, 100.0, rt.settings({"HOLD": 60})))
        self.assertEqual(rt.signal_exit("mine", 60, 90.0, 100.0, rt.settings({"HOLD": 60})), "time")


class Tester(unittest.TestCase):
    def test_replay_matches_step_and_reports(self):
        s = rt.settings({"RULE": "breakout", "LOOK": 5, "SLOW": 10, "FAST": 5, "TARGET": 1.0, "STOP": 0.5, "HOLD": 30, "FEE": 0.0})
        prices = [100.0] * 20 + [100.0 + 0.2 * i for i in range(1, 15)] + [103.0] * 10
        r = ft.report("BTC-USD", bars(prices), s)
        self.assertGreaterEqual(r["trades"], 1)
        self.assertEqual(r["won"], r["trades"])
        self.assertGreater(r["return_pct"], 0)
        self.assertIsNotNone(r["luck_pct"])
        self.assertTrue(any("random entries" in x for x in ft.verdict(r)))

    def test_no_trades_says_so(self):
        r = ft.report("BTC-USD", bars([100.0] * 60), rt.settings({"RULE": "revert"}))
        self.assertEqual((r["trades"], r["luck_pct"]), (0, None))
        self.assertIn("never fired", ft.verdict(r)[0])

    def test_a_ticker_off_the_list_is_refused(self):
        with self.assertRaises(SystemExit):
            ft.main(["--product", "FAKE-USD"])


if __name__ == "__main__":
    unittest.main()
