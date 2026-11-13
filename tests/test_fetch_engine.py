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

class PnlTest(unittest.TestCase):
    def test_starts_at_zero_and_counts_a_closed_trade(self):
        eng = fe.Engine("BTC-USD", rt.settings({"FEE": 0.0}), data=tempfile.mkdtemp())
        eng.on_trade(1_000_000.0, 100.0, 1, "buy")
        p = eng.pnl()
        self.assertEqual((p["total"], p["realized"], p["open"], p["trades"]), (0.0, 0.0, 0.0, 0))
        eng.st.pos = {"entry": 100.0, "qty": 10.0, "invest": 1000.0, "ts": 0, "stop": 99, "target": 102, "rule": "revert"}
        eng.st.cash = 0.0
        eng.bid = 101.0
        p = eng.pnl()
        self.assertAlmostEqual(p["open"], 10 * 101 * (1 - eng.s["SLIP"]) - 1000, places=6)
        self.assertEqual(p["realized"], 0.0)
        eng.st.pos, eng.st.cash = None, 1012.0                     # sold: +$12
        e = rt.Event("sell", 1_000_060.0, "revert", 101.2, 101.2, {}, 99.0, 102.0, "target", 1.2, 12.0, 1, 6, 30.0)
        eng._keep([e])
        p = eng.pnl()
        self.assertEqual((round(p["total"], 6), p["wins"], p["losses"]), (12.0, 1, 0))
        self.assertEqual(eng.pnl_hist[-1][1], 12.0)

    def test_book_keeps_since_and_the_line(self):
        d = tempfile.mkdtemp()
        eng = fe.Engine("BTC-USD", rt.settings({}), data=d)
        eng.since = 123.0
        eng.pnl_hist.append([200.0, 1.5])
        eng.wins = 2
        eng.save_book()
        new = fe.Engine("BTC-USD", rt.settings({}), data=d)
        new.restore(fe.saved_book("BTC-USD", d))
        self.assertEqual((new.since, list(new.pnl_hist), new.wins), (123.0, [[200.0, 1.5]], 2))
        self.assertIsNone(fe.saved_book("ETH-USD", d))

    def test_today_counts_from_the_day_start(self):
        eng = fe.Engine("BTC-USD", rt.settings({}), data=tempfile.mkdtemp())
        day = 86400 * 20000
        eng.pnl_hist.extend([[day - 600, 5.0]])
        eng.st.cash = rt.new_state(fe.START_CASH).cash + 8.0
        self.assertAlmostEqual(eng.pnl_today(day + 3600, None), 3.0)


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


class ControlsTest(unittest.TestCase):
    """The user's pause and ticker pick (fetch_control.py, data/.../control.json)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _ctl(self, **d):
        with open(os.path.join(self.dir, "control.json"), "w") as f:
            json.dump(d, f)

    def test_pause_drops_buys_but_not_sells(self):
        eng = fe.Engine("BTC-USD", rt.settings({"FEE": 0.0}), data=self.dir)
        self._ctl(paused=True, since="x", by="t")
        self.assertIsNone(eng.poll(T0))
        self.assertTrue(eng.paused)
        eng.st.pending = ("buy", {})
        self.assertEqual(eng.on_trade(T0 + 1, 100.0, 1, "buy"), [])
        self.assertIsNone(eng.st.pending)
        eng.st.pos = {"entry": 100.0, "qty": 10.0, "invest": 1000.0, "ts": T0, "stop": 95, "target": 101, "rule": "revert"}
        eng.st.cash = 0.0
        ev = eng.on_trade(T0 + 2, 101.5, 1, "buy")
        self.assertEqual((ev[0].kind, ev[0].reason), ("sell", "target"))
        eng.write_live(T0 + 3)
        with open(os.path.join(self.dir, "live.json")) as f:
            live = json.load(f)
        self.assertTrue(live["paused"])
        self.assertTrue(any(c["product"] == "ETH-USD" for c in live["choices"]))

    def test_a_new_ticker_closes_the_trade_and_carries_the_pnl(self):
        eng = fe.Engine("BTC-USD", rt.settings({"FEE": 0.0, "SLIP": 0.0}), data=self.dir)
        eng.st.pos = {"entry": 100.0, "qty": 10.0, "invest": 1000.0, "ts": T0, "stop": 90, "target": 120, "rule": "revert"}
        eng.st.cash = 0.0
        eng.bid = 105.0
        self._ctl(product="ETH-USD")
        self.assertEqual(eng.poll(T0), "ETH-USD")
        ev = eng.switch("ETH-USD", T0)
        self.assertEqual((ev[0].reason, round(ev[0].pnl, 6)), ("switch", 50.0))
        self.assertIn("new ticker", rt.say(ev[0]))
        back = fe.load_book("ETH-USD", self.dir)
        self.assertAlmostEqual(back.cash, 1050.0)
        self.assertIsNone(back.pos)
        self.assertEqual(fe.saved_book("ETH-USD", self.dir)["wins"], 1)

    def test_a_ticker_off_the_list_or_of_the_other_kind_is_ignored(self):
        eng = fe.Engine("BTC-USD", rt.settings({}), data=self.dir)
        self._ctl(product="SPY")
        self.assertIsNone(eng.poll(T0))
        self._ctl(product="FAKE-USD")
        self.assertIsNone(eng.poll(T0 + 10))

    def test_chosen_product(self):
        self._ctl(product="SOL-USD")
        cfg = os.path.join(self.dir, "fetch.json")
        with open(cfg, "w") as f:
            json.dump({"PRODUCT": "BTC-USD"}, f)
        self.assertEqual(fe.chosen_product(self.dir, cfg), "SOL-USD")
        self._ctl(product="AAPL")
        self.assertEqual(fe.chosen_product(self.dir, cfg), "BTC-USD")


class FetchControlTest(unittest.TestCase):
    def test_set_ticker_and_pause_write_control_json(self):
        import fetch_control as fc
        root = tempfile.mkdtemp()
        self.assertEqual(fc.ticker("stock", root), "SPY")
        self.assertIn("can't", fc.set_ticker("stock", "GME", root))
        self.assertIn("Apple", fc.set_ticker("stock", "AAPL", root))
        self.assertEqual(fc.ticker("stock", root), "AAPL")
        self.assertIn("already", fc.set_ticker("stock", "AAPL", root))
        self.assertIn("paused", fc.set_paused("stock", True, "t", root))
        self.assertTrue(fc.paused("stock", root))
        self.assertEqual(fc.ticker("stock", root), "AAPL")                   # pause kept the pick
        self.assertIn("resumed", fc.set_paused("stock", False, "t", root))
        self.assertIsNone(fc.paused("stock", root))


class FlowTest(unittest.TestCase):
    def test_last_ten_minutes_by_side(self):
        eng = fe.Engine("BTC-USD", rt.settings({}), data=tempfile.mkdtemp())
        eng.on_trade(T0 + 5, 100.0, 2, "buy")
        eng.on_trade(T0 + 65, 100.0, 1, "sell")
        eng.on_trade(T0 + 70, 100.0, 1, "buy")
        self.assertEqual(eng.flow10(T0 + 80), {"minutes": 10, "buy": 300.0, "sell": 100.0})
        self.assertEqual(eng.flow10(T0 + 10 * 60), {"minutes": 10, "buy": 100.0, "sell": 100.0})   # the first minute aged out
