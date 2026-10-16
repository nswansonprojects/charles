"""rebalancer: monthly decisions, the news veto, plain reasons, and a full run against a fake broker."""
import json
import os
from datetime import datetime, timezone

from base import BotTestCase

import allocator as al
import alpaca_executor as ex
import news_veto
import rebalancer as rb
import risk_manager as rm

S = dict(al.DEFAULTS, AGGRESSIVE=False)       # aggressive mode has its own tests below
AGG = dict(al.DEFAULTS)


def rising(n, start=100.0, step=0.5):
    return [start + step * i for i in range(n)]


def market(spy_up=True):
    closes = {"SPY": rising(300) if spy_up else rising(299) + [50.0], "SGOV": [100.0] * 300}
    for k, sym in enumerate(["NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META"]):
        closes[sym] = rising(300, step=1.0 - k * 0.1)
    return closes


class Decide(BotTestCase):
    def test_first_run_picks_the_top_four(self):
        d = rb.decide("2026-10-19", market(), {}, S)
        self.assertTrue(d["full"])
        self.assertEqual(d["mode"], al.INVEST)
        self.assertEqual(d["picks"], ["NVDA", "AAPL", "MSFT", "GOOGL"])
        self.assertEqual(d["targets"]["SPY"], 0.8)

    def test_mid_month_keeps_picks_and_mode(self):
        state = {"mode": al.INVEST, "picks": ["AAPL", "MSFT", "GOOGL", "AMZN"], "last_rebalance": "2026-10-19"}
        d = rb.decide("2026-10-20", market(spy_up=False), state, S)
        self.assertFalse(d["full"])
        self.assertEqual(d["mode"], al.INVEST)            # switch only looked at monthly
        self.assertEqual(d["picks"], state["picks"])

    def test_month_end_below_average_goes_to_safety(self):
        state = {"mode": al.INVEST, "picks": ["NVDA"], "last_rebalance": "2026-10-19"}
        d = rb.decide("2026-11-02", market(spy_up=False), state, S)
        self.assertTrue(d["switched"])
        self.assertEqual(d["targets"], {"SGOV": 1.0})
        self.assertEqual(d["picks"], [])

    def test_daily_check_setting(self):
        state = {"mode": al.INVEST, "picks": ["NVDA"], "last_rebalance": "2026-10-19"}
        d = rb.decide("2026-10-20", market(spy_up=False), state, dict(S, SWITCH_CHECK="daily"))
        self.assertTrue(d["switched"] and d["full"])

    def test_switch_off_always_invests(self):
        d = rb.decide("2026-11-02", market(spy_up=False), {"mode": al.INVEST, "last_rebalance": "2026-10-19"},
                      dict(S, SAFETY_SWITCH=False))
        self.assertEqual(d["mode"], al.INVEST)

    def test_veto_hands_the_slot_to_the_next_stock(self):
        asked = []
        def veto(sym, mom):
            asked.append(sym)
            return {"veto": sym == "AAPL", "reason": "SEC probe"}
        state = {"mode": al.INVEST, "picks": ["NVDA"], "last_rebalance": "2026-10-19"}
        d = rb.decide("2026-11-02", market(), state, S, veto=veto)
        self.assertEqual(d["picks"], ["NVDA", "MSFT", "GOOGL", "AMZN"])
        self.assertEqual(d["vetoes"], [{"symbol": "AAPL", "reason": "SEC probe", "rank": 2}])
        self.assertNotIn("NVDA", asked)                     # already held: not re-checked

    def test_pending_rebalance_is_finished_next_day(self):
        state = {"mode": al.INVEST, "picks": ["NVDA"], "last_rebalance": "2026-10-01", "pending": True}
        self.assertTrue(rb.decide("2026-10-02", market(), state, S)["full"])


class Reasons(BotTestCase):
    def test_plain_sentences(self):
        d = rb.decide("2026-10-19", market(), {}, S)
        r = lambda o, st={}: rb.reason(o, d, st, S, first_run=True)
        self.assertIn("old trading rules", r({"symbol": "XOM", "side": "sell", "all": True}))
        self.assertIn("whole market", r({"symbol": "SPY", "side": "buy", "all": False}))
        self.assertIn("New momentum pick", r({"symbol": "NVDA", "side": "buy", "all": False}))
        self.assertIn("rank 1 of 6", r({"symbol": "NVDA", "side": "buy", "all": False}))
        self.assertIn("Kept as a momentum pick", r({"symbol": "NVDA", "side": "sell", "all": False, "held": True}))

    def test_dropped_pick(self):
        state = {"mode": al.INVEST, "picks": ["META"], "last_rebalance": "2026-10-01"}
        d = rb.decide("2026-11-02", market(), state, S)
        txt = rb.reason({"symbol": "META", "side": "sell", "all": True}, d, state, S, first_run=False)
        self.assertIn("left the top 4", txt)
        self.assertIn("ranked 6", txt)

    def test_safety_sale(self):
        state = {"mode": al.INVEST, "picks": ["NVDA"], "last_rebalance": "2026-10-01"}
        d = rb.decide("2026-11-02", market(spy_up=False), state, S)
        self.assertIn("Safety mode", rb.reason({"symbol": "NVDA", "side": "sell", "all": True}, d, state, S, False))


class NextRebalance(BotTestCase):
    def test_first_weekday_of_next_month(self):
        self.assertEqual(rb.next_rebalance("2026-10-19"), "2026-11-02")    # Nov 1 is a Sunday
        self.assertEqual(rb.next_rebalance("2026-12-15"), "2027-01-01")
        self.assertEqual(rb.next_rebalance("2027-01-04"), "2027-02-01")


class FakeClient:
    pass


class Run(BotTestCase):
    """A full rebalance against a fake account: old positions out, the plan in, logged and saved."""
    def setUp(self):
        super().setUp()
        self.plan = os.path.join(self.tmp, "plan.json")
        self.patch(ex, "TRADE_LOG_FILE", os.path.join(self.tmp, "trades.json"))
        self.held = {"XOM": 100.0, "ONON": 70.0}
        self.cash = 830.0
        self.calls = []

        def summary():
            return {"total_value": round(self.cash + sum(self.held.values()), 2), "cash": self.cash,
                    "day_change": -3.0, "positions": [{"symbol": k, "market_value": v} for k, v in self.held.items()]}

        def sell(c, sym, amount):
            v = self.held.pop(sym) if amount is None else amount
            if amount is not None:
                self.held[sym] -= amount
            self.cash += v
            self.calls.append(("sell", sym, amount))
            return {"order_id": "s1", "status": "filled", "value": v, "price": 10.0, "qty": v / 10,
                    "pnl": 5.0, "pnl_pct": 5.0}

        def buy(c, sym, amount):
            self.cash -= amount
            self.held[sym] = self.held.get(sym, 0) + amount
            self.calls.append(("buy", sym, amount))
            return {"order_id": "b1", "status": "filled", "value": amount, "price": 10.0, "qty": amount / 10}

        self.patch(ex, "_client", lambda: FakeClient())
        self.patch(ex, "market_open", lambda c=None: True)
        self.patch(ex, "get_account_summary", summary)
        self.patch(ex, "sell", sell)
        self.patch(ex, "buy", buy)
        self.patch(rb, "closes_for", lambda syms, today: (market(), "2026-10-16"))
        self.patch(rb, "settings", lambda: dict(S))
        self.patch(news_veto, "check", lambda sym, mom=None: {"veto": False, "reason": ""})

    def test_switchover(self):
        now = datetime(2026, 10, 19, 14, 0, tzinfo=timezone.utc)
        res = rb.run(plan_path=self.plan, now=now)
        self.assertEqual(self.calls[:2], [("sell", "XOM", None), ("sell", "ONON", None)])
        bought = {c[1]: c[2] for c in self.calls if c[0] == "buy"}
        self.assertEqual(set(bought), {"SPY", "NVDA", "AAPL", "MSFT", "GOOGL"})
        self.assertAlmostEqual(bought["SPY"], 800.0)
        with open(self.plan) as f:
            st = json.load(f)
        self.assertEqual((st["mode"], st["last_rebalance"], st["started"]), ("invest", "2026-10-19", "2026-10-19"))
        self.assertEqual(st["next_rebalance"], "2026-11-02")
        with open(ex.TRADE_LOG_FILE) as f:
            trades = json.load(f)
        self.assertEqual(len(trades), 7)
        self.assertTrue(all(t["source"] == "rebalance" and t["reasoning"] for t in trades))
        self.assertGreater(self.cash, 0)                      # never spends more than it has
        self.assertEqual(len(res["done"]), 7)
        self.assertEqual(self.alerts.calls[-1][0], "rebalanced")

    def test_second_run_same_day_does_nothing(self):
        now = datetime(2026, 10, 19, 14, 0, tzinfo=timezone.utc)
        rb.run(plan_path=self.plan, now=now)
        n = len(self.calls)
        rb.run(plan_path=self.plan, now=now.replace(hour=18))
        self.assertEqual(len(self.calls), n)

    def test_loss_limit_blocks_buys_and_retries_tomorrow(self):
        self.patch(rm, "is_trading_halted", lambda: True)
        rb.run(plan_path=self.plan, now=datetime(2026, 10, 19, 14, 0, tzinfo=timezone.utc))
        self.assertEqual([c[0] for c in self.calls], ["sell", "sell"])
        with open(self.plan) as f:
            st = json.load(f)
        self.assertTrue(st["pending"])
        self.assertIsNone(st["last_rebalance"])

    def test_market_closed_does_nothing(self):
        self.patch(ex, "market_open", lambda c=None: False)
        self.assertEqual(rb.run(plan_path=self.plan), {"skipped": "market closed"})
        self.assertFalse(os.path.exists(self.plan))

    def test_dry_run_places_nothing(self):
        res = rb.run(dry_run=True, plan_path=self.plan, now=datetime(2026, 10, 19, 14, 0, tzinfo=timezone.utc))
        self.assertEqual(self.calls, [])
        self.assertFalse(os.path.exists(self.plan))
        self.assertEqual(len(res["orders"]), 7)

    def test_no_spy_history_does_nothing(self):
        self.patch(rb, "closes_for", lambda syms, today: ({"NVDA": rising(300)}, "2026-10-16"))
        self.assertEqual(rb.run(plan_path=self.plan, now=datetime(2026, 10, 19, 14, tzinfo=timezone.utc)),
                         {"skipped": "no price history"})
        self.assertEqual(self.calls, [])


def choppy(n=300, start=100.0):
    """Rising over the year, but swinging ±3% a day lately (well above the 25% a year exit)."""
    c = rising(n - 20, start)
    for i in range(20):
        c.append(c[-1] * (1.03 if i % 2 else 0.975))
    return c


class Aggressive(BotTestCase):
    def test_calm_rising_market_uses_the_2x_fund_from_the_monthly_check(self):
        d = rb.decide("2026-11-02", market(), {"mode": al.INVEST, "picks": ["NVDA"], "last_rebalance": "2026-10-01"}, AGG)
        self.assertEqual(d["mode"], al.AGGRESSIVE)
        self.assertEqual(d["targets"]["SSO"], 0.8)
        self.assertNotIn("SPY", d["targets"])

    def test_not_started_mid_month(self):
        state = {"mode": al.INVEST, "picks": ["NVDA"], "last_rebalance": "2026-11-02"}
        self.assertEqual(rb.decide("2026-11-10", market(), state, AGG)["mode"], al.INVEST)

    def test_choppy_day_drops_leverage_at_once_without_a_repick(self):
        closes = market(); closes["SPY"] = choppy()
        state = {"mode": al.AGGRESSIVE, "picks": ["AAPL", "MSFT"], "last_rebalance": "2026-11-02"}
        d = rb.decide("2026-11-10", closes, state, AGG)
        self.assertEqual(d["mode"], al.INVEST)
        self.assertTrue(d["switched"] and d["full"])
        self.assertEqual(d["picks"], ["AAPL", "MSFT"])          # only the core fund changes
        self.assertEqual(d["targets"]["SPY"], 0.8)

    def test_below_the_average_drops_leverage_but_waits_for_the_month_end_switch(self):
        state = {"mode": al.AGGRESSIVE, "picks": ["NVDA"], "last_rebalance": "2026-11-02"}
        d = rb.decide("2026-11-10", market(spy_up=False), state, AGG)
        self.assertEqual(d["mode"], al.INVEST)

    def test_choppy_month_start_stays_plain(self):
        closes = market(); closes["SPY"] = choppy()
        d = rb.decide("2026-12-01", closes, {"mode": al.INVEST, "last_rebalance": "2026-11-02"}, AGG)
        self.assertEqual(d["mode"], al.INVEST)

    def test_toggle_off_never_uses_it(self):
        d = rb.decide("2026-11-02", market(), {"mode": al.INVEST, "last_rebalance": "2026-10-01"}, dict(AGG, AGGRESSIVE=False))
        self.assertEqual(d["mode"], al.INVEST)

    def test_reasons(self):
        d = rb.decide("2026-11-02", market(), {"mode": al.INVEST, "picks": ["NVDA"], "last_rebalance": "2026-10-01"}, AGG)
        st = {"mode": al.INVEST, "picks": ["NVDA"]}
        self.assertIn("Aggressive mode", rb.reason({"symbol": "SSO", "side": "buy", "all": False}, d, st, AGG, False))
        self.assertIn("Swapped", rb.reason({"symbol": "SPY", "side": "sell", "all": True}, d, st, AGG, False))
        closes = market(); closes["SPY"] = choppy()
        d2 = rb.decide("2026-11-10", closes, {"mode": al.AGGRESSIVE, "picks": ["NVDA"], "last_rebalance": "2026-11-02"}, AGG)
        self.assertIn("choppy", rb.reason({"symbol": "SSO", "side": "sell", "all": True}, d2, {"mode": al.AGGRESSIVE}, AGG, False))


class Volatility(BotTestCase):
    def test_annualised_swings(self):
        self.assertEqual(al.volatility([100.0] * 30), 0.0)
        self.assertGreater(al.volatility(choppy()), 0.25)
        self.assertLess(al.volatility(rising(300)), 0.05)
        self.assertIsNone(al.volatility([100.0] * 10))
