from datetime import datetime, timedelta, timezone

import maker

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


def mkt(t, yb, ya, nb, na):
    return {"ticker": t, "yes_bid_dollars": str(yb), "yes_ask_dollars": str(ya),
            "no_bid_dollars": str(nb), "no_ask_dollars": str(na)}


REL = {"type": "strike_ladder", "event": "E", "category": "T", "title": "t", "why": "w", "payout": 1,
       "close_time": "2026-11-01T00:00:00Z",
       "legs": [{"ticker": "L", "side": "yes"}, {"ticker": "H", "side": "no"}]}
# YES(L) bid .55 ask .60 ; NO(H) bid .38 ask .44 -> our bids .56 + .39 = .95 (+ maker fees) < 1
BOOK = {"L": mkt("L", .55, .60, .40, .45), "H": mkt("H", .56, .62, .38, .44)}


def test_bid_improves_best_by_a_cent_but_never_crosses():
    assert maker.bid_price(mkt("X", .30, .35, .65, .70), "yes") == 0.31
    assert maker.bid_price(mkt("X", .30, .31, .69, .70), "yes") == 0.30   # 1c spread: join, don't cross
    assert maker.bid_price(mkt("X", .01, .02, .98, .99), "yes") is None   # penny leg


def test_plan_prices_set_at_our_bids_after_maker_fees():
    p = maker.plan_for(REL, BOOK, equity=1000, now=NOW)
    assert [l["price"] for l in p["legs"]] == [0.56, 0.39]
    assert 0.01 <= p["edge_per_set"] < 0.05
    assert p["held"] == round((0.56 + 0.39) * p["sets"], 2) and p["held"] <= 80


def test_no_plan_when_bids_dont_leave_an_edge():
    tight = {"L": mkt("L", .55, .56, .44, .45), "H": mkt("H", .45, .46, .54, .55)}
    assert maker.plan_for(REL, tight, 1000, NOW) is None


def test_yes_bid_fills_only_from_sellers_at_or_below_our_price():
    leg = {"ticker": "L", "side": "yes", "price": 0.56, "size": 10, "filled": 0.0, "fee": 0.0}
    trades = [
        {"taker_side": "yes", "yes_price_dollars": "0.50", "count_fp": "5"},   # a buyer: not us
        {"taker_side": "no", "yes_price_dollars": "0.57", "count_fp": "5"},    # seller above our bid
        {"taker_side": "no", "yes_price_dollars": "0.55", "count_fp": "4"},    # seller hit 0.55: would hit us
    ]
    assert maker.apply_trades(leg, trades) == 4
    assert leg["filled"] == 4 and leg["fee"] > 0


def test_no_bid_fills_from_yes_buyers_and_caps_at_size():
    leg = {"ticker": "H", "side": "no", "price": 0.39, "size": 3, "filled": 0.0, "fee": 0.0}
    trades = [{"taker_side": "yes", "no_price_dollars": "0.38", "count_fp": "50"}]
    assert maker.apply_trades(leg, trades) == 3 and leg["filled"] == 3


def test_reward_share_and_rate():
    prog = {"discount_factor_bps": 5000, "target_size_fp": "1000", "period_reward": 1000000,
            "start_date": "2026-10-09T00:00:00Z", "end_date": "2026-10-10T00:00:00Z"}
    assert abs(maker.reward_rate(prog) * 86400 - 100.0) < 1e-6  # $100/day
    leg = {"side": "yes", "price": 0.56, "size": 100, "filled": 0.0}
    book = {"yes": [(0.55, 100), (0.54, 400)], "no": []}
    # ours 100*1 ; theirs 100*.5 + 400*.25 = 150 -> 0.4
    assert abs(maker.reward_share(leg, book, prog) - 0.4) < 1e-9
    plan = {"legs": [dict(leg, ticker="L")], "last_check": (NOW - timedelta(hours=1)).isoformat(), "rewards": 0.0}
    got = maker.accrue_rewards(plan, {"L": book}, {"L": prog}, NOW)
    assert abs(got - 100 / 24 * 0.4 * maker.REWARD_SIDE_SHARE) < 1e-6


def _plan(filled_l, filled_h):
    p = maker.plan_for(REL, BOOK, 1000, NOW)
    p["legs"][0]["filled"], p["legs"][1]["filled"] = filled_l, filled_h
    return p


def test_expired_unfilled_plan_is_cancelled():
    assert maker.resolve_expired(_plan(0, 0), BOOK)["action"] == "cancel"


def test_expired_partial_completes_when_set_still_pays():
    p = _plan(0, 0)
    n = p["sets"]
    p["legs"][0]["filled"] = n                      # YES(L) all filled at .56
    cheap = {"L": BOOK["L"], "H": mkt("H", .60, .62, .38, .40)}   # NO(H) now offered at .40
    res = maker.resolve_expired(p, cheap)
    assert res["action"] == "complete" and res["pnl"] > 0
    maker.finish_with_buys(p, res["buys"])
    assert maker.complete(p) and p["legs"][1]["price"] == 0.40
    bet = maker.to_bet(p, NOW)
    assert bet["profit"] == round(n - bet["cost"], 2)


def test_expired_partial_unwinds_when_completion_would_lose():
    p = _plan(0, 0)
    p["legs"][0]["filled"] = p["sets"]
    moved = {"L": mkt("L", .40, .45, .55, .60), "H": mkt("H", .30, .35, .65, .70)}  # NO(H) now .70
    res = maker.resolve_expired(p, moved)
    assert res["action"] == "unwind" and res["pnl"] < 0   # sold YES(L) at .40 after buying at .56


def test_plan_ends_before_the_market_closes():
    soon = dict(REL, close_time=(NOW + timedelta(hours=10)).isoformat())
    p = maker.plan_for(soon, BOOK, 1000, NOW)
    assert maker.ends_at(p) == NOW + timedelta(hours=9)          # close minus the 1h buffer, not +24h
    assert maker.plan_for(dict(REL, close_time=(NOW + timedelta(hours=3)).isoformat()), BOOK, 1000, NOW) is None


def test_old_plan_past_its_close_ends_and_stops_earning():
    # saved before the cap: 24h expiry on a market that closed an hour ago (like KXAAAGASDIN-26OCT09)
    prog = {"discount_factor_bps": 5000, "target_size_fp": "1000", "period_reward": 1000000,
            "start_date": "2026-10-09T00:00:00Z", "end_date": "2026-10-10T00:00:00Z"}
    books = {"L": {"yes": [(0.55, 100)], "no": []}, "H": {"yes": [], "no": [(0.38, 100)]}}
    p = _plan(0, 0)
    p["close_time"] = "2026-10-09T11:00:00Z"              # buffer -> plan ended 10:00
    p["expires"] = (NOW + timedelta(hours=12)).isoformat()
    p["last_check"] = "2026-10-09T09:00:00+00:00"
    assert maker.ends_at(p) < NOW
    got = maker.accrue_rewards(p, books, {"L": prog, "H": prog}, NOW)
    open_market = dict(_plan(0, 0), last_check="2026-10-09T09:00:00+00:00")
    one_hour = maker.accrue_rewards(open_market, books, {"L": prog, "H": prog}, NOW - timedelta(hours=2))
    assert got > 0 and abs(got - one_hour) < 1e-9          # 09:00-10:00 only, not up to 12:00
