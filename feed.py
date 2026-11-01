"""
feed.py — the feed beside every bot's page (owner 2026-10-31: "almost like a twitter feed", always open).

Charles's own posts (what it traded, what it noted, Claude's check-up) and news about what it holds, newest first, in one
shape: {"ts", "who": "charles" | "claude" | "news", "name", "text", "tag", "pnl", "amount", "link", "tone", "bot"}.
The bot's notices (alerts.py) are written for a log, so they're turned into plain sentences here. Nothing is made up:
a notice this can't read is passed on as it is.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿️⏩-⏺]+\\s*")
_BRACKETS = re.compile(r"\s*\((on the dashboard|or )[^)]*\)")
_DAY = re.compile(r"(−?\$[\d,]+\.\d\d)\s*[,|]\s*([+−-]?)\+?(−?)\$([\d,]+\.\d\d)\s*today(?:,\s*([+−-]?[\d.]+%) since the start)?")
_LIMIT = re.compile(r"\$([\d,]+\.\d\d)\D+\$([\d,]+\.\d\d)")
MODES = (("Safety mode on", "Moved to safety: everything waits in T-bills until the market's trend recovers."),
         ("Aggressive mode on", "Calm and rising, so aggressive mode is on: the core moves to the 2× fund."),
         ("plain SPY core", "A choppy day, so the core goes back to plain SPY."))
NOTE = {"stocks": "News, not advice. Charles doesn't trade on these; only a new pick gets the AI news check.",
        "crypto": "News, not advice. Charles doesn't trade on these.",
        "kalshi": "News, not advice. Charles doesn't bet on these."}


def plain(text) -> str:
    return _EMOJI.sub("", str(text or "")).strip()


def first_sentence(text, limit: int = 150) -> str:
    text = plain(text)
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    text = m.group(1) if m else text
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def _names(items: list) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _post(ts, text, who="charles", **more) -> dict:
    return {"ts": ts, "who": who, "name": {"charles": "Charles", "claude": "Claude's check-up"}.get(who, who), "text": text, **more}


def _claude(title: str, message: str, level: str, bot: str) -> dict:
    head = title.split(":", 1)[1].strip() if ":" in title else title
    lines = [re.sub(r"^[-•]\s*", "", x).strip() for x in plain(message).split("\n") if x.strip()]
    body = " ".join(lines[:2])
    text = f"{head[:1].upper()}{head[1:]}." + (f" {body}" if body else "")
    return {"who": "claude", "text": text if len(text) <= 260 else text[:260].rsplit(" ", 1)[0] + "…", "tone": "error" if level == "error" else None, "bot": bot}


def notice_post(n: dict) -> dict | None:
    """One notice as a post, or None when it isn't one: "Rebalanced" only says what its trades say, unless the mode moved."""
    title, message, ts, level = plain(n.get("title")), plain(n.get("message")), n.get("ts"), n.get("level")
    tone = "error" if level == "error" else None
    if title.startswith("Charles dev"):                 # the daily dev session's note to the owner
        return None
    if title.startswith("Kalshi:"):
        return {"ts": ts, "name": "Claude's check-up", **_claude(title, message, level, "kalshi")}
    if title.startswith("Charles:"):
        return {"ts": ts, "name": "Claude's check-up", **_claude(title, message, level, "stocks")}
    if title == "Daily summary":
        m = _DAY.search(message)
        if m:
            val, sign, minus, amount, since = m.groups()
            day = float(amount.replace(",", "")) * (-1 if "−" in (sign + minus) or "-" in sign else 1)
            return _post(ts, f"Ended the day at {val}" + (f", {since} since the start." if since else "."), pnl=day, bot="stocks")
        return _post(ts, message or title, bot="stocks")
    if title.startswith("Buys paused"):
        m = _LIMIT.search(message)
        said = f"down ${m.group(1)}, past the ${m.group(2)} daily limit" if m else "past the daily loss limit"
        return _post(ts, f"Stopped buying for today: {said}. Sales still go through, and buys are back tomorrow.", tone=tone, bot="stocks")
    if title == "Charles started":
        return _post(ts, "Up and running. The plan gets checked at 10:00 AM ET on weekdays.", bot="stocks")
    if title == "Charles hit an error":
        return _post(ts, f"Hit a problem: {message}" if message else "Hit a problem.", tone=tone, bot="stocks")
    if title == "Rebalanced":
        said = [t for k, t in MODES if k in message]
        return _post(ts, said[0], bot="stocks") if said else None
    text = _BRACKETS.sub("", title)
    rest = _BRACKETS.sub("", message)
    if rest and not rest.startswith("Asked from"):
        text = f"{text.rstrip('.')}. {rest}"
    return _post(ts, text if len(text) <= 260 else text[:260].rsplit(" ", 1)[0] + "…", tone=tone, bot="stocks")


def trade_posts(trades: list, keep: int = 4) -> list:
    """A day's trades as one post: five lines saying "Bought" say less than one line naming the five. A lone trade keeps its reason."""
    days: dict[str, list] = {}
    for t in trades or []:
        if t.get("timestamp"):
            days.setdefault(t["timestamp"][:10], []).append(t)
    out = []
    for rows in list(days.values())[:keep]:
        buys = [t for t in rows if str(t.get("action") or "").startswith("BUY")]
        sells = [t for t in rows if t not in buys]
        said = []
        if buys:
            said.append(f"Bought {_names([t.get('symbol') for t in buys])}.")
        if sells:
            said.append(f"Sold {_names([t.get('symbol') for t in sells])}.")
        if len(rows) == 1 and rows[0].get("reasoning"):
            said.append(first_sentence(rows[0]["reasoning"]))
        pnl = [t["pnl"] for t in sells if t.get("pnl") is not None]
        out.append(_post(rows[0]["timestamp"], " ".join(said), pnl=round(sum(pnl), 2) if pnl else None,
                         amount=rows[0].get("value") if len(rows) == 1 and buys else None, bot="stocks"))
    return out


def coin_posts(crypto: dict, keep: int = 6) -> list:
    book = crypto.get("book") or {}
    out = []
    for t in (book.get("trades") or [])[:keep]:
        name = (crypto.get("names") or {}).get(t.get("product"), t.get("product"))
        buy = str(t.get("side")).upper() == "BUY"
        reason = first_sentence(t.get("reason"), 120)
        out.append(_post(t.get("timestamp"), f"{'Bought' if buy else 'Sold'} {str(name).lower()}." + (f" {reason}" if reason else ""), amount=t.get("value"), bot="crypto"))
    return out


def _market(b: dict) -> str:
    title = str(b.get("title") or "").rstrip("?")
    return f"{b.get('sub')}, on “{title}”" if b.get("sub") and title else b.get("sub") or title


def bet_posts(kalshi: dict, keep: int = 8) -> list:
    bets = kalshi.get("betting") or {}
    out = []
    who = lambda b: {"name": "Evil Charles"} if b.get("evil") else {}
    for b in sorted(bets.get("closed") or [], key=lambda x: str(x.get("closed")), reverse=True)[:keep]:
        word = {"won": "Won", "lost": "Lost", "sold": "Sold", "void": "Refunded"}.get(b.get("how"), "Closed")
        out.append({**_post(b.get("closed"), f"{word}: {_market(b)}.", pnl=b.get("pnl"), bot="kalshi"), **who(b)})
    for b in sorted(bets.get("open") or [], key=lambda x: str(x.get("opened")), reverse=True)[:keep]:
        side = str(b.get("side") or "").upper()
        out.append({**_post(b.get("opened"), f"New bet{f', {side}' if side else ''}: {_market(b)}.", amount=b.get("cost"), bot="kalshi"), **who(b)})
    return out


def news_posts(items: list, bot: str) -> list:
    return [{"ts": n.get("published"), "who": "news", "name": n.get("source") or "News", "text": n.get("title"), "tag": n.get("symbol"), "link": n.get("link") or None, "bot": bot}
            for n in items or []]


def _when(p: dict) -> datetime:
    try:
        t = datetime.fromisoformat(str(p.get("ts")).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def merge(posts: list, keep: int = 30) -> list:
    """Newest first; a post with no date at all goes last."""
    return sorted(posts, key=_when, reverse=True)[:keep]


def for_stocks(state: dict, noted: list, news: list) -> list:
    notes = [p for p in map(notice_post, noted) if p and p.get("bot") == "stocks"]
    return merge(notes + trade_posts(state.get("trades")) + news_posts(news, "stocks"))


def for_crypto(state: dict, news: list) -> list:
    return merge(coin_posts(state) + news_posts(news, "crypto"))


def for_kalshi(state: dict, noted: list, news: list) -> list:
    notes = [p for p in map(notice_post, noted) if p and p.get("bot") == "kalshi"]
    return merge(notes + bet_posts(state) + news_posts(news, "kalshi"))
