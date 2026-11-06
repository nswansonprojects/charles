import webdash as wd


def bet(s, pnl, how, fair, opened, closed):
    return {"strategy": s, "pnl": pnl, "how": how, "fair": fair, "opened": opened, "closed": closed}


CLOSED = [bet("weather", -10, "lost", 0.6, "2026-10-09T10:00", "2026-10-10T05:00"),
          bet("weather", 8, "won", 0.5, "2026-10-09T11:00", "2026-10-11T05:00"),
          bet("weather", 2, "sold", 0.7, "2026-10-10T11:00", "2026-10-10T15:00"),
          bet("sports", 5, "won", 0.55, "2026-10-10T12:00", "2026-10-11T03:00")]


def test_pnl_lines_accumulate_from_zero_in_close_order():
    lines = wd.pnl_lines(CLOSED)
    assert lines["weather"] == [["2026-10-09T10:00", 0.0], ["2026-10-10T05:00", -10], ["2026-10-10T15:00", -8],
                                ["2026-10-11T05:00", 0]]
    assert lines["sports"][-1] == ["2026-10-11T03:00", 5]


def test_calibration_uses_settled_bets_only():
    cal = {c["key"]: c for c in wd.calibration(CLOSED)}
    assert (cal["weather"]["n"], cal["weather"]["expected"], cal["weather"]["actual"]) == (2, 55.0, 50.0)
    assert "econ" not in cal


def test_levels_read_nothing_while_off_and_reread_only_a_changed_file(tmp_path, monkeypatch):
    # 2026-11-05: every rebuild read the whole ~60 MB graded file (~3 s, ~300 MB) for levels that are off during the
    # test, keeping kalshi-web at its 300 MB cap: the review's check took over 10 s and /kalshi/ gave a 502.
    import json
    from datetime import datetime, timezone
    import gates
    import predictions
    monkeypatch.setattr(wd, "DATA", str(tmp_path))
    monkeypatch.setitem(wd._LEVELS, "stamp", None)
    reads, real = [], predictions.load_graded
    monkeypatch.setattr(predictions, "load_graded", lambda *a, **k: reads.append(k) or real(*a, **k))
    now = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
    graded = tmp_path / "predictions_graded.jsonl"
    row = {"strategy": "weather", "key": "K", "y": 1.0, "title": "t", "signal": None,
           "first": {"ts": "2026-11-01T00:00:00+00:00", "model_yes": 0.7, "fair_yes": 0.7, "market_yes": 0.5}}
    graded.write_text(json.dumps(row) + "\n")
    (tmp_path / "levels.json").write_text(json.dumps({"weather": {"level": "predict", "since": "2026-10-01T00:00:00+00:00",
                                                                  "history": []}}))
    monkeypatch.setattr(gates, "ON", False)
    assert wd.levels_view(now) is None and reads == []
    monkeypatch.setattr(gates, "ON", True)
    first = wd.levels_view(now)
    assert first["weather"]["card"]["graded"] == 1 and reads == [{"slim_rows": True}]
    assert wd.levels_view(now) is first and len(reads) == 1          # same file: no second read
    with open(graded, "a") as f:
        f.write(json.dumps({**row, "key": "K2"}) + "\n")
    assert wd.levels_view(now)["weather"]["card"]["graded"] == 2 and len(reads) == 2


def test_evil_card_keeps_its_own_class():
    # 2026-10-26 the Charles vs Evil Charles card reused class "vs" as a two-column grid, which also split
    # each Return / Worst drop box under the Home chart into two columns: 483px wide on a 390px phone.
    import os
    with open(os.path.join(os.path.dirname(wd.__file__), "web", "index.html")) as f:
        page = f.read()
    assert 'class="duel" id="evil-vs"' in page and 'class="vs" id="evil-vs"' not in page


def test_page_works_alone_and_inside_charles():
    # Inside Charles's dashboard (/kalshi/) webdash swaps in the shared header, which has no theme button:
    # the page must not need one, and its sections stay in one row of its own.
    import os
    with open(os.path.join(os.path.dirname(wd.__file__), "web", "index.html")) as f:
        page = f.read()
    assert 'if (!$("theme")) return;' in page and 'id="seg"' in page
    assert 'id="tabs"' not in page and "appswitch" not in page


def _fresh_cache(monkeypatch):
    monkeypatch.setattr(wd, "_state_body", None)
    monkeypatch.setattr(wd, "_state_at", 0.0)


def test_state_is_built_once_however_many_pages_ask_at_the_same_time(monkeypatch):
    """Found 2026-10-31: each look re-read a 42 MB file, so a few at once filled the service's memory cap and it stopped answering."""
    import threading
    import time as t
    _fresh_cache(monkeypatch)
    builds = []

    def slow():
        builds.append(1)
        t.sleep(.15)
        return {"n": len(builds)}
    monkeypatch.setattr(wd, "state", slow)
    got = []
    threads = [threading.Thread(target=lambda: got.append(wd.state_json())) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(builds) == 1 and got == [b'{"n": 1}'] * 8


def test_a_look_during_a_rebuild_gets_the_last_state_rather_than_waiting(monkeypatch):
    _fresh_cache(monkeypatch)
    monkeypatch.setattr(wd, "state", lambda: {"v": 1})
    assert wd.state_json() == b'{"v": 1}'
    wd.state_changed()
    assert wd._state_lock.acquire(blocking=False)
    try:
        assert wd.state_json() == b'{"v": 1}'        # another thread "is building": no second build, no wait
    finally:
        wd._state_lock.release()


def test_flipping_the_switch_shows_at_the_next_look(monkeypatch):
    _fresh_cache(monkeypatch)
    monkeypatch.setattr(wd, "state", lambda: {"v": 1})
    wd.state_json()
    monkeypatch.setattr(wd, "state", lambda: {"v": 2})
    assert wd.state_json() == b'{"v": 1}'            # still fresh
    wd.state_changed()
    assert wd.state_json() == b'{"v": 2}'


# ---------------------------------------------------------------- one account, two bots (owner 2026-11-03)
def test_each_account_point_says_who_it_followed():
    st = {"evil": True, "history": [{"ts": "2026-10-28T15:42:43+00:00", "evil": True}]}
    eq = [{"ts": "2026-10-27T00:00:00+00:00", "account_value": 560.0},                     # before the flip: Charles
          {"ts": "2026-10-29T00:00:00+00:00", "account_value": 555.0},                     # after it: Evil Charles
          {"ts": "2026-11-03T02:00:00+00:00", "account_value": 554.0, "mode": "charles"}]  # tagged by the scan itself
    assert [r[2] for r in wd.tagged_equity(eq, st)] == ["charles", "evil", "charles"]
    assert wd.tagged_equity(eq, st)[0][:2] == ["2026-10-27T00:00:00+00:00", 560.0]        # Charles's Home still reads [ts, value]
    paid = eq + [{"ts": "2026-11-04T02:00:00+00:00", "account_value": 600.0, "mode": "evil", "by": "charles"}]
    assert wd.tagged_equity(paid, st)[-1][2:] == ["evil", "charles"]                    # a Charles bet paid out: a blue rise


def test_bot_view_keeps_each_bots_record_apart():
    import json
    import betting
    import os
    os.makedirs(betting.BOOKS, exist_ok=True)
    path, eq = betting.bot_paths("evil")
    with open(path, "w") as f:
        json.dump({"start_cash": 1000.0, "cash": 990.0, "started": "2026-11-03T01:00:00+00:00", "open": [{"cost": 4.0}],
                   "closed": [{"strategy": "weather", "pnl": 6.0, "how": "won"}, {"strategy": "sports", "pnl": -5.0, "how": "lost"}]}, f)
    with open(eq, "w") as f:
        f.write(json.dumps({"ts": "2026-11-03T02:00:00+00:00", "account_value": 1003.5}) + "\n")
    v = wd.bot_view("evil")
    assert (v["name"], v["earned"], v["won"], v["lost"], v["open"]) == ("Evil Charles", 3.5, 1, 1, 1)
    assert v["by_strategy"]["weather"] == {"settled": 1, "pnl": 6.0}
    assert wd.bot_view("charles")["placed"] == 0 and wd.bot_view("charles")["earned"] == 0.0      # no book yet: a fresh $1,000


def test_self_tune_shows_the_test_while_the_levels_are_off():
    from datetime import datetime, timezone
    now = datetime(2026, 11, 10, 15, tzinfo=timezone.utc)
    bots = {b: {"started": "2026-11-03T01:00:00+00:00",
                "by_strategy": {s: {"settled": 10 if b == "charles" else 8, "pnl": 1.0} for s in wd.STRATEGY_INFO}}
            for b in ("charles", "evil")}
    test = wd.test_view(now, bots, has_review=True)
    assert test["review"] == "2026-11-30" and 0 < test["progress"] < 0.5 and test["days_left"] == 20
    m = wd.test_milestones(bots, test)
    assert [x["key"] for x in m] == ["weather", "sports", "econ", "longshot", "review"]
    assert m[0]["progress"] == 0.4 and m[0]["detail"].startswith("8 settled each")                 # the smaller of the two books
    assert m[-1]["detail"].startswith("Mon Nov 30") and not m[-1].get("level")
    alone = wd.test_view(now, bots, has_review=False)                     # a public install: no review to promise
    assert alone["review"] is None and [x["key"] for x in wd.test_milestones(bots, alone)][-1] == "longshot"
