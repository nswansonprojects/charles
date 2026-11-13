"""leaderboard/why.py — the website's one-tap "What stopped you?" (roadmap s-why): totals only.

A visitor on the setup guide or the paid page may tap one answer. The leaderboard server adds one to that day's count for
that page and answer, and keeps nothing else: no address, no time of day, no browser. (The per-address limit lives only in
the server's memory, as for leaderboard submissions.) The board's Numbers tab shows the totals (ops/board/stats.py).
"""
from datetime import datetime, timezone

PAGES = ("guide", "paid")
REASONS = {                                    # id: the words on the button (the website shows the same, in js/why.js)
    "tech": "Looks too technical",
    "server": "I don't want a server bill",
    "price": "The price",
    "trust": "Not sure I trust a trading bot",
    "info": "I need to know more first",
    "later": "Just looking for now",
}
SCHEMA = """CREATE TABLE IF NOT EXISTS why_counts (day TEXT NOT NULL, page TEXT NOT NULL, reason TEXT NOT NULL, n INTEGER NOT NULL,
            PRIMARY KEY (day, page, reason))"""


class Bad(ValueError):
    pass


def record(db, body: dict, now: datetime | None = None) -> dict:
    """Add one to today's count for (page, reason). Anything but a known page and answer is refused."""
    if not isinstance(body, dict) or body.get("page") not in PAGES or body.get("reason") not in REASONS:
        raise Bad("unknown page or answer")
    day = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    db.execute(SCHEMA)
    db.execute("INSERT INTO why_counts (day, page, reason, n) VALUES (?, ?, ?, 1) "
               "ON CONFLICT (day, page, reason) DO UPDATE SET n = n + 1", (day, body["page"], body["reason"]))
    db.commit()
    return {"ok": True}


def totals(db) -> dict:
    """{day: {page: {reason: n}}}, every day kept (they're only counts)."""
    db.execute(SCHEMA)
    out: dict = {}
    for day, page, reason, n in db.execute("SELECT day, page, reason, n FROM why_counts ORDER BY day"):
        out.setdefault(day, {}).setdefault(page, {})[reason] = n
    return out
