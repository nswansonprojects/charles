"""Prediction log (predictions.py), strategy levels (gates.py), and how the betting book obeys them."""
import json
from datetime import datetime, timedelta, timezone

import betting
import gates
import kalshi_api
import predictions

NOW = datetime(2026, 10, 15, 15, 0, tzinfo=timezone.utc)
SINCE = "2026-10-01T00:00:00+00:00"


def mkt(t, yb=0.40, ya=0.42, close="2026-10-16T00:00:00Z"):
    return {"ticker": t, "yes_bid_dollars": str(yb), "yes_ask_dollars": str(ya), "close_time": close,
            "no_bid_dollars": str(round(1 - ya, 2)), "no_ask_dollars": str(round(1 - yb, 2))}


def pick(t, fair=0.60, model=0.80, strategy="weather", event="E"):
    return {"strategy": strategy, "ticker": t, "event": event, "title": "t", "sub": "s", "source": "src",
            "fair_yes": fair, "model_yes": model}


# ---------------------------------------------------------------- predictions.py
def test_record_keeps_the_first_snapshot_and_the_first_signal():
    book = {"open": {}}
    sig = {"A": {"side": "yes", "ask": 0.42, "cost": 0.4363, "edge": 0.16, "fair": 0.60}}
    assert predictions.record(book, [pick("A")], sig, {"A": "K"}, {"A": mkt("A")}, NOW) == 1
    later = NOW + timedelta(minutes=15)
    sig2 = {"A": {"side": "no", "ask": 0.30, "cost": 0.31, "edge": 0.1, "fair": 0.4}}
    assert predictions.record(book, [pick("A", fair=0.70)], sig2, {}, {"A": mkt("A", 0.5, 0.52)}, later) == 0
    r = book["open"]["A"]
    assert r["first"]["fair_yes"] == 0.60 and r["first"]["market_yes"] == 0.41 and r["key"] == "K"
    assert r["last"]["fair_yes"] == 0.70 and r["last"]["market_yes"] == 0.51
    assert r["signal"]["side"] == "yes"  # the first would-be bet is the one graded


def test_record_skips_one_sided_markets():
    book = {"open": {}}
    predictions.record(book, [pick("A")], {}, {}, {"A": mkt("A", 0.0, 0.05)}, NOW)
    assert book["open"] == {}


def test_grade_moves_settled_markets_and_scores_the_shadow_bet(tmp_path):
    book = {"open": {}}
    sig = {"A": {"side": "yes", "ask": 0.42, "cost": 0.44, "edge": 0.16, "fair": 0.60}}
    predictions.record(book, [pick("A"), pick("B")], sig, {}, {"A": mkt("A"), "B": mkt("B")}, NOW)
    after = NOW + timedelta(days=1)  # both closed
    asked = []

    def lookup(ts):
        asked.extend(ts)
        return [{"ticker": "A", "status": "finalized", "result": "yes"},
                {"ticker": "B", "status": "closed", "result": ""}]  # B not settled yet: kept
    out = predictions.grade(book, {}, lookup, after)
    assert sorted(asked) == ["A", "B"]
    assert [g["ticker"] for g in out] == ["A"] and out[0]["y"] == 1.0 and out[0]["signal"]["pnl"] == 0.56
    assert list(book["open"]) == ["B"]
    assert [g["ticker"] for g in predictions.load_graded()] == ["A"]


def test_grade_drops_voided_and_long_unsettled_markets():
    book = {"open": {}}
    predictions.record(book, [pick("V"), pick("S")], {}, {}, {"V": mkt("V"), "S": mkt("S")}, NOW)
    lookup = lambda ts: [{"ticker": "V", "status": "settled", "result": "void"}]
    assert predictions.grade(book, {}, lookup, NOW + timedelta(days=1)) == []
    assert list(book["open"]) == ["S"]                         # S: lookup missed it, still recent
    predictions.grade(book, {}, lambda ts: [], NOW + timedelta(days=20))
    assert book["open"] == {}                                  # given up after GIVE_UP_DAYS


def test_grade_leaves_markets_that_are_still_open():
    book = {"open": {}}
    predictions.record(book, [pick("A")], {}, {}, {"A": mkt("A")}, NOW)
    called = []
    assert predictions.grade(book, {"A": mkt("A")}, lambda ts: called.append(ts) or [], NOW) == []
    assert not called and "A" in book["open"]


def row(i, y, fair, market, strategy="weather", signal=None, ts="2026-10-05T00:00:00+00:00", key=None):
    return {"strategy": strategy, "ticker": f"T{i}", "key": key or f"K{i // 3}", "y": y,
            "first": {"ts": ts, "model_yes": fair, "fair_yes": fair, "market_yes": market}, "signal": signal}


def good_rows(n=60, strategy="weather", ts="2026-10-05T00:00:00+00:00"):
    """A model that beats the market: it says 0.8 / 0.2 where the market says 0.6 / 0.4, and is right.
    Half the markets have a winning shadow bet (YES at 55c on YES outcomes, NO at 45c on NO ones)."""
    out = []
    for i in range(n):
        y = float(i % 2 == 0)
        sig = None
        if i % 4 == 0:
            sig = {"side": "yes", "ask": 0.55, "cost": 0.57, "pnl": 0.43}
        elif i % 4 == 1:
            sig = {"side": "no", "ask": 0.45, "cost": 0.47, "pnl": 0.53}
        out.append(row(i, y, 0.8 if y else 0.2, 0.6 if y else 0.4, strategy, sig, ts))
    return out


def test_scorecard_measures_model_against_market():
    c = predictions.scorecard(good_rows(), "weather")
    assert c["graded"] == 60 and c["events"] == 20
    assert c["brier_blend"] == 0.04 and c["brier_market"] == 0.16 and c["beats_market_by"] == 0.12
    assert c["signals"] == 30 and c["shadow_roi_pct"] > 0
    assert predictions.scorecard(good_rows(), "weather", since="2026-10-06")["graded"] == 0
    assert predictions.scorecard(good_rows(), "weather", last=10)["graded"] == 10
    assert predictions.scorecard(good_rows(), "sports")["graded"] == 0


# ---------------------------------------------------------------- gates.py
def test_everyone_starts_at_predict():
    lv, moves = gates.update(betting.STRATEGIES, {"closed": []}, [], {}, 1000, NOW)
    assert set(lv.values()) == {"predict"} and moves == []
    assert json.load(open(gates.STATE))["weather"]["since"] == NOW.isoformat(timespec="seconds")


def _state(level, since=SINCE):
    gates.save({s: {"level": level if s == "weather" else "predict", "since": since, "history": []}
                for s in betting.STRATEGIES})


def test_promotes_to_small_only_when_the_model_beats_the_market_and_shadow_bets_paid():
    _state("predict")
    rows = good_rows()
    for i in range(60, 80):  # enough shadow bets: 20 more winning YES signals
        rows.append(row(i, 1.0, 0.8, 0.6, signal={"side": "yes", "ask": 0.55, "cost": 0.57, "pnl": 0.43}))
    lv, moves = gates.update(betting.STRATEGIES, {"closed": []}, rows, {"weather": 0.10}, 1000, NOW)
    assert lv["weather"] == "small" and moves[0]["strategy"] == "weather"
    st = json.load(open(gates.STATE))["weather"]
    assert st["since"] == NOW.isoformat(timespec="seconds") and st["history"][0]["to"] == "small"
    # the evidence count restarted: the same rows don't promote it again
    lv, moves = gates.update(betting.STRATEGIES, {"closed": []}, rows, {"weather": 0.10}, 1000, NOW)
    assert lv["weather"] == "small" and moves == []


def test_no_promotion_without_enough_data_or_when_behind_the_market():
    _state("predict")
    few = good_rows(20)
    assert gates.update(betting.STRATEGIES, {"closed": []}, few, {}, 1000, NOW)[0]["weather"] == "predict"
    bad = [dict(r, first={**r["first"], "fair_yes": 1 - r["first"]["fair_yes"]}) for r in good_rows(100)]
    assert gates.update(betting.STRATEGIES, {"closed": []}, bad, {}, 1000, NOW)[0]["weather"] == "predict"
    ok, why = gates.proven(predictions.scorecard(bad, "weather"))
    assert not ok and "not beating the market" in why


def test_losing_shadow_bets_block_promotion_even_with_a_better_brier():
    rows = [dict(r, signal={**r["signal"], "pnl": -0.57}) if r["signal"] else r for r in good_rows(100)]
    ok, why = gates.proven(predictions.scorecard(rows, "weather"))
    assert not ok and "shadow bets lost" in why


def test_demotes_when_recent_predictions_fall_behind_the_market():
    _state("small", since="2026-10-10T00:00:00+00:00")
    bad = [dict(r, first={**r["first"], "fair_yes": 1 - r["first"]["fair_yes"]})
           for r in good_rows(40, ts="2026-10-11T00:00:00+00:00")]
    lv, moves = gates.update(betting.STRATEGIES, {"closed": []}, bad, {"weather": 0.1}, 1000, NOW)
    assert lv["weather"] == "predict" and "stopped beating the market" in moves[0]["why"]


def test_demotes_on_losses_since_promotion():
    _state("small", since="2026-10-10T00:00:00+00:00")
    lost = [{"strategy": "weather", "opened": "2026-10-11T00:00:00+00:00", "pnl": -5.0} for _ in range(5)]
    old = [{"strategy": "weather", "opened": "2026-10-01T00:00:00+00:00", "pnl": -500.0}]  # before: ignored
    lv, _ = gates.update(betting.STRATEGIES, {"closed": lost[:4] + old}, [], {"weather": 0.1}, 1000, NOW)
    assert lv["weather"] == "small"  # -$20 is under the $25 floor
    lv, moves = gates.update(betting.STRATEGIES, {"closed": lost}, [], {"weather": 0.1}, 1000, NOW)
    assert lv["weather"] == "predict" and "lost $25.00" in moves[0]["why"]


def test_small_to_normal_needs_30_winning_real_bets():
    _state("small", since="2026-10-02T00:00:00+00:00")
    rows = good_rows(80, ts="2026-10-05T00:00:00+00:00")
    for i in range(80, 100):
        rows.append(row(i, 1.0, 0.8, 0.6, signal={"side": "yes", "ask": 0.55, "cost": 0.57, "pnl": 0.43},
                        ts="2026-10-05T00:00:00+00:00"))
    bets = [{"strategy": "weather", "opened": "2026-10-03T00:00:00+00:00", "pnl": 1.0} for _ in range(29)]
    assert gates.update(betting.STRATEGIES, {"closed": bets}, rows, {"weather": 0.1}, 1000, NOW)[0]["weather"] == "small"
    bets.append(dict(bets[0]))
    assert gates.update(betting.STRATEGIES, {"closed": bets}, rows, {"weather": 0.1}, 1000, NOW)[0]["weather"] == "normal"


def test_cap_dollars_by_level():
    assert gates.cap_dollars("predict", 0.3, 1000) == 0
    assert gates.cap_dollars("small", 0.3, 1000) == 50
    assert gates.cap_dollars("small", 0.0, 1000) == 0      # owner's pause still wins
    assert gates.cap_dollars("normal", 0.3, 1000) == 300


def test_progress_reports_level_and_data_collected():
    p = gates.progress(betting.STRATEGIES, good_rows(25, ts="2026-10-16T00:00:00+00:00"), NOW)
    assert p["weather"]["level"] == "predict" and 0 < p["weather"]["data_progress"] < 1
    assert "needs more data" in p["weather"]["why"]


# ---------------------------------------------------------------- the betting book obeys the levels
PICK = {"strategy": "sports", "ticker": "X", "event": "E", "category": "c", "title": "t", "sub": "s",
        "source": "src", "fair_yes": 0.30}


def _run(picks, markets, level):
    if level:
        gates.save({s: {"level": level, "since": SINCE, "history": []} for s in betting.STRATEGIES})
    betting.gather = lambda ev, now: (picks, {})
    book = lambda t: {"yes": [(round(1 - float(markets[t]["no_ask_dollars"]), 2), 500)],
                      "no": [(round(1 - float(markets[t]["yes_ask_dollars"]), 2), 500)]}
    summary = betting.run([], markets, NOW, get_book=book, get_market=lambda t: {}, get_markets=lambda ts: [])
    return summary, betting.load()


def _picks():
    return [dict(PICK, ticker=f"G{i}", event=f"E{i}", fair_yes=0.70) for i in range(3)]


def test_predict_level_bets_nothing_but_logs_every_prediction(monkeypatch, tmp_path):
    monkeypatch.setattr(betting, "TUNING", str(tmp_path / "t.json"))
    monkeypatch.setattr(betting, "gather", betting.gather)
    markets = {p["ticker"]: mkt(p["ticker"], 0.38, 0.40) for p in _picks()}
    summary, led = _run(_picks(), markets, None)  # fresh install: everyone at predict
    assert led["open"] == [] and set(summary["levels"].values()) == {"predict"}
    logged = predictions.load()["open"]
    assert sorted(logged) == ["G0", "G1", "G2"] and all(r["signal"]["side"] == "yes" for r in logged.values())


def test_small_level_bets_at_most_five_dollars(monkeypatch, tmp_path):
    monkeypatch.setattr(betting, "TUNING", str(tmp_path / "t.json"))
    monkeypatch.setattr(betting, "gather", betting.gather)
    markets = {p["ticker"]: mkt(p["ticker"], 0.38, 0.40) for p in _picks()}
    _, led = _run(_picks(), markets, "small")
    assert led["open"] and all(b["cost"] <= 5.0 and b["level"] == "small" for b in led["open"])
    _, led2 = _run([dict(p, ticker=p["ticker"] + "n", event=p["event"] + "n") for p in _picks()],
                   {k + "n": dict(v, ticker=k + "n") for k, v in markets.items()}, "normal")
    assert max(b["cost"] for b in led2["open"] if b["level"] == "normal") > 5.0


# ---------------------------------------------------------------- exits
def _bet(n=20, cost=8.4, side="yes"):
    return {"strategy": "sports", "ticker": "X", "side": side, "contracts": n, "cost": cost, "fee": 0.4,
            "expected_profit": 2.0, "id": "b1", "avg_price": 0.40}


def test_exit_sells_what_the_bid_can_take_and_keeps_the_rest():
    bet = _bet()
    led = {"cash": 0.0, "open": [bet], "closed": []}
    m = {"yes_bid_dollars": "0.70", "yes_bid_size_fp": "10"}  # room for 5 of 20 (fill_share 0.5)
    sold = betting.exits(led, {"X": 0.60}, {"X": m}, NOW)
    assert len(sold) == 1 and sold[0]["contracts"] == 5 and sold[0]["how"] == "sold"
    assert sold[0]["cost"] == 2.1 and led["open"][0]["contracts"] == 15 and led["open"][0]["cost"] == 6.3
    assert led["cash"] == round(5 * 0.70 - betting.fee(5, 0.70), 2)


def test_exit_holds_while_the_bid_is_below_fair_value():
    led = {"cash": 0.0, "open": [_bet()], "closed": []}
    m = {"yes_bid_dollars": "0.60", "yes_bid_size_fp": "100"}
    assert betting.exits(led, {"X": 0.65}, {"X": m}, NOW) == [] and led["open"][0]["contracts"] == 20


# ---------------------------------------------------------------- kalshi_api
def test_markets_lookup_batches_100_tickers_per_request(monkeypatch):
    calls = []
    monkeypatch.setattr(kalshi_api, "_get", lambda path, params: calls.append(params) or
                        {"markets": [{"ticker": t} for t in params["tickers"].split(",")]})
    got = kalshi_api.markets([f"T{i}" for i in range(250)])
    assert len(got) == 250 and [c["limit"] for c in calls] == [100, 100, 50]
