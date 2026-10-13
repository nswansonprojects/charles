"""Find bets whose combined payout is guaranteed by the markets' own rules.

Pure functions over the /events data. Each "relation" is a set of legs
(ticker, side) that together pay at least `payout` dollars per contract set
however the events turn out, if the rules relationship holds:

- me_all_no: event flagged mutually exclusive (at most one market says YES).
  Buying NO on all k markets pays at least k-1.
- strike_ladder: same question, same close, different thresholds. If "above
  200" is YES then "above 100" is YES, so YES(100) + NO(200) pays at least 1.
- date_ladder: same "before <date>" question at different deadlines. If it
  happens before the early deadline it happens before the late one, so
  YES(late) + NO(early) pays at least 1.
"""
import math
import re
from collections import defaultdict

# Base confidence that the rules relationship really holds (data/wording risk).
CONFIDENCE = {"me_all_no": 0.99, "strike_ladder": 0.98, "date_ladder": 0.95}

_MONTHS = r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_NUM = r"[-+]?\$?\d[\d,]*(\.\d+)?%?"


def norm_rules(text: str, strip_dates: bool = False) -> str:
    s = (text or "").lower()
    if strip_dates:
        s = re.sub(_MONTHS, "<m>", s)
    return re.sub(_NUM, "#", s).strip()


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def top_ask(m: dict, side: str):
    p = _f(m.get(f"{side}_ask_dollars"))
    return p if p is not None and 0 < p < 1 else None


def _leg(m, side):
    return {"ticker": m["ticker"], "side": side, "title": m.get("title", ""), "sub": m.get("yes_sub_title") or "",
            "rules": m.get("rules_primary", ""), "close_time": m.get("close_time", "")}


def _rel(kind, legs, payout, e, why):
    return {"type": kind, "legs": legs, "payout": payout,
            "event": e.get("event_ticker"), "series": e.get("series_ticker"),
            "category": e.get("category") or "Other", "title": e.get("title", ""),
            "close_time": max(l["close_time"] for l in legs), "why": why}


# "before Jan 1, 2027", "by the end of 2026", "by December 31" — a cumulative deadline
_DEADLINE = re.compile(r"\b(before|by)\s+(the\s+end\s+of\s+)?(" + _MONTHS[2:] + r"|\d{4}\b)")

_DATE = re.compile(_MONTHS + r"(\s+\d{1,2})?(,?\s+\d{4})?|\b\d{4}\b")


def only_deadline_differs(rules_a: str, rules_b: str) -> bool:
    """True if the two rules differ in exactly one date and it follows 'before'/'by'
    (so a different start date or reporting period never counts as a ladder)."""
    a, b = rules_a.lower(), rules_b.lower()
    da, db = list(_DATE.finditer(a)), list(_DATE.finditer(b))
    if len(da) != len(db):
        return False
    diffs = [x for x, y in zip(da, db) if x.group(0) != y.group(0)]
    if len(diffs) != 1:
        return False
    return bool(re.search(r"\b(before|by)\s+(the\s+end\s+of\s+)?$", a[:diffs[0].start()]))


_UP = ("greater", "greater_or_equal")
_DOWN = ("less", "less_or_equal")
# Kalshi's strike_type labels aren't reliable ("exactly 4" can be labelled "less"),
# so the rules wording must agree before a market joins a threshold ladder.
_UP_WORDS = re.compile(r"\b(above|at least|more than|greater than|or more|or above|exceeds?|over)\b")
_DOWN_WORDS = re.compile(r"\b(below|less than|under|at most|or fewer|or less|or below)\b")
_NOT_LADDER = re.compile(r"\b(exactly|between)\b")


def ladder_strike(m: dict):
    """The threshold if `m` is a one-sided above/below market, else None."""
    st, rules = m.get("strike_type"), (m.get("rules_primary") or "").lower()
    if _NOT_LADDER.search(rules):
        return None
    if st in _UP and m.get("cap_strike") is None and _UP_WORDS.search(rules) and not _DOWN_WORDS.search(rules):
        return _f(m.get("floor_strike"))
    if st in _DOWN and m.get("floor_strike") is None and _DOWN_WORDS.search(rules) and not _UP_WORDS.search(rules):
        return _f(m.get("cap_strike"))
    return None


def relations(events: list[dict]) -> list[dict]:
    out = []
    by_series = defaultdict(list)
    for e in events:
        ms = [m for m in e.get("markets", []) if m.get("status") == "active"
              and m.get("market_type", "binary") == "binary"]
        for m in ms:
            by_series[e.get("series_ticker")].append((e, m))

        if e.get("mutually_exclusive") and len(ms) >= 2:
            out.append(_rel("me_all_no", [_leg(m, "no") for m in ms], len(ms) - 1, e,
                            f"At most one of {len(ms)} outcomes can be YES, so NO on all pays at least {len(ms) - 1}."))

        groups = defaultdict(list)
        for m in ms:
            k = ladder_strike(m)
            if k is not None:
                groups[(m["strike_type"], m.get("close_time"), norm_rules(m.get("rules_primary")))].append((k, m))
        for (st, _, _), g in groups.items():
            g.sort(key=lambda x: x[0])
            for i, (ka, a) in enumerate(g):
                for kb, b in g[i + 1:]:
                    if ka == kb:
                        continue
                    # implier: the stricter market; implied: the looser one
                    implier, implied = (b, a) if st in _UP else (a, b)
                    out.append(_rel("strike_ladder", [_leg(implied, "yes"), _leg(implier, "no")], 1, e,
                                    f"'{implier.get('yes_sub_title') or implier['ticker']}' YES forces "
                                    f"'{implied.get('yes_sub_title') or implied['ticker']}' YES."))

    for series, items in by_series.items():
        groups = defaultdict(list)
        for e, m in items:
            rules = m.get("rules_primary") or ""
            if not _DEADLINE.search(rules.lower()):
                continue
            key = (m.get("strike_type"), m.get("floor_strike"), m.get("cap_strike"),
                   norm_rules(m.get("yes_sub_title"), strip_dates=True), norm_rules(rules, strip_dates=True))
            groups[key].append((m.get("close_time") or "", e, m))
        for g in groups.values():
            g.sort(key=lambda x: x[0])
            for i, (ca, ea, a) in enumerate(g):
                for cb, eb, b in g[i + 1:]:
                    if ca == cb or not only_deadline_differs(a.get("rules_primary") or "", b.get("rules_primary") or ""):
                        continue
                    out.append(_rel("date_ladder", [_leg(b, "yes"), _leg(a, "no")], 1, eb,
                                    f"If it happens before {ca[:10]} it also happens before {cb[:10]}."))
    return out


def fee(contracts: float, price: float, rate: float = 0.07) -> float:
    """Kalshi taker fee for one order, rounded up to the cent."""
    return math.ceil(round(rate * contracts * price * (1 - price) * 100, 6)) / 100


def quick_edge(rel: dict, by_ticker: dict) -> float | None:
    """Per-set profit at top-of-book prices after fees, or None if a leg has no offer."""
    cost = 0.0
    for l in rel["legs"]:
        p = top_ask(by_ticker[l["ticker"]], l["side"])
        if p is None:
            return None
        cost += p + 0.07 * p * (1 - p)  # unrounded: fees round per order, not per contract
    return rel["payout"] - cost
