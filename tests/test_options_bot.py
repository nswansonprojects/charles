"""Options sleeve (options_bot.py): spread choice, exits, risk budget, and keeping option legs
out of the stock logic (stops / take-profit / time exits must never touch them)."""
import os
import types
from datetime import date

from base import BotTestCase

import alpaca_executor as ax
import options_bot as ob

TODAY = date(2026, 10, 9)
EXP = date(2026, 11, 6)        # 28 days out


def chain(strikes, exp=EXP):
    return [{"symbol": f"X{exp:%y%m%d}C{int(k * 1000):08d}", "strike": k, "expiry": exp} for k in strikes]


def quotes_for(contracts, prices):
    """prices: strike -> (bid, ask)"""
    return {c["symbol"]: prices[c["strike"]] for c in contracts if c["strike"] in prices}


class ChooseSpread(BotTestCase):
    def test_widest_spread_within_max_loss_and_width_rule(self):
        cs = chain([48, 49, 50, 51, 52, 53, 55])
        q = quotes_for(cs, {48: (2.9, 3.1), 49: (2.2, 2.4), 50: (1.6, 1.8), 51: (1.1, 1.3),
                            52: (0.75, 0.85), 53: (0.45, 0.55), 55: (0.15, 0.25)})
        sp, why = ob.choose_spread(50.2, cs, q, TODAY, max_loss=60)
        self.assertIsNone(why)
        self.assertEqual(sp["long_strike"], 50)
        # mids 1.70 / 1.20 / 0.80: 50/51 debit .50 (50% of width); 50/52 debit .90 > $60 cap
        self.assertEqual(sp["short_strike"], 51)
        self.assertAlmostEqual(sp["debit"], 0.50)
        self.assertEqual(sp["max_loss"], 50.0)
        self.assertEqual(sp["max_profit"], 50.0)

    def test_bigger_budget_allows_wider_spread(self):
        cs = chain([50, 51, 52, 53])
        q = quotes_for(cs, {50: (1.6, 1.8), 51: (1.1, 1.3), 52: (0.75, 0.85), 53: (0.45, 0.55)})
        sp, _ = ob.choose_spread(50.0, cs, q, TODAY, max_loss=125)
        self.assertEqual(sp["short_strike"], 53)       # debit 1.20 on width 3 = 40%
        self.assertLessEqual(sp["max_loss"], 125)

    def test_rejects_when_debit_too_close_to_width(self):
        cs = chain([50, 51])
        q = quotes_for(cs, {50: (1.6, 1.8), 51: (0.75, 0.85)})   # debit .90 on width 1
        sp, why = ob.choose_spread(50.0, cs, q, TODAY, max_loss=100)
        self.assertIsNone(sp)
        self.assertIn("no short strike fits", why)

    def test_skips_illiquid_legs_and_out_of_window_expiries(self):
        cs = chain([50, 51])
        q = quotes_for(cs, {50: (0.5, 1.8), 51: (1.1, 1.3)})     # long leg 113% wide
        self.assertIsNone(ob.choose_spread(50.0, cs, q, TODAY, 60)[0])
        near = chain([50, 51], exp=date(2026, 10, 16))            # 7 days: too close
        q2 = quotes_for(near, {50: (1.6, 1.8), 51: (1.1, 1.3)})
        sp, why = ob.choose_spread(50.0, near, q2, TODAY, 60)
        self.assertIsNone(sp)
        self.assertIn("2–6 weeks", why)

    def test_no_budget_no_spread(self):
        cs = chain([50, 51])
        q = quotes_for(cs, {50: (1.6, 1.8), 51: (1.1, 1.3)})
        sp, why = ob.choose_spread(50.0, cs, q, TODAY, max_loss=5)
        self.assertIsNone(sp)
        self.assertIn("budget", why)


class Retry(BotTestCase):
    def test_retry_halfway_to_natural_only_within_rules(self):
        sp = {"debit": 0.40, "natural": 0.60, "width": 1.0}
        self.assertEqual(ob.retry_price(sp, 60), 0.50)
        self.assertIsNone(ob.retry_price(sp, 45))                           # over the max loss
        self.assertIsNone(ob.retry_price({**sp, "natural": 1.0}, 100))     # .70 > 65% of width
        self.assertIsNone(ob.retry_price({**sp, "natural": 0.40}, 60))     # nothing to step up to


class Budget(BotTestCase):
    def test_per_trade_cap_and_total_cap(self):
        self.assertEqual(ob.risk_budget(1000, []), 60.0)
        self.assertEqual(ob.risk_budget(1000, [{"max_loss": 60}, {"max_loss": 60}]), 60.0)
        self.assertAlmostEqual(ob.risk_budget(1000, [{"max_loss": 60}] * 3), 20.0)   # 200 − 180
        self.assertEqual(ob.risk_budget(1000, [{"max_loss": 100}] * 2), 0.0)


class Exits(BotTestCase):
    SP = {"debit": 0.60, "width": 1.0, "expiry": "2026-11-06"}

    def test_take_profit_at_half_max_profit(self):
        self.assertEqual(ob.exit_reason(self.SP, 0.80, TODAY), "take_profit")   # .60 + .5*.40
        self.assertIsNone(ob.exit_reason(self.SP, 0.79, TODAY))

    def test_stop_at_half_debit_lost(self):
        self.assertEqual(ob.exit_reason(self.SP, 0.30, TODAY), "stop_loss")
        self.assertIsNone(ob.exit_reason(self.SP, 0.31, TODAY))

    def test_close_before_expiry_even_without_quotes(self):
        self.assertEqual(ob.exit_reason(self.SP, None, date(2026, 11, 2)), "expiry")
        self.assertIsNone(ob.exit_reason(self.SP, None, TODAY))


def pos(symbol, asset_class):
    return types.SimpleNamespace(symbol=symbol, asset_class=types.SimpleNamespace(value=asset_class),
                                 qty="1", side=types.SimpleNamespace(value="long"), avg_entry_price="1",
                                 current_price="1", market_value="1", unrealized_pl="0", unrealized_plpc="-0.9")


class StockLogicIgnoresOptions(BotTestCase):
    def test_stock_positions_drop_option_legs(self):
        c = types.SimpleNamespace(get_all_positions=lambda: [
            pos("NU", "us_equity"), pos("NU261106C00015000", "us_option"), pos("NU261106C00016000", "us_option")])
        self.assertEqual([p.symbol for p in ax._stock_positions(c)], ["NU"])

    def test_state_file_round_trip(self):
        old = ob.STATE_FILE
        ob.STATE_FILE = os.path.join(self.tmp, "options.json")
        try:
            st = ob.load()
            self.assertEqual(st, {"open": [], "closed": []})
            st["open"].append({"symbol": "NU", "max_loss": 50})
            ob.save(st)
            self.assertEqual(ob.load()["open"][0]["symbol"], "NU")
            self.assertEqual(ob.summary()["at_risk"], 50)
        finally:
            ob.STATE_FILE = old
