"""learning_model.py: rows, labels, walk-forward without peeking, progress before it's ready."""
import os
import random
from datetime import date, datetime, timedelta, timezone

from base import BotTestCase

import learning_model as lm


def dec(sym, day, price=100.0, held=False, ai="HOLD", **feat):
    f = {"rsi_7": 50, "above_ma50": True, "hourly_trend": "bullish", **feat}
    return {"ts": f"{day}T15:00:00+00:00", "symbol": sym, "price": price, "held": held, "regime": "RANGING",
            "ai_action": ai, "rsi": 55, "features": f}


class LearningModel(BotTestCase):
    def setUp(self):
        super().setUp()
        self.patch(lm, "OUT_FILE", os.path.join(self.tmp, "lm.json"))

    def test_rows_entries_only_once_per_day(self):
        ds = [dec("A", "2026-10-05"), dec("A", "2026-10-05"), dec("B", "2026-10-05", held=True),
              dict(dec("C", "2026-10-05"), features=None), dec("A", "2026-10-06")]
        self.assertEqual([(r["symbol"], r["day"]) for r in lm.rows(ds)], [("A", "2026-10-05"), ("A", "2026-10-06")])

    def test_label_five_trading_days_later(self):
        r = dict(dec("A", "2026-10-05"), day="2026-10-05")
        closes = [(f"2026-10-{d:02d}", c) for d, c in ((5, 100), (6, 90), (7, 90), (8, 90), (9, 90))]
        self.assertIsNone(lm.label(r, closes))                       # only 4 days after
        self.assertEqual(lm.label(r, closes + [("2026-10-12", 101.0)]), 1)
        self.assertEqual(lm.label(r, closes + [("2026-10-12", 99.0)]), 0)

    def test_progress_only_before_ready(self):
        calls = []
        ds = [dec("A", "2026-10-05"), dec("A", "2026-10-13"), dec("A", "2026-10-19")]
        out = lm.run(closes_for=lambda s: calls.append(s) or [],
                     now=datetime(2026, 10, 22, 20, tzinfo=timezone.utc), decisions=ds)
        self.assertEqual(out["labelled_weeks"], 2)                   # Oct 19 is under 8 days old
        self.assertAlmostEqual(out["progress"], round(2 / 12, 2))
        self.assertNotIn("result", out)
        self.assertEqual(calls, [])                                  # no downloads before ready

    def test_walk_forward_learns_a_real_pattern_out_of_sample(self):
        rng = random.Random(1)
        labelled = []
        d0 = date(2026, 10, 5)
        for i in range(14 * 5):                                       # 14 weeks of weekdays
            day = d0 + timedelta(days=i // 5 * 7 + i % 5)
            for s in range(8):
                x = rng.uniform(-3, 3)
                r = dict(dec(f"S{s}", day.isoformat(), vol_ratio=x, ai="BUY" if s < 4 else "HOLD"),
                         day=day.isoformat(), y=int(x + rng.gauss(0, 0.5) > 0))
                labelled.append(r)
        res = lm.walk_forward(labelled)
        self.assertEqual(res["test_weeks"], 14 - lm.MIN_TRAIN_WEEKS - 1)
        self.assertGreater(res["accuracy_pct"], 85)
        self.assertTrue(res["beats_both"])

    def test_walk_forward_never_trains_on_the_test_week_or_the_one_before(self):
        seen = []
        real_fit = lm.fit
        def spy_fit(X, y, **kw):
            seen.append(len(X))
            return real_fit(X, y, **kw)
        self.patch(lm, "fit", spy_fit)
        labelled = [dict(dec("A", (date(2026, 10, 5) + timedelta(weeks=w)).isoformat()),
                         day=(date(2026, 10, 5) + timedelta(weeks=w)).isoformat(), y=w % 2) for w in range(9)]
        lm.walk_forward(labelled)
        self.assertEqual(seen, [6, 7])                               # test weeks 8 and 9 (0-based 7, 8)
