"""feed: the side feed on every bot's page. Charles's own posts in plain words, with nothing made up."""
from base import BotTestCase

import feed


class Notices(BotTestCase):
    def post(self, title, message="", level="info"):
        return feed.notice_post({"ts": "2026-10-31T09:00:00+00:00", "title": title, "message": message, "level": level})

    def test_a_daily_summary_is_a_sentence_with_the_days_money(self):
        p = self.post("Daily summary", "$1,030.15, −$44.58 today, +3.02% since the start. Aggressive mode, next re-pick 2026-11-02")
        self.assertEqual(p["text"], "Ended the day at $1,030.15, +3.02% since the start.")
        self.assertEqual(p["pnl"], -44.58)
        self.assertEqual(self.post("Daily summary", "$1,050.13 | −$21.21 today")["pnl"], -21.21)        # the older form
        self.assertEqual(self.post("Daily summary", "$1,050.13, +$21.21 today")["pnl"], 21.21)

    def test_one_it_cant_read_is_passed_on_as_it_is(self):
        self.assertEqual(self.post("Daily summary", "Something new")["text"], "Something new")
        self.assertEqual(self.post("Something else", "With a message.")["text"], "Something else. With a message.")

    def test_buys_paused_says_what_happened(self):
        p = self.post("Buys paused for today", "Down $44.58 today, past the $20.60 limit.")
        self.assertIn("down $44.58, past the $20.60 daily limit", p["text"])
        self.assertIn("past the daily loss limit", self.post("Buys paused", "")["text"])

    def test_a_rebalance_is_only_news_when_the_mode_moved(self):
        self.assertIsNone(self.post("Rebalanced", "Bought 4 stocks."))
        self.assertIn("safety", self.post("Rebalanced", "Safety mode on: SPY fell below its trend")["text"])

    def test_claudes_notes_go_to_the_right_bot_without_emoji(self):
        p = self.post("🟢 Charles: all good", "- Bot healthy\n- No trades\n- Nothing else")
        self.assertEqual((p["who"], p["bot"]), ("claude", "stocks"))
        self.assertEqual(p["text"], "All good. Bot healthy No trades")
        self.assertEqual(self.post("Kalshi: all good", "- Fine")["bot"], "kalshi")
        self.assertIsNone(self.post("Charles dev: built something"))

    def test_an_error_stands_out(self):
        p = self.post("Charles hit an error", "Disk full", "error")
        self.assertEqual((p["text"], p["tone"]), ("Hit a problem: Disk full", "error"))


class Trades(BotTestCase):
    def trade(self, day, sym, action="BUY", **kw):
        return {"timestamp": f"2026-10-{day}T14:00:00+00:00", "symbol": sym, "action": action, "value": 50.0, "pnl": None, **kw}

    def test_a_days_trades_are_one_post(self):
        rows = feed.trade_posts([self.trade(16, "STX"), self.trade(16, "DELL"), self.trade(16, "MU"),
                                 self.trade(16, "ETSY", "SELL", pnl=3.5), self.trade(16, "ONON", "SELL", pnl=-1.0)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], "Bought STX, DELL and MU. Sold ETSY and ONON.")
        self.assertEqual(rows[0]["pnl"], 2.5)

    def test_a_lone_trade_keeps_its_reason_and_size(self):
        p = feed.trade_posts([self.trade(14, "NU", reasoning="Top of the ranking. Above its average.")])[0]
        self.assertEqual((p["text"], p["amount"]), ("Bought NU. Top of the ranking.", 50.0))

    def test_only_the_last_few_days(self):
        self.assertEqual(len(feed.trade_posts([self.trade(d, "X") for d in range(20, 10, -1)])), 4)


class Bets(BotTestCase):
    def test_results_and_new_bets_in_words(self):
        rows = feed.bet_posts({"betting": {
            "closed": [{"closed": "2026-10-30T10:00:00+00:00", "how": "lost", "sub": "58° or above", "title": "Lowest temperature in Dallas?", "pnl": -0.8}],
            "open": [{"opened": "2026-10-11T10:00:00+00:00", "side": "no", "sub": "Above 94", "title": "Target Foot Traffic?", "cost": 25.65, "evil": True}]}})
        self.assertEqual(rows[0]["text"], "Lost: 58° or above, on “Lowest temperature in Dallas”.")
        self.assertEqual(rows[0]["pnl"], -0.8)
        self.assertEqual(rows[1]["text"], "New bet, NO: Above 94, on “Target Foot Traffic”.")
        self.assertEqual((rows[1]["amount"], rows[1]["name"]), (25.65, "Evil Charles"))


class Together(BotTestCase):
    NEWS = [{"symbol": "MU", "title": "Micron news", "link": "https://x.com/a", "source": "x.com", "published": "2026-10-31T10:00:00+00:00"},
            {"symbol": "MU", "title": "Undated", "link": "", "source": "", "published": None}]
    NOTED = [{"ts": "2026-10-31T09:00:00+00:00", "title": "Charles started", "level": "info"},
             {"ts": "2026-10-31T09:30:00+00:00", "title": "Kalshi: all good", "level": "info"}]

    def test_stocks_is_newest_first_and_only_its_own(self):
        rows = feed.for_stocks({"trades": []}, self.NOTED, self.NEWS)
        self.assertEqual([r["who"] for r in rows], ["news", "charles", "news"])
        self.assertEqual(rows[0]["tag"], "MU")
        self.assertIsNone(rows[2]["ts"])                                    # no date goes last, instead of breaking the sort
        self.assertIsNone(rows[2]["link"])

    def test_kalshi_gets_its_own_notes(self):
        rows = feed.for_kalshi({"betting": {}}, self.NOTED, [])
        self.assertEqual([r["who"] for r in rows], ["claude"])

    def test_nothing_at_all_is_no_posts(self):
        self.assertEqual(feed.for_stocks({}, [], []), [])
        self.assertEqual(feed.for_crypto({}, []), [])
        self.assertEqual(feed.for_kalshi({}, [], []), [])
