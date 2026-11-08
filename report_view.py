"""
report_view.py — Claude's daily report as short findings, each with a small chart (owner 2026-10-31: "small bullet points and
graph representations" instead of a wall of text behind a button).

The check-up and the Kalshi review write a `## Findings` section, one line per finding:  - <chart> | <what it found>
The chart is named, never drawn, by the report: its numbers come from the bot's own records. Only `bars:` carries
numbers of its own, the same ones the report would otherwise have put in a sentence. A report without the section (every report
before 2026-10-31) is read from its bullets instead: their first sentence, and a chart picked from what that sentence is about.
The full text stays one tap away.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

MAX_ITEMS = 6
MAX_ALSO = 3
STRATEGY_WORDS = (("weather", r"\bweather\b"), ("sports", r"\bsports\b"), ("econ", r"\b(inflation|cpi)\b"), ("longshot", r"\blongshot\b"))
SYMBOL = re.compile(r"^[A-Z][A-Z.\-]{0,5}$")
BAR = re.compile(r"\s*([^=,]{1,24})=\s*([+\-−]?\d[\d,]*\.?\d*)\s*")


def sections(text: str) -> dict[str, list[str]]:
    """{"where things stand": [bullet, ...], ...}: each `## Heading`'s bullets, a bullet's wrapped lines joined."""
    out: dict[str, list[str]] = {}
    cur = None
    for raw in str(text or "").splitlines():
        line = raw.rstrip()
        m = re.match(r"^##\s+(.*)$", line)
        if m:
            cur = out.setdefault(m.group(1).strip().lower(), [])
        elif cur is None or not line.strip():
            continue
        elif re.match(r"^\s*[-*]\s+", line):
            cur.append(re.sub(r"^\s*[-*]\s+", "", line))
        elif cur and raw[:1].isspace():
            cur[-1] += " " + line.strip()
    return out


def _plain(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("**", "").replace("`", "")).strip()


def short(bullet: str, limit: int = 100) -> str:
    """A bullet down to one short line: its bold lead when that says something by itself, else its first sentence, cut at a natural
    break when it's still long."""
    s = bullet.strip()
    m = re.match(r"^\*\*(.+?)\*\*\s*(.*)$", s, re.S)
    if m:
        head, rest = m.group(1).strip(), m.group(2).strip()
        if head.endswith(":"):                                  # "**Weather:** −$331 all-time": only a label
            s = f"{head} {rest}"
        elif re.match(r"^(This|That|It|These|Those)\b", head) and rest:   # "**This also explains yesterday's puzzle.**" says nothing alone
            s = rest
        else:
            s = head
    s = _plain(s)
    m = re.match(r"^(.+?[.!?])(?=\s+[A-Z$“\"(−+\d]|\s*$)", s)
    s = m.group(1) if m else s
    if len(s) > limit:
        cut = max(s.rfind(x, 0, limit) for x in (", ", "; ", ": ", " — ", " (")) if len(s) > limit else -1
        s = s[:cut].rstrip() if cut >= limit * .5 else s[:limit].rsplit(" ", 1)[0].rstrip() + "…"
    return s.rstrip(",:;")


def declared(text: str) -> list[tuple[str, str]] | None:
    """The `## Findings` lines as [(chart, sentence)], or None if the report has no such section."""
    rows = sections(text).get("findings")
    if rows is None:
        return None
    out = []
    for r in rows:
        spec, bar, said = _plain(r).partition("|")
        if bar and said.strip():
            out.append((spec.strip().lower() if not spec.strip().lower().startswith(("stock:", "bars:", "strategy:")) else spec.strip(), said.strip()))
        elif r.strip():
            out.append(("none", _plain(r)))
    return out


# ── What a sentence is about, for a report with no Findings section ─────────
def guess_stocks(sentence: str, state: dict) -> str:
    held = [p.get("symbol") for p in state.get("positions") or []]
    named = [s for s in held if s and re.search(rf"\b{re.escape(s)}\b", sentence)]
    if re.search(r"daily[- ]loss|loss limit", sentence, re.I):
        return "loss-limit"
    if len(named) >= 2:
        return "holdings"
    if named:
        return f"stock:{named[0]}"
    if re.search(r"200-day|safety switch|switch reading", sentence, re.I):
        return "trend"
    if re.search(r"\bSPY\b", sentence) and re.search(r"\b(plan|charles|vs|ahead|behind)\b", sentence, re.I):
        return "vs-spy"
    if re.search(r"\b(account|overnight|closed?|value|drop)\b", sentence, re.I):
        return "account"
    return "none"


def guess_kalshi(sentence: str, state: dict) -> str:
    named = [k for k, rx in STRATEGY_WORDS if re.search(rx, sentence, re.I)]
    if len(named) >= 2:
        return "strategies"
    if named:
        return f"strategy:{named[0]}"
    if re.search(r"\bbook\b", sentence, re.I):
        return "book"
    if re.search(r"\blevels?\b", sentence, re.I):
        return "levels"
    return "none"


def guessed(text: str, bot: str, state: dict) -> list[tuple[str, str]]:
    """The bullets of a report with no Findings section, the overview first."""
    sec = sections(text)
    stand = sec.get("where things stand") or []
    rows = stand[:2] + (sec.get("how each strategy is doing") or []) + (sec.get("what i noticed") or []) + stand[2:]
    guess = guess_kalshi if bot == "kalshi" else guess_stocks
    return [(guess(s, state), s) for s in map(short, rows) if s]


# ── The charts, from the bot's own records ──────────────────────────────────
def _thin(rows: list, keep: int = 60) -> list:
    if len(rows) <= keep:
        return rows
    step = (len(rows) - 1) / (keep - 1)
    return [rows[round(i * step)] for i in range(keep)]


def _recent(rows: list, now: datetime, days: int = 30) -> list:
    cut = (now - timedelta(days=days)).isoformat()
    kept = [r[:2] for r in rows or [] if str(r[0]) >= cut]
    return kept if len(kept) >= 2 else [r[:2] for r in (rows or [])[-30:]]


def _line(rows: list, fmt: str = "usd", zero: bool = False) -> dict | None:
    pts = _thin(rows)
    return {"type": "line", "points": pts, "fmt": fmt, "zero": zero} if len(pts) >= 2 else None


def _bars_from(spec: str) -> dict | None:
    """bars:DELL=-13,MU=-9,SSO=-2:pts, the numbers the report would have written in its sentence."""
    body = spec[5:]
    unit = ""
    if ":" in body:
        body, unit = body.rsplit(":", 1)
    rows = []
    for m in BAR.finditer(body):
        try:
            rows.append([m.group(1).strip(), float(m.group(2).replace("−", "-").replace(",", ""))])
        except ValueError:
            continue
    return {"type": "bars", "rows": rows[:8], "fmt": unit.strip()[:6]} if rows else None


def _vs_spy(state: dict) -> dict | None:
    """The plan vs SPY since it started, both at the same market closes, as in the status the report quotes (plan_vs_spy):
    from the last close before the start (else the first one after it) to the latest. Midnight and after-hours marks don't count."""
    import plan_vs_spy
    started = str((state.get("plan") or {}).get("started") or "")[:10]
    closes = plan_vs_spy.close_points([{"ts": r[0], "value": r[1]} for r in state.get("equity") or []])
    spy = {str(d)[:10]: c for d, c in state.get("spy") or [] if c and c == c}
    days = sorted(d for d in closes if d in spy)
    before = [d for d in days if d < started]
    days = [d for d in days if d >= (before[-1] if before else started)]
    if len(days) < 2 or not closes[days[0]]["value"]:
        return None
    a0, s0 = closes[days[0]]["value"], spy[days[0]]
    joined = _thin([(closes[d]["ts"], closes[d]["value"], spy[d]) for d in days])
    return {"type": "lines", "fmt": "pct", "series": [
        {"label": "Charles", "points": [[t, round((v / a0 - 1) * 100, 2)] for t, v, _ in joined]},
        {"label": "SPY", "dashed": True, "points": [[t, round((c / s0 - 1) * 100, 2)] for t, _, c in joined]}]}


def day_move(equity: list, day: str | None) -> float | None:
    """The account's move on a date: its last value that day minus its last before it."""
    on = [r for r in equity or [] if str(r[0])[:10] == day]
    before = [r for r in equity or [] if str(r[0])[:10] < str(day)]
    return round(on[-1][1] - before[-1][1], 2) if on and before else None


def stocks_chart(spec: str, state: dict, now: datetime, stock_line=None, day: str | None = None) -> dict | None:
    kind, _, arg = spec.partition(":")
    plan = state.get("plan") or {}
    if kind == "account":
        return _line(_recent(state.get("equity"), now))
    if kind == "vs-spy":
        return _vs_spy(state)
    if kind == "trend":
        gap = (plan.get("trend") or {}).get("gap_pct")
        if gap is None:
            return None
        return {"type": "gauge", "value": gap, "min": -20, "max": 20, "mark": 0, "fmt": "pct", "label": "SPY vs its 200-day line"}
    if kind == "holdings":
        rows = [[p.get("symbol"), round(p.get("unrealized_pct") or 0, 1)] for p in state.get("positions") or [] if p.get("symbol")]
        return {"type": "bars", "rows": rows[:8], "fmt": "%"} if rows else None
    if kind == "loss-limit":
        # A finding about the limit is about the report's day, and today may be a quiet Saturday.
        limit = (state.get("today") or {}).get("limit")
        moved = day_move(state.get("equity"), day)
        if moved is None:
            moved, day = (state.get("account") or {}).get("day_change"), None
        if not limit or moved is None:
            return None
        when = datetime.strptime(day, "%Y-%m-%d").strftime("%b %-d") if day else "today"
        return {"type": "gauge", "value": moved, "min": -limit * 2, "max": limit * 2, "mark": -limit, "fmt": "usd", "label": f"{when}, limit −${limit:,.0f}"}
    if kind == "stock" and SYMBOL.match(arg) and stock_line:
        rows = stock_line(arg)
        return _line(rows or [], "price") if rows else None
    if kind == "bars":
        return _bars_from(spec)
    return None


def kalshi_chart(spec: str, state: dict, now: datetime, stock_line=None, day: str | None = None) -> dict | None:
    kind, _, arg = spec.partition(":")
    bets = state.get("betting") or {}
    if kind == "book":
        return _line(_recent(bets.get("equity"), now))
    if kind == "strategy":
        return _line((bets.get("pnl_lines") or {}).get(arg) or [], zero=True)
    if kind == "strategies":
        rows = [[s.get("name"), round(s.get("pnl") or 0, 2)] for s in bets.get("strategies") or []]
        return {"type": "bars", "rows": rows, "fmt": "$"} if rows else None
    if kind == "levels":
        rows = [[str(m.get("name")).split(":")[0], round((m.get("progress") or 0) * 100)] for m in bets.get("milestones") or [] if m.get("level")]
        return {"type": "bars", "rows": rows, "fmt": "%", "max": 100} if rows else None
    if kind == "bars":
        return _bars_from(spec)
    return None


def view(report: dict | None, bot: str, state: dict, now: datetime | None = None, stock_line=None) -> dict:
    """GET /api/findings: {"date", "items": [{"text", "chart"}], "also": [line, ...], "text": the whole report}. A chart appears once:
    a second finding about the same thing goes in `also`, as does one with nothing to draw."""
    now = now or datetime.now(timezone.utc)
    if not report or not report.get("text"):
        return {"date": None, "items": [], "also": [], "text": ""}
    text = report["text"]
    rows = declared(text)
    rows = guessed(text, bot, state) if rows is None else rows
    chart_of = kalshi_chart if bot == "kalshi" else stocks_chart
    items, also, used = [], [], set()
    for spec, said in rows:
        chart = chart_of(spec, state, now, stock_line, report.get("date")) if spec != "none" and spec not in used and len(items) < MAX_ITEMS else None
        if chart:
            used.add(spec)
            items.append({"text": said, "chart": chart})
        else:
            also.append(said)
    return {"date": report.get("date"), "items": items, "also": also[:MAX_ALSO], "text": text}
