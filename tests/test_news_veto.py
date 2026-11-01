"""news_veto: reads the AI's JSON answer, and fails open (no veto) whenever anything is missing."""
import os

from base import BotTestCase

import news_veto as nv


class NewsVeto(BotTestCase):
    def setUp(self):
        super().setUp()
        self.patch(nv, "LOG_FILE", os.path.join(self.tmp, "vetoes.jsonl"))

    def test_parse(self):
        self.assertEqual(nv.parse('Sure. {"veto": true, "reason": "SEC probe"}'), {"veto": True, "reason": "SEC probe"})
        self.assertIsNone(nv.parse('{"veto": "maybe"}'))
        self.assertIsNone(nv.parse("no json here"))

    def test_veto(self):
        r = nv.check("ABC", 50.0, ask=lambda p: '{"veto": true, "reason": "Accounting probe"}', news=["- Probe"])
        self.assertEqual((r["veto"], r["reason"], r["by"]), (True, "Accounting probe", "ai"))
        with open(nv.LOG_FILE) as f:
            self.assertIn('"momentum": 50.0', f.read())

    def test_fails_open(self):
        def boom(p):
            raise RuntimeError("down")
        self.assertFalse(nv.check("ABC", ask=boom, news=["- x"])["veto"])
        self.assertFalse(nv.check("ABC", ask=lambda p: "???", news=["- x"])["veto"])
        r = nv.check("ABC", ask=boom, news=[])
        self.assertEqual((r["veto"], r["by"]), (False, "none"))

    def test_prompt_has_the_headlines(self):
        seen = []
        nv.check("XYZ", ask=lambda p: seen.append(p) or '{"veto": false, "reason": "ok"}', news=["- Big contract"])
        self.assertIn("XYZ", seen[0])
        self.assertIn("Big contract", seen[0])

    def test_headlines_from_the_rss_feed(self):
        from datetime import datetime, timezone
        rss = b"""<?xml version="1.0"?><rss version="2.0"><channel>
          <item><title>Old news</title><pubDate>Mon, 01 Jun 2026 10:00:00 GMT</pubDate></item>
          <item><title>Earlier</title><pubDate>Tue, 13 Oct 2026 10:00:00 GMT</pubDate></item>
          <item><title>Latest</title><pubDate>Thu, 15 Oct 2026 10:00:00 GMT</pubDate></item>
        </channel></rss>"""
        got = nv.headlines("MU", now=datetime(2026, 10, 16, tzinfo=timezone.utc), fetch=lambda url: rss)
        self.assertEqual(got, ["- Oct 15: Latest", "- Oct 13: Earlier"])

    def test_items_keep_the_link_and_time(self):
        from datetime import datetime, timezone
        rss = b"""<?xml version="1.0"?><rss version="2.0"><channel>
          <item><title>Undated</title><link>https://example.com/u</link></item>
          <item><title>Latest</title><link>https://example.com/a</link><pubDate>Thu, 15 Oct 2026 10:00:00 GMT</pubDate></item>
        </channel></rss>"""
        got = nv.items("MU", now=datetime(2026, 10, 16, tzinfo=timezone.utc), fetch=lambda url: rss)
        self.assertEqual([(r["title"], r["link"]) for r in got], [("Undated", "https://example.com/u"), ("Latest", "https://example.com/a")])
        self.assertEqual(got[1]["published"], datetime(2026, 10, 15, 10, tzinfo=timezone.utc))
        self.assertIsNone(got[0]["published"])

    def test_a_date_without_a_zone_counts_as_utc(self):
        from datetime import datetime, timezone
        rss = b"""<?xml version="1.0"?><rss version="2.0"><channel>
          <item><title>No zone</title><pubDate>Thu, 15 Oct 2026 10:00:00 -0000</pubDate></item>
        </channel></rss>"""
        got = nv.headlines("MU", now=datetime(2026, 10, 16, tzinfo=timezone.utc), fetch=lambda url: rss)
        self.assertEqual(got, ["- Oct 15: No zone"])
