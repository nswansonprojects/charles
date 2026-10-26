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


def _graded(side, result, cost, opp_cost=None, ts="2026-10-20T00:00:00+00:00", key="K"):
    sig = {"side": side, "ask": cost, "cost": cost, "pnl": (1.0 if side == result else 0.0) - cost}
    if opp_cost is not None:
        sig["opp_cost"] = opp_cost
    return {"key": key, "result": result, "first": {"ts": ts, "market_yes": 0.5}, "signal": sig}


def test_evil_record_compares_charles_with_the_opposite_bets():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 26, tzinfo=timezone.utc)
    rows = [_graded("yes", "no", 0.40, 0.62), _graded("yes", "yes", 0.40, 0.62, key="K2"),
            _graded("no", "yes", 0.30), _graded("yes", "no", 0.40, 0.62, ts="2026-09-01T00:00:00+00:00")]
    r = wd.evil_record(rows, now)
    assert (r["bets"], r["events"], r["estimated"]) == (3, 2, 1)       # the September one is too old
    assert r["charles"]["won"] == 1 and r["evil"]["won"] == 2
    assert r["charles"]["roi_pct"] < 0 < r["evil"]["roi_pct"]
