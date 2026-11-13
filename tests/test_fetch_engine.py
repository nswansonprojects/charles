"""Charles Fetch's live engine (fetch_engine.py) without the network: bars from trades, the book, the files it writes."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fetch_engine as fe                    # noqa: E402
import fetch_plan as rt                      # noqa: E402

T0 = 20000 * 86400


class Bars(unittest.TestCase):
    def test_a_new_minute_closes_the_last(self):
        b = fe.BarBuilder()
        self.assertEqual(b.add(T0 + 1, 100, 1), [])
        b.add(T0 + 20, 103, 2)
        b.add(T0 + 40, 99, 1)
        out = b.add(T0 + 61, 101, 1)
        self.assertEqual(out, [rt.Bar(T0, 100, 103, 99, 99, 4)])
        self.assertEqual(b.forming()[1], 101)

    def test_flush_closes_a_quiet_minute(self):
        b = fe.BarBuilder()
        b.add(T0 + 5, 100)
        self.assertEqual(b.flush(T0 + 59), [])
        self.assertEqual(len(b.flush(T0 + 60)), 1)
        self.assertIsNone(b.forming())

    def test_a_late_trade_closes_nothing(self):
        b = fe.BarBuilder()
        b.add(T0 + 65, 100)
        self.assertEqual(b.add(T0 + 30, 90), [])
        self.assertEqual(b.forming()[3], 100)


class EngineRun(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_a_dip_buys_then_the_target_sells_and_files_are_written(self):
        s = rt.settings({"RULE": "revert", "SLOW": 5, "DIP": 1.0, "TARGET": 1.0, "STOP": 5.0, "HOLD": 500, "SLIP": 0.0, "FEE": 0.0})
        eng = fe.Engine("BTC-USD", s, data=self.dir)
        eng.warm([rt.Bar(T0 + i * 60, p, p, p, p) for i, p in enumerate([100, 100, 100, 100, 100, 101, 102, 103, 104, 105])])
        ev = []
        t = T0 + 600
        ev += eng.on_trade(t, 100.0, 0.1, "sell")      # a minute far under the rising average
        self.assertEqual(ev, [])
        eng.on_quote(100.4, 100.6)
        ev += eng.on_trade(t + 61, 100.5, 0.1, "buy")  # the next minute's first trade closes it; the buy fills on it, at the ask
        self.assertEqual([e.kind for e in ev], ["buy"])
        self.assertEqual(ev[0].price, 100.6)
        ev = eng.on_trade(t + 62, 102.0, 0.1, "buy")
        self.assertEqual((ev[0].kind, ev[0].reason), ("sell", "target"))
        eng.on_clock(t + 3)
        eng.write_live(t + 3)
        with open(os.path.join(self.dir, "live.json")) as f:
            live = json.load(f)
        self.assertTrue(live["practice"])
        self.assertEqual(len(live["events"]), 2)
        self.assertTrue(live["watching"])
        self.assertEqual(live["tape"][-1]["p"], 102.0)
        with open(os.path.join(self.dir, "events.jsonl")) as f:
            lines = f.read().splitlines()
        self.assertIn("Target reached", json.loads(lines[-1])["say"])
        back = fe.load_book("BTC-USD", self.dir)
        self.assertGreater(back.cash, 1000.0)
        self.assertIsNone(fe.load_book("ETH-USD", self.dir))  # another coin starts a new book

    def test_settings_file(self):
        p = os.path.join(self.dir, "fetch.json")
        with open(p, "w") as f:
            json.dump({"PRODUCT": "ETH-USD", "RULE": "breakout", "TARGET": 99}, f)
        s = fe.settings_from(p)
        self.assertEqual((s["RULE"], s["TARGET"]), ("breakout", 10.0))   # clamped
        self.assertEqual(fe.product_from(p), "ETH-USD")
        self.assertEqual(fe.product_from(os.path.join(self.dir, "none.json")), "BTC-USD")

    def test_the_repo_settings_are_valid(self):
        with open(fe.SETTINGS_PATH) as f:
            raw = json.load(f)
        rt.check({k: v for k, v in raw.items() if k != "PRODUCT"})


# ------------------------------------------------------------------ a stock (IEX)

class StockTest(unittest.TestCase):
    def test_calendar_tries_live_keys_after_paper(self):
        import urllib.error
        seen = []

        def get(url, keys):
            seen.append(url)
            if "paper-api" in url:
                raise urllib.error.HTTPError(url, 403, "forbidden", None, None)
            return [{"date": url.split("start=")[1][:10], "open": "09:30", "close": "13:00"}]
        old = fe._get
        fe._get = get
        try:
            ses = fe.session_today({"key": "k", "secret": "s"}, now=1794588223.0)
        finally:
            fe._get = old
        self.assertEqual(len(seen), 2)
        self.assertEqual(ses.close - ses.open, 3.5 * 3600)               # a half day keeps its own close


    def test_is_stock(self):
        from fetch_engine import is_stock
        assert is_stock("SPY") and is_stock("BRK.B") and not is_stock("BTC-USD") and not is_stock("spy") and not is_stock("")


    def test_taker_side_from_quote(self):
        from fetch_engine import taker_side
        assert taker_side(100.02, 100.0, 100.02, None) == "buy"
        assert taker_side(100.0, 100.0, 100.02, None) == "sell"
        assert taker_side(100.015, 100.0, 100.02, None) == "buy"      # nearer the ask
        assert taker_side(100.01, 100.0, 100.02, 100.02) == "sell"    # the middle: down from the last trade
        assert taker_side(100.01, None, None, 100.0) == "buy"


    def test_rfc_ts_nanoseconds(self):
        from fetch_engine import rfc_ts
        assert abs(rfc_ts("2026-11-13T14:30:00.123456789Z") - 1794580200.123456) < 1e-5
        assert rfc_ts("2026-11-13T14:30:00Z") == 1794580200.0


    def test_stock_trades_outside_session_left_out(self):
        import fetch_engine as fe
        import fetch_plan as rt
        ses = rt.us_session("2026-11-13")
        eng = fe.Engine("SPY", rt.settings({}), data=tempfile.mkdtemp(), session=ses)
        assert eng.on_trade(ses.open - 600, 500.0, 10, "buy") == [] and not eng.tape     # pre-market
        eng.on_trade(ses.open + 60, 500.0, 10, "buy")
        assert len(eng.tape) == 1
        eng.on_trade(ses.close + 60, 501.0, 10, "buy")                                    # after hours
        assert len(eng.tape) == 1
        v = eng.view(ses.open + 90)
        assert v["market"] == "stock" and v["open"] and v["session"] == [ses.open, ses.close]


    def test_stock_closed_sentence(self):
        import fetch_engine as fe
        import fetch_plan as rt
        ses = rt.us_session("2026-11-13")
        eng = fe.Engine("SPY", rt.settings({}), data=tempfile.mkdtemp(), session=ses)
        eng.on_clock(ses.open - 1800)
        assert "opens in 30 minutes" in eng.watch_say
        eng.session = None
        eng.watch_at = 0
        eng.on_clock(ses.close + 3600)
        assert "closed" in eng.watch_say


    def test_coin_unchanged_by_session(self):
        import fetch_engine as fe
        import fetch_plan as rt
        eng = fe.Engine("BTC-USD", rt.settings({}), data=tempfile.mkdtemp())
        eng.on_trade(1_000_000.0, 70000.0, 0.1, "buy")
        assert len(eng.tape) == 1 and eng.view(1_000_001.0)["market"] == "coin"


if __name__ == "__main__":
    unittest.main()
