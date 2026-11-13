"""Charles Fetch's rules (fetch_plan.py): the three entries, every exit, the fill model, the guards, the stock session, the limits, the narrator.
Synthetic bars only; no network, no data/, logs/ or .env."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fetch_plan as rt                      # noqa: E402
from fetch_plan import Bar                   # noqa: E402

BASE = 20000 * 86400                      # a UTC midnight


def bars(closes, start=0, first_open=None):
    """Flat-ish bars from closes: open = the last close, high and low are the open/close extremes."""
    out, prev = [], closes[0] if first_open is None else first_open
    for i, c in enumerate(closes):
        out.append(Bar(BASE + (start + i) * 60, prev, max(prev, c), min(prev, c), c, 1.0))
        prev = c
    return out


def run(blist, over=None, session=None, state=None):
    s = rt.settings({"SLIP": 0.0, "FEE": 0.0, **(over or {})})
    st = state or rt.new_state(1000.0)
    ev = []
    for b in blist:
        ev += rt.step(st, b, s, session(b) if callable(session) else session)
    return ev, st


PULL = {"RULE": "pullback", "FAST": 2, "SLOW": 5, "LOOK": 5, "DIP": 0.5, "TARGET": 5.0, "STOP": 5.0, "HOLD": 500}
# up, a dip, then back above the two minute average
UP_DIP = [100, 102, 104, 106, 108, 106.8, 106.5, 107.8]


class Entries(unittest.TestCase):
    def test_pullback_enters_on_the_bar_after_the_close_back_above_fast(self):
        ev, st = run(bars(UP_DIP + [108]), PULL)
        self.assertEqual([e.kind for e in ev], ["buy"])
        self.assertEqual(ev[0].price, 107.8)                     # the next bar's open: the signal bar's close
        self.assertEqual(ev[0].numbers["fast_minutes"], 2)
        self.assertGreaterEqual(ev[0].numbers["dip_pct"], 0.5)
        self.assertAlmostEqual(ev[0].stop, 107.8 * 0.95)
        self.assertAlmostEqual(ev[0].target, 107.8 * 1.05)

    def test_pullback_waits_without_a_dip(self):
        ev, _ = run(bars([100, 102, 104, 106, 108, 107.9, 107.8, 108.5, 109]), {**PULL, "DIP": 1.0})
        self.assertEqual(ev, [])

    def test_pullback_needs_the_trend_up(self):
        ev, _ = run(bars([110, 108, 106, 104, 102, 100.5, 100.2, 101.4, 101.5]), PULL)
        self.assertEqual(ev, [])

    def test_pullback_signal_is_the_cross_not_every_bar_above(self):
        ev, st = run(bars(UP_DIP + [108, 108.1, 108.2]), {**PULL, "TARGET": 50.0})
        self.assertEqual(len([e for e in ev if e.kind == "buy"]), 1)

    BREAK = {"RULE": "breakout", "SLOW": 5, "LOOK": 5, "TARGET": 5.0, "STOP": 5.0, "HOLD": 500}

    def test_breakout_fires_above_the_look_high(self):
        ev, _ = run(bars([100, 100.5, 100.2, 100.6, 100.4, 100.5, 101.5, 101.6]), self.BREAK)
        self.assertEqual([e.kind for e in ev], ["buy"])
        self.assertEqual(ev[0].price, 101.5)
        self.assertEqual(ev[0].numbers["look_minutes"], 5)

    def test_breakout_does_not_fire_inside_the_range_or_below_the_slow_average(self):
        ev, _ = run(bars([100, 100.5, 100.2, 100.6, 100.4, 100.5, 100.4, 100.55]), self.BREAK)
        self.assertEqual(ev, [])
        ev, _ = run(bars([110, 109, 108, 107, 106, 105, 104, 103, 102, 108]), {**self.BREAK, "SLOW": 8, "LOOK": 2})
        self.assertEqual(ev, [])                                    # above the 2 minute high, still under the slow average

    REV = {"RULE": "revert", "SLOW": 5, "DIP": 0.5, "TARGET": 5.0, "STOP": 5.0, "HOLD": 500, "FAST": 2, "LOOK": 5}

    def test_revert_buys_a_dip_under_a_rising_slow_average(self):
        closes = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 106.5, 106.5]
        ev, _ = run(bars(closes), self.REV)
        self.assertEqual([e.kind for e in ev][:1], ["buy"])
        self.assertGreaterEqual(ev[0].numbers["gap_pct"], 0.5)
        self.assertGreater(ev[0].numbers["slow"], ev[0].numbers["slow_ago"])

    def test_revert_waits_when_the_slow_average_is_falling_or_the_gap_is_small(self):
        down = [109, 108, 107, 106, 105, 104, 103, 102, 101, 100, 97.5, 97.5]
        self.assertEqual(run(bars(down), self.REV)[0], [])
        small = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 108.9, 108.9]
        self.assertEqual(run(bars(small), self.REV)[0], [])

    def test_revert_sells_when_the_close_returns_to_the_slow_average(self):
        closes = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 106.5, 106.5, 106.6, 109.5, 109.5, 109.5]
        ev, _ = run(bars(closes), self.REV)
        self.assertEqual([e.kind for e in ev], ["buy", "sell"])
        self.assertEqual(ev[1].reason, "trend")
        self.assertEqual(ev[1].rule, "revert")


def long_trade(extra, over=None, slip=0.0, fee=0.0):
    """A pullback buy filling at 107.8, then the `extra` bars."""
    blist = bars(UP_DIP + [108]) + extra
    return run(blist, {**PULL, "SLIP": slip, "FEE": fee, **(over or {})})


def nxt(o, h, l, c):
    return Bar(BASE + 9 * 60 + 60, o, h, l, c, 1.0)


class Exits(unittest.TestCase):
    def after(self, *rows):
        return [Bar(BASE + (9 + i) * 60, *r, 1.0) for i, r in enumerate(rows)]

    def test_target(self):
        ev, st = long_trade(self.after((108, 114, 108, 113)), {"TARGET": 5.0})
        self.assertEqual([e.reason for e in ev if e.kind == "sell"], ["target"])
        sell = ev[-1]
        self.assertAlmostEqual(sell.price, 107.8 * 1.05)
        self.assertAlmostEqual(sell.result_pct, 5.0, places=6)
        self.assertIsNone(st.pos)

    def test_stop(self):
        ev, _ = long_trade(self.after((108, 108.2, 100, 101)), {"STOP": 5.0})
        self.assertEqual(ev[-1].reason, "stop")
        self.assertAlmostEqual(ev[-1].price, 107.8 * 0.95)
        self.assertLess(ev[-1].pnl, 0)

    def test_stop_comes_first_when_one_bar_touches_both(self):
        ev, _ = long_trade(self.after((108, 120, 95, 110)))
        self.assertEqual(ev[-1].reason, "stop")

    def test_gap_through_the_stop_sells_at_the_open(self):
        ev, _ = long_trade(self.after((90, 91, 89, 90)))
        self.assertEqual(ev[-1].reason, "stop")
        self.assertEqual(ev[-1].price, 90)

    def test_time_limit_fills_on_the_next_open(self):
        rows = [(108, 108.1, 107.9, 108)] * 5
        ev, _ = long_trade(self.after(*rows, (107.95, 108, 107.9, 107.95)), {"HOLD": 5})
        sells = [e for e in ev if e.kind == "sell"]
        self.assertEqual(sells[0].reason, "time")
        self.assertEqual(sells[0].price, 107.95)                     # the open of the bar after the 3 minutes were up
        self.assertEqual(sells[0].held_min, 6)

    def test_close_under_the_slow_average_is_a_trend_exit(self):
        ev, _ = long_trade(self.after((108, 108, 100, 100.5), (100.5, 100.6, 100.4, 100.5)), {"STOP": 20.0})
        sell = [e for e in ev if e.kind == "sell"][0]
        self.assertEqual(sell.reason, "trend")
        self.assertEqual(sell.price, 100.5)


class Fills(unittest.TestCase):
    def test_slip_and_fee_arithmetic(self):
        rows = [Bar(BASE + (9 + i) * 60, 108, 114, 108, 113, 1.0) for i in range(1)]
        ev, st = long_trade(rows, {"TARGET": 5.0}, slip=0.001, fee=0.002)
        buy, sell = [e for e in ev if e.kind == "buy"][0], ev[-1]
        self.assertAlmostEqual(buy.price, 107.8 * 1.001)
        self.assertEqual(buy.basis, 107.8)
        self.assertAlmostEqual(sell.basis, buy.target)
        self.assertAlmostEqual(sell.price, buy.target * 0.999)
        expect = (1 - 0.002) ** 2 * sell.price / buy.price - 1
        self.assertAlmostEqual(sell.result_pct, expect * 100, places=9)
        self.assertAlmostEqual(st.cash, 1000 * (1 + expect), places=6)
        self.assertAlmostEqual(sell.pnl, st.cash - 1000, places=6)

    def test_size_buys_only_a_share_of_the_book(self):
        ev, st = long_trade([], {"SIZE": 0.5})
        self.assertAlmostEqual(st.cash, 500.0)
        self.assertAlmostEqual(rt.value(st, 107.8), 1000.0)

    def test_a_signal_never_fills_on_its_own_bar(self):
        ev, _ = run(bars(UP_DIP), PULL)                              # the cross is the last bar: no next bar yet
        self.assertEqual(ev, [])


class Guards(unittest.TestCase):
    def stops(self, n):
        """n losing round trips: a pullback buy, then a bar that hits the stop."""
        out, t = [], 0
        for _ in range(n):
            out += bars(UP_DIP + [108], start=t)
            t += 9
            out.append(Bar(BASE + t * 60, 108, 108, 100, 100.5, 1.0))
            t += 1
        return out

    def test_max_trades_a_day(self):
        ev, _ = run(self.stops(4), {**PULL, "MAX_TRADES": 2, "COOL": 0, "STOP": 1.0, "DAY_STOP": 10.0})
        self.assertEqual(len([e for e in ev if e.kind == "buy"]), 2)

    def test_a_new_day_resets_the_count(self):
        b = self.stops(2)
        later = [x._replace(ts=x.ts + 86400) for x in self.stops(1)]
        ev, _ = run(b + later, {**PULL, "MAX_TRADES": 2, "COOL": 0, "STOP": 1.0, "DAY_STOP": 10.0})
        self.assertEqual(len([e for e in ev if e.kind == "buy"]), 3)

    def test_cool_after_a_loss(self):
        base = {**PULL, "COOL": 0, "STOP": 1.0, "DAY_STOP": 10.0}
        free, _ = run(self.stops(2), base)
        cooled, _ = run(self.stops(2), {**base, "COOL": 300})
        self.assertEqual(len([e for e in free if e.kind == "buy"]), 2)
        self.assertEqual(len([e for e in cooled if e.kind == "buy"]), 1)

    def test_day_stop_blocks_new_entries(self):
        base = {**PULL, "COOL": 0, "STOP": 1.0}
        ev, _ = run(self.stops(3), {**base, "DAY_STOP": 1.0})        # one 1% loss reaches it
        self.assertEqual(len([e for e in ev if e.kind == "buy"]), 1)
        ev, _ = run(self.stops(3), {**base, "DAY_STOP": 10.0})
        self.assertEqual(len([e for e in ev if e.kind == "buy"]), 3)

    def test_a_gap_in_the_bars_restarts_the_averages(self):
        b = bars(UP_DIP + [108])
        ev, _ = run(b[:-1] + [x._replace(ts=x.ts + 3600) for x in b[-1:]], PULL)
        self.assertEqual(ev, [])


class Stock(unittest.TestCase):
    def day(self):
        return rt.Session("d", BASE + 100 * 60, BASE + 100 * 60 + 390 * 60)

    def run_at(self, minute_of_session, **over):
        sess = self.day()
        shift = int((sess.open - BASE) / 60) + minute_of_session - 8        # the signal bar lands on that minute
        ev, st = run(bars(UP_DIP + [108, 108], start=shift), {**PULL, **over}, session=sess)
        return ev, st

    def test_no_entry_in_the_first_five_minutes(self):
        self.assertEqual(self.run_at(2)[0], [])                  # its fill would be minute 3
        self.assertEqual(len(self.run_at(15)[0]), 1)             # a normal time

    def test_first_allowed_fill_is_minute_five(self):
        sess = self.day()
        s = rt.settings(PULL)
        st = rt.new_state()
        self.assertFalse(rt._may_buy(st, Bar(sess.open + 3 * 60, 1, 1, 1, 1), s, sess))    # fills at minute 4
        self.assertTrue(rt._may_buy(st, Bar(sess.open + 4 * 60, 1, 1, 1, 1), s, sess))     # fills at minute 5

    def test_no_entry_in_the_last_fifteen_minutes(self):
        sess = self.day()
        s = rt.settings(PULL)
        st = rt.new_state()
        self.assertTrue(rt._may_buy(st, Bar(sess.close - 17 * 60, 1, 1, 1, 1), s, sess))   # fills 16 minutes before the close
        self.assertFalse(rt._may_buy(st, Bar(sess.close - 16 * 60, 1, 1, 1, 1), s, sess))  # fills 15 minutes before

    def test_flat_by_the_close(self):
        sess = self.day()
        shift = 100                                                     # the buy fills around minute 100 of the session
        pre = bars(UP_DIP + [108], start=int((sess.open - BASE) / 60) + shift)
        t = pre[-1].ts
        hold = []
        while t + 60 < sess.close:
            t += 60
            hold.append(Bar(t, 108, 108.2, 107.9, 108, 1.0))
        ev, st = run(pre + hold, {**PULL, "HOLD": 5000, "STOP": 20.0, "TARGET": 20.0}, session=sess)
        self.assertEqual(ev[-1].kind, "sell")
        self.assertEqual(ev[-1].reason, "close")
        self.assertEqual(ev[-1].ts, sess.close - 60)
        self.assertIsNone(st.pos)

    def test_a_position_left_over_is_sold_at_the_next_session_open(self):
        sess = self.day()
        st = rt.new_state(1000.0)
        s = rt.settings(PULL)
        pre = bars(UP_DIP + [108], start=int((sess.open - BASE) / 60) + 100)
        for b in pre:
            rt.step(st, b, s, sess)
        self.assertIsNotNone(st.pos)
        nextday = rt.Session("d2", sess.open + 86400, sess.close + 86400)
        ev = rt.step(st, Bar(nextday.open, 109, 109, 109, 109, 1.0), s, nextday)
        self.assertEqual([e.reason for e in ev], ["close"])

    def test_us_session_is_a_regular_day(self):
        s = rt.us_session("2026-11-12")
        self.assertEqual(s.close - s.open, 390 * 60)


class Limits(unittest.TestCase):
    def test_settings_clamp(self):
        s = rt.settings({"STOP": 99, "FAST": 1, "SLOW": 3, "MAX_TRADES": 0, "SLIP": -1, "SIZE": 7})
        self.assertEqual(s["STOP"], rt.LIMITS["STOP"][1])
        self.assertEqual(s["MAX_TRADES"], 1)
        self.assertEqual(s["SLIP"], 0.0)
        self.assertEqual(s["SIZE"], 1.0)
        self.assertLess(s["FAST"], s["SLOW"])

    def test_settings_ignore_bad_input(self):
        s = rt.settings({"RULE": "gamble", "DIP": "big", "TARGET": True, "NOPE": 1})
        self.assertEqual(s, rt.DEFAULTS)

    def test_check_refuses(self):
        for bad in ({"RULE": "x"}, {"STOP": 99}, {"STOP": "1"}, {"HOLD": 10.5}, {"NOPE": 1}, {"FAST": 60, "SLOW": 50}, {"SIZE": True}):
            with self.assertRaises(ValueError, msg=str(bad)):
                rt.check(bad)
        self.assertEqual(rt.check({"STOP": 0.3, "RULE": "revert"}), {"STOP": 0.3, "RULE": "revert"})

    def test_defaults_are_inside_limits(self):
        for k, (lo, hi, _t) in rt.LIMITS.items():
            self.assertTrue(lo <= rt.DEFAULTS[k] <= hi, k)
        rt.check({k: v for k, v in rt.DEFAULTS.items()})


class Say(unittest.TestCase):
    def events(self):
        a, _ = long_trade([Bar(BASE + 9 * 60, 108, 114, 108, 113, 1.0)], {"TARGET": 5.0})
        b, _ = long_trade([Bar(BASE + 9 * 60, 108, 108.2, 100, 101, 1.0)], {"STOP": 5.0})
        return a, b

    def test_buy_and_sells(self):
        a, b = self.events()
        text = [rt.say(e) for e in a + b]
        self.assertIn("Buying at 107.80", text[0])
        self.assertIn("for a gain", text[0])
        self.assertIn("Target reached.", text[1])
        self.assertIn("gain of $", text[1])
        self.assertIn("Stop reached.", text[3])
        self.assertIn("loss of $", text[3])
        self.assertIn("trade 1 of 6 today", text[3])

    def test_every_kind_is_plain(self):
        a, b = self.events()
        evs = list(a + b)
        base = evs[0]
        for rule, numbers in (("breakout", {"look_minutes": 30}), ("revert", {"gap_pct": 0.6, "slow_minutes": 60})):
            evs.append(rt.Event("buy", 0, rule, 100.0, 100.0, numbers, 99.0, 101.0, trade_n=1, max_trades=6))
        for reason in ("target", "stop", "time", "trend", "close"):
            for rule in ("pullback", "revert"):
                evs.append(rt.Event("sell", 0, rule, 100.0, 100.0, {}, 99.0, 101.0, reason, -0.5, -2.5, 3, 6, 42))
        evs.append(rt.Event("sell", 0, "pullback", 100.0, 100.0, {}, 99.0, 101.0, "time", 0.0, 0.0, 3, 6, 42))
        for e in evs:
            t = rt.say(e)
            self.assertTrue(t.endswith("."), t)
            for banned in ("should", "!", "—", "–", " - ", "will rise", "will fall"):
                self.assertNotIn(banned, t)
        self.assertEqual(rt.say(evs[3]) if evs[3].reason == "stop" else "", "Stop reached. Sold at 102.41 for a loss of $%.2f. That is trade 1 of 6 today." % abs(evs[3].pnl))
        self.assertEqual(base.kind, "buy")

    def test_the_example_tone(self):
        e = rt.Event("sell", 0, "pullback", 511.02, 511.02, {}, 511.02, 514.86, "stop", -0.5, -2.5, 3, 6, 30)
        self.assertEqual(rt.say(e), "Stop reached. Sold at 511.02 for a loss of $2.50. That is trade 3 of 6 today.")


class Watch(unittest.TestCase):
    def feed(self, closes, over=None, session=None):
        s = rt.settings({"SLIP": 0.0, "FEE": 0.0, **(over or {})})
        st = rt.new_state(1000.0)
        for b in bars(closes):
            rt.step(st, b, s, session)
        return st, s

    def check_plain(self, e):
        text = rt.say(e)
        self.assertTrue(text.endswith("."), text)
        for banned in ("should", "!", "—", "–", " - "):
            self.assertNotIn(banned, text)
        return text

    def test_in_a_position(self):
        st, s = self.feed(UP_DIP + [108, 108.1], {**PULL, "TARGET": 5.0, "STOP": 5.0, "HOLD": 30})
        before = (st.cash, dict(st.pos), list(st.c))
        e = rt.watch(st, 108.4, s)
        self.assertEqual(e.kind, "watch")
        self.assertEqual((st.cash, st.pos, st.c), before)                 # nothing changed
        self.assertAlmostEqual(e.numbers["gain_pct"], (108.4 / 107.8 - 1) * 100)
        self.assertAlmostEqual(e.target, 107.8 * 1.05)
        self.assertEqual(e.numbers["minutes_left"], 30 - 1)
        text = self.check_plain(e)
        self.assertEqual(text, f"Up 0.6% on this trade. Target {107.8 * 1.05:,.2f}, stop {107.8 * 0.95:,.2f}, 29 minutes left.")
        self.assertTrue(self.check_plain(rt.watch(st, 107.0, s)).startswith("Down 0.7"))

    def test_waiting_for_the_cross(self):
        st, s = self.feed(UP_DIP[:-1], PULL)                               # the dip is in, the price is still under its fast average
        e = rt.watch(st, st.c[-1], s)
        self.assertEqual(e.numbers["waiting"], "cross")
        self.assertGreater(e.numbers["gap_pct"], 0)
        self.assertIn("under my 2 minute average. I am waiting for a close above", self.check_plain(e))

    def test_waiting_for_the_dip_and_the_trend(self):
        st, s = self.feed([100, 102, 104, 106, 108, 107.95, 108.5], {**PULL, "DIP": 1.0})
        e = rt.watch(st, 108.5, s)
        self.assertEqual(e.numbers["waiting"], "dip")
        self.assertIn("waiting for a dip of", self.check_plain(e))
        st, s = self.feed([110, 108, 106, 104, 102, 100.5, 100.2], PULL)
        e = rt.watch(st, 100.2, s)
        self.assertEqual(e.numbers["waiting"], "trend")
        self.assertIn("The trend is not up yet", self.check_plain(e))

    def test_warming_up(self):
        st, s = self.feed([100, 101], PULL)
        e = rt.watch(st, 101, s)
        self.assertEqual(e.numbers, {"waiting": "warmup", "have": 2, "need": 5})
        self.assertIn("2 of 5 minutes", self.check_plain(e))

    def test_breakout_and_revert_levels(self):
        st, s = self.feed([100, 100.5, 100.2, 100.6, 100.4, 100.5], TestBreak.BREAK)
        e = rt.watch(st, 100.5, s)
        self.assertAlmostEqual(e.numbers["level"], 100.6)
        self.assertIn("waiting for a close above 100.60", self.check_plain(e))
        up = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 108.5]
        st, s = self.feed(up, TestBreak.REV)
        e = rt.watch(st, 108.5, s)
        self.assertTrue(e.numbers["rising"])
        self.assertIn("above the level I buy at", self.check_plain(e))
        st, s = self.feed(list(reversed(up)), TestBreak.REV)
        self.assertIn("is not rising", self.check_plain(rt.watch(st, st.c[-1], s)))

    def test_guards(self):
        base = {**PULL, "COOL": 30, "STOP": 1.0, "DAY_STOP": 10.0, "MAX_TRADES": 1}
        s = rt.settings({"SLIP": 0.0, **base})
        st = rt.new_state(1000.0)
        for b in Guards().stops(1):
            rt.step(st, b, s)
        e = rt.watch(st, 100.5, s)
        self.assertEqual(e.numbers["waiting"], "cap")                       # the cap is checked first
        self.assertEqual(self.check_plain(e), "Done for today: 1 of 1 trades used.")
        s2 = rt.settings({"SLIP": 0.0, **{**base, "MAX_TRADES": 5}})
        e = rt.watch(st, 100.5, s2)
        self.assertEqual(e.numbers["waiting"], "cool")
        self.assertIn("Resting after a loss", self.check_plain(e))
        e = rt.watch(st, 100.5, s2, ts=st.cool_until + 60)
        self.assertNotEqual(e.numbers["waiting"], "cool")
        s3 = rt.settings({"SLIP": 0.0, **{**base, "MAX_TRADES": 5, "COOL": 0, "DAY_STOP": 0.5}})
        e = rt.watch(st, 100.5, s3)
        self.assertEqual(e.numbers["waiting"], "daystop")
        self.assertIn("loss limit of 0.5% is reached", self.check_plain(e))

    def test_session_edges(self):
        sess = rt.Session("d", BASE + 100 * 60, BASE + 100 * 60 + 390 * 60)
        s = rt.settings({**PULL, "SLIP": 0.0})
        st = rt.new_state(1000.0)
        st.last_ts = sess.open + 60
        e = rt.watch(st, 100.0, s, sess)
        self.assertEqual(e.numbers["waiting"], "edge_open")
        self.assertIn("I start looking for a buy in", self.check_plain(e))
        st.last_ts = sess.close - 10 * 60
        e = rt.watch(st, 100.0, s, sess)
        self.assertEqual(e.numbers["waiting"], "edge_close")
        self.assertEqual(self.check_plain(e), "Too close to the end of the session for a new buy.")


TestBreak = Entries


if __name__ == "__main__":
    unittest.main()


class LiveHelpers(unittest.TestCase):
    """tick() fills a waiting order on the next trade and acts on the stop and target at once; warm(), dump() and load()."""

    def _armed(self):
        st = rt.new_state(1000.0)
        s = rt.settings({"SLIP": 0.0, "FEE": 0.0, **PULL})
        for b in bars(UP_DIP):
            rt.step(st, b, s)
        self.assertEqual(st.pending[0], "buy")
        return st, s

    def test_tick_fills_the_waiting_buy_at_the_ask(self):
        st, s = self._armed()
        ev = rt.tick(st, BASE + 9 * 60 + 5, 107.9, s, bid=107.85, ask=107.95)
        self.assertEqual([e.kind for e in ev], ["buy"])
        self.assertEqual(ev[0].price, 107.95)
        self.assertIsNone(st.pending)
        self.assertEqual(rt.step(st, bars([108], start=9, first_open=107.9)[0], s), [])   # step does not buy again

    def test_tick_sells_at_the_stop_and_at_the_target(self):
        st, s = self._armed()
        rt.tick(st, BASE + 9 * 60, 100.0, s)
        ev = rt.tick(st, BASE + 9 * 60 + 1, 94.0, s, bid=93.9)
        self.assertEqual((ev[0].reason, ev[0].price), ("stop", 93.9))      # the market was already under the stop
        st, s = self._armed()
        rt.tick(st, BASE + 9 * 60, 100.0, s)
        ev = rt.tick(st, BASE + 9 * 60 + 1, 106.0, s)
        self.assertEqual((ev[0].reason, ev[0].price), ("target", 105.0))
        self.assertIsNone(st.pos)

    def test_tick_does_nothing_between_levels(self):
        st, s = self._armed()
        rt.tick(st, BASE + 9 * 60, 100.0, s)
        self.assertEqual(rt.tick(st, BASE + 9 * 60 + 1, 101.0, s), [])

    def test_warm_fills_the_averages_without_trading(self):
        st = rt.new_state(1000.0)
        rt.warm(st, bars(UP_DIP))
        self.assertEqual(st.c, UP_DIP)
        self.assertIsNone(st.pos)
        self.assertEqual(st.cash, 1000.0)

    def test_dump_and_load_round_trip(self):
        st, s = self._armed()
        rt.tick(st, BASE + 9 * 60, 100.0, s)
        back = rt.load(json.loads(json.dumps(rt.dump(st))))
        self.assertEqual(back.pos, st.pos)
        self.assertEqual(back.c, st.c)
        self.assertEqual(back.cash, st.cash)
        st2 = rt.new_state(5.0)
        st2.pending = ("sell", "time", {"close": 1})
        self.assertEqual(rt.load(json.loads(json.dumps(rt.dump(st2)))).pending, ("sell", "time", {"close": 1}))
