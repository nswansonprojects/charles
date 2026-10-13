from datetime import datetime, timezone

import evaluate
import network
import paper

NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)
CLOSE = "2026-12-01T00:00:00Z"


def mk(t, st=None, floor=None, cap=None, rules="", ya="0.50", na="0.50", close=CLOSE, sub=None):
    return {"ticker": t, "status": "active", "market_type": "binary", "strike_type": st,
            "floor_strike": floor, "cap_strike": cap, "rules_primary": rules,
            "yes_ask_dollars": ya, "no_ask_dollars": na, "close_time": close,
            "yes_sub_title": sub or t}


def ev(markets, me=False, et="E", series="S"):
    return {"event_ticker": et, "series_ticker": series, "category": "Test",
            "mutually_exclusive": me, "markets": markets}


def kinds(rels, kind):
    return [r for r in rels if r["type"] == kind]


def legs(r):
    return [(l["side"], l["ticker"]) for l in r["legs"]]


def test_me_all_no_pays_n_minus_one():
    rels = network.relations([ev([mk("A"), mk("B"), mk("C")], me=True)])
    [r] = kinds(rels, "me_all_no")
    assert r["payout"] == 2 and legs(r) == [("no", "A"), ("no", "B"), ("no", "C")]


def test_up_ladder_buys_yes_on_lower_and_no_on_higher():
    up = "If X is above {} on Dec 1, then the market resolves to Yes."
    rels = network.relations([ev([mk("H", "greater", 200, rules=up.format(200)),
                                  mk("L", "greater", 100, rules=up.format(100))])])
    [r] = kinds(rels, "strike_ladder")
    assert legs(r) == [("yes", "L"), ("no", "H")]


def test_down_ladder_buys_yes_on_higher_and_no_on_lower():
    dn = "If X is below {} on Dec 1, then the market resolves to Yes."
    rels = network.relations([ev([mk("L", "less", cap=100, rules=dn.format(100)),
                                  mk("H", "less", cap=200, rules=dn.format(200))])])
    [r] = kinds(rels, "strike_ladder")
    assert legs(r) == [("yes", "H"), ("no", "L")]


def test_exactly_markets_mislabelled_less_are_not_a_ladder():
    ex = "If exactly {} launches reach space in 2026, then the market resolves to Yes."
    rels = network.relations([ev([mk("4", "less", 4, 4, ex.format(4)),
                                  mk("9", "less", 9, 9, ex.format(9))])])
    assert kinds(rels, "strike_ladder") == []


def test_different_subjects_in_one_event_are_not_a_ladder():
    rels = network.relations([ev([
        mk("AUS", "greater", 1, rules="If Austin gets above 1 inch of rain ..."),
        mk("ATL", "greater", 2, rules="If Atlanta gets above 2 inches of rain ...")])])
    assert kinds(rels, "strike_ladder") == []


def test_date_ladder_buys_yes_late_and_no_early():
    r27 = mk("S-27", "greater_or_equal", 100, rules="If S has at least 100 subs before Jan 1, 2027, ...",
             close="2027-01-01T00:00:00Z", sub="100")
    r28 = mk("S-28", "greater_or_equal", 100, rules="If S has at least 100 subs before Jan 1, 2028, ...",
             close="2028-01-01T00:00:00Z", sub="100")
    rels = network.relations([ev([r28], et="E28"), ev([r27], et="E27")])
    [r] = kinds(rels, "date_ladder")
    assert legs(r) == [("yes", "S-28"), ("no", "S-27")]


def test_monthly_reports_are_not_a_date_ladder():
    a = mk("CPI-SEP", rules="If CPI as reported by BLS for September 2026 is above 3%, ...", close="2026-10-15T00:00:00Z")
    b = mk("CPI-OCT", rules="If CPI as reported by BLS for October 2026 is above 3%, ...", close="2026-11-15T00:00:00Z")
    assert kinds(network.relations([ev([a], et="A"), ev([b], et="B")]), "date_ladder") == []


def test_different_start_dates_are_not_a_date_ladder():
    a = mk("B-OCT", rules="If BTC is above $130000 starting Sep 22, 2026 and before Oct 29, 2026, ...",
           close="2026-10-29T00:00:00Z", sub="x")
    b = mk("B-NOV", rules="If BTC is above $130000 starting Oct 30, 2026 and before Nov 26, 2026, ...",
           close="2026-11-26T00:00:00Z", sub="x")
    assert kinds(network.relations([ev([a], et="A"), ev([b], et="B")]), "date_ladder") == []
    b["rules_primary"] = "If BTC is above $130000 starting Sep 22, 2026 and before Nov 26, 2026, ..."
    assert len(kinds(network.relations([ev([a], et="A"), ev([b], et="B")]), "date_ladder")) == 1


def test_fee_rounds_up_per_order():
    assert network.fee(1, 0.5) == 0.02
    assert network.fee(100, 0.5) == 1.75


def test_asks_come_from_opposite_bids():
    book = {"yes": [(0.30, 10)], "no": [(0.60, 5), (0.65, 2)]}
    assert evaluate.asks(book, "yes") == [(0.35, 2), (0.4, 5)]
    assert evaluate.asks(book, "no") == [(0.7, 10)]


def _ladder_rel():
    return {"type": "strike_ladder", "payout": 1, "event": "E", "category": "T", "title": "",
            "why": "", "close_time": CLOSE,
            "legs": [{"ticker": "L", "side": "yes"}, {"ticker": "H", "side": "no"}]}


def test_price_stops_where_depth_stops_paying():
    # YES L offered at 0.40 (5) then 0.55; NO H at 0.50 (100): 0.90/set, then 1.05/set
    books = {"L": {"yes": [], "no": [(0.60, 5), (0.45, 50)]}, "H": {"yes": [(0.50, 100)], "no": []}}
    bet = evaluate.price(_ladder_rel(), books, budget=1000, now=NOW)
    assert bet["sets"] == 5
    assert bet["profit"] == round(5 - bet["cost"], 2) and bet["profit"] > 0
    assert bet["worst_case"] == -bet["cost"]


def test_price_none_when_no_edge():
    books = {"L": {"yes": [], "no": [(0.40, 50)]}, "H": {"yes": [(0.45, 50)], "no": []}}
    assert evaluate.price(_ladder_rel(), books, budget=1000, now=NOW) is None


def test_choose_one_bet_per_market_and_keeps_cash_reserve():
    books = {"L": {"yes": [], "no": [(0.70, 1000)]}, "H": {"yes": [(0.60, 1000)], "no": []}}
    a = _ladder_rel()
    b = {**_ladder_rel(), "event": "E2"}
    chosen, rejected = evaluate.choose([a, b], books, equity=1000, cash=1000, now=NOW)
    assert len(chosen) == 1 and chosen[0]["cost"] <= 100
    assert rejected[0]["reason"].startswith("shares a market")
    chosen, _ = evaluate.choose([a], books, equity=1000, cash=105, now=NOW)
    assert chosen == []  # 100 reserve leaves only 5 to spend


def test_choose_skips_markets_already_held():
    books = {"L": {"yes": [], "no": [(0.70, 1000)]}, "H": {"yes": [(0.60, 1000)], "no": []}}
    chosen, _ = evaluate.choose([_ladder_rel()], books, 1000, 1000, held_tickers={"H"}, now=NOW)
    assert chosen == []


def test_paper_place_and_settle_including_void(tmp_path):
    led = paper.load(str(tmp_path / "none.json"))
    books = {"L": {"yes": [], "no": [(0.70, 1000)]}, "H": {"yes": [(0.60, 1000)], "no": []}}
    [bet], _ = evaluate.choose([_ladder_rel()], books, 1000, 1000, now=NOW)
    paper.place(led, bet, NOW)
    assert led["cash"] == round(1000 - bet["cost"], 2)
    results = {"L": {"status": "active"}, "H": {"status": "finalized", "result": "no"}}
    assert paper.settle(led, results.get, NOW) == []
    results["L"] = {"status": "finalized", "result": "yes"}
    [done] = paper.settle(led, results.get, NOW)
    assert done["received"] == 2 * bet["sets"]  # both legs won
    assert led["open"] == [] and led["cash"] == round(1000 - bet["cost"] + done["received"], 2)

    led2 = paper.load(str(tmp_path / "none.json"))
    paper.place(led2, bet, NOW)
    void = {"L": {"status": "finalized", "result": ""}, "H": {"status": "finalized", "result": ""}}
    [done] = paper.settle(led2, void.get, NOW)
    assert abs(done["pnl"]) < 0.02  # refunded


def test_mark_values_legs_at_current_bids(tmp_path):
    led = paper.load(str(tmp_path / "none.json"))
    books = {"L": {"yes": [], "no": [(0.70, 1000)]}, "H": {"yes": [(0.60, 1000)], "no": []}}
    [bet], _ = evaluate.choose([_ladder_rel()], books, 1000, 1000, now=NOW)
    paper.place(led, bet, NOW)
    n = bet["sets"]
    acct = paper.mark(led, {"L": {"yes_bid_dollars": "0.25"}, "H": {"no_bid_dollars": "0.35"}}, NOW)
    assert acct["positions_value"] == round(n * 0.60, 2)
    assert acct["account_value"] == round(led["cash"] + n * 0.60, 2)
    assert acct["settlement_value"] == round(led["cash"] + n * 1, 2)
    # a market missing from the listing keeps its last mark
    acct = paper.mark(led, {"L": {"yes_bid_dollars": "0.30"}}, NOW)
    assert acct["positions_value"] == round(n * 0.65, 2)
