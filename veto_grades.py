"""
veto_grades.py — did the AI's vetoes help? Read-only and pure (no network, no files).

For each answer in data/vetoes.jsonl with `veto: true`, the vetoed stock's return over the
following month (WINDOW trading days from the close of the veto day) against the average of the
picks the AI let through at the same check (the same UTC day, `veto: false`). A vetoed stock that
did worse than the picks is a veto that helped.

The caller passes the rows and each symbol's daily closes: {symbol: [(YYYY-MM-DD, close), ...]}.
"""

WINDOW = 21          # trading days, about a month


def read_rows(lines) -> list:
    """The vetoes.jsonl lines as dicts; unreadable lines are skipped."""
    import json
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(row, dict) and row.get("symbol") and row.get("ts"):
            out.append(row)
    return out


def symbols_needed(rows: list) -> list:
    """Every symbol a grade needs prices for: the vetoed ones and the picks checked the same day."""
    days = {r["ts"][:10] for r in rows if r.get("veto") is True}
    return sorted({r["symbol"] for r in rows if r["ts"][:10] in days})


def forward_return(closes: list, day: str, window: int = WINDOW):
    """(return %, trading days measured) from the close on `day` (or the last close before it)
    to `window` closes later, or to the latest close if a month hasn't passed. None without data."""
    pts = sorted((d[:10], float(c)) for d, c in (closes or []) if c == c)
    start = [i for i, (d, _) in enumerate(pts) if d <= day]
    if not start:
        return None
    i = start[-1]
    j = min(i + window, len(pts) - 1)
    if j == i:
        return None
    return round((pts[j][1] / pts[i][1] - 1) * 100, 2), j - i


def grade(rows: list, closes: dict, window: int = WINDOW) -> dict:
    """{"vetoes", "graded", "complete", "helped", "avg_vs_picks_pts", "grades": [...]}.
    A grade is complete once `window` trading days have passed; until then it shows the days so far."""
    vetoed = [r for r in rows if r.get("veto") is True]
    grades = []
    for v in vetoed:
        day = v["ts"][:10]
        g = {"date": day, "symbol": v["symbol"], "reason": v.get("reason", "")}
        fr = forward_return(closes.get(v["symbol"]), day, window)
        picks = sorted({r["symbol"] for r in rows if r["ts"][:10] == day and r.get("veto") is False})
        pick_rets = [x for x in (forward_return(closes.get(s), day, window) for s in picks) if x]
        g["picks"] = picks
        if fr is None or not pick_rets:
            g["note"] = "no prices yet" if fr is None else "no picks with prices that day to compare with"
            grades.append(g)
            continue
        avg = round(sum(x[0] for x in pick_rets) / len(pick_rets), 2)
        g.update(stock_pct=fr[0], picks_avg_pct=avg, vs_picks_pts=round(fr[0] - avg, 2),
                 days=fr[1], complete=fr[1] >= window, helped=fr[0] < avg)
        grades.append(g)
    done = [g for g in grades if g.get("complete")]
    return {"vetoes": len(vetoed),
            "graded": sum("vs_picks_pts" in g for g in grades),
            "complete": len(done),
            "helped": sum(g["helped"] for g in done),
            "avg_vs_picks_pts": round(sum(g["vs_picks_pts"] for g in done) / len(done), 2) if done else None,
            "grades": grades}
