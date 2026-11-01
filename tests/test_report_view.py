"""report_view: Claude's report as short findings, each with a small chart from the bot's own records."""
from datetime import datetime, timezone

from base import BotTestCase

import report_view as rv

NOW = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
STOCKS = {"positions": [{"symbol": "DELL", "unrealized_pct": -7.0}, {"symbol": "MU", "unrealized_pct": -1.0}],
          "equity": [["2026-10-29T20:00:00+00:00", 1070.0], ["2026-10-30T20:00:00+00:00", 1030.0], ["2026-10-31T03:00:00+00:00", 1031.0]],
          "spy": [["2026-10-16", 600.0], ["2026-10-29", 606.0], ["2026-10-30", 603.0]],
          "account": {"day_change": 0.0}, "today": {"limit": 20.0},
          "plan": {"started": "2026-10-16", "trend": {"gap_pct": 6.0}}}
KALSHI = {"betting": {"equity": [["2026-10-20T00:00:00+00:00", 570.0], ["2026-10-31T00:00:00+00:00", 560.0]],
                      "pnl_lines": {"weather": [["2026-10-09", 0.0], ["2026-10-20", -331.0]]},
                      "strategies": [{"key": "weather", "name": "Weather", "pnl": -331.49}, {"key": "sports", "name": "Sports", "pnl": -30.19}],
                      "milestones": [{"key": "weather", "name": "Weather: predicting", "level": "predict", "progress": .5}, {"key": "tune", "name": "Next tune", "progress": .7}]}}
OLD = """# Report

## Where things stand
- Account **$1,041.53** before the open (−$30.43 since yesterday's report). It wrapped
  onto a second line.
- Plan vs SPY since it started: the status shows Charles −2.84% vs SPY −0.31%.

## What I noticed
- **The overnight drop is mostly DELL and MU.** DELL fell 7%. MU fell 1%.
- **The daily-loss limit will probably trip** at the 10:00 check, so buys stop.
- **This also explains yesterday's puzzle.** The checks ran on time at 10:00 and 2 PM.
- Nothing else came up worth saying here.

## What I changed
- Nothing.
"""


class Short(BotTestCase):
    def test_a_bold_lead_that_says_something_is_enough(self):
        self.assertEqual(rv.short("**The overnight drop is mostly DELL and MU.** DELL fell 7%."), "The overnight drop is mostly DELL and MU.")

    def test_a_vague_or_label_lead_keeps_what_follows(self):
        self.assertEqual(rv.short("**This also explains it.** The checks ran on time."), "The checks ran on time.")
        self.assertEqual(rv.short("**Weather:** −$331.49 all-time, unchanged."), "Weather: −$331.49 all-time, unchanged.")

    def test_a_decimal_is_not_the_end_of_a_sentence(self):
        self.assertEqual(rv.short("Brier 0.434 vs 0.162 this week. More follows here."), "Brier 0.434 vs 0.162 this week.")

    def test_long_ones_stop_at_a_natural_break(self):
        s = rv.short("The account fell a long way overnight because several of the holdings dropped, and the market followed them down later on")
        self.assertLessEqual(len(s), 101)
        self.assertTrue(s.endswith("dropped"))


class Declared(BotTestCase):
    REPORT = {"date": "2026-10-30", "text": """## Where things stand
- Ignored once there are findings.

## Findings
- account | The account fell overnight.
- holdings | The drop is mostly DELL and MU.
- stock:DELL | Dell fell 7%.
- bars:DELL=-13,MU=-9.5:pts | Past the line by this much.
- none | The checks ran on time.
- nonsense | A chart we don't know.
"""}

    def test_each_line_gets_its_chart(self):
        v = rv.view(self.REPORT, "stocks", STOCKS, NOW, lambda sym: [["2026-10-01", 100.0], ["2026-10-30", 90.0]])
        self.assertEqual([i["chart"]["type"] for i in v["items"]], ["line", "bars", "line", "bars"])
        self.assertEqual(v["items"][1]["chart"]["rows"], [["DELL", -7.0], ["MU", -1.0]])
        self.assertEqual(v["items"][3]["chart"], {"type": "bars", "rows": [["DELL", -13.0], ["MU", -9.5]], "fmt": "pts"})
        self.assertEqual(v["also"], ["The checks ran on time.", "A chart we don't know."])
        self.assertEqual(v["text"], self.REPORT["text"])

    def test_a_chart_shows_once(self):
        r = {"date": "2026-10-30", "text": "## Findings\n- account | One.\n- account | Two.\n"}
        v = rv.view(r, "stocks", STOCKS, NOW)
        self.assertEqual([i["text"] for i in v["items"]], ["One."])
        self.assertEqual(v["also"], ["Two."])

    def test_a_bad_symbol_never_reaches_the_fetch(self):
        asked = []
        r = {"date": "2026-10-30", "text": "## Findings\n- stock:../../etc | Nope.\n"}
        rv.view(r, "stocks", STOCKS, NOW, lambda s: asked.append(s))
        self.assertEqual(asked, [])

    def test_no_report(self):
        self.assertEqual(rv.view(None, "stocks", STOCKS, NOW)["items"], [])
        self.assertEqual(rv.view({"text": ""}, "kalshi", KALSHI, NOW)["also"], [])


class Guessed(BotTestCase):
    """Every report written before the Findings section: read from its bullets."""
    def test_a_chart_from_what_the_sentence_is_about(self):
        v = rv.view({"date": "2026-10-30", "text": OLD}, "stocks", STOCKS, NOW)
        got = {i["text"]: i["chart"]["type"] for i in v["items"]}
        self.assertEqual(got["The overnight drop is mostly DELL and MU."], "bars")
        self.assertEqual(got["The daily-loss limit will probably trip"], "gauge")
        self.assertEqual(got["Plan vs SPY since it started: the status shows Charles −2.84% vs SPY −0.31%."], "lines")
        self.assertIn("The checks ran on time at 10:00 and 2 PM.", v["also"])

    def test_wrapped_lines_join_and_other_sections_stay_out(self):
        secs = rv.sections(OLD)
        self.assertIn("onto a second line", secs["where things stand"][0])
        self.assertNotIn("Nothing.", " ".join(rv.view({"text": OLD}, "stocks", STOCKS, NOW)["also"]))

    def test_the_loss_limit_shows_the_reports_day(self):
        g = rv.view({"date": "2026-10-30", "text": OLD}, "stocks", STOCKS, NOW)["items"]
        c = next(i["chart"] for i in g if i["chart"]["type"] == "gauge")
        self.assertEqual(c["value"], -40.0)                 # the report's day, though today is a quiet Saturday
        self.assertEqual(c["mark"], -20.0)
        self.assertIn("Oct 30", c["label"])

    def test_vs_spy_counts_from_the_plans_start(self):
        c = rv.stocks_chart("vs-spy", STOCKS, NOW)
        self.assertEqual([s["label"] for s in c["series"]], ["Charles", "SPY"])
        self.assertEqual(c["series"][0]["points"][0][1], 0.0)
        self.assertEqual(c["series"][1]["points"][-1][1], -0.5)         # 606 → 603, from the first day both have

    def test_kalshi_by_strategy(self):
        r = {"date": "2026-10-29", "text": """## Where things stand
- Book value **$562.74**, down from the start.
- 9 bets open (2 inflation, 7 longshot), $170 at cost.

## How each strategy is doing
- **Weather:** −$331.49 all-time.
"""}
        v = rv.view(r, "kalshi", KALSHI, NOW)
        self.assertEqual([i["chart"]["type"] for i in v["items"]], ["line", "bars", "line"])
        self.assertEqual(v["items"][1]["chart"]["rows"][0], ["Weather", -331.49])
        self.assertTrue(v["items"][2]["chart"]["zero"])

    def test_kalshi_levels(self):
        c = rv.kalshi_chart("levels", KALSHI, NOW)
        self.assertEqual(c["rows"], [["Weather", 50]])
        self.assertEqual(c["max"], 100)


class Charts(BotTestCase):
    def test_a_chart_with_no_data_is_no_chart(self):
        for spec in ("account", "vs-spy", "trend", "holdings", "loss-limit", "stock:DELL", "bars:"):
            self.assertIsNone(rv.stocks_chart(spec, {}, NOW), spec)
        for spec in ("book", "strategy:weather", "strategies", "levels"):
            self.assertIsNone(rv.kalshi_chart(spec, {}, NOW), spec)

    def test_a_long_history_is_thinned_keeping_the_ends(self):
        rows = [[f"2026-10-{i:02d}", float(i)] for i in range(1, 31)] * 4
        out = rv._thin(rows)
        self.assertEqual(len(out), 60)
        self.assertEqual(out[0], rows[0])
        self.assertEqual(out[-1], rows[-1])
