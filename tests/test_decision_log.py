"""brain._log_decision: inputs (features, sentiment) are saved for the future learning model."""
import json
import os

from base import BotTestCase

import brain


class DecisionLog(BotTestCase):
    def test_features_and_sentiment_saved(self):
        path = os.path.join(self.tmp, "decisions.jsonl")
        old = brain.DECISIONS_FILE
        brain.DECISIONS_FILE = path
        try:
            d = {"price": 10.0, "rsi": 55, "vol_ratio": 2.5, "ma50_dist_pct": 4.0, "above_ma50": True,
                 "price_history": [1, 2, 3], "rsi_21": None}
            brain._log_decision("ABC", d, False, "RANGING", {"action": "BUY", "confidence": 0.7},
                                {"action": "BUY"}, sentiment=0.3)
        finally:
            brain.DECISIONS_FILE = old
        row = json.loads(open(path).read().splitlines()[0])
        self.assertEqual(row["sentiment"], 0.3)
        self.assertEqual(row["features"], {"vol_ratio": 2.5, "ma50_dist_pct": 4.0, "above_ma50": True})
