"""portfolio.py, the Custom Plan (owner 2026-11-03): the user's own tickers in long-term, sector and momentum blocks, the
whole-portfolio share, Charles's presets (amounts only), saved presets, and the guards that keep it off the leaderboard."""
import json
import os

from base import BotTestCase

import allocator as al
import chat
import portfolio as pf
import rebalancer as rb
import strategy_settings
from leaderboard import client, common

S = dict(al.DEFAULTS, AGGRESSIVE=False)


def rising(n, start=100.0, step=0.5):
    return [start + step * i for i in range(n)]


def falling_late(n):
    return rising(n - 40) + [rising(n - 40)[-1] - 3 * i for i in range(1, 41)]


BLOCKS = [
    {"name": "Index funds", "type": "long", "pct": 0.5, "tickers": ["SPY", "VTI"]},
    {"name": "Energy", "type": "sector", "pct": 0.2, "tickers": ["XLE", "XLK"]},
    {"name": "Tech", "type": "momentum", "pct": 0.3, "tickers": ["NVDA", "AAPL", "MSFT"], "picks": 2},
]


def market():
    closes = {"SPY": rising(300), "VTI": rising(300, 50), "SGOV": [100.0] * 300, "XLE": falling_late(300), "XLK": rising(300, 20)}
    for k, sym in enumerate(["NVDA", "AAPL", "MSFT"]):
        closes[sym] = rising(300, step=1.0 - k * 0.2)
    return closes


class Checks(BotTestCase):
    def test_a_good_plan_is_kept_exactly(self):
        self.assertEqual(pf.check_blocks(BLOCKS), BLOCKS)
        self.assertIsNone(pf.check_blocks(None))

    def test_bad_plans_say_why(self):
        bad = [
            ([], "at least one block"),
            ([{**BLOCKS[0], "pct": 0.7}, BLOCKS[1], BLOCKS[2]], "add up to 120%"),
            ([{**BLOCKS[0], "type": "options"}], "type must be"),
            ([{**BLOCKS[0], "tickers": ["spy"]}], "doesn't look like a ticker"),
            ([BLOCKS[0], {**BLOCKS[1], "tickers": ["VTI"]}], "VTI is in two blocks"),
            ([{**BLOCKS[2], "picks": 4}], "picks must be"),
            ([{**BLOCKS[0], "picks": 1}], "only a momentum block"),
            ([{**BLOCKS[0], "name": "<script>"}], "can use letters"),
            ([BLOCKS[0], {**BLOCKS[1], "name": "index FUNDS"}], "two blocks are called"),
            ([{**BLOCKS[0], "pct": True}], "between 1% and 100%"),
            ([{**BLOCKS[0], "secret": 1}], "unknown field"),
            ([dict(BLOCKS[0], name=f"B{i}", tickers=[f"T{i}"], pct=0.1) for i in range(7)], "at most 6 blocks"),
        ]
        for blocks, said in bad:
            with self.assertRaises(ValueError) as e:
                pf.check_blocks(blocks)
            self.assertIn(said, str(e.exception), blocks)

    def test_the_file_reader_drops_a_bad_plan_and_keeps_the_rest(self):
        path = os.path.join(self.tmp, "strategy.json")
        with open(path, "w") as f:
            json.dump({"settings": {"PICKS": 3, "INVESTED": 0.05, "SELF_TUNE": False, "BLOCKS": [{"name": "x"}], "PRESETS": []}}, f)
        got = strategy_settings.load(path)
        self.assertEqual(got, {"PICKS": 3, "INVESTED": 0.10, "SELF_TUNE": False})      # INVESTED clamped, BLOCKS dropped

    def test_strict_checks_only_where_allowed(self):
        common.validate_settings("stocks", {"BLOCKS": BLOCKS, "INVESTED": 0.6, "SELF_TUNE": False}, custom=True)
        for k, v in (("BLOCKS", BLOCKS), ("INVESTED", 0.6), ("SELF_TUNE", True), ("PRESETS", [])):
            with self.assertRaises(ValueError):          # never on the leaderboard
                common.validate_settings("stocks", {k: v})
        with self.assertRaises(ValueError):
            common.validate_settings("stocks", {"INVESTED": 0.05}, custom=True)

    def test_presets_hold_plan_settings_only(self):
        ok = [{"name": "Calm", "settings": {"INVESTED": 0.5, "AGGRESSIVE": False}}, {"name": "Mine", "settings": {"BLOCKS": BLOCKS}}]
        common.validate_settings("stocks", {"PRESETS": ok}, custom=True)
        for bad in ([{"name": "A", "settings": {"PRESETS": []}}], [{"name": "A", "settings": {"SELF_TUNE": True}}],
                    [{"name": "A", "settings": {"PICKS": 99}}], ok + [{"name": "calm", "settings": {"PICKS": 2}}],
                    [{"name": f"P{i}", "settings": {"PICKS": 2}} for i in range(9)]):
            with self.assertRaises(ValueError):
                common.validate_settings("stocks", {"PRESETS": bad}, custom=True)


class Holdings(BotTestCase):
    def test_each_kind_of_block(self):
        s = dict(S, BLOCKS=BLOCKS)
        ch = pf.choose(market(), None, True, s)
        self.assertEqual(ch["held"]["Index funds"], ["SPY", "VTI"])             # long term: always
        self.assertEqual(ch["held"]["Energy"], ["XLK"])                         # sector: XLE fell below its line
        self.assertEqual(ch["held"]["Tech"], ["NVDA", "AAPL"])                  # momentum: the 2 strongest
        t = pf.targets(al.INVEST, ch["held"], s)
        self.assertEqual(t, {"SPY": 0.25, "VTI": 0.25, "XLK": 0.1, "NVDA": 0.15, "AAPL": 0.15, "SGOV": 0.1})

    def test_half_invested_and_aggressive(self):
        s = dict(S, BLOCKS=BLOCKS, INVESTED=0.5)
        held = pf.choose(market(), None, True, s)["held"]
        t = pf.targets(al.AGGRESSIVE, held, s)
        self.assertEqual(t["SSO"], 0.125)                  # SPY in a long-term block becomes the 2x fund
        self.assertNotIn("SPY", t)
        self.assertAlmostEqual(sum(t.values()), 1.0, places=3)
        self.assertAlmostEqual(t["SGOV"], 0.5 + 0.05, places=4)       # half kept aside, and the Energy slot XLE left
        self.assertEqual(pf.targets(al.SAFETY, held, s), {"SGOV": 1.0})

    def test_between_repicks_the_holdings_stay(self):
        s = dict(S, BLOCKS=BLOCKS)
        prev = {"Index funds": ["SPY", "VTI"], "Energy": ["XLE"], "Tech": ["MSFT", "GONE"]}
        held = pf.choose(market(), prev, False, s)["held"]
        self.assertEqual(held["Energy"], ["XLE"])
        self.assertEqual(held["Tech"], ["MSFT"])          # a ticker taken out of the block goes at once

    def test_new_momentum_picks_get_the_news_check(self):
        s = dict(S, BLOCKS=BLOCKS)
        ch = pf.choose(market(), None, True, s, veto=lambda sym, m: {"veto": sym == "NVDA", "reason": "fraud headline"})
        self.assertEqual(ch["held"]["Tech"], ["AAPL", "MSFT"])
        self.assertEqual(ch["vetoes"][0]["symbol"], "NVDA")

    def test_a_ticker_with_no_prices_waits_in_t_bills(self):
        s = dict(S, BLOCKS=[{"name": "New", "type": "long", "pct": 1.0, "tickers": ["SPY", "NEWCO"]}])
        held = pf.choose(market(), None, True, s)["held"]
        self.assertEqual(pf.targets(al.INVEST, held, s), {"SPY": 0.5, "SGOV": 0.5})


class Daily(BotTestCase):
    def test_the_default_plan_is_unchanged(self):
        closes = market()
        d1 = rb.decide("2026-10-19", closes, {}, S)
        d2 = rb.decide("2026-10-19", closes, {}, dict(S, BLOCKS=None, INVESTED=1.0))
        self.assertEqual(d1, d2)
        self.assertNotIn("blocks_sig", d1)

    def test_moving_invested_resizes_every_holding_at_once(self):
        closes = market()
        d = rb.decide("2026-10-19", closes, {}, S)
        state = {"mode": d["mode"], "picks": d["picks"], "last_rebalance": "2026-10-19", "invested": 1.0}
        self.assertFalse(rb.decide("2026-10-20", closes, state, S)["full"])
        half = rb.decide("2026-10-20", closes, state, dict(S, INVESTED=0.5))
        self.assertTrue(half["full"])
        self.assertEqual(half["targets"]["SPY"], 0.4)
        self.assertFalse(rb.decide("2026-10-21", closes, {**state, "invested": 0.5}, dict(S, INVESTED=0.5))["full"])
        self.assertFalse(rb.decide("2026-10-20", closes, {k: v for k, v in state.items() if k != "invested"}, S)["full"])   # an old plan file

    def test_a_custom_plan_through_decide(self):
        s = dict(S, BLOCKS=BLOCKS)
        d = rb.decide("2026-10-19", market(), {}, s)
        self.assertEqual(d["mode"], al.INVEST)
        self.assertEqual(d["picks"], ["NVDA", "AAPL"])
        self.assertEqual(d["targets"]["VTI"], 0.25)
        # mid-month the same plan keeps its holdings...
        state = {"mode": d["mode"], "picks": d["picks"], "last_rebalance": "2026-10-19", "blocks_held": d["blocks_held"], "blocks_sig": d["blocks_sig"]}
        d2 = rb.decide("2026-10-20", market(), state, s)
        self.assertFalse(d2["full"])
        # ...but a change to the blocks is used at the next check
        s3 = dict(s, BLOCKS=[{**BLOCKS[0], "pct": 1.0}])
        d3 = rb.decide("2026-10-20", market(), state, s3)
        self.assertTrue(d3["full"])
        self.assertEqual(d3["targets"], {"SPY": 0.5, "VTI": 0.5})
        # and going back to Charles's own plan re-picks from its list at once
        d4 = rb.decide("2026-10-20", market(), {**state, "blocks_sig": d3["blocks_sig"]}, S)
        self.assertTrue(d4["full"])
        self.assertIn("SPY", d4["targets"])

    def test_reasons_name_the_block(self):
        s = dict(S, BLOCKS=BLOCKS)
        d = rb.decide("2026-10-19", market(), {}, s)
        say = lambda sym, side="buy", all_=False, held=False: rb.reason({"symbol": sym, "side": side, "all": all_, "held": held}, d, {}, s, True)
        self.assertIn("Tech (momentum): NVDA", say("NVDA"))
        self.assertIn("Index funds (long term): VTI is held all the time", say("VTI"))
        self.assertIn("Not in your plan anymore", say("XOM", "sell", True, True))
        self.assertIn("Energy: XLE closed below its 200-day average", say("XLE", "sell", True, True))

    def test_the_backtest_runs_a_custom_plan(self):
        import backtest_core as bc
        n = 600                                         # 25 trading days a month, two years
        dates = [f"{2006 + i // 300}-{1 + (i // 25) % 12:02d}-{1 + i % 25:02d}" for i in range(n)]
        closes = {k: rising(n, 100 if k != "XLE" else 50, 0.2) for k in market()}
        opens = closes
        r = bc.simulate(dates, opens, closes, dict(S, BLOCKS=BLOCKS, SAFE="SGOV"), start=dates[320])
        self.assertGreater(r["values"][-1], 1000)
        held = {sym for _, _, picks in r["picks_log"] for sym in picks}
        self.assertTrue(held <= {"NVDA", "AAPL", "MSFT"})


class Sharing(BotTestCase):
    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.tmp, "strategy.json")
        with open(self.path, "w") as f:
            json.dump({"settings": {"PICKS": 4, "BLOCKS": BLOCKS, "SELF_TUNE": False,
                                    "PRESETS": [{"name": "Calm", "settings": {"INVESTED": 0.5}}]}}, f)

    def test_a_custom_plan_is_not_shared(self):
        self.patch(client, "FILES", {"stocks": "strategy.json", "crypto": "crypto.json"})
        with self.assertRaises(ValueError) as e:
            client.current_settings("stocks", self.tmp)
        self.assertIn("can't be shared", str(e.exception))

    def test_a_copy_keeps_the_users_own_choices_and_drops_their_blocks(self):
        self.patch(client, "FILES", {"stocks": "strategy.json", "crypto": "crypto.json"})
        client.install({"bot": "stocks", "id": "lb-123456", "name": "Ann", "settings": {"PICKS": 6}}, self.tmp)
        with open(self.path) as f:
            got = json.load(f)["settings"]
        self.assertEqual(got, {"PICKS": 6, "SELF_TUNE": False, "PRESETS": [{"name": "Calm", "settings": {"INVESTED": 0.5}}]})

    def test_charles_copy_works_from_a_custom_plan(self):
        """Bug hunt 2026-11-06: the command stopped with a traceback ("can't be shared") for anyone with their own tickers."""
        import io, contextlib
        self.patch(client, "FILES", {"stocks": "strategy.json", "crypto": "crypto.json"})
        self.patch(client, "fetch", lambda eid: {"bot": "stocks", "id": eid, "name": "Ann", "settings": {"PICKS": 4}})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(client.main(["lb-123456", "--dir", self.tmp, "--yes"]), 0)
        self.assertIn("Your own tickers and any money kept aside go", out.getvalue())       # same PICKS, but the blocks go
        with open(self.path) as f:
            self.assertNotIn("BLOCKS", json.load(f)["settings"])

    def test_the_chat_cant_set_tickers_but_the_sliders_can(self):
        with self.assertRaises(ValueError):
            chat.check_settings({"BLOCKS": BLOCKS}, self.path)
        self.assertEqual(chat.check_settings({"PICKS": 3}, self.path), {"PICKS": 3})        # the file's own blocks don't block the chat
        self.assertEqual(chat.check_settings({"INVESTED": 0.7}, self.path, custom=True), {"INVESTED": 0.7})

    def test_apply_and_undo_a_custom_change(self):
        hist = os.path.join(self.tmp, "hist.jsonl")
        ok, said = chat.apply_settings({"BLOCKS": None, "INVESTED": 0.5}, "test", path=self.path, history=hist, tell=False, custom=True,
                                       source="the sliders")
        self.assertTrue(ok)
        self.assertIn("Your blocks 3 blocks (Index funds, Energy, Tech) → the default plan", said)
        self.assertIn("Invested 100% → 50%", said)
        chat.undo_settings("test", path=self.path, history=hist, tell=False)
        with open(self.path) as f:
            self.assertEqual(json.load(f)["settings"]["BLOCKS"], BLOCKS)


class SelfTune(BotTestCase):
    """evaluate.py with a Custom Plan, and the Self-Tune tab's switch."""
    def test_custom_variants_change_rules_and_amounts_never_tickers(self):
        import evaluate
        vs = evaluate.custom_variants(dict(al.DEFAULTS, BLOCKS=BLOCKS))
        self.assertTrue(vs)
        for v in vs:
            self.assertNotIn("INVESTED", v["change"])                     # how much is invested stays the user's
            if v["setting"] == "BLOCKS":
                self.assertEqual([b["tickers"] for b in v["change"]["BLOCKS"]], [b["tickers"] for b in BLOCKS])
                pf.check_blocks(v["change"]["BLOCKS"])                    # every variant is a valid plan
        self.assertIn("AGGRESSIVE", [v["setting"] for v in vs])           # SPY is in a long-term block
        self.assertIn("5 points from Index funds to Tech", [v["label"] for v in vs])
        self.assertIn("Tech: picks", [v["label"] for v in vs])

    def test_self_tune_applies_at_most_once_a_quarter_and_only_when_on(self):
        import evaluate
        from datetime import datetime, timezone, timedelta
        now = datetime(2026, 12, 6, 8, tzinfo=timezone.utc)
        cand = {"label": "Trend line (days)", "from": 200, "to": 150, "why": "Better in every test.", "change": {"TREND_DAYS": 150}}
        queued = []
        q = lambda *a, **k: (queued.append(k), "Queued: apply-settings")[1]
        none = os.path.join(self.tmp, "no-checkup")
        self.assertIn("off", evaluate.self_tune({"candidate": cand}, {"SELF_TUNE": False}, self.tmp, now, q, none))
        self.assertIn("No change", evaluate.self_tune({"candidate": None}, {}, self.tmp, now, q, none))
        self.assertEqual(queued, [])
        said = evaluate.self_tune({"candidate": cand}, {"SELF_TUNE": True}, self.tmp, now, q, none)
        self.assertIn("Trend line (days) 200 → 150", said)
        self.assertEqual((queued[0]["settings"], queued[0]["source"]), ({"TREND_DAYS": 150}, "self-tune"))
        with open(os.path.join(self.tmp, "settings_history.jsonl"), "w") as f:
            f.write(json.dumps({"at": (now - timedelta(days=40)).isoformat(), "by": "self-tune", "changes": {}}) + "\n")
        self.assertIn("this quarter", evaluate.self_tune({"candidate": cand}, {}, self.tmp, now, q, none))
        with open(none, "w") as f:
            f.write("")
        self.assertIn("check-up decides", evaluate.self_tune({"candidate": cand}, {}, self.tmp, now - timedelta(days=200), q, none))
        self.assertEqual(len(queued), 1)

    def test_self_tune_changes_from_the_custom_sources_only(self):
        import updates
        self.assertIn("self-tune", updates.SOURCES)
        self.assertEqual(updates.CUSTOM_SOURCES, ("the sliders", "self-tune"))


class Dashboard(BotTestCase):
    """The Plan tab's data: the ticker search, the holdings' blocks, the presets and the switch, on a preview's test folder."""
    LIST = [{"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust", "kind": "fund"}, {"symbol": "SPOT", "name": "Spotify", "kind": "stock"},
            {"symbol": "XLE", "name": "Energy Select Sector SPDR", "kind": "fund"}, {"symbol": "ASPY", "name": "Aspy Inc", "kind": "stock"},
            {"symbol": "NVDA", "name": "NVIDIA Corp", "kind": "stock"}]

    def test_the_search_puts_the_symbol_first_and_suggests_a_block(self):
        import webdash as wd
        got = wd.ticker_search("spy", (self.LIST,))
        self.assertEqual([r["symbol"] for r in got], ["SPY"])                     # a name only counts from a word's start
        self.assertEqual(got[0]["type"], "long")
        self.assertEqual([r["symbol"] for r in wd.ticker_search("sp", (self.LIST,))], ["SPY", "SPOT", "XLE"])     # XLE by "SPDR"; not ASPY
        self.assertEqual(wd.ticker_search("energy", (self.LIST,))[0]["type"], "sector")
        self.assertEqual(wd.ticker_search("nvidia", (self.LIST,))[0], {"symbol": "NVDA", "name": al.NAMES["NVDA"], "kind": "stock", "type": "momentum"})
        self.assertEqual(wd.ticker_search("   ", (self.LIST,)), [])
        self.assertEqual(len(wd.ticker_search("s", (self.LIST * 3,), limit=2)), 2)
        self.assertEqual(wd.block_type({"kind": "fund", "name": "SPDR Dow Jones Industrial Average ETF Trust"}), "long")
        lookalikes = [{"symbol": "NVD", "name": "GraniteShares 2x Short NVDA Daily ETF", "kind": "fund"}, {"symbol": "NVDA", "name": "NVIDIA Corporation", "kind": "stock"}]
        self.assertEqual([r["symbol"] for r in wd.ticker_search("nvd", (lookalikes,))], ["NVDA", "NVD"])      # the familiar one first
        energy = [{"symbol": "ERII", "name": "Energy Recovery, Inc.", "kind": "stock"}, {"symbol": "ET.PRI", "name": "Energy Transfer 9.25% Preferred", "kind": "stock"},
                  {"symbol": "XLE", "name": "State Street Energy Select Sector SPDR ETF", "kind": "fund"}]
        self.assertEqual([r["symbol"] for r in wd.ticker_search("energy", (energy,))], ["XLE", "ERII"])       # no preferred shares
        self.assertEqual(wd.plain_name("Direxion Shares ETF Trust Direxion Daily NVDA Bear 1X Shares"), "Direxion Shares ETF Trust Direxion Daily NVDA Bear 1X Shares")

    def test_the_built_in_list_is_valid(self):
        import webdash as wd
        rows = wd._builtin_tickers()
        self.assertGreater(len(rows), 150)
        for r in rows:
            self.assertRegex(r["symbol"], pf.SYMBOL)
            self.assertIn(r["kind"], ("stock", "fund"))
        self.assertTrue(set(al.UNIVERSE) <= {r["symbol"] for r in rows})

    def test_holdings_are_named_by_their_block(self):
        import webdash as wd
        plan = {"settings": {"CORE": "SPY", "SAFE": "SGOV", "AGG_CORE": "SSO", "BLOCKS": BLOCKS},
                "targets": {"SSO": 0.25, "VTI": 0.25, "XLK": 0.1, "NVDA": 0.15, "AAPL": 0.15, "SGOV": 0.1}, "picks": ["NVDA", "AAPL"]}
        pos = [{"symbol": s, "market_value": v} for s, v in (("SSO", 250), ("XLK", 100), ("NVDA", 150), ("SGOV", 100), ("XOM", 50))]
        got = {h["symbol"]: (h["role"], h.get("block")) for h in wd.holdings(pos, plan, 1000)}
        self.assertEqual(got, {"SSO": ("core", "Index funds"), "XLK": ("sector", "Energy"), "NVDA": ("pick", "Tech"),
                               "SGOV": ("safety", None), "XOM": ("leaving", None)})
        self.assertEqual([h["symbol"] for h in wd.holdings(pos, plan, 1000)], ["SSO", "XLK", "NVDA", "SGOV", "XOM"])

    def test_charles_presets_move_amounts_only(self):
        for p in pf.PRESETS:
            if p["settings"]:
                common.validate_settings("stocks", p["settings"], custom=True)
                self.assertFalse({"BLOCKS", "PRESETS", "SELF_TUNE"} & set(p["settings"]))
        self.assertEqual([p["name"] for p in pf.PRESETS], ["Default plan", "0.5× aggressive", "2× aggressive"])

    def test_a_preview_saves_blocks_presets_and_the_switch_to_its_test_folder(self):
        import tempfile
        from unittest import mock
        import webdash as wd
        post = lambda **body: wd.settings_post(json.dumps(body).encode(), None)
        state = {"rules": {"settings": dict(al.DEFAULTS)}}
        with tempfile.TemporaryDirectory() as d, mock.patch.object(wd, "LEADERBOARD_BOT_DIR", d), mock.patch.object(wd, "STATE_FROM", "http://x"), \
                mock.patch.object(wd, "_cached", side_effect=lambda k, t, f: state if k == "state" else [] if k == "alpaca_assets" else f()), \
                mock.patch.object(wd.control, "request", side_effect=AssertionError("left a request")), \
                mock.patch.object(chat, "_tell", side_effect=AssertionError("told someone")):
            v = wd.settings_view("stocks", None, None)
            self.assertEqual((v["settings"]["BLOCKS"], v["settings"]["INVESTED"], v["self_tune"], v["presets"]), (None, 1.0, True, []))
            self.assertEqual(v["limits"]["INVESTED"], {"min": 0.1, "max": 1.0})
            self.assertEqual(v["defaults"]["BLOCKS"], None)
            self.assertEqual([p["id"] for p in v["charles_presets"]], ["default", "half", "aggressive"])
            self.assertEqual(post(bot="stocks", settings={"BLOCKS": BLOCKS, "INVESTED": 0.6})[0], 200)
            saved = [{"name": "Mine", "settings": {**{k: al.DEFAULTS[k] for k in wd.PLAN_KEYS}, "BLOCKS": BLOCKS, "INVESTED": 0.6}}]
            self.assertEqual(post(bot="stocks", settings={"PRESETS": saved})[0], 200)
            self.assertEqual(post(bot="stocks", settings={"SELF_TUNE": False})[0], 200)
            v = wd.settings_view("stocks", None, None)
            self.assertEqual((v["settings"]["BLOCKS"], v["settings"]["INVESTED"], v["self_tune"]), (BLOCKS, 0.6, False))
            self.assertEqual(v["presets"], saved)
            self.assertNotIn("SELF_TUNE", v["settings"])                  # the switch isn't a slider
            self.assertEqual(v["names"]["NVDA"], al.NAMES["NVDA"])
            bad = post(bot="stocks", settings={"BLOCKS": [{**BLOCKS[0], "tickers": []}]})
            self.assertEqual(bad[0], 400)
            self.assertIn("add at least one ticker", bad[1]["error"])
            post(bot="stocks", undo=True)                                   # the switch back on
            self.assertTrue(wd.settings_view("stocks", None, None)["self_tune"])

    def test_the_self_tune_tab_hears_what_self_tune_did(self):
        import webdash as wd
        v = wd.evaluation_view({"variants": [{"passes": True}], "candidate": None, "custom": True, "self_tune_said": "Self-tune is off, so nothing was changed."})
        self.assertEqual((v["custom"], v["self_tune_said"]), (True, "Self-tune is off, so nothing was changed."))


class AlpacaList(BotTestCase):
    def test_the_raw_list_keeps_what_can_be_bought_in_dollars(self):
        from unittest import mock
        import webdash as wd
        raw = [{"symbol": "NVDA", "name": "NVIDIA Corporation Common Stock", "tradable": True, "fractionable": True, "exchange": "NASDAQ"},
               {"symbol": "BRK/B", "name": "BERKSHIRE HATHAWAY Class B", "tradable": True, "fractionable": True, "exchange": "NYSE"},
               {"symbol": "TINY", "name": "Tiny Co", "tradable": True, "fractionable": False, "exchange": "TXSE"},
               {"symbol": "XLE", "name": "State Street Energy Select Sector SPDR ETF", "tradable": True, "fractionable": True, "exchange": "ARCA"},
               {"symbol": "GONE", "name": "Gone Inc", "tradable": False, "fractionable": True, "exchange": "NYSE"}]
        client = mock.Mock()
        client.get.return_value = raw
        with mock.patch("alpaca_executor._client", return_value=client):
            got = wd._alpaca_assets()
        client.get.assert_called_once_with("/assets", {"status": "active", "asset_class": "us_equity"})
        self.assertEqual(got, [{"symbol": "NVDA", "name": "NVIDIA Corporation", "kind": "stock"},
                               {"symbol": "BRK.B", "name": "Berkshire Hathaway Class B", "kind": "stock"},
                               {"symbol": "XLE", "name": "State Street Energy Select Sector SPDR ETF", "kind": "fund"}])
