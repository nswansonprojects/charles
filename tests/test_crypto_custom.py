"""The crypto plan's own coins, presets, Invested and self-tune (owner 2026-11-03: "complete the crypto customization as well to
match the stock configuration"): the blocks, the book trading them, the checks, the coin search and the monthly check."""
import json
import os
from datetime import datetime, timezone, timedelta
from unittest import mock

from base import BotTestCase

import crypto_paper
import crypto_plan as cp
import crypto_settings
import evaluate
import updates
import webdash as wd
from leaderboard import client

S = {"TREND_DAYS": 3}
UP, DOWN = [1.0] * 3 + [2.0], [2.0] * 3 + [1.0]          # above / below a 3-day line
NOW = datetime(2026, 11, 3, 20, tzinfo=timezone.utc)


def rising(n, end):
    """n closes climbing to `end` (so above any shorter line), with a 90-day momentum that grows with `end`."""
    return [end * (0.5 + 0.5 * i / (n - 1)) for i in range(n)]


TREND = {"name": "Majors", "type": "trend", "pct": 0.5, "tickers": ["BTC-USD", "ETH-USD"]}
MOM = {"name": "Alts", "type": "momentum", "pct": 0.3, "tickers": ["SOL-USD", "DOGE-USD", "ADA-USD"], "picks": 2}
LONG = {"name": "Hold", "type": "long", "pct": 0.2, "tickers": ["LTC-USD"]}


class Blocks(BotTestCase):
    def test_trend_coins_wait_in_cash_under_their_line(self):
        d = cp.decide({"BTC-USD": UP, "ETH-USD": DOWN}, {**S, "BLOCKS": [TREND]})
        self.assertEqual(d["targets"], {"BTC-USD": 0.25, "ETH-USD": 0.0})          # half the block each; ether's half in cash
        self.assertEqual(d["coins"]["ETH-USD"]["block"], "Majors")

    def test_long_term_coins_are_held_either_way(self):
        d = cp.decide({"LTC-USD": DOWN}, {**S, "BLOCKS": [LONG]})
        self.assertEqual(d["targets"], {"LTC-USD": 0.2})

    def test_momentum_holds_the_strongest_above_their_line(self):
        closes = {"SOL-USD": rising(120, 3), "DOGE-USD": rising(120, 2), "ADA-USD": rising(120, 1)}
        closes["SOL-USD"][-1] = 0.1                                                   # the strongest is under its line now
        d = cp.decide(closes, {**S, "BLOCKS": [MOM]})
        self.assertEqual(d["targets"]["SOL-USD"], 0.0)
        self.assertEqual(d["targets"]["DOGE-USD"], 0.15)                              # 30% over 2 picks
        self.assertEqual(d["targets"]["ADA-USD"], 0.15)

    def test_a_coin_without_90_days_is_not_a_momentum_pick(self):
        d = cp.decide({"SOL-USD": rising(40, 3), "DOGE-USD": rising(120, 1), "ADA-USD": rising(120, 2)}, {**S, "BLOCKS": [MOM]})
        self.assertEqual(d["targets"]["SOL-USD"], 0.0)

    def test_invested_scales_every_share(self):
        d = cp.decide({"BTC-USD": UP, "ETH-USD": UP}, {**S, "INVESTED": 0.5})
        self.assertEqual(d["targets"], {"BTC-USD": 0.3, "ETH-USD": 0.2})
        d = cp.decide({"BTC-USD": UP, "ETH-USD": UP, "LTC-USD": UP}, {**S, "INVESTED": 0.5, "BLOCKS": [TREND, LONG]})
        self.assertEqual(d["targets"], {"BTC-USD": 0.125, "ETH-USD": 0.125, "LTC-USD": 0.1})

    def test_what_the_checks_refuse(self):
        for bad, why in (([{**TREND, "tickers": ["BTC"]}], "doesn't look like a coin"),
                         ([TREND, {**LONG, "tickers": ["BTC-USD"]}], "in two blocks"),
                         ([{**TREND, "type": "sector"}], "type must be"),
                         ([{**TREND, "tickers": [f"C{i}-USD" for i in range(cp.MAX_COINS + 1)]}], f"at most {cp.MAX_COINS}"),
                         ([{**MOM, "picks": 4}], "")):
            with self.assertRaises(ValueError) as e:
                cp.check_blocks(bad)
            self.assertIn(why, str(e.exception))
        self.assertEqual(cp.check_blocks([TREND, MOM, LONG]), [TREND, MOM, LONG])
        self.assertIsNone(cp.check_blocks(None))

    def test_presets_move_only_amounts(self):
        for p in cp.PRESETS:
            self.assertFalse(set(p["settings"] or {}) & {"BLOCKS", "BTC_PCT", "ETH_PCT", "WEIGHTS"}, p["name"])
        self.assertEqual([p["name"] for p in cp.PRESETS], ["Default plan", "0.5× aggressive", "2× aggressive"])


class Reasons(BotTestCase):
    def test_a_block_named_like_its_coin_is_not_repeated(self):
        coin = {"price": 2, "average": 1, "above": True, "gap_pct": 4.3, "block": "Ether", "type": "trend"}
        self.assertEqual(cp.reason("ETH-USD", coin, S), "Ether is 4.3% above its 3-day average.")
        self.assertTrue(cp.reason("ETH-USD", {**coin, "block": "Majors"}, S).startswith("Majors: Ether"))

    def test_a_resize_says_so(self):
        coin = {"price": 2, "average": 1, "above": True, "gap_pct": 4.8, "type": "trend"}
        self.assertEqual(cp.reason("BTC-USD", coin, S, side="SELL", target=0.25),
                         "Bitcoin down to its 25% share of the book (4.8% above its 3-day average).")
        self.assertEqual(cp.reason("LTC-USD", {**coin, "type": "long"}, S, names={"LTC-USD": "Litecoin"}, side="BUY", target=0.1),
                         "Litecoin up to its 10% share of the book (held all the time).")


class Book(BotTestCase):
    def setUp(self):
        super().setUp()
        self.prices = {p: 2.0 for p in ("BTC-USD", "ETH-USD", "LTC-USD")}
        self.closes = {p: UP for p in self.prices}

    def first(self, settings=S):
        return crypto_paper.step(crypto_paper.new_book(NOW.isoformat()), self.closes, self.prices, NOW, settings)

    def test_a_saved_change_brings_the_check_forward(self):
        b = self.first()
        n = len(b["trades"])
        b = crypto_paper.step(b, self.closes, self.prices, NOW + timedelta(hours=1), {**S, "INVESTED": 0.5})
        self.assertEqual([t["side"] for t in b["trades"][n:]], ["SELL", "SELL"])
        self.assertIn("share of the book", b["trades"][-1]["reason"])

    def test_a_change_saved_while_a_price_is_missing_still_brings_the_check_forward(self):
        b = self.first()
        n = len(b["trades"])
        half = {**S, "INVESTED": 0.5}
        no_eth = {p: v for p, v in self.prices.items() if p != "ETH-USD"}
        b = crypto_paper.step(b, self.closes, no_eth, NOW + timedelta(hours=1), half)
        self.assertEqual(len(b["trades"]), n)                                   # waits for ether's price
        b = crypto_paper.step(b, self.closes, self.prices, NOW + timedelta(hours=2), half)
        self.assertEqual([t["side"] for t in b["trades"][n:]], ["SELL", "SELL"])

    def test_no_change_waits_for_the_week(self):
        b = self.first()
        n = len(b["trades"])
        b = crypto_paper.step(b, self.closes, self.prices, NOW + timedelta(hours=1), S)
        self.assertEqual(len(b["trades"]), n)

    def test_a_coin_the_plan_dropped_is_sold(self):
        b = self.first()
        b = crypto_paper.step(b, self.closes, self.prices, NOW + timedelta(hours=1), {**S, "BLOCKS": [LONG]})
        self.assertEqual(b["qty"].get("BTC-USD", 0), 0)
        sold = [t for t in b["trades"] if t["product"] == "BTC-USD" and t["side"] == "SELL"]
        self.assertEqual(sold[-1]["reason"], "Bitcoin isn't in your plan anymore.")
        self.assertGreater(b["qty"]["LTC-USD"], 0)

    def test_a_book_from_before_this_release_checks_when_your_plan_is_new(self):
        old = self.first()
        old.pop("sig")
        n = len(old["trades"])
        same = crypto_paper.step(json.loads(json.dumps(old)), self.closes, self.prices, NOW + timedelta(hours=1), S)
        self.assertEqual(len(same["trades"]), n)                                       # Charles's plan as before: waits for the week
        mine = crypto_paper.step(old, self.closes, self.prices, NOW + timedelta(hours=1), {**S, "BLOCKS": [LONG]})
        self.assertGreater(len(mine["trades"]), n)


class Settings(BotTestCase):
    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.tmp, "crypto.json")
        self.history = os.path.join(self.tmp, "crypto_settings_history.jsonl")

    def apply(self, changes, source="the sliders", custom=True):
        with mock.patch.object(crypto_settings, "_tell"):
            return crypto_settings.apply_settings(changes, "test", path=self.path, history=self.history, source=source, custom=custom)

    def test_your_coins_and_invested_need_the_plan_tab(self):
        with self.assertRaises(ValueError):
            crypto_settings.check({"BLOCKS": [TREND]}, self.path)
        with self.assertRaises(ValueError):
            crypto_settings.check({"INVESTED": 0.5}, self.path)
        self.assertEqual(crypto_settings.check({"BLOCKS": [TREND], "INVESTED": 0.5}, self.path, custom=True), {"BLOCKS": [TREND], "INVESTED": 0.5})

    def test_invested_stays_in_its_range(self):
        lo, hi = crypto_settings.INVESTED_RANGE
        for bad in (lo - 0.01, hi + 0.01, True, "half"):
            with self.assertRaises(ValueError):
                crypto_settings.check({"INVESTED": bad}, self.path, custom=True)

    def test_your_plan_lands_in_the_file_the_book_reads(self):
        self.apply({"BLOCKS": [TREND, LONG], "INVESTED": 0.8})
        s = crypto_settings.load(self.path)
        self.assertEqual((s["BLOCKS"], s["INVESTED"]), ([TREND, LONG], 0.8))
        self.assertEqual(cp.products({**cp.DEFAULTS, **s}), ["BTC-USD", "ETH-USD", "LTC-USD"])

    def test_a_preset_with_a_bad_coin_is_refused(self):
        with self.assertRaises(ValueError):
            crypto_settings.check({"PRESETS": [{"name": "Mine", "settings": {"BLOCKS": [{**TREND, "tickers": ["nope"]}]}}]}, self.path, custom=True)
        ok = [{"name": "Mine", "settings": {"BLOCKS": [TREND], "INVESTED": 0.5}}]
        self.assertEqual(crypto_settings.check({"PRESETS": ok}, self.path, custom=True), {"PRESETS": ok})

    def test_self_tune_is_on_by_default_and_can_be_turned_off(self):
        self.assertTrue(crypto_settings.current(self.path).get("SELF_TUNE", True))
        self.apply({"SELF_TUNE": False})
        self.assertIs(crypto_settings.current(self.path)["SELF_TUNE"], False)

    def test_your_own_plan_is_not_shared_on_the_leaderboard(self):
        self.apply({"BLOCKS": [TREND]})
        os.replace(self.path, os.path.join(self.tmp, client.FILES["crypto"]))
        with self.assertRaises(ValueError):
            client.current_settings("crypto", self.tmp)

    def test_a_copy_goes_back_to_charless_coins(self):
        os.makedirs(os.path.join(self.tmp, "data"), exist_ok=True)
        self.path = os.path.join(self.tmp, "crypto.json")
        self.history = os.path.join(self.tmp, "data", "crypto_settings_history.jsonl")
        self.apply({"BLOCKS": [TREND], "INVESTED": 0.5})
        cfg = {"id": "abc", "name": "trendrider", "bot": "crypto", "settings": {"TREND_DAYS": 150}}
        with mock.patch.object(crypto_settings, "_tell"):
            ok, said = updates.copy_config("abc", "test", fetch=lambda e: cfg, root=self.tmp)
        self.assertTrue(ok, said)
        s = crypto_settings.current(self.path)
        self.assertEqual((s["BLOCKS"], s["INVESTED"], s["TREND_DAYS"]), (None, 1.0, 150))


class CoinSearch(BotTestCase):
    LIST = [{"product": "SOL-USD", "name": "Solana"}, {"product": "LAYER-USD", "name": "Solayer"},
            {"product": "BTC-USD", "name": "Bitcoin"}, {"product": "SHIB-USD", "name": "Shiba Inu"}]

    def test_symbol_first_then_name(self):
        hits = wd.coin_search("sol", (self.LIST, []))
        self.assertEqual([h["symbol"] for h in hits][:2], ["SOL-USD", "LAYER-USD"])
        self.assertEqual(wd.coin_search("shiba", (self.LIST, []))[0]["symbol"], "SHIB-USD")

    def test_bitcoin_goes_to_a_trend_block_others_to_momentum(self):
        types = {h["symbol"]: h["type"] for h in wd.coin_search("b", (self.LIST, [])) + wd.coin_search("sol", (self.LIST, []))}
        self.assertEqual((types["BTC-USD"], types["SOL-USD"]), ("trend", "momentum"))

    def test_without_coinbase_the_built_in_list_answers_and_coinbase_is_asked_again_soon(self):
        with mock.patch.object(wd, "_coinbase_coins", side_effect=OSError("down")), mock.patch.dict(wd._cache, clear=True):
            self.assertEqual(wd.coin_search("eth")[0]["symbol"], "ETH-USD")
            at = wd._cache["coinbase_coins"][0]
            self.assertLess(at, wd.time.time() - 86400 + 601)                      # stale again in 10 minutes, not a day

    def test_nothing_for_an_empty_search(self):
        self.assertEqual(wd.coin_search("  ", (self.LIST, [])), [])


class MonthlyCheck(BotTestCase):
    def fetch(self, product):
        """Made-up daily closes from 2016: bitcoin and ether climb with dips, other coins start in 2021."""
        start = datetime(2016, 1, 1) if product in ("BTC-USD", "ETH-USD") else datetime(2021, 1, 1)
        days = (datetime(2026, 11, 1) - start).days
        k = 1.0 if product == "BTC-USD" else 1.3
        return [((start + timedelta(days=i)).date().isoformat(), 100 * (1.0006 ** i) * (1 + 0.25 * k * ((i // 97) % 3 == 0)))
                for i in range(days)]

    def run_check(self, settings, queue):
        path = os.path.join(self.tmp, "crypto.json")
        with open(path, "w") as f:
            json.dump({"settings": settings}, f)
        with mock.patch("crypto_backtest.history", side_effect=lambda ps, fetch=None: {
                p: {"days": [d for d, _ in self.fetch(p)], "close": [c for _, c in self.fetch(p)]} for p in ps}):
            return evaluate.crypto_main(out=os.path.join(self.tmp, "ev.json"), data_dir=self.tmp, path=path, queue=queue,
                                        now=NOW)

    def test_variants_never_pick_coins_or_how_much_is_invested(self):
        for cur in ({**crypto_settings.current(os.path.join(self.tmp, "none.json"))},
                    {**crypto_settings.current(os.path.join(self.tmp, "none.json")), "BLOCKS": [TREND, MOM]}):
            for v in evaluate.crypto_variants(cur):
                self.assertNotIn("INVESTED", v["change"])
                if "BLOCKS" in v["change"]:
                    self.assertEqual([b["tickers"] for b in v["change"]["BLOCKS"]], [b["tickers"] for b in cur["BLOCKS"]])

    def test_a_passing_change_goes_through_the_updater_and_off_means_off(self):
        asked = []
        q = lambda *a, **k: asked.append(k) or "queued"
        r = self.run_check({}, q)
        self.assertTrue(r["variants"])
        if r["candidate"]:
            self.assertEqual(asked[-1]["bot"], "crypto")
            self.assertEqual(asked[-1]["source"], "self-tune")
            self.assertEqual(asked[-1]["settings"], r["candidate"]["change"])
        else:
            self.assertEqual(asked, [])
        asked.clear()
        r = self.run_check({"SELF_TUNE": False}, q)
        self.assertEqual(asked, [])
        self.assertIn("off", r["self_tune_said"])
        self.assertFalse(r["self_tune"])

    def test_the_self_tune_tab_gets_each_test_and_its_worse_half(self):
        r = self.run_check({}, lambda *a, **k: "queued")
        with open(os.path.join(self.tmp, "ev.json")) as f:
            v = wd.crypto_evaluation_view(json.load(f))
        self.assertEqual(v["tested"], len(r["variants"]))
        self.assertEqual(v["now"], r["current"]["coins"]["all"])
        t, var = v["tests"][0], r["variants"][0]
        worst = min(var["results"]["coins"][h]["cagr_pct"] - r["current"]["coins"][h]["cagr_pct"] for h in ("first", "second"))
        self.assertAlmostEqual(t["worst"], round(worst, 1))
        self.assertIsNone(wd.crypto_evaluation_view(None))


class SettingsView(BotTestCase):
    def test_crypto_has_presets_self_tune_and_its_own_kinds(self):
        with mock.patch.object(wd, "LEADERBOARD_BOT_DIR", wd.ROOT), mock.patch.object(wd, "STATE_FROM", ""), \
                mock.patch.object(wd.auth, "access", return_value="open"), mock.patch.object(crypto_settings, "last_change", return_value=None):
            v = wd.settings_view("crypto", None, None)
        self.assertEqual([p["id"] for p in v["charles_presets"]], ["default", "half", "aggressive"])
        self.assertEqual(set(v["custom"]["types"]), {"long", "trend", "momentum"})
        self.assertIn(v["self_tune"], (True, False))
        self.assertEqual(v["fee"], cp.DEFAULTS["FEE"])
        self.assertEqual(v["custom"]["mom_days"], cp.MOM_DAYS)
