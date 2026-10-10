"""decision_scorer: grouping, once-per-day counting, horizons, missing data."""
import json
import os

from base import BotTestCase

import decision_scorer as ds

# Closes on the trading days after 2026-10-01 (a Thursday)
CLOSES = {
    "UP":   [("2026-10-01", 100.0), ("2026-10-02", 101.0), ("2026-10-05", 102.0),
             ("2026-10-06", 103.0), ("2026-10-07", 104.0), ("2026-10-08", 105.0)],
    "DOWN": [("2026-10-01", 100.0), ("2026-10-02", 99.0), ("2026-10-05", 98.0),
             ("2026-10-06", 97.0), ("2026-10-07", 96.0), ("2026-10-08", 95.0)],
    "NEW":  [("2026-10-01", 50.0)],     # nothing after the decision day yet
}


def dec(sym, ai="HOLD", rule="HOLD", held=False, conf=0.6, ts="2026-10-01T14:00:00+00:00",
        src="llm:groq", price=100.0):
    return {"ts": ts, "symbol": sym, "price": price, "held": held, "ai_action": ai,
            "ai_conf": conf, "rule_signal": rule, "source": src}


class Scorer(BotTestCase):
    def score(self, decisions):
        return ds.score(decisions, lambda s: CLOSES[s])

    def test_ai_buy_vs_baseline(self):
        g = self.score([dec("UP", ai="BUY", conf=0.7), dec("DOWN")])
        self.assertEqual(g["AI BUY"]["1d"], {"n": 1, "mean_pct": 1.0, "median_pct": 1.0, "up_pct": 100})
        self.assertEqual(g["AI BUY"]["5d"]["mean_pct"], 5.0)
        self.assertEqual(g["AI passed (HOLD/SELL)"]["3d"]["mean_pct"], -3.0)
        self.assertEqual(g["all candidates (baseline)"]["3d"]["n"], 2)
        self.assertEqual(g["all candidates (baseline)"]["3d"]["mean_pct"], 0.0)
        self.assertIn("AI BUY conf 0.65-0.75", g)

    def test_repeats_same_day_count_once(self):
        same_day = [dec("UP", ai="BUY", ts=f"2026-10-01T{h}:00:00+00:00") for h in (14, 15, 16, 17)]
        self.assertEqual(self.score(same_day)["AI BUY"]["1d"]["n"], 1)

    def test_et_day_used_for_evening_utc_timestamps(self):
        # 00:30 UTC on Oct 2 is still Oct 1 in New York, so day 1 is Oct 2's close
        g = self.score([dec("UP", ai="BUY", ts="2026-10-02T00:30:00+00:00")])
        self.assertEqual(g["AI BUY"]["1d"]["mean_pct"], 1.0)

    def test_held_positions_score_exits(self):
        g = self.score([dec("DOWN", ai="SELL", held=True), dec("UP", held=True)])
        self.assertEqual(g["held: AI said SELL"]["1d"]["mean_pct"], -1.0)   # stock fell: good sell
        self.assertEqual(g["held: AI said HOLD"]["1d"]["mean_pct"], 1.0)
        self.assertNotIn("all candidates (baseline)", g)

    def test_rule_fallback_not_counted_as_ai(self):
        g = self.score([dec("UP", ai="BUY", rule="BUY", src="rules")])
        self.assertNotIn("AI BUY", g)
        self.assertIn("rule BUY", g)

    def test_not_enough_days_yet(self):
        g = self.score([dec("NEW", ai="BUY", price=50.0)])
        self.assertEqual(g, {})
        g = self.score([dec("UP", ai="BUY", ts="2026-10-06T14:00:00+00:00")])
        self.assertIn("1d", g["AI BUY"])
        self.assertNotIn("3d", g["AI BUY"])

    def test_bad_rows_and_price_failures_are_skipped(self):
        def closes(s):
            if s == "BROKEN":
                raise RuntimeError("no data")
            return CLOSES[s]
        g = ds.score([{"symbol": "UP"}, dec("BROKEN", ai="BUY"), dec("UP", ai="BUY")], closes)
        self.assertEqual(g["AI BUY"]["1d"]["n"], 1)

    def test_run_writes_scorecard(self):
        path = os.path.join(self.tmp, "decisions.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps(dec("UP", ai="BUY")) + "\nnot json\n")
        self.patch(ds, "DECISIONS_FILE", path)
        self.patch(ds, "SCORECARD_FILE", os.path.join(self.tmp, "scorecard.json"))
        self.patch(ds, "_yf_closes", lambda s, start: CLOSES[s])
        card = ds.run()
        self.assertEqual(card["decisions"], 1)
        with open(ds.SCORECARD_FILE) as f:
            self.assertEqual(json.load(f)["groups"]["AI BUY"]["1d"]["n"], 1)
