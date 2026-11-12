"""The legal documents (ops/legal/: owner 2026-11-07, "anything legal ... i want to sell charles and i want it to be legal"):
every page is made from the templates and business.json and is up to date, every fact is filled in or reads naturally
without it, the numbers the setup service uses are the ones the documents promise, and the website says nothing the
documents would have to take back."""
import glob
import importlib.util
import json
import os
import re
import unittest

from base import BotTestCase

import terms

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENDER = os.path.join(ROOT, "ops", "legal", "render.py")
SITE = os.environ.get("CHARLES_SITE_DIR", "/root/site-next")


def load_render():
    spec = importlib.util.spec_from_file_location("legal_render", RENDER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(os.path.exists(RENDER), "a public copy without ops/")
class Documents(BotTestCase):
    def setUp(self):
        super().setUp()
        self.r = load_render()

    def test_the_repos_documents_are_up_to_date(self):
        for path, text in self.r.outputs([]).items():
            with open(path) as f:
                self.assertEqual(f.read(), text, f"{os.path.basename(path)} is out of date: run python3 -I ops/legal/render.py")

    def test_the_agreement_is_this_version_with_its_short_version(self):
        with open(os.path.join(ROOT, "LICENSE.md")) as f:
            text = f.read()
        self.assertIn(f"Version {terms.VERSION}, effective {terms.EFFECTIVE}", text)
        for line in terms.SHORT:
            self.assertIn(line, text)
        for must in ("binding individual arbitration", "opt out", "AS IS", "not investment advice", "18"):
            self.assertIn(must.lower(), text.lower(), must)

    def test_each_document_gets_its_own_link_preview_tags(self):
        shell = ('<head><title>Set up Charles</title><meta name="description" content="Setup.">'
                 '<meta property="og:title" content="Set up Charles"><meta property="og:description" content="Setup.">'
                 '<meta property="og:url" content="https://charles-bot.xyz/setup.html"></head><body><main>x</main><footer>f</footer></body>')
        name = next(iter(self.r.DOCS))
        _, _, file, title, _, about = self.r.DOCS[name]
        page = self.r.page(name, "# Heading\n\nVersion 1\n\nText.", shell)
        self.assertIn(f'<meta property="og:url" content="https://charles-bot.xyz/{file}">', page)
        self.assertIn(f'<meta property="og:title" content="{title}">', page)
        self.assertNotIn("setup.html", page.split("</head>")[0])

    def test_every_fact_is_filled_in_or_reads_naturally(self):
        blank = self.r.context({"price_cents": 2999, "refund_days": 14, "unused_refund_days": 14, "keep_server_details_hours": 24,
                                "keep_order_records_years": 2}, "9.9", "January 1, 2030")
        for name in self.r.DOCS:
            md = self.r.markdown(name, blank)
            self.assertNotIn("{{", md, name)
            self.assertNotIn("None", md, name)
            self.assertNotIn("- \n", md, name)
        self.assertEqual(blank["PRICE"], "$29.99")
        self.assertIn("the people who develop Charles", blank["PARTY"])
        with self.assertRaises(KeyError):
            self.r.fill("Hello {{NOBODY}}", blank)

    def test_a_named_seller_goes_everywhere_it_should(self):
        ctx = self.r.context({"operator": "Charles Bot LLC", "entity": "an Ohio limited liability company", "state": "Ohio",
                              "county": "Franklin County", "address": "1 Main St, Columbus, OH 43215", "support_email": "help@example.com",
                              "price_cents": 2999, "refund_days": 14, "unused_refund_days": 14, "keep_server_details_hours": 24,
                              "keep_order_records_years": 2}, "2.0", "November 7, 2026")
        self.assertEqual(ctx["PARTY"], "Charles Bot LLC, an Ohio limited liability company")
        self.assertEqual(ctx["LAW"], "the laws of the State of Ohio")
        self.assertIn("Franklin County, Ohio", ctx["VENUE"])
        terms_md = self.r.markdown("terms", ctx)
        for want in ("Charles Bot LLC", "1 Main St, Columbus, OH 43215", "help@example.com", "the laws of the State of Ohio"):
            self.assertIn(want, terms_md)

    def test_html_is_escaped_and_links_are_safe(self):
        out = self.r.inline('A <script>x</script> & **bold** [Terms](https://charles-bot.xyz/terms.html), https://example.com/a. help@example.com')
        self.assertIn("&lt;script&gt;", out)
        self.assertIn('<a href="terms.html">Terms</a>', out)                       # the same site: a relative link
        self.assertIn('<a href="https://example.com/a" rel="noopener">https://example.com/a</a>.', out)
        self.assertIn('<a href="mailto:help@example.com">help@example.com</a>', out)
        self.assertIn("<b>bold</b>", out)
        title, ver, parts = self.r.body("# T\n\nVersion 1\n\n## 3. Not advice\n\nNOTHING HERE IS ADVICE. WE ARE NOT ADVISERS OF ANY KIND AT ALL.\n")
        self.assertEqual((title, ver), ("T", "Version 1"))
        self.assertIn('<h2 id="s3">', parts[0])
        self.assertIn('class="caps"', parts[1])

    def test_the_numbers_the_service_uses_are_the_ones_the_documents_promise(self):
        from setupservice import business, service
        facts = business.facts()
        self.assertEqual(facts["price_cents"], 2999)                                # owner 2026-11-07: "i want to sell it for 29.99"
        for key, default in service.POLICY.items():
            self.assertEqual(facts[key], default, key)
        ctx = self.r.context(facts, *self.r.version())
        refunds = self.r.markdown("refunds", ctx)
        privacy = self.r.markdown("privacy", ctx)
        self.assertIn("within 14 days", refunds)
        self.assertIn("$29.99", refunds)
        self.assertIn("24 hours", privacy)
        self.assertNotIn("support_email_verified", json.dumps(facts))          # proof of the inbox lives with the service, not here


@unittest.skipUnless(os.path.exists(RENDER) and os.path.exists(os.path.join(SITE, "terms.html")), "no website checkout here")
class Website(BotTestCase):
    """What the website says has to be true, and the documents have to be one click away on every page."""
    def pages(self):
        out = {}
        for path in sorted(glob.glob(os.path.join(SITE, "*.html"))):
            with open(path) as f:
                out[os.path.basename(path)] = f.read()
        return out

    def test_the_legal_pages_are_made_from_the_current_documents(self):
        r = load_render()
        for path, text in r.outputs([SITE]).items():
            if path.startswith(SITE):
                with open(path) as f:
                    self.assertEqual(f.read(), text, f"{path} is out of date: run python3 -I ops/legal/render.py --site {SITE}")

    def test_every_page_links_the_documents_and_says_what_charles_isnt(self):
        for name, html in self.pages().items():
            for doc in ("terms.html", "privacy.html", "risk.html", "refunds.html", "legal.html"):
                self.assertIn(f'href="{doc}"', html, f"{name} has no link to {doc}")
            self.assertIn("not investment advice", html, name)
            self.assertIn("support@charles-bot.xyz", html, name)

    def test_no_promises_and_no_hindsight(self):
        banned = ("Copy what works", "guarantee", "risk-free", "risk free", "can't lose", "passive income", "18.9%", "beat the market",
                  "Today's stocks*")
        for name, html in self.pages().items():
            if name in ("terms.html", "privacy.html", "risk.html", "refunds.html", "legal.html"):
                continue                                         # the documents may name what they disclaim
            for phrase in banned:
                self.assertNotIn(phrase.lower(), html.lower(), f"{name} says {phrase!r}")
        with open(os.path.join(SITE, "js", "home.js")) as f:
            self.assertNotIn("Today's stocks", f.read())

    def test_nothing_is_loaded_from_another_site(self):
        for name, html in self.pages().items():
            self.assertNotIn("fonts.googleapis.com", html, name)
            self.assertNotIn("fonts.gstatic.com", html, name)
            for src in re.findall(r'<(?:script|link)(?![^>]*rel="canonical")[^>]+(?:src|href)="(https?://[^"]+)"', html):
                self.fail(f"{name} loads {src}")
            # a canonical link names the page's own address: nothing is fetched, and it must be ours
            for own in re.findall(r'<link rel="canonical" href="([^"]*)"', html):
                self.assertTrue(own.startswith("https://charles-bot.xyz/"), f"{name}: canonical {own}")
        with open(os.path.join(SITE, "css", "styles.css")) as f:
            css = f.read()
        self.assertNotIn("googleapis", css)
        for font in re.findall(r"url\(\.\./(assets/fonts/[^)]+)\)", css):
            self.assertTrue(os.path.exists(os.path.join(SITE, font)), font)

    def test_the_referral_link_is_disclosed_where_it_is(self):
        for name, html in self.pages().items():
            if "m.do.co/c/" in html:
                self.assertIn("referral link", html, name)
                self.assertIn('rel="noopener sponsored"', html, name)

    def test_the_paid_setup_is_shown_only_when_the_service_says_it_sells(self):
        html = self.pages()
        for name in ("index.html", "setup.html"):
            for tag in re.findall(r"<[^>]*data-sfm[^-][^>]*>", html[name]):
                self.assertIn("hidden", tag, f"{name}: {tag}")
        with open(os.path.join(SITE, "js", "site.js")) as f:
            js = f.read()
        self.assertIn("/api/setup/status", js)
        self.assertIn("d.mode !== 'live'", js)
