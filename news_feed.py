"""
news_feed.py — headlines about the stocks Charles holds, for the dashboard (owner's Phase 3 plan, 2026-10-29).

News, not advice. Nothing here trades: only a new pick gets the AI news check (news_veto.py), and this
reads the same Yahoo Finance feed. A stock's feed mixes in other companies' news, sales pitches ("If I had
to put $5,000 into one stock…", "Which stock offers more growth?"), guesses ("Will Moderna soar?") and
market-research ads, so only headlines that name the company and aren't one of those are kept. The funds (SPY, SSO, SGOV) are left out: their feeds are general market chatter.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlparse

import news_veto

ROLES = ("pick", "leaving")          # the stocks; the core and safety funds are left out
PER_STOCK = 3
SHOWN = 8
PITCH = re.compile(r"\b(a buy|buy now|strong buy|should you|price targets?|millionaires?|best stocks?|stocks? to (buy|own|watch)|"
                   r"stock picks?|top picks?|passive income|(business|market|research|industry) report)\b|\$[\d,]+ (in|into)\b|"
                   r"\bwhich\b[^.!?]*\bstocks?\b|\bfeatured highlights\b|\d+% (upside|downside)\b|\?\s*$", re.I)        # a question is a guess, not news
SOURCES = {"finance.yahoo.com": "Yahoo Finance"}


def mentions(title: str, symbol: str, name: str | None) -> bool:
    """Does the headline name this company: its short name, a name in brackets ("Alphabet (Google)"),
    or its ticker in capitals?"""
    names = [n.strip() for n in re.split(r"[()]", name or "") if len(n.strip()) >= 3]
    if any(re.search(rf"\b{re.escape(n)}\b", title, re.I) for n in names):
        return True
    return len(symbol) >= 2 and re.search(rf"\b{re.escape(symbol)}\b", title) is not None


def source(link: str) -> str:
    host = urlparse(link).hostname or ""
    host = host[4:] if host.startswith("www.") else host
    return SOURCES.get(host, host)


def for_stock(symbol: str, name: str | None, now: datetime, fetch=None) -> list[dict]:
    rows = []
    for r in news_veto.items(symbol, now, fetch):
        link = r["link"] if r["link"].startswith(("https://", "http://")) else ""
        if mentions(r["title"], symbol, name) and not PITCH.search(r["title"]):
            rows.append({"symbol": symbol, "title": r["title"][:220], "link": link, "source": source(link),
                         "published": r["published"].astimezone(timezone.utc).isoformat() if r["published"] else None})
    return rows[:PER_STOCK]


def _pool(jobs, one):
    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(one, jobs))


def holdings_news(positions: list[dict], names: dict, now: datetime | None = None, fetch=None) -> dict:
    """{"as_of", "items": the newest across the stocks held, "by_symbol": {symbol: [...]}, "failed": [symbols]}.
    One feed that fails just leaves that stock out."""
    now = now or datetime.now(timezone.utc)
    symbols = list(dict.fromkeys(p["symbol"] for p in positions if p.get("role") in ROLES))
    by, failed = {}, []

    def one(sym):
        try:
            return sym, for_stock(sym, names.get(sym), now, fetch)
        except Exception:
            return sym, None
    for sym, rows in _pool(symbols, one):
        if rows is None:
            failed.append(sym)
        else:
            by[sym] = rows
    every = sorted((r for rows in by.values() for r in rows), key=lambda r: r["published"] or now.isoformat(), reverse=True)
    return {"as_of": now.isoformat(), "items": every[:SHOWN], "by_symbol": by, "failed": failed}


# ── Crypto: Yahoo's own feeds for the two coins ─────────────────────────────
COINS = (("BTC", re.compile(r"\b(bitcoin|btc)\b", re.I)), ("ETH", re.compile(r"\b(ether|ethereum|eth)\b", re.I)),
         ("Crypto", re.compile(r"\bcrypto(currenc\w+)?\b", re.I)))


def coin_news(now: datetime | None = None, fetch=None) -> dict:
    """{"items": [...]}: headlines that name a coin or crypto, tagged by the first one they name. Both coins' feeds overlap, so each
    headline shows once."""
    now = now or datetime.now(timezone.utc)

    def one(sym):
        try:
            return news_veto.items(sym, now, fetch)
        except Exception:
            return []
    rows = {}
    for got in _pool(("BTC-USD", "ETH-USD"), one):
        for r in got:
            tag = next((t for t, rx in COINS if rx.search(r["title"])), None)
            link = r["link"] if r["link"].startswith(("https://", "http://")) else ""
            if tag and not PITCH.search(r["title"]) and r["title"] not in rows:
                rows[r["title"]] = {"symbol": tag, "title": r["title"][:220], "link": link, "source": source(link),
                                    "published": r["published"].astimezone(timezone.utc).isoformat() if r["published"] else None}
    every = sorted(rows.values(), key=lambda r: r["published"] or now.isoformat(), reverse=True)
    return {"as_of": now.isoformat(), "items": every[:SHOWN]}


# ── Kalshi: what's in the news about the things it has bet on ───────────────
GOOGLE = "https://news.google.com/rss/search?q={q}+when:7d&hl=en-US&gl=US&ceid=US:en"
_ASKS = re.compile(r"^(when will|what will|who will|will|how (low|high|much|many) will)\s+", re.I)
_DATES = re.compile(r"\s*\b(in|by|before|on|during|this)\s+((jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?(\s+\d{1,2})?(,?\s*\d{4})?|\d{4}|year|month|week)\b", re.I)
_SMALL = {"the", "and", "for", "with", "that", "this", "from", "into", "than", "more", "less", "above", "below", "get", "been", "have"}
PER_TOPIC = 2
TOPICS = 6


def topic_of(title: str) -> str:
    """A market's title as a news search: "Will a plague case be reported in the U.S. this year?" → "a plague case be reported in the U.S"."""
    t = _ASKS.sub("", str(title or "").strip()).rstrip("?").strip()
    return _DATES.sub("", t).strip(" .,")


def topic_words(topic: str) -> list[str]:
    return [w for w in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", topic) if w.lower() not in _SMALL]


def _word_in(word: str, headline: str) -> bool:
    """A word, or one that starts with it ("Rain" finds "Rainy"). A capitalised one has to stay capitalised: the brand Target isn't "DB target"."""
    first = word[0] if word[0].isupper() else f"[{word[0].upper()}{word[0]}]"
    return re.search(rf"\b{first}(?i:{re.escape(word[1:])})", headline) is not None


def about_topic(headline: str, topic: str) -> bool:
    """A search brings back loose matches. A headline counts only if it has the topic's first word, its subject, and half of the rest:
    no headline is better than one about something else."""
    words = topic_words(topic)
    return bool(words) and _word_in(words[0], headline) and sum(_word_in(w, headline) for w in words) * 2 >= len(words)


_TAG_ENDS = {"in", "on", "at", "of", "for", "by", "to", "be", "is", "are", "than", "above", "below", "with", "from"}


def topic_tag(topic: str) -> str:
    """A short label for a topic: its words up to the first small one. "Diesel price on Election Day" → "Diesel price"."""
    out = []
    for w in topic.split():
        if w.lower() in _TAG_ENDS and out:
            break
        if w.lower() not in ("a", "an", "the"):
            out.append(w)
    tag = " ".join(out[:3])
    return tag[:1].upper() + tag[1:]


def _topic_news(topic: str, now: datetime, fetch=None) -> list[dict]:
    import feedparser
    from email.utils import parsedate_to_datetime
    from urllib.parse import quote_plus
    if fetch is None:
        import requests
        fetch = lambda url: requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"}).content
    rows = []
    for e in feedparser.parse(fetch(GOOGLE.format(q=quote_plus(topic)))).entries:
        name = re.sub(r"^www\.", "", ((e.get("source") or {}).get("title") or "").strip())
        title = (e.get("title") or "").strip()
        title = re.sub(r"\s+-\s+[^-]{2,60}$", "", title) if " - " in title else title    # Google adds " - The Source"
        if not title or not about_topic(title, topic) or PITCH.search(title):
            continue
        try:
            ts = parsedate_to_datetime(e.get("published", "")).astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            ts = None
        link = (e.get("link") or "").strip()
        rows.append({"symbol": topic_tag(topic), "title": title[:220], "link": link if link.startswith(("https://", "http://")) else "", "source": name or source(link), "published": ts})
    rows.sort(key=lambda r: r["published"] or "", reverse=True)
    return rows[:PER_TOPIC]


def bet_news(bets: list[dict], now: datetime | None = None, fetch=None) -> dict:
    """{"items": [...]}: the news about what the open bets are on, a topic per market. One search that fails just leaves that topic out."""
    now = now or datetime.now(timezone.utc)
    topics = list(dict.fromkeys(t for t in (topic_of(b.get("title")) for b in bets) if topic_words(t)))[:TOPICS]

    def one(topic):
        try:
            return _topic_news(topic, now, fetch)
        except Exception:
            return []
    seen, rows = set(), []
    for got in _pool(topics, one):
        for r in got:
            if r["title"] not in seen:
                seen.add(r["title"])
                rows.append(r)
    rows.sort(key=lambda r: r["published"] or "", reverse=True)
    return {"as_of": now.isoformat(), "items": rows[:SHOWN]}
