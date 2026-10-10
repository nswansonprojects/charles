"""Opening Range Breakout sleeve (orb.py) and its backtest (backtest_orb.py): range, breakout,
filters, stop-before-target, DST, sizing, daily loss limit, and ORB off = Charles unchanged."""
import json
import os
import types
from datetime import date, datetime, timedelta, timezone

from base import BotTestCase

import alpaca_executor as ax
import backtest_orb as bt
import config
import orb
import regime
import risk_manager as rm
from timeofday import ET

DAY = date(2026, 10, 9)


def _read(path):
    with open(path) as f:
        return json.load(f)


def cfg(**over):
    base = dict(symbols=["QQQ"], range_min=5, cutoff_min=60, max_trades=1, allow_short=False,
                min_range_atr=0.0, max_range_atr=0.0, stop_mode="range", fixed_stop_pct=0.005,
                reward_risk=1.5, trail_pct=0.0, flatten="15:55", max_risk=5.0, max_pos_pct=0.15,
                daily_loss=10.0, use_regime=False, use_llm=False)
    base.update(over)
    return orb.settings(**base)


def bar(minute, o, h, l, c, day=DAY):
    """A 1-min bar starting `minute` minutes after 09:30 ET."""
    return {"t": datetime.combine(day, datetime.min.time(), ET).replace(hour=9, minute=30) + timedelta(minutes=minute),
            "o": o, "h": h, "l": l, "c": c}


RANGE = [bar(0, 100, 101, 99.5, 100.5), bar(1, 100.5, 101.2, 100, 101), bar(2, 101, 101.1, 99, 99.5),
         bar(3, 99.5, 100, 99.2, 99.8), bar(4, 99.8, 100.4, 99.6, 100.2)]      # high 101.2, low 99


def feed(st, bars, c, atr=0.0, times=None):
    times = times or orb.session_times(DAY, c)
    return [orb.on_bar(st, "QQQ", b, times, c, atr) for b in bars]


class Range(BotTestCase):
    def test_range_is_high_low_of_first_n_minutes(self):
        c = cfg()
        times = orb.session_times(DAY, c)
        self.assertEqual(orb.opening_range(RANGE + [bar(5, 100, 105, 90, 100)], times),
                         {"high": 101.2, "low": 99, "bars": 5})
        st = orb.new_day(DAY, c)
        feed(st, RANGE, c)
        s = st["symbols"]["QQQ"]
        self.assertEqual((s["status"], s["range"]["high"], s["range"]["low"]), ("watch", 101.2, 99))

    def test_longer_range_waits_for_its_bars(self):
        c = cfg(range_min=15)
        st = orb.new_day(DAY, c)
        feed(st, RANGE, c)
        self.assertEqual(st["symbols"]["QQQ"]["status"], "range")


class Breakout(BotTestCase):
    def test_close_above_high_signals_long_wick_does_not(self):
        c = cfg()
        st = orb.new_day(DAY, c)
        feed(st, RANGE, c)
        self.assertIsNone(feed(st, [bar(5, 101, 101.9, 100.8, 101.1)], c)[0])        # wick above, close inside
        self.assertEqual(feed(st, [bar(6, 101.1, 101.6, 101, 101.5)], c)[0],
                         {"do": "signal", "side": "long", "price": 101.5})

    def test_long_only_ignores_downside_break_short_when_allowed(self):
        c = cfg()
        st = orb.new_day(DAY, c)
        feed(st, RANGE, c)
        self.assertIsNone(feed(st, [bar(5, 99, 99, 98, 98.5)], c)[0])
        c2 = cfg(allow_short=True)
        st2 = orb.new_day(DAY, c2)
        feed(st2, RANGE, c2)
        self.assertEqual(feed(st2, [bar(5, 99, 99, 98, 98.5)], c2)[0]["side"], "short")

    def test_no_entry_after_cutoff(self):
        c = cfg(cutoff_min=10)
        st = orb.new_day(DAY, c)
        feed(st, RANGE, c)
        self.assertIsNone(feed(st, [bar(10, 101, 102, 101, 101.9)], c)[0])       # ends 09:41 > 09:40
        self.assertEqual(st["symbols"]["QQQ"]["status"], "done")

    def test_atr_filter_and_nan_safety(self):
        rng = {"high": 101.2, "low": 99}
        off = cfg()
        self.assertIsNone(orb.range_filter(rng, float("nan"), off))                # filter off: ATR unused
        on = cfg(min_range_atr=0.1, max_range_atr=0.5)
        self.assertIn("NaN", orb.range_filter(rng, float("nan"), on))
        self.assertIn("NaN", orb.range_filter(rng, 0.0, on))
        self.assertIsNone(orb.range_filter(rng, 10.0, on))                          # 2.2 / 10 = .22
        self.assertIn("<", orb.range_filter(rng, 30.0, on))
        self.assertIn(">", orb.range_filter(rng, 4.0, on))
        st = orb.new_day(DAY, on)
        feed(st, RANGE, on, atr=float("nan"))
        self.assertEqual(st["symbols"]["QQQ"]["status"], "done")

    def test_blockers_conflict_regime_halt_and_limit(self):
        c = cfg(use_regime=True)
        st = orb.new_day(DAY, c)
        self.assertEqual(orb.entry_blockers(st, "QQQ", c, regime="TRENDING_BULL"), [])
        self.assertTrue(orb.entry_blockers(st, "QQQ", c, regime="TRENDING_BEAR"))
        self.assertTrue(orb.entry_blockers(st, "QQQ", c, held_elsewhere=True))
        self.assertTrue(orb.entry_blockers(st, "QQQ", c, halted=True))
        st["trades"] = 1
        self.assertTrue(orb.entry_blockers(st, "QQQ", c))
        self.assertTrue(orb.entry_blockers(orb.new_day(DAY, cfg(use_llm=True)), "QQQ", cfg(use_llm=True),
                                           llm=(False, 0.4)))


class Exits(BotTestCase):
    def test_levels_range_and_fixed(self):
        rng = {"high": 101.2, "low": 99}
        self.assertEqual(orb.exit_levels("long", 101.5, rng, cfg()), (99, 105.25))   # risk 2.5 → +3.75
        stop, target = orb.exit_levels("long", 100.0, rng, cfg(stop_mode="fixed", fixed_stop_pct=0.01, reward_risk=2))
        self.assertAlmostEqual(stop, 99.0)
        self.assertAlmostEqual(target, 102.0)
        self.assertIsNone(orb.exit_levels("long", 98.5, rng, cfg()))                 # filled below the stop

    def test_target_on_close_and_flatten(self):
        c = cfg()
        st = orb.new_day(DAY, c)
        feed(st, RANGE, c)
        st["symbols"]["QQQ"]["status"] = "entering"
        orb.filled_entry(st, "QQQ", "long", 101.5, 1.0, bar(6, 0, 0, 0, 0)["t"], c)
        self.assertIsNone(feed(st, [bar(7, 101.5, 106, 101.4, 105.0)], c)[0])           # high hit, close not
        self.assertEqual(feed(st, [bar(8, 105, 105.5, 104.9, 105.3)], c)[0], {"do": "exit", "reason": "target"})
        self.assertEqual(feed(st, [bar(384, 102, 102, 102, 102)], c)[0], {"do": "exit", "reason": "flatten"})

    def test_trailing_stop_ratchets_up(self):
        c = cfg(trail_pct=0.01, reward_risk=10)
        st = orb.new_day(DAY, c)
        feed(st, RANGE, c)
        orb.filled_entry(st, "QQQ", "long", 101.5, 1.0, bar(6, 0, 0, 0, 0)["t"], c)
        self.assertEqual(feed(st, [bar(7, 103, 104, 103, 104)], c)[0], {"do": "trail", "stop": 102.96})

    def test_stop_checked_before_target_in_the_same_bar(self):
        """Backtest: a bar that touches the stop AND closes past the target is a stop."""
        c = cfg()
        days = [bar(i, 100, 100.2, 99.8, 100) for i in range(5)]                 # range 99.8–100.2
        days += [bar(5, 100, 100.6, 100, 100.5)]                                  # breakout close
        days += [bar(6, 100.5, 100.6, 100.4, 100.5)]                              # entry fill @ open
        days += [bar(7, 100.5, 110, 99.0, 109)]                                   # both in one bar
        days += [bar(i, 109, 109, 109, 109) for i in range(8, 390)]
        t = [b["t"] for b in days]
        mkt = types.SimpleNamespace(days_bars={"QQQ": {DAY: (t, [b["o"] for b in days], [b["h"] for b in days],
                                                             [b["l"] for b in days], [b["c"] for b in days])}},
                                    atr={"QQQ": {}}, regime={})
        res = bt.simulate(mkt, [DAY], c, slip_bps=0, commission=0)
        self.assertEqual(len(res["trades"]), 1)
        tr = res["trades"][0]
        self.assertEqual((tr["reason"], tr["entry"], tr["exit"]), ("stop", 100.5, 99.8))
        self.assertEqual(tr["entry_ts"], t[6].isoformat())                        # next bar after the signal

    def test_gap_through_stop_fills_at_open(self):
        self.assertEqual(orb.stop_fill({"side": "long", "stop": 99.0}, bar(9, 98.0, 98.5, 97.5, 98.2)), 98.0)
        self.assertEqual(orb.stop_fill({"side": "long", "stop": 99.0}, bar(9, 99.5, 99.6, 98.9, 99.2)), 99.0)
        self.assertIsNone(orb.stop_fill({"side": "long", "stop": 99.0}, bar(9, 99.5, 99.6, 99.1, 99.2)))


class Sizing(BotTestCase):
    def test_risk_and_position_caps(self):
        c = cfg()
        self.assertEqual(orb.size(100, 99, 1000, 1000, c), 1.5)        # $150 cap (15%) beats $5 risk (5 sh)
        self.assertEqual(orb.size(100, 90, 1000, 1000, c), 0.5)        # $5 / $10 risk
        self.assertEqual(orb.size(100, 99, 1000, 50, c), 0.475)        # cash
        self.assertEqual(orb.size(100, 99, 1000, 1000, c, dd_mult=0.6), 0.9)
        self.assertEqual(orb.size(100, 100, 1000, 1000, c), 0.0)

    def test_daily_loss_limit_disables_orb(self):
        c = cfg(max_trades=3, daily_loss=1.0)
        st = orb.new_day(DAY, c)
        feed(st, RANGE, c)
        t = bar(6, 0, 0, 0, 0)["t"]
        orb.filled_entry(st, "QQQ", "long", 101.5, 1.0, t, c)
        rec = orb.filled_exit(st, "QQQ", 99.0, t, "stop", c)
        self.assertEqual((rec["pnl"], rec["r"], rec["strategy"]), (-2.5, -1.0, "orb"))
        self.assertIn("daily loss", st["disabled"])
        self.assertTrue(orb.entry_blockers(st, "QQQ", c))


class SessionTimes(BotTestCase):
    def test_open_follows_dst(self):
        c = cfg()
        summer = orb.session_times(date(2026, 7, 1), c)["open"].astimezone(timezone.utc)
        winter = orb.session_times(date(2026, 12, 1), c)["open"].astimezone(timezone.utc)
        self.assertEqual((summer.hour, summer.minute), (13, 30))
        self.assertEqual((winter.hour, winter.minute), (14, 30))
        # the DST switch day itself (2026-11-01 is a Sunday; Monday after)
        self.assertEqual(orb.session_times(date(2026, 11, 2), c)["open"].astimezone(timezone.utc).hour, 14)

    def test_half_day_flattens_before_early_close(self):
        c = cfg()
        t = orb.session_times(date(2026, 11, 27), c, close=datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc))
        self.assertEqual(t["flatten"].astimezone(ET).strftime("%H:%M"), "12:55")
        self.assertEqual(orb.session_times(DAY, c)["flatten"].strftime("%H:%M"), "15:55")


def pos(symbol):
    return types.SimpleNamespace(symbol=symbol, asset_class=types.SimpleNamespace(value="us_equity"))


class FlagOff(BotTestCase):
    """With ORB_ENABLED off, nothing ORB does reaches Charles."""

    def setUp(self):
        super().setUp()
        self.patch(orb, "STATE_FILE", os.path.join(self.tmp, "orb.json"))
        self.patch(orb, "TRADES_FILE", os.path.join(self.tmp, "orb_trades.json"))
        self.patch(orb, "SWITCH_FILE", os.path.join(self.tmp, "control", "orb_switch.json"))
        st = orb.new_day(DAY, cfg())
        st["symbols"]["QQQ"]["status"] = "in"
        orb.save_state(st)                              # ORB "holds" QQQ on file
        self.client = types.SimpleNamespace(get_all_positions=lambda: [pos("QQQ"), pos("NU")])

    def test_off_charles_sees_every_position(self):
        self.patch(config, "ORB_ENABLED", False)
        self.assertEqual([p.symbol for p in ax._stock_positions(self.client)], ["QQQ", "NU"])
        self.assertEqual(ax._orb_held(), set())

    def test_on_orb_shares_are_kept_out_of_charles_logic(self):
        self.patch(config, "ORB_ENABLED", True)
        self.assertEqual([p.symbol for p in ax._stock_positions(self.client)], ["NU"])

    def test_on_charles_will_not_buy_a_symbol_orb_holds(self):
        self.patch(config, "ORB_ENABLED", True)
        self.patch(ax, "_client", lambda: self.fail("must not reach the broker"))
        d = {"symbol": "QQQ", "action": "BUY", "confidence": 0.9, "reasoning": "x"}
        self.assertIsNone(ax.execute_decision(d))

    def test_tick_does_nothing_when_off(self):
        self.patch(config, "ORB_ENABLED", False)
        self.patch(ax, "_client", lambda: self.fail("must not reach the broker"))
        self.assertIsNone(orb.tick())

    def test_daily_summary_unchanged_without_extra(self):
        real = self._saved_alerts
        if real is None:
            import importlib.util
            spec = importlib.util.spec_from_file_location("alerts_real", os.path.join(os.path.dirname(ax.__file__), "alerts.py"))
            real = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(real)
        sent = []
        self.patch(real, "_send", sent.append)
        self.patch(real, "_notify", lambda *a, **k: None)
        s = {"total_value": 1066.3, "total_return_pct": 6.63, "win_rate": 50.0, "total_trades": 90}
        real.daily_summary(s)
        real.daily_summary(s, ["📐 ORB today +$1.00"])
        self.assertTrue(sent[0].endswith("🔄 Total trades: `90`"))
        self.assertEqual(sent[1], sent[0] + "\n📐 ORB today +$1.00")


class Switch(BotTestCase):
    def setUp(self):
        super().setUp()
        self.patch(orb, "SWITCH_FILE", os.path.join(self.tmp, "control", "orb_switch.json"))

    def test_on_by_default_and_round_trips(self):
        self.assertTrue(orb.switched_on())
        self.assertFalse(orb.set_switch(False)["on"])
        self.assertFalse(orb.switched_on())
        orb.set_switch(True)
        self.assertTrue(orb.switched_on())

    def test_blocker(self):
        st = orb.new_day(DAY, cfg())
        self.assertEqual(orb.entry_blockers(st, "QQQ", cfg()), [])
        self.assertEqual(orb.entry_blockers(st, "QQQ", cfg(), switched_off=True), ["ORB is switched off on the dashboard"])


class SharedMath(BotTestCase):
    def test_atr_from_ohlc_nan_safe(self):
        self.assertEqual(rm.atr_from_ohlc([1, float("nan")], [1, 1], [1, 1], 1), 0.0)
        self.assertAlmostEqual(rm.atr_from_ohlc([11, 12, 13], [9, 10, 11], [10, 11, 12], 2), 2.0)

    def test_regime_classify_matches_rules(self):
        up = [100 + i for i in range(60)]
        name, d = regime.classify(up, [c + 0.5 for c in up], [c - 0.5 for c in up], [1e6] * 60)
        self.assertEqual(name, "TRENDING_BULL")
        self.assertTrue(d["trend_up"])
        flat = [100 + (i % 2) for i in range(60)]
        self.assertEqual(regime.classify(flat, [c + 0.5 for c in flat], [c - 0.5 for c in flat], [1e6] * 60)[0],
                         "RANGING")


class FakeBroker:
    """Just enough of Alpaca's TradingClient for orb.tick(): market orders fill at `price`,
    stop orders fill when a bar trades through them."""

    def __init__(self):
        self.orders, self.price, self.n = {}, None, 0

    def get_clock(self):
        return types.SimpleNamespace(is_open=True, next_close=datetime.combine(DAY, datetime.min.time(), ET).replace(hour=16))

    def get_account(self):
        return types.SimpleNamespace(portfolio_value="1000", cash="800")

    def submit_order(self, req):
        self.n += 1
        stop = getattr(req, "stop_price", None)
        o = types.SimpleNamespace(id=f"o{self.n}", client_order_id=req.client_order_id, qty=req.qty,
                                  side=req.side, stop_price=stop, filled_avg_price=None,
                                  status=types.SimpleNamespace(value="new" if stop else "filled"))
        if not stop:
            o.filled_avg_price = str(self.price)
        self.orders[o.id] = o
        return o

    def get_order_by_id(self, oid):
        return self.orders[oid]

    def cancel_order_by_id(self, oid):
        if self.orders[oid].status.value == "new":
            self.orders[oid].status.value = "canceled"

    def trade_through(self, b):
        for o in self.orders.values():
            if o.stop_price and o.status.value == "new" and b["l"] <= o.stop_price:
                o.status.value, o.filled_avg_price = "filled", str(min(b["o"], o.stop_price))


class LiveTick(BotTestCase):
    """orb.tick() minute by minute against a fake broker: the live wiring end to end."""

    def setUp(self):
        super().setUp()
        self.patch(orb, "STATE_FILE", os.path.join(self.tmp, "orb.json"))
        self.patch(orb, "TRADES_FILE", os.path.join(self.tmp, "orb_trades.json"))
        self.patch(orb, "SWITCH_FILE", os.path.join(self.tmp, "control", "orb_switch.json"))
        self.patch(config, "ORB_ENABLED", True)
        self.patch(config, "ORB_SYMBOLS", ["QQQ"])
        self.patch(config, "ORB_MIN_RANGE_ATR", 0.0)
        self.patch(config, "ORB_MAX_RANGE_ATR", 0.0)
        self.patch(config, "ORB_USE_REGIME_FILTER", False)
        self.patch(config, "ORB_USE_LLM_CONFIRM", False)
        self.broker = FakeBroker()
        self.patch(ax, "_client", lambda: self.broker)
        self.patch(ax, "_stock_positions", lambda c: [])
        self.patch(orb, "_daily_atr", lambda sym: 2.0)
        self.patch(regime, "detect_regime", lambda: ("RANGING", {}))
        self.patch(orb, "_alert", lambda *a, **k: None)
        self.patch(orb, "_wait_fill", lambda c, oid, timeout=15.0: orb._order_status(c, oid))
        self.patch(ax, "_wait_final", lambda c, oid, timeout: orb._order_status(c, oid)[0])

    def run_day(self, bars):
        start = bars[0]["t"]
        self.patch(orb, "_bars_today", lambda syms, times, now: {"QQQ": [b for b in bars if b["t"] + timedelta(minutes=1) <= now]})
        for i in range(1, len(bars)):
            now = start + timedelta(minutes=i, seconds=10)
            self.broker.trade_through(bars[i - 1])
            self.broker.price = bars[i]["o"]
            orb.tick(now)
        return _read(orb.TRADES_FILE) if os.path.exists(orb.TRADES_FILE) else []

    def test_breakout_to_target(self):
        bars = list(RANGE) + [bar(5, 100.2, 101.6, 100.2, 101.5)]                     # signal
        bars += [bar(6, 101.6, 101.8, 101.5, 101.7)]                                   # entry fill @101.6
        bars += [bar(i, 104 + i / 100, 106, 104, 105.5) for i in range(7, 12)]        # target 105.5 closes
        bars += [bar(i, 105, 105, 105, 105) for i in range(12, 30)]
        trades = self.run_day(bars)
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual((t["reason"], t["entry"], t["stop"], t["strategy"]), ("target", 101.6, 99, "orb"))
        self.assertGreater(t["pnl"], 0)
        stops = [o for o in self.broker.orders.values() if o.stop_price]
        self.assertEqual([o.status.value for o in stops], ["canceled"])                # stop pulled before the sell
        self.assertTrue(all(o.client_order_id.startswith("charles-orb-") for o in self.broker.orders.values()))
        st = _read(orb.STATE_FILE)
        self.assertEqual(st["symbols"]["QQQ"]["status"], "done")
        self.assertEqual(orb.held_symbols(), set())

    def test_broker_stop_fill_is_booked(self):
        bars = list(RANGE) + [bar(5, 100.2, 101.6, 100.2, 101.5), bar(6, 101.6, 101.8, 101.5, 101.7)]
        bars += [bar(7, 101, 101, 98.5, 98.7)] + [bar(i, 98.7, 98.8, 98.6, 98.7) for i in range(8, 15)]
        trades = self.run_day(bars)
        self.assertEqual([(t["reason"], t["exit"]) for t in trades], [("stop", 99.0)])
        self.assertLess(rm.get_daily_stats()["loss"], 0)                               # counts toward the kill switch

    def test_charles_holding_the_symbol_blocks_orb(self):
        self.patch(ax, "_stock_positions", lambda c: [pos("QQQ")])
        bars = list(RANGE) + [bar(5, 100.2, 101.6, 100.2, 101.5)] + [bar(i, 101.6, 101.8, 101.5, 101.7) for i in range(6, 12)]
        self.assertEqual(self.run_day(bars), [])
        self.assertEqual(self.broker.orders, {})
        self.assertIn("Charles holds QQQ", _read(orb.STATE_FILE)["symbols"]["QQQ"]["note"])

    def test_switched_off_takes_no_new_trade(self):
        orb.set_switch(False, "test")
        bars = list(RANGE) + [bar(5, 100.2, 101.6, 100.2, 101.5)] + [bar(i, 101.6, 101.8, 101.5, 101.7) for i in range(6, 12)]
        self.assertEqual(self.run_day(bars), [])
        self.assertEqual(self.broker.orders, {})
        self.assertIn("switched off on the dashboard", _read(orb.STATE_FILE)["symbols"]["QQQ"]["note"])

    def test_switching_off_mid_trade_still_exits(self):
        bars = list(RANGE) + [bar(5, 100.2, 101.6, 100.2, 101.5), bar(6, 101.6, 101.8, 101.5, 101.7)]
        bars += [bar(7, 102, 102.2, 101.9, 102)]
        bars += [bar(i, 104 + i / 100, 106, 104, 105.5) for i in range(8, 12)]
        bars += [bar(i, 105, 105, 105, 105) for i in range(12, 20)]
        start = bars[0]["t"]
        real_tick = orb.tick
        def tick(now):
            if now >= start + timedelta(minutes=8):
                orb.set_switch(False, "test")             # the button is pressed while ORB is in the trade
            real_tick(now)
        self.patch(orb, "tick", tick)
        trades = self.run_day(bars)
        self.assertEqual([t["reason"] for t in trades], ["target"])
        self.assertFalse(orb.switched_on())

    def test_flatten_at_1555(self):
        bars = list(RANGE) + [bar(5, 100.2, 101.6, 100.2, 101.5), bar(6, 101.6, 101.8, 101.5, 101.7)]
        bars += [bar(i, 102, 102.2, 101.9, 102) for i in range(7, 390)]
        trades = self.run_day(bars)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["reason"], "flatten")
        self.assertTrue(trades[0]["exit_ts"].startswith("2026-10-09T15:55") or trades[0]["exit_ts"].startswith("2026-10-09T15:56"))
