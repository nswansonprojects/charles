"""
news_veto.py — the AI's one job now: before Charles buys a new momentum pick, read the stock's
recent headlines and say whether there is serious bad news (then the next-ranked stock gets the
slot). It never picks stocks and never sells.

Fails open: no headlines, no Groq key or a Groq error means no veto, so the plan still runs.
Every answer is kept in data/vetoes.jsonl so the vetoes can be graded later.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone, timedelta

import config

log = logging.getLogger(__name__)
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "vetoes.jsonl")
MAX_HEADLINES = 12
NEWS_DAYS = 14

PROMPT = """You check one stock for an automated portfolio that is about to buy it because its price \
has risen strongly over the past year. You only decide whether there is SERIOUS company-specific bad \
news in the headlines below that makes buying it now a bad idea.

Veto only for things like: fraud or accounting investigations, a missed or delayed financial filing, \
bankruptcy or going-concern risk, a failed key drug trial or product recall, a large guidance cut, \
a major lawsuit or regulator action against the company, an agreed takeover at a fixed price (the \
price can't rise further), or the CEO/CFO leaving suddenly. Ordinary price drops, analyst downgrades, \
market-wide news, valuation worries and rumours are NOT reasons to veto.

Stock: {symbol}
Headlines from the last {days} days (newest first):
{headlines}

Answer with JSON only: {{"veto": true or false, "reason": "one short plain-English sentence"}}"""


RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"


def headlines(symbol: str, now: datetime | None = None, fetch=None) -> list[str]:
    """Recent headlines for one stock, newest first: Yahoo Finance's per-stock RSS feed
    (yfinance's .news came back empty from this server in Oct 2026). `fetch(url) -> bytes` is for tests."""
    import feedparser
    from email.utils import parsedate_to_datetime
    now = now or datetime.now(timezone.utc)
    if fetch is None:
        import requests
        fetch = lambda url: requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"}).content
    rows = []
    for e in feedparser.parse(fetch(RSS.format(symbol=symbol))).entries:
        title = (e.get("title") or "").strip()
        try:
            ts = parsedate_to_datetime(e.get("published", ""))
        except (TypeError, ValueError):
            ts = None
        if title and (ts is None or now - ts <= timedelta(days=NEWS_DAYS)):
            rows.append((ts, title))
    rows.sort(key=lambda r: r[0] or now, reverse=True)
    return [f"- {ts:%b %d}: {t}" if ts else f"- {t}" for ts, t in rows[:MAX_HEADLINES]]


def parse(text: str) -> dict | None:
    """The model's {"veto", "reason"} from its reply, or None if it can't be read."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(d.get("veto"), bool):
        return None
    return {"veto": d["veto"], "reason": str(d.get("reason", ""))[:240]}


def _ask(prompt: str) -> str:
    import requests
    import credential_manager as cm
    key = cm.groq_api_key()
    if not key or key.startswith(("YOUR_", "REPLACE_")):
        return ""
    r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                      json={"model": config.GROQ_MODEL, "messages": [{"role": "user", "content": prompt}],
                            "temperature": 0.1, "max_tokens": 1200}, timeout=40)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def check(symbol: str, momentum_pct: float | None = None, ask=None, news=None) -> dict:
    """{"symbol", "veto", "reason", "headlines": n, "by"}. `ask` and `news` are for tests."""
    try:
        heads = news if news is not None else headlines(symbol)
    except Exception as e:
        log.warning(f"  📰 {symbol}: headlines unavailable ({e}) — no veto")
        heads = []
    result = {"symbol": symbol, "veto": False, "reason": "No recent headlines.", "headlines": len(heads), "by": "none"}
    if heads:
        try:
            reply = (ask or _ask)(PROMPT.format(symbol=symbol, days=NEWS_DAYS, headlines="\n".join(heads)))
            got = parse(reply)
            if got:
                result.update(got, by="ai")
            else:
                result["reason"] = "The AI's answer couldn't be read, so no veto."
        except Exception as e:
            log.warning(f"  🤖 {symbol}: AI check failed ({e}) — no veto")
            result["reason"] = "The AI wasn't reachable, so no veto."
    _record({**result, "momentum": momentum_pct})
    return result


def _record(row: dict):
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), **row}) + "\n")
    except Exception as e:
        log.debug(f"veto log failed: {e}")
