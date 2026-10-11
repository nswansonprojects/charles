"""
promotion.py — the roadmap task 8 promotion rules as code, so every book's verdict is known any day.

A shadow rule may go live (one at a time, rules in ops/daily-dev.md "Promotions") only when its
book has the evidence:
  intraday books (shadow.py): ≥30 closed trades, return above baseline's, profit per trade above
    baseline's, total P&L ahead of baseline by more than one average baseline trade, and a worst
    drop no deeper than baseline's or SPY's over the book's life;
  daily books (shadow_daily.py, few trades by design): ≥3 months of data, return above SPY's over
    the same days, worst drop no deeper than SPY's.
Read-only: returns verdicts, never changes a setting. Not before REVIEW_FROM.
"""
from datetime import datetime, timezone

EVIDENCE_TRADES = 30
DAILY_DAYS = 91
REVIEW_FROM = "2026-11-02"


def _spy_window(spy: list, since: str) -> list:
    before = [c for d, c in spy if d < since]
    return before[-1:] + [c for d, c in spy if d >= since]


def verdict(name: str, s: dict, base: dict, base_trades: list, spy: list, daily: bool,
            now: datetime) -> dict:
    """One book's verdict: {"ready": bool, "progress": 0..1, "checks": [(label, passed)], "why": str}."""
    import shadow_daily
    since = (s.get("since") or "")[:10]
    pts = _spy_window(spy, since) if since else []
    spy_ret = round((pts[-1] / pts[0] - 1) * 100, 2) if len(pts) >= 2 else None
    spy_dd = shadow_daily.max_drawdown_pct(pts) if pts else None
    dd = s.get("max_drawdown_pct")
    checks = []
    if daily:
        days = (now - datetime.fromisoformat(since).replace(tzinfo=timezone.utc)).days if since else 0
        progress = min(1.0, days / DAILY_DAYS)
        checks.append((f"{DAILY_DAYS} days of data ({days} so far)", days >= DAILY_DAYS))
        checks.append(("return above SPY's", spy_ret is not None and s.get("return_pct", -1e9) > spy_ret))
        checks.append(("worst drop no deeper than SPY's", spy_dd is not None and dd is not None and dd >= spy_dd))
    else:
        n, bn = s.get("closed", 0), base.get("closed", 0)
        progress = min(1.0, n / EVIDENCE_TRADES)
        ppt = s["realized_pnl"] / n if n else None
        bppt = base.get("realized_pnl", 0) / bn if bn else None
        avg_trade = sum(abs(t.get("pnl", 0)) for t in base_trades) / len(base_trades) if base_trades else None
        checks.append((f"{EVIDENCE_TRADES} closed trades ({n} so far)", n >= EVIDENCE_TRADES))
        checks.append(("return above baseline's", s.get("return_pct", -1e9) > base.get("return_pct", 1e9)))
        checks.append(("profit per trade above baseline's", ppt is not None and bppt is not None and ppt > bppt))
        checks.append(("P&L ahead of baseline by more than one average trade",
                       avg_trade is not None and s.get("realized_pnl", 0) - base.get("realized_pnl", 0) > avg_trade))
        checks.append(("worst drop no deeper than baseline's or SPY's",
                       dd is not None and ((base.get("max_drawdown_pct") is not None and dd >= base["max_drawdown_pct"])
                                           or (spy_dd is not None and dd >= spy_dd))))
    ready = all(ok for _, ok in checks) and name != "baseline"
    failing = [label for label, ok in checks if not ok]
    return {"ready": ready, "progress": round(progress, 2), "spy_return_pct": spy_ret,
            "checks": [{"check": label, "passed": ok} for label, ok in checks],
            "why": "meets every rule" if ready else ("reference book" if name == "baseline" else
                                                     "not yet: " + "; ".join(failing))}


def review(summary: dict, spy: list, base_trades: list = (), now: datetime | None = None) -> dict:
    """Verdict per book. summary: shadow.summary(); spy: [[date, close], ...]; base_trades:
    baseline's closed trades (for the average trade size)."""
    import shadow_daily
    now = now or datetime.now(timezone.utc)
    base = summary.get("baseline", {})
    books = {name: verdict(name, s, base, list(base_trades), spy, name in shadow_daily.STRATEGIES, now)
             for name, s in summary.items()}
    ready = sorted((n for n, v in books.items() if v["ready"]),
                   key=lambda n: summary[n].get("return_pct", 0), reverse=True)
    return {"review_from": REVIEW_FROM, "review_open": now.date().isoformat() >= REVIEW_FROM,
            "ready": ready, "promote_first": ready[0] if ready else None, "books": books}


def from_files(spy: list, now: datetime | None = None) -> dict:
    import shadow
    books = shadow.load()
    return review(shadow.summary(), spy, books.get("baseline", {}).get("closed", []), now)
