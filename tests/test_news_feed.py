"""news_feed: headlines about the stocks held, for the dashboard. News, not advice: it never trades."""
from datetime import datetime, timezone

from base import BotTestCase

import news_feed as nf

NOW = datetime(2026, 10, 29, 21, tzinfo=timezone.utc)


def feed(*items):
    body = "".join(f"<item><title>{t}</title><link>{link}</link><pubDate>{d}</pubDate></item>" for t, link, d in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{body}</channel></rss>'.encode()


class Mentions(BotTestCase):
    def test_the_company_or_its_ticker(self):
        self.assertTrue(nf.mentions("Micron Stock Falls as Samsung Earnings Show the High Bar", "MU", "Micron"))
        self.assertTrue(nf.mentions("Why Google shares jumped", "GOOGL", "Alphabet (Google)"))
        self.assertTrue(nf.mentions("MU slides 5% on memory worries", "MU", None))
        self.assertFalse(nf.mentions("Alphabet drops 12% in deepening tech and semiconductor rout", "MU", "Micron"))
        self.assertFalse(nf.mentions("Mutual funds see outflows", "MU", "Micron"))       # not inside a word

    def test_pitches_are_not_news(self):
        for t in ("If I Had to Put $5,000 Into Just One Stock, This Would Be It", "Is Micron a Buy?",
                  "3 Stocks to Buy Now", "Analyst raises Micron price target", "Should You Buy Dell?",
                  "GSK vs. Moderna: Which Large-Cap Biotech Stock Offers More Growth?",
                  "SMB IT Spending - Global Strategic Business Report: Capitalize on the $900 Billion Surge",
                  "Will Moderna Soar on Nov. 6?", "DELL Expands AI Security Capabilities: Can It Challenge CSCO & HPE?",
                  "Zacks.com featured highlights include Seagate and Valero Energy",
                  "BofA sees 80% upside for Seagate stock, but Micron shares look cheaper"):
            self.assertRegex(t, nf.PITCH)
        for t in ("Micron completes a buyback", "Dell agrees to buy a storage startup", "Moderna CEO to retire",
                  "Micron to invest $200 billion in US plants", "Which chips Dell will use next. Stock falls 3%",
                  "5 revealing analyst questions from Seagate's Q3 earnings call"):
            self.assertNotRegex(t, nf.PITCH)


class HoldingsNews(BotTestCase):
    def test_only_the_stocks_newest_first_with_their_source(self):
        feeds = {
            "MU": feed(("Micron slides 5% as Samsung misses", "https://finance.yahoo.com/news/a", "Thu, 29 Oct 2026 19:09:00 +0000"),
                       ("Alphabet drops 12%", "https://finance.yahoo.com/video/b", "Thu, 29 Oct 2026 20:26:00 +0000"),
                       ("Is Micron a Buy?", "https://www.fool.com/c", "Thu, 29 Oct 2026 18:00:00 +0000")),
            "DELL": feed(("Dell beats estimates", "https://www.reuters.com/d", "Thu, 29 Oct 2026 20:00:00 +0000")),
        }
        asked = []

        def fetch(url):
            sym = url.split("s=")[1].split("&")[0]
            asked.append(sym)
            return feeds[sym]
        pos = [{"symbol": "SSO", "role": "core"}, {"symbol": "MU", "role": "pick"}, {"symbol": "DELL", "role": "pick"},
               {"symbol": "SGOV", "role": "safety"}]
        got = nf.holdings_news(pos, {"MU": "Micron", "DELL": "Dell"}, now=NOW, fetch=fetch)
        self.assertEqual(sorted(asked), ["DELL", "MU"])                        # no fund feeds
        self.assertEqual([(r["symbol"], r["title"], r["source"]) for r in got["items"]],
                         [("DELL", "Dell beats estimates", "reuters.com"), ("MU", "Micron slides 5% as Samsung misses", "Yahoo Finance")])
        self.assertEqual(got["by_symbol"]["MU"][0]["published"], "2026-10-29T19:09:00+00:00")
        self.assertEqual(got["failed"], [])

    def test_a_failed_feed_leaves_that_stock_out_and_bad_links_are_dropped(self):
        def fetch(url):
            if "s=MU" in url:
                raise OSError("timeout")
            return feed(("Dell beats estimates", "javascript:alert(1)", "Thu, 29 Oct 2026 20:00:00 +0000"))
        got = nf.holdings_news([{"symbol": "MU", "role": "pick"}, {"symbol": "DELL", "role": "pick"}],
                               {"DELL": "Dell"}, now=NOW, fetch=fetch)
        self.assertEqual(got["failed"], ["MU"])
        self.assertEqual(got["items"][0]["link"], "")


def google(*items):
    body = "".join(f"<item><title>{t}</title><link>https://news.google.com/x</link><pubDate>{d}</pubDate><source url='https://x.com'>{src}</source></item>" for t, src, d in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{body}</channel></rss>'.encode()


class CoinNews(BotTestCase):
    def test_names_a_coin_tagged_and_only_once(self):
        def fetch(url):
            return feed(("Bitcoin slides as ETFs see outflows", "https://www.reuters.com/a", "Thu, 29 Oct 2026 12:00:00 GMT"),
                        ("Ethereum upgrade goes live", "https://coindesk.com/b", "Thu, 29 Oct 2026 14:00:00 GMT"),
                        ("Stocks rise on earnings", "https://x.com/c", "Thu, 29 Oct 2026 15:00:00 GMT"),
                        ("3 Crypto Stocks to Buy Now", "https://x.com/d", "Thu, 29 Oct 2026 15:00:00 GMT"))
        got = nf.coin_news(NOW, fetch)["items"]
        self.assertEqual([(g["symbol"], g["title"]) for g in got], [("ETH", "Ethereum upgrade goes live"), ("BTC", "Bitcoin slides as ETFs see outflows")])

    def test_a_dead_feed_is_just_no_news(self):
        def fetch(url):
            raise OSError("down")
        self.assertEqual(nf.coin_news(NOW, fetch)["items"], [])


class BetNews(BotTestCase):
    def test_a_market_as_a_search(self):
        self.assertEqual(nf.topic_of("Will a plague case be reported in the U.S. this year?"), "a plague case be reported in the U.S")
        self.assertEqual(nf.topic_of("When will OpenAI release a personal AI agent?"), "OpenAI release a personal AI agent")
        self.assertEqual(nf.topic_of("Rain in Los Angeles in Oct 2026"), "Rain in Los Angeles")

    def test_only_headlines_about_it(self):
        self.assertFalse(nf.about_topic("Alabama priority 6-foot-3 DB target set to announce", "Target Foot Traffic"))     # not the shop
        self.assertFalse(nf.about_topic("Trump cut funding for farmers before Election Day", "Diesel price on Election Day"))   # not its subject
        self.assertTrue(nf.about_topic("Rainy Halloween in Downtown Los Angeles", "Rain in Los Angeles"))
        self.assertTrue(nf.about_topic("OpenAI's Dot agent can now text you", "OpenAI release a personal AI agent"))

    def test_short_labels(self):
        self.assertEqual(nf.topic_tag("Diesel price on Election Day"), "Diesel price")
        self.assertEqual(nf.topic_tag("a pneumonic plague case be reported"), "Pneumonic plague case")
        self.assertEqual(nf.topic_tag("CPI in October"), "CPI")

    def test_per_topic_newest_first_with_the_source_out_of_the_headline(self):
        def fetch(url):
            return google(("Rain in Los Angeles soaks Halloween - The Times", "The Times", "Thu, 29 Oct 2026 12:00:00 GMT"),
                          ("Rain in Los Angeles again - www.ktla.com", "www.ktla.com", "Thu, 29 Oct 2026 14:00:00 GMT"),
                          ("Rain in Los Angeles, day three - KTLA", "KTLA", "Thu, 29 Oct 2026 16:00:00 GMT"),
                          ("Sunny in Miami - Herald", "Herald", "Thu, 29 Oct 2026 18:00:00 GMT"))
        got = nf.bet_news([{"title": "Rain in Los Angeles in Oct 2026?"}, {"title": "Rain in Los Angeles in Oct 2026?"}], NOW, fetch)["items"]
        self.assertEqual([g["title"] for g in got], ["Rain in Los Angeles, day three", "Rain in Los Angeles again"])       # two a topic, the same topic once
        self.assertEqual(got[0]["source"], "KTLA")
        self.assertEqual(got[1]["source"], "ktla.com")
        self.assertEqual(got[0]["symbol"], "Rain")

    def test_one_search_failing_leaves_just_that_topic_out(self):
        def fetch(url):
            if "Rain" in url:
                raise OSError("down")
            return google(("Plague case found in Arizona - AP", "AP", "Thu, 29 Oct 2026 12:00:00 GMT"))
        got = nf.bet_news([{"title": "Rain in Los Angeles?"}, {"title": "Will a plague case be found?"}], NOW, fetch)["items"]
        self.assertEqual([g["title"] for g in got], ["Plague case found in Arizona"])
