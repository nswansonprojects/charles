from datetime import date, datetime, timedelta, timezone

import betting
import strategies as S

NOW = datetime(2026, 10, 10, 0, 30, tzinfo=timezone.utc)  # Fri Oct 9 evening in the US


def wmkt(t, st, fl, cap, sub, yb=.30, ya=.32):
    return {"ticker": t, "strike_type": st, "floor_strike": fl, "cap_strike": cap, "yes_sub_title": sub,
            "yes_bid_dollars": str(yb), "yes_ask_dollars": str(ya), "no_bid_dollars": str(round(1 - ya, 2)),
            "no_ask_dollars": str(round(1 - yb, 2)),
            "rules_primary": "If the maximum temperature recorded at New York City (CLINYC) for Oct 10, 2026, is ..."}


# ---------------------------------------------------------------- weather
def test_temp_range_reads_kalshi_strikes_and_checks_the_label():
    assert S.temp_range(wmkt("a", "less", None, 66, "65° or below")) == (None, 65)
    assert S.temp_range(wmkt("b", "between", 66, 67, "66° to 67°")) == (66, 67)
    assert S.temp_range(wmkt("c", "greater", 73, None, "74° or above")) == (74, None)
    # label disagrees with the strike fields (the "exactly N" trap): skip, never guess
    assert S.temp_range(wmkt("d", "less", None, 66, "66° or below")) is None


def test_day_values_use_the_local_standard_time_climate_day():
    # NYC standard time is UTC-5: Oct 10 climate day = 05:00Z Oct 10 .. 04:00Z Oct 11
    start = datetime(2026, 10, 10, 0, tzinfo=timezone.utc)
    times = [(start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(48)]
    temps = [50.0] * 48
    temps[4] = 90.0    # 04:00Z Oct 10 = still Oct 9 locally: must be ignored
    temps[20] = 70.0   # inside the day
    temps[29] = 80.0   # 05:00Z Oct 11 = Oct 11 locally: ignored
    vals = S.day_values({"time": times, "members": [temps]}, date(2026, 10, 10), -5, "max")
    assert vals == [70.0]
    assert S.day_values({"time": times[:20], "members": [temps[:20]]}, date(2026, 10, 10), -5, "max") == []


def test_prob_in_range_covers_rounding_and_sums_to_one():
    vals = [67.0, 68.0, 69.0]
    buckets = [(None, 65), (66, 67), (68, 69), (70, 71), (72, None)]
    ps = [S.prob_in_range(vals, lo, hi, 1.6) for lo, hi in buckets]
    assert abs(sum(ps) - 1) < 1e-9
    assert max(ps) == ps[2]


def test_nws_center_takes_the_day_high_and_the_night_before_low():
    periods = [{"start": "2026-10-09T18:00:00-04:00", "day": False, "temp": 56},
               {"start": "2026-10-10T06:00:00-04:00", "day": True, "temp": 70},
               {"start": "2026-10-10T18:00:00-04:00", "day": False, "temp": 58}]
    assert S.nws_center(periods, date(2026, 10, 10), "max") == 70
    assert S.nws_center(periods, date(2026, 10, 10), "min") == 56


def test_weather_skips_today_and_centers_on_nws():
    times = [(datetime(2026, 10, 10, tzinfo=timezone.utc) + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M")
             for h in range(72)]
    ens = {"CLINYC": {"time": times, "members": [[60.0 + (i % 3)] * 72 for i in range(30)]}}  # raw mean 61
    nws = {"CLINYC": [{"start": "2026-10-10T06:00:00-04:00", "day": True, "temp": 70}]}
    ev = {"event_ticker": "KXHIGHNY-26OCT10", "series_ticker": "KXHIGHNY", "title": "t",
          "markets": [wmkt("B", "between", 70, 71, "70° to 71°")]}
    early = NOW - timedelta(hours=12)                          # 16.5h before the Oct 10 climate day
    picks = S.weather_picks([ev], ens, nws, early)
    assert len(picks) == 1 and picks[0]["fair_yes"] > 0.4      # centered on 70, not the raw 61
    late = NOW + timedelta(hours=10)                           # Oct 10 locally: same-day, skipped
    assert S.weather_picks([ev], ens, nws, late) == []
    # 2026-10-15: highs also need 12h before the climate day (NOW is 4.5h before: 2 of 16 such bets won)
    assert S.weather_picks([ev], ens, nws, NOW) == []


def test_weather_center_blends_nws_with_the_models():
    assert S.weather_center(70, []) == 70                      # no models loaded: NWS alone
    assert S.weather_center(70, [64.0, 66.0]) == 67.5          # half NWS, half the models' mean
    times = [(datetime(2026, 10, 10, tzinfo=timezone.utc) + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M")
             for h in range(72)]
    ens = {"CLINYC": {"time": times, "members": [[60.0 + (i % 3)] * 72 for i in range(30)]}}
    nws = {"CLINYC": [{"start": "2026-10-10T06:00:00-04:00", "day": True, "temp": 70}]}
    models = {"CLINYC": {"time": times, "members": [[66.0] * 72, [66.0] * 72]}}
    ev = {"event_ticker": "KXHIGHNY-26OCT10", "series_ticker": "KXHIGHNY", "title": "t",
          "markets": [wmkt("B", "between", 68, 69, "68° to 69°")]}
    early = NOW - timedelta(hours=12)
    alone = S.weather_picks([ev], ens, nws, early)[0]
    blended = S.weather_picks([ev], ens, nws, early, models)[0]
    assert blended["fair_yes"] > alone["fair_yes"]             # center 68 instead of 70
    assert "2 models" in blended["source"]


# ---------------------------------------------------------------- sports
def team(loc, name, ml, home):
    return {"location": loc, "displayName": f"{loc} {name}", "shortDisplayName": name, "name": name,
            "abbreviation": loc[:3].upper(), "ml": ml, "home": home}


GAME = {"id": "1", "start": "2026-10-19T00:20Z", "state": "pre", "book": "DraftKings", "name": "x",
        "teams": [team("San Francisco", "49ers", "-400", True), team("Washington", "Commanders", "+310", False)]}
SEV = {"event_ticker": "KXNFLGAME-26OCT18WASSF", "series_ticker": "KXNFLGAME", "title": "WAS vs SF",
       "markets": [{"ticker": "SF", "yes_sub_title": "San Francisco"}, {"ticker": "WAS", "yes_sub_title": "Washington"}]}


def test_american_odds():
    assert S.american_to_prob("-400") == 0.8
    assert S.american_to_prob("+300") == 0.25
    assert S.american_to_prob("EVEN") == 0.5
    assert S.american_to_prob("OFF") is None


def test_sports_removes_the_vig_and_matches_teams():
    picks = {p["ticker"]: p["fair_yes"] for p in S.sports_picks([SEV], {"football/nfl": [GAME]}, NOW)}
    assert abs(picks["SF"] + picks["WAS"] - 1) < 0.001
    assert 0.76 < picks["SF"] < 0.775  # 0.80 / (0.80 + 0.244)


def test_sports_skips_started_ambiguous_or_wrong_day_games():
    live = {**GAME, "state": "in"}
    assert S.sports_picks([SEV], {"football/nfl": [live]}, NOW) == []
    other_day = {**GAME, "id": "2", "start": "2026-10-26T00:20Z"}
    assert S.sports_picks([SEV], {"football/nfl": [other_day]}, NOW) == []
    preseason = {**GAME, "season_type": 1}
    assert S.sports_picks([SEV], {"football/nfl": [preseason]}, NOW) == []
    twin = {**GAME, "id": "3"}
    assert S.sports_picks([SEV], {"football/nfl": [GAME, twin]}, NOW) == []
    st = {"location": "Delaware State", "displayName": "Delaware State Hornets", "shortDisplayName": "Del State",
          "name": "Hornets", "abbreviation": "DSU"}
    assert S.team_matches("Delaware St.", st)
    assert S.team_matches("Los Angeles D", {"displayName": "Los Angeles Dodgers", "location": "Los Angeles"})


# ---------------------------------------------------------------- economics
def nowcast(period, value, actual=""):
    return {"chart": {"subcaption": period},
            "dataset": [{"seriesname": "CPI Inflation", "data": [{"value": "0.1"}, {"value": str(value)}]},
                        {"seriesname": "Actual CPI Inflation", "data": [{"value": actual}]}]}


def test_nowcast_value_stops_once_the_number_is_out():
    assert S.nowcast_value([nowcast("2026-9", 0.31)], "2026-9", "CPI Inflation") == 0.31
    assert S.nowcast_value([nowcast("2026-9", 0.31, "0.4")], "2026-9", "CPI Inflation") is None


def test_econ_counts_one_decimal_rounding():
    ev = {"event_ticker": "KXCPI-26SEP", "series_ticker": "KXCPI", "title": "CPI",
          "markets": [{"ticker": "T0.2", "strike_type": "greater", "floor_strike": 0.2, "yes_sub_title": "Above 0.2%"}]}
    # nowcast exactly 0.25 = the rounding line between 0.2 and 0.3: a coin flip
    p = S.econ_picks([ev], {"month": [nowcast("2026-9", 0.25)]})[0]["fair_yes"]
    assert abs(p - 0.5) < 0.01


# ---------------------------------------------------------------- longshot
def lmkt(yb, ya, vol=500, days=5):
    return {"ticker": "L", "yes_bid_dollars": str(yb), "yes_ask_dollars": str(ya), "volume_24h_fp": str(vol),
            "close_time": (NOW + timedelta(days=days)).isoformat()}


def test_longshot_filters():
    ev = lambda m, s="KXFOO": {"event_ticker": "E", "series_ticker": s, "title": "t", "markets": [m]}
    assert len(S.longshot_picks([ev(lmkt(.05, .06))], NOW)) == 1
    assert S.longshot_picks([ev(lmkt(.15, .16))], NOW) == []           # not a longshot
    assert S.longshot_picks([ev(lmkt(.02, .08))], NOW) == []           # spread too wide
    assert S.longshot_picks([ev(lmkt(.05, .06, vol=0))], NOW) == []    # dead market
    assert S.longshot_picks([ev(lmkt(.05, .06, days=90))], NOW) == []  # money locked too long
    assert S.longshot_picks([ev(lmkt(.05, .06), "KXHIGHNY")], NOW) == []  # has its own model


# ---------------------------------------------------------------- engine
def book_for_no(ask, qty=500):
    """An order book whose best NO offer is `ask` (a YES bid at 1-ask)."""
    return {"yes": [(round(1 - ask, 2), qty)], "no": []}


PICK = {"strategy": "sports", "ticker": "X", "event": "E", "category": "c", "title": "t", "sub": "s",
        "source": "src", "fair_yes": 0.30}


def test_fee_rounds_up_per_order():
    assert betting.fee(1, 0.5) == 0.02
    assert betting.fee(100, 0.5) == 1.75


def test_size_respects_edge_budget_and_kelly(monkeypatch):
    monkeypatch.setitem(betting.RULES, "min_win_ratio", 0.0)  # sizing alone; the payout bar has its own test
    f = betting.size(PICK, "no", book_for_no(0.60), budget=30, bank=1000)
    assert f and f["side"] == "no" and f["cost"] <= 30
    assert f["edge"] >= betting.RULES["min_edge"]["sports"]
    assert betting.size(PICK, "no", book_for_no(0.68), budget=30, bank=1000) is None  # 2c edge: too thin
    small = betting.size(PICK, "no", book_for_no(0.60), budget=1000, bank=100)
    assert small["cost"] < 15  # quarter Kelly of a $100 bank


def test_settle_won_lost_void():
    led = {"cash": 0.0, "open": [], "closed": []}
    for t, side in (("W", "yes"), ("L", "no"), ("V", "yes")):
        led["open"].append({"ticker": t, "side": side, "contracts": 10, "cost": 4.0, "strategy": "sports"})
    res = {"W": "yes", "L": "yes", "V": ""}
    betting.settle(led, lambda t: {"status": "finalized", "result": res[t]}, NOW)
    by = {b["ticker"]: b for b in led["closed"]}
    assert by["W"]["pnl"] == 6.0 and by["L"]["pnl"] == -4.0 and by["V"]["pnl"] == 0.0
    assert led["cash"] == 14.0 and not led["open"]


def test_exit_sells_when_bid_passes_fair_but_never_longshots():
    mk = {"X": {"yes_bid_dollars": "0.70", "yes_bid_size_fp": "100"}}
    led = {"cash": 0.0, "closed": [], "open": [
        {"ticker": "X", "side": "yes", "contracts": 10, "cost": 5.0, "strategy": "sports"}]}
    assert betting.exits(led, {"X": 0.60}, mk, NOW) and led["closed"][0]["how"] == "sold"
    led = {"cash": 0.0, "closed": [], "open": [
        {"ticker": "X", "side": "yes", "contracts": 10, "cost": 5.0, "strategy": "sports"}]}
    assert betting.exits(led, {"X": 0.75}, mk, NOW) == []  # market hasn't caught up: hold
    led["open"][0]["strategy"] = "longshot"
    assert betting.exits(led, {"X": 0.10}, mk, NOW) == []


def test_blend_keeps_the_raw_model_and_weights_the_market():
    m = {"X": {"yes_bid_dollars": "0.48", "yes_ask_dollars": "0.52"}}
    p = betting.blend([{**PICK, "strategy": "weather", "fair_yes": 0.10}], m)[0]
    assert p["model_yes"] == 0.10 and abs(p["fair_yes"] - 0.30) < 1e-9


def test_run_writes_only_where_ledger_points(tmp_path, monkeypatch):
    for k, f in (("TUNING", "tuning.json"), ("LEDGER", "b.json"), ("EQUITY", "e.jsonl"), ("LATEST", "l.json")):
        monkeypatch.setattr(betting, k, str(tmp_path / f))
    monkeypatch.setattr(betting, "gather", lambda ev, now: ([], {}))
    betting.run([], {}, NOW, get_book=lambda t: {}, get_market=lambda t: {})
    assert (tmp_path / "b.json").exists()


def test_weather_skips_lows_that_are_only_hours_away():
    times = [(datetime(2026, 10, 10, tzinfo=timezone.utc) + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M")
             for h in range(96)]
    ens = {"CLINYC": {"time": times, "members": [[50.0 + (i % 3)] * 96 for i in range(30)]}}
    nws = {"CLINYC": [{"start": "2026-10-09T18:00:00-04:00", "day": False, "temp": 50},
                      {"start": "2026-10-10T18:00:00-04:00", "day": False, "temp": 52}]}
    mk = lambda d: {"event_ticker": f"KXLOWTNYC-26OCT{d}", "series_ticker": "KXLOWTNYC", "title": "t",
                    "markets": [wmkt("B", "between", 50, 51, "50° to 51°")]}
    # NOW = 8:30 PM ET Oct 9: Oct 10's climate day starts in 4.5 h -> skip; Oct 11's in 28.5 h -> fine
    assert S.weather_picks([mk(10)], ens, nws, NOW) == []
    assert len(S.weather_picks([mk(11)], ens, nws, NOW)) == 1


# ---------------------------------------------------------------- tuning.json
def test_apply_tuning_clamps_ignores_junk_and_falls_back(tmp_path):
    import json
    p = tmp_path / "tuning.json"
    p.write_text(json.dumps({"settings": {"trust_model": {"weather": 0.25, "nope": 0.9},
                                          "min_edge": {"econ": 5}, "kelly_fraction": "x",
                                          "cash_reserve_pct": 0.0}}))
    try:
        r = betting.apply_tuning(str(p))
        assert r["trust_model"]["weather"] == 0.25 and "nope" not in r["trust_model"]
        assert r["min_edge"]["econ"] == betting.LIMITS["min_edge"]["max"]       # clamped
        assert r["kelly_fraction"] == betting.DEFAULT_RULES["kelly_fraction"]  # bad value ignored
        assert r["cash_reserve_pct"] == betting.DEFAULT_RULES["cash_reserve_pct"]  # not tunable
        p.write_text("{broken")
        assert betting.apply_tuning(str(p)) == betting.DEFAULT_RULES
    finally:
        betting.apply_tuning(str(tmp_path / "missing.json"))


def test_shipped_tuning_file_is_valid_and_inside_limits():
    import json
    with open(betting.TUNING) as f:
        settings = json.load(f)["settings"]
    for key, val in settings.items():
        lim = betting.LIMITS[key]
        for v in (val.values() if isinstance(val, dict) else [val]):
            assert lim["min"] <= v <= lim["max"], key


def test_no_bets_that_win_too_little_for_their_stake():
    # owner's bar (2026-10-15): a win pays at least the stake, so nothing above ~49c
    assert betting.pays_enough(0.30) and betting.pays_enough(0.48) and not betting.pays_enough(0.55)
    assert not betting.pays_enough(0.91)
    sure = dict(PICK, fair_yes=0.01)  # model says NO is ~99% likely, but NO at 91c wins too little
    assert betting.size(sure, "no", book_for_no(0.91), budget=30, bank=1000) is None
    assert betting.quick_edge(sure, {"no_ask_dollars": "0.91", "yes_ask_dollars": "0.10"})[0] == "yes"  # NO never offered
    assert betting.size(PICK, "no", book_for_no(0.45), budget=30, bank=1000)  # good edge, ~1.2x payout: allowed
    pricey = dict(PICK, fair_yes=0.15)  # NO worth 85c, offered at 60c: good edge, but a win pays only 0.67x
    assert betting.size(pricey, "no", book_for_no(0.60), budget=30, bank=1000) is None
    cheap = dict(PICK, fair_yes=0.70)  # NO worth 30c, offered at 20c
    f = betting.size(cheap, "no", book_for_no(0.20), budget=30, bank=1000)
    assert f and f["contracts"] - f["cost"] >= betting.RULES["min_win_ratio"] * f["cost"]


# ---------------------------------------------------------------- correlated exposure (2026-10-14)
def test_exposure_key_groups_one_cpi_release():
    k = lambda ev, s="econ": betting.exposure_key({"strategy": s, "event": ev})
    assert k("KXCPI-26OCT") == k("KXCPICORE-26OCT") == k("KXCPIYOY-26OCT") == "CPI-26OCT"
    assert k("KXCPI-26OCT") != k("KXCPI-26NOV")
    assert k("KXNBAGAME-26OCT13GSWLAL", "sports") == "KXNBAGAME-26OCT13GSWLAL"


def test_book_walk_stops_near_the_best_offer():
    rich = dict(PICK, fair_yes=0.0)                     # NO worth $1: every level has edge
    book = {"yes": [(0.78, 10), (0.55, 500)], "no": []}  # NO offers: 10 at 22c, then 500 at 45c
    f = betting.size(rich, "no", book, budget=100, bank=10000)
    assert f["contracts"] == 5 and f["avg_price"] == 0.22  # half the 22c offer (fill_share), none at 45c


def _run_with(tmp_path, monkeypatch, picks, markets, ledger=None, level="normal"):
    import json
    import gates
    for k, f in (("TUNING", "tuning.json"), ("LEDGER", "b.json"), ("EQUITY", "e.jsonl"), ("LATEST", "l.json")):
        monkeypatch.setattr(betting, k, str(tmp_path / f))
    if ledger:
        (tmp_path / "b.json").write_text(json.dumps(ledger))
    if level:  # sizing tests: strategies already promoted (the gates have their own tests)
        gates.save({s: {"level": level, "since": "2026-10-01T00:00:00+00:00", "history": []}
                    for s in betting.STRATEGIES})
    monkeypatch.setattr(betting, "gather", lambda ev, now: (picks, {}))
    book = lambda t: {"yes": [(round(1 - float(markets[t]["no_ask_dollars"]), 2), 500)],
                      "no": [(round(1 - float(markets[t]["yes_ask_dollars"]), 2), 500)]}
    betting.run([], markets, NOW, get_book=book, get_market=lambda t: {}, get_markets=lambda ts: [])
    return betting.load()


def _mkt(ya, na):
    return {"yes_bid_dollars": str(round(1 - na, 2)), "yes_ask_dollars": str(ya),
            "no_bid_dollars": str(round(1 - ya, 2)), "no_ask_dollars": str(na), "close_time": ""}


def test_one_bet_per_game(tmp_path, monkeypatch):
    # Kalshi lists both teams: NO on GSW and YES on LAL are the same bet
    ev = "KXNBAGAME-26OCT13GSWLAL"
    picks = [dict(PICK, ticker=f"{ev}-GSW", event=ev, fair_yes=0.30),
             dict(PICK, ticker=f"{ev}-LAL", event=ev, fair_yes=0.70)]
    led = _run_with(tmp_path, monkeypatch, picks, {f"{ev}-GSW": _mkt(0.47, 0.55), f"{ev}-LAL": _mkt(0.47, 0.55)})
    assert len(led["open"]) == 1


def test_one_cpi_release_shares_one_cap(tmp_path, monkeypatch):
    picks = [dict(PICK, strategy="econ", ticker=f"{ev}-T0.4", event=ev, fair_yes=0.95)
             for ev in ("KXCPI-26SEP", "KXCPICORE-26SEP", "KXCPIYOY-26SEP", "KXCPICOREYOY-26SEP")]
    markets = {p["ticker"]: _mkt(0.40, 0.62) for p in picks}
    led = _run_with(tmp_path, monkeypatch, picks, markets)
    assert led["open"]
    assert sum(b["cost"] for b in led["open"]) <= betting.START_CASH * betting.RULES["max_event_pct"] + 0.01
