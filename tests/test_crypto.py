"""Crypto demo: the trend rule, weekly timing, orders and fills, the paper book's step, the backtest, the page's view."""
import os
from datetime import datetime, timezone, timedelta

from base import BotTestCase

import crypto_backtest
import crypto_market
import crypto_paper
import crypto_plan as cp
import webdash as wd

S = {"TREND_DAYS": 3}


class Plan(BotTestCase):
    def test_each_coin_held_only_above_its_average(self):
        d = cp.decide({"BTC-USD": [10, 10, 10, 13], "ETH-USD": [10, 10, 10, 7]}, S)
        self.assertEqual(d["targets"], {"BTC-USD": 0.6, "ETH-USD": 0.0})
        self.assertTrue(d["coins"]["BTC-USD"]["above"])
        self.assertEqual(d["coins"]["ETH-USD"]["gap_pct"], -22.2)

    def test_short_history_waits_in_cash(self):
        d = cp.decide({"BTC-USD": [10, 12], "ETH-USD": []}, S)
        self.assertEqual(d["targets"], {"BTC-USD": 0.0, "ETH-USD": 0.0})
        self.assertIn("not enough history", cp.reason("ETH-USD", d["coins"]["ETH-USD"], S))

    def test_check_is_weekly(self):
        self.assertTrue(cp.check_due(None, "2026-10-24"))
        self.assertFalse(cp.check_due("2026-10-20T05:00:00+00:00", "2026-10-26"))
        self.assertTrue(cp.check_due("2026-10-20T05:00:00+00:00", "2026-10-27"))

    def test_orders_sell_first_and_skip_small_moves(self):
        prices = {"BTC-USD": 100.0, "ETH-USD": 10.0}
        o = cp.orders({"BTC-USD": 6.0, "ETH-USD": 0.0}, prices, 400.0, {"BTC-USD": 0.0, "ETH-USD": 0.4})
        self.assertEqual([(x["product"], x["side"], x["value"]) for x in o], [("BTC-USD", "SELL", 600.0), ("ETH-USD", "BUY", 400.0)])
        self.assertEqual(cp.orders({"BTC-USD": 6.1, "ETH-USD": 39.0}, prices, 0.0, {"BTC-USD": 0.6, "ETH-USD": 0.4}), [])

    def test_fills_pay_the_fee_and_never_overspend(self):
        prices = {"BTC-USD": 100.0}
        qty, cash = cp.apply({}, 50.0, prices, [{"product": "BTC-USD", "side": "BUY", "value": 80.0, "qty": 0.8}], 0.01)
        self.assertAlmostEqual(qty["BTC-USD"], 0.495)
        self.assertEqual(cash, 0.0)
        qty, cash = cp.apply(qty, cash, prices, [{"product": "BTC-USD", "side": "SELL", "value": 99, "qty": 9.0}], 0.01)
        self.assertAlmostEqual(cash, 49.0050)                  # sells only what it holds
        self.assertEqual(qty["BTC-USD"], 0.0)


class Paper(BotTestCase):
    def test_step_trades_on_first_run_then_waits_a_week(self):
        now = datetime(2026, 10, 24, 6, tzinfo=timezone.utc)
        closes = {"BTC-USD": [1, 1, 1, 2], "ETH-USD": [1, 1, 1, 2]}
        prices = {"BTC-USD": 2.0, "ETH-USD": 2.0}
        b = crypto_paper.step(crypto_paper.new_book(now.isoformat()), closes, prices, now, S)
        self.assertEqual(len(b["trades"]), 2)
        self.assertAlmostEqual(b["curve"][-1][1], 994.0)       # $1,000 less the 0.6% fee
        self.assertEqual(b["start_prices"], prices)
        closes = {"BTC-USD": [2, 2, 2, 1], "ETH-USD": [2, 2, 2, 1]}   # both fall below: no trade until the week is up
        b = crypto_paper.step(b, closes, {"BTC-USD": 1.0, "ETH-USD": 1.0}, now + timedelta(days=3), S)
        self.assertEqual(len(b["trades"]), 2)
        b = crypto_paper.step(b, closes, {"BTC-USD": 1.0, "ETH-USD": 1.0}, now + timedelta(days=7), S)
        self.assertEqual([t["side"] for t in b["trades"][2:]], ["SELL", "SELL"])
        self.assertEqual(b["targets"], {"BTC-USD": 0.0, "ETH-USD": 0.0})

    def test_save_and_load_round_trip(self):
        path = os.path.join(self.tmp, "crypto_paper.json")
        crypto_paper.save(crypto_paper.new_book("2026-10-24T00:00:00+00:00"), path)
        self.assertEqual(crypto_paper.load(path)["cash"], 1000.0)
        self.assertIsNone(crypto_paper.load(os.path.join(self.tmp, "missing.json")))


class Market(BotTestCase):
    def test_candles_become_daily_closes_oldest_first(self):
        t = datetime(2026, 10, 22, tzinfo=timezone.utc).timestamp()
        rows = [[t + 86400, 0, 0, 0, 11.0, 1], [t, 0, 0, 0, 10.0, 1], [t, 0, 0, 0, 10.0, 1]]
        self.assertEqual(crypto_market.candles_to_closes(rows), [["2026-10-22", 10.0], ["2026-10-23", 11.0]])


class Backtest(BotTestCase):
    def test_simulate_holds_then_switches_to_cash(self):
        days = ["d0", "d1", "d2", "d3"]
        closes = {"BTC-USD": {"days": ["a", "b", "c"] + days, "close": [1, 1, 1, 2, 2, 1, 1]},
                  "ETH-USD": {"days": ["a", "b", "c"] + days, "close": [1, 1, 1, 2, 2, 1, 1]}}
        curve = crypto_backtest.simulate(days, closes, {"TREND_DAYS": 3, "CHECK_DAYS": 2, "FEE": 0.0})
        self.assertEqual([v for _, v in curve], [1000.0, 1000.0, 500.0, 500.0])   # bought at 2, sold at 1 on day 2
        hold = crypto_backtest.simulate(days, closes, {"FEE": 0.0}, hold="BTC-USD")
        self.assertEqual(hold[-1][1], 500.0)

    def test_stats(self):
        st = crypto_backtest.stats([["2020-01-01", 1000], ["2020-06-01", 500], ["2021-01-01", 1500]])
        self.assertEqual(st["max_drawdown_pct"], -50.0)
        self.assertEqual(st["yearly"], {"2020": -50.0, "2021": 200.0})


class View(BotTestCase):
    def test_view_values_the_book_at_live_prices(self):
        now = datetime(2026, 10, 25, 6, tzinfo=timezone.utc)
        book = {"started": "2026-10-24T06:00:00+00:00", "start_cash": 1000.0, "cash": 0.0,
                "qty": {"BTC-USD": 6.0, "ETH-USD": 40.0}, "last_check": "2026-10-24T06:00:00+00:00",
                "start_prices": {"BTC-USD": 100.0, "ETH-USD": 10.0},
                "coins": {"BTC-USD": {"average": 80.0}, "ETH-USD": {"average": 12.0}},
                "targets": {"BTC-USD": 0.6, "ETH-USD": 0.4}, "curve": [["2026-10-24T06:00:00+00:00", 994.0]],
                "trades": [{"product": "BTC-USD", "side": "BUY", "value": 600.0, "price": 100.0, "fee": 3.6}]}
        v = wd.crypto_view(book, {"BTC-USD": 110.0}, now)["book"]          # ETH price missing: no crash
        self.assertEqual(v["value"], 6 * 110 + 40 * 0)                     # ETH has no price and no fill → 0
        self.assertEqual(v["btc_pct"], 10.0)
        self.assertEqual(v["coins"][0]["gap_pct"], 37.5)
        self.assertEqual(v["check_progress"], round(1 / 7, 2))
        self.assertEqual(v["curve"][-1][1], 660.0)

    def test_view_without_a_book(self):
        self.assertIsNone(wd.crypto_view(None, {}, datetime.now(timezone.utc))["book"])


class LiveChartData(BotTestCase):
    def test_hourly_candles_are_stamped_when_the_hour_ends(self):
        now = datetime(2026, 10, 24, 7, 30, tzinfo=timezone.utc)
        t6 = datetime(2026, 10, 24, 6, tzinfo=timezone.utc).timestamp()
        rows = [[t6 + 3600, 0, 0, 0, 102.0, 1], [t6, 0, 0, 0, 101.0, 1], [t6 - 3600, 0, 0, 0, 100.0, 1]]
        self.assertEqual(crypto_market.hours_from_candles(rows, now),
                         [["2026-10-24T06:00+00:00", 100.0], ["2026-10-24T07:00+00:00", 101.0]])   # 07:00-08:00 still forming

    def test_cost_basis_counts_fees_and_shrinks_on_sells(self):
        trades = [{"product": "BTC-USD", "side": "BUY", "value": 600.0, "price": 100.0, "fee": 3.6},
                  {"product": "ETH-USD", "side": "BUY", "value": 400.0, "price": 10.0, "fee": 2.4},
                  {"product": "ETH-USD", "side": "SELL", "value": 500.0, "price": 12.5, "fee": 3.0}]   # sells all ether
        self.assertEqual(wd.cost_basis(trades), {"BTC-USD": 600.0})
        half = trades[:1] + [{"product": "BTC-USD", "side": "SELL", "value": 298.2, "price": 100.0, "fee": 1.8}]
        self.assertEqual(wd.cost_basis(half), {"BTC-USD": 300.0})

    def test_today_counts_from_the_value_a_day_ago(self):
        now = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
        book = {"started": "2026-10-24T06:00:00+00:00", "start_cash": 1000.0, "cash": 1010.0, "qty": {},
                "curve": [["2026-10-24T06:00:00+00:00", 1000.0], ["2026-10-24T11:00:00+00:00", 1004.0],
                          ["2026-10-25T06:00:00+00:00", 1008.0]], "trades": []}
        v = wd.crypto_view(book, {}, now)["book"]
        self.assertEqual(v["day_change"], 6.0)
