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
