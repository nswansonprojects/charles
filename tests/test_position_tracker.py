"""position_tracker: each live position's P&L path, its peak, and what it gave back when sold."""
import json
import os
import types
from datetime import datetime, timedelta, timezone

from base import BotTestCase

import position_tracker as pt

T0 = datetime(2026, 10, 9, 14, 0, tzinfo=timezone.utc)


def pos(sym, entry, price, qty=1.0):
    return types.SimpleNamespace(symbol=sym, avg_entry_price=str(entry), current_price=str(price), qty=str(qty))


class Tracker(BotTestCase):
    def setUp(self):
        super().setUp()
        self.old = (pt.PATHS_FILE, pt.HISTORY_FILE, pt.TRADES_FILE)
        pt.PATHS_FILE = os.path.join(self.tmp, "paths.json")
        pt.HISTORY_FILE = os.path.join(self.tmp, "history.jsonl")
        pt.TRADES_FILE = os.path.join(self.tmp, "trades.json")

    def tearDown(self):
        pt.PATHS_FILE, pt.HISTORY_FILE, pt.TRADES_FILE = self.old
        super().tearDown()

    def trades(self, rows):
        with open(pt.TRADES_FILE, "w") as f:
            json.dump(rows, f)

    def test_peak_and_trough_follow_the_marks(self):
        paths = {}
        for i, px in enumerate([100, 104, 108.6, 101, 97, 103]):
            pt.snapshot(paths, "AMD", 100.0, px, 0.5, T0 + timedelta(minutes=15 * i))
        r = paths["AMD"]
        self.assertEqual((r["peak_pnl"], r["peak_pct"]), (4.3, 8.6))
        self.assertEqual(r["trough_pnl"], -1.5)
        self.assertEqual(r["last_pnl"], 1.5)
        self.assertEqual(len(r["marks"]), 6)

    def test_sold_position_closes_path_with_give_back(self):
        self.trades([{"timestamp": "2026-10-09T13:00:00+00:00", "symbol": "AMD", "action": "BUY"}])
        pt.update([pos("AMD", 100, 100)], T0, price_range=lambda s, since: None)
        pt.update([pos("AMD", 100, 108)], T0 + timedelta(minutes=15))
        self.trades([{"timestamp": "2026-10-09T13:00:00+00:00", "symbol": "AMD", "action": "BUY"},
                     {"timestamp": "2026-10-09T14:40:00+00:00", "symbol": "AMD", "action": "SELL",
                      "pnl": 2.0, "source": "risk_management", "reason": "broker_stop"}])
        done = pt.update([], T0 + timedelta(minutes=45))
        self.assertEqual(len(done), 1)
        d = done[0]
        self.assertEqual((d["peak_pnl"], d["exit_pnl"], d["gave_back"]), (8.0, 2.0, 6.0))
        self.assertEqual(d["exit_source"], "risk_management")
        self.assertEqual(pt.current(), {})
        self.assertEqual(pt.history()[0]["symbol"], "AMD")

    def test_position_bought_before_tracking_is_seeded_from_price_history(self):
        self.trades([{"timestamp": "2026-10-05T15:00:00+00:00", "symbol": "NU", "action": "BUY"}])
        rng = lambda s, since: (17.0, "2026-10-06T15:00", 15.0, "2026-10-07T15:00")
        pt.update([pos("NU", 16.0, 16.2, qty=5)], T0, price_range=rng)
        r = pt.current()["NU"]
        self.assertEqual(r["opened"], "2026-10-05T15:00")
        self.assertEqual((r["peak_pnl"], r["trough_pnl"]), (5.0, -5.0))
        self.assertTrue(r["seeded"])

    def test_adding_to_a_holding_keeps_its_path(self):
        pt.update([pos("X", 10, 12)], T0, price_range=lambda s, since: None)
        pt.update([pos("X", 11, 12, qty=2)], T0 + timedelta(minutes=15))
        r = pt.current()["X"]
        self.assertEqual(len(r["marks"]), 2)
        self.assertEqual(r["peak_pnl"], 2.0)
