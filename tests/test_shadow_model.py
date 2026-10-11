"""shadow_model: a second AI model answers the live prompts in the background; never blocks."""
import json
import os
import threading

from base import BotTestCase

import brain
import shadow_model as sm

PROMPTS = {"AAA": {"prompt": "p-a", "held": False, "rule_signal": "BUY", "price": 10.0},
           "BBB": {"prompt": "p-b", "held": True, "rule_signal": "HOLD", "price": 20.0},
           "CCC": {"prompt": "p-c", "held": False, "rule_signal": "HOLD", "price": 30.0}}
LIVE = [{"symbol": "AAA", "action": "HOLD"}, {"symbol": "BBB", "action": "HOLD"}]


def answer(action, conf=0.7):
    return 'thinking... {"action": "%s", "confidence": %s, "reasoning": "x"}' % (action, conf)


class SecondModel(BotTestCase):
    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.tmp, "b.jsonl")

    def rows(self):
        with open(self.path) as f:
            return [json.loads(l) for l in f]

    def test_logs_each_answer_like_the_live_log(self):
        n = sm.run(PROMPTS, LIVE, "RANGING", ask=lambda p: answer("BUY"), sleep=lambda s: None, path=self.path)
        self.assertEqual(n, 3)
        r = self.rows()[0]
        self.assertEqual((r["symbol"], r["ai_action"], r["ai_conf"], r["held"], r["live_action"]),
                         ("AAA", "BUY", 0.7, False, "HOLD"))
        self.assertEqual((r["price"], r["regime"], r["rule_signal"]), (10.0, "RANGING", "BUY"))
        self.assertTrue(r["source"].startswith("llm:"))

    def test_429_stops_the_cycle_and_errors_skip_one(self):
        def ask(p):
            if p == "p-a":
                raise ValueError("bad gateway")
            raise sm.RateLimited()
        n = sm.run(PROMPTS, LIVE, "RANGING", ask=ask, sleep=lambda s: None, path=self.path)
        self.assertEqual(n, 0)
        self.assertFalse(os.path.exists(self.path))
        calls = []
        def ask2(p):
            calls.append(p)
            return "no json here" if p == "p-a" else answer("SELL")
        n = sm.run(PROMPTS, LIVE, "RANGING", ask=ask2, sleep=lambda s: None, path=self.path)
        self.assertEqual((n, len(calls)), (2, 3))                   # unparseable answer skipped

    def test_parse(self):
        self.assertEqual(sm.parse(answer("hold", 0.4)), ("HOLD", 0.4))
        self.assertIsNone(sm.parse('{"action": "SHORT"}'))
        self.assertIsNone(sm.parse(""))

    def test_start_runs_in_background_and_skips_while_busy(self):
        gate, done = threading.Event(), threading.Event()
        def fake_run(prompts, decisions, regime):
            gate.wait(2)
            done.set()
            return len(prompts)
        self.patch(sm, "run", fake_run)
        brain._LAST_PROMPTS.update(PROMPTS)
        self.assertTrue(sm.start(LIVE, "RANGING"))                  # returns at once
        self.assertEqual(brain._LAST_PROMPTS, {})                   # prompts handed over
        brain._LAST_PROMPTS.update(PROMPTS)
        self.assertFalse(sm.start(LIVE, "RANGING"))                 # previous run still going
        gate.set()
        self.assertTrue(done.wait(2))
        brain._LAST_PROMPTS.clear()
        self.assertFalse(sm.start(LIVE, "RANGING"))                 # nothing to ask
