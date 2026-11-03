"""
portfolio.py — the Custom Plan (owner 2026-11-03): the user's own tickers in blocks, in place of Charles's default plan
(the S&P 500 core and momentum picks from its own list). Pure functions, like allocator.py, so the rebalancer and the
backtest run the same rules.

A block is {"name", "type", "pct", "tickers"} (+ "picks" for momentum):
  long      Long term: every ticker held all the time, an equal share each (like SPY in the default plan).
  sector    Sector: each ticker held while it closes above its own trend line; below it, its share waits in T-bills.
  momentum  Momentum: the block's `picks` strongest tickers over the past year (skipping the latest month), only
            above their trend line, re-picked monthly like the default plan's picks. New picks get the news check.
`pct` is the block's share of the invested money; INVESTED (the whole-portfolio slider, 10-100%) is how much of the
account is invested at all. What no block uses waits in T-bills (SAFE). The safety switch and aggressive mode work as
in the default plan: a market below its trend line at a month's end moves everything to T-bills, and in aggressive
mode SPY in a long-term block is held as the 2x fund.

Charles never chooses tickers for a custom plan: the user adds every one. Its presets change only amounts
(PRESETS below). Custom plans can't be shared on the leaderboard (a list of small stocks there could be used to pump them).
"""
from __future__ import annotations

import hashlib
import json
import re

import allocator as al

TYPES = ("long", "sector", "momentum")
TYPE_NAMES = {"long": "Long term", "sector": "Sector", "momentum": "Momentum"}
MAX_BLOCKS = 6
MAX_TICKERS = 25                  # in one block
MAX_PICKS = 8
MAX_PRESETS = 8
NAME_MAX = 30
SYMBOL = re.compile(r"^[A-Z][A-Z0-9]{0,5}([.-][A-Z]{1,2})?$")
_NAME = re.compile(r"^[\w][\w &'+,./()-]*$")
INVESTED_MIN = 0.10

# Charles's presets (owner: "these are only configuring the amount invested ... not recommending tickers"). They keep
# the user's tickers and blocks and move only these numbers; nothing changes until Save.
PRESETS = [        # settings None: every number back to Charles's own (allocator.DEFAULTS); the user's blocks and tickers stay
    {"id": "default", "name": "Default plan", "about": "Charles's own numbers: all of your buying power invested, the 2× fund only while the market is calm. Your tickers stay.",
     "settings": None},
    {"id": "half", "name": "0.5× aggressive", "about": "Half of your buying power invested, the other half in T-bills, and no 2× fund. Your tickers stay.",
     "settings": {"INVESTED": 0.5, "AGGRESSIVE": False}},
    {"id": "aggressive", "name": "2× aggressive", "about": "All of your buying power invested, and the S&P 500 part in the 2× fund more often: whenever the market swings less than 25% a year. Your tickers stay.",
     "settings": {"INVESTED": 1.0, "AGGRESSIVE": True, "AGG_CORE": "SSO", "AGG_VOL_MAX": 0.25, "AGG_VOL_EXIT": 0.30}},
]


def check_name(name, what: str = "A name") -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{what} can't be empty")
    name = " ".join(name.split())
    if len(name) > NAME_MAX:
        raise ValueError(f"{what} can be at most {NAME_MAX} characters")
    if not _NAME.match(name):
        raise ValueError(f"{what} can use letters, numbers, spaces and & ' + , . / ( ) -")
    return name


def check_blocks(blocks, kinds: tuple = TYPES, symbol=SYMBOL, max_tickers: int = MAX_TICKERS, max_picks: int = MAX_PICKS,
                 what: str = "ticker") -> list | None:
    """The blocks exactly as given if they make a valid plan; ValueError saying why otherwise (never clamped).
    None means Charles's default plan. The crypto plan uses the same checks with its own kinds and coins (crypto_plan.check_blocks)."""
    if blocks is None:
        return None
    if not isinstance(blocks, list) or not blocks:
        raise ValueError("a custom plan needs at least one block")
    if len(blocks) > MAX_BLOCKS:
        raise ValueError(f"a plan can have at most {MAX_BLOCKS} blocks")
    names, seen, total = set(), {}, 0.0
    for b in blocks:
        if not isinstance(b, dict):
            raise ValueError("each block must be an object")
        extra = set(b) - {"name", "type", "pct", "tickers", "picks"}
        if extra:
            raise ValueError(f"a block has an unknown field {sorted(extra)[0]!r}")
        name = check_name(b.get("name"), "A block's name")
        if name != b["name"]:
            raise ValueError(f"the block name {b['name']!r} has extra spaces")
        if name.lower() in names:
            raise ValueError(f"two blocks are called {name}")
        names.add(name.lower())
        if b.get("type") not in kinds:
            raise ValueError(f"{name}: the type must be one of {', '.join(kinds)}")
        pct = b.get("pct")
        if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not 0.01 <= pct <= 1:
            raise ValueError(f"{name}: its share must be between 1% and 100%")
        total += pct
        tickers = b.get("tickers")
        if not isinstance(tickers, list) or not tickers:
            raise ValueError(f"{name}: add at least one {what}")
        if len(tickers) > max_tickers:
            raise ValueError(f"{name}: at most {max_tickers} {what}s in a block")
        for t in tickers:
            if not isinstance(t, str) or not symbol.match(t):
                raise ValueError(f"{name}: {str(t)[:12]!r} doesn't look like a {what}")
            if t in seen:
                raise ValueError(f"{t} is in two blocks ({seen[t]} and {name})")
            seen[t] = name
        if b["type"] == "momentum":
            n = b.get("picks")
            if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= min(max_picks, len(tickers)):
                raise ValueError(f"{name}: picks must be a whole number from 1 to {min(max_picks, len(tickers))}")
        elif "picks" in b:
            raise ValueError(f"{name}: only a momentum block has picks")
    if total > 1 + 1e-9:
        raise ValueError(f"the blocks add up to {total * 100:.0f}% of the invested money; at most 100%")
    return blocks


def check_presets(presets, validate) -> list:
    """The user's own named presets: [{"name", "settings"}], each settings checked by `validate` (strict)."""
    if not isinstance(presets, list):
        raise ValueError("PRESETS must be a list")
    if len(presets) > MAX_PRESETS:
        raise ValueError(f"at most {MAX_PRESETS} saved presets")
    names = set()
    for p in presets:
        if not isinstance(p, dict) or set(p) != {"name", "settings"}:
            raise ValueError("a preset is a name and its settings")
        name = check_name(p["name"], "A preset's name")
        if name != p["name"] or name.lower() in names:
            raise ValueError(f"the preset name {p['name']!r} is taken or has extra spaces")
        names.add(name.lower())
        if not isinstance(p["settings"], dict) or not p["settings"] or {"PRESETS", "SELF_TUNE"} & set(p["settings"]):
            raise ValueError(f"{name}: a preset holds plan settings only")
        validate(p["settings"])
    return presets


def symbols(s: dict) -> list[str]:
    """Every ticker the custom plan can hold, in block order."""
    return [t for b in s.get("BLOCKS") or [] for t in b["tickers"]]


def signature(s: dict) -> str:
    """Changes when the blocks or INVESTED change, so the next check rebuilds the holdings at once."""
    raw = json.dumps({"b": s.get("BLOCKS"), "i": s.get("INVESTED", 1.0)}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def choose(closes: dict[str, list[float]], prev: dict | None, repick: bool, s: dict, veto=None,
           rank_always: bool = True) -> dict:
    """What each block holds: {"held": {block: [tickers]}, "vetoes", "ranked"}. Between re-picks the holdings stay as
    they were (`prev`), apart from tickers a block no longer has. `veto(symbol, momentum)` is the news check."""
    held, vetoes, ranked = {}, [], []
    prev = prev or {}
    for b in s["BLOCKS"]:
        name, tickers = b["name"], b["tickers"]
        mine = {k: closes[k] for k in tickers if closes.get(k)}
        rows = al.rank(mine, s) if b["type"] == "momentum" and (repick or rank_always) else []
        for i, r in enumerate(rows):
            r["rank"], r["block"] = i + 1, name
        ranked += rows
        if not repick and name in prev:
            held[name] = [t for t in prev[name] if t in tickers]
            continue
        if b["type"] == "long":
            held[name] = [t for t in tickers if t in mine]
        elif b["type"] == "sector":
            keep = []
            for t in tickers:
                avg = al.sma(mine.get(t, []), s["TREND_DAYS"])
                if avg is not None and mine[t][-1] > avg:
                    keep.append(t)
            held[name] = keep
        else:
            before, picks = set(prev.get(name, [])), []
            for r in rows:
                if len(picks) >= b["picks"]:
                    break
                if not r["eligible"]:
                    continue
                if veto and r["symbol"] not in before:
                    v = veto(r["symbol"], r["momentum"])
                    if v and v.get("veto"):
                        vetoes.append({"symbol": r["symbol"], "reason": v.get("reason", ""), "rank": r["rank"], "block": name})
                        continue
                picks.append(r["symbol"])
            held[name] = picks
    ranked.sort(key=lambda r: r["momentum"], reverse=True)
    return {"held": held, "vetoes": vetoes, "ranked": ranked}


def targets(mode: str, held: dict, s: dict) -> dict[str, float]:
    """Target weights (fractions of the account) for the custom plan; the rest waits in T-bills."""
    room = 1 - s.get("CASH_PCT", 0.0)
    if mode not in (al.INVEST, al.AGGRESSIVE):
        return {s["SAFE"]: round(room, 4)}
    out = {}
    for b in s["BLOCKS"]:
        share = s.get("INVESTED", 1.0) * b["pct"]
        slot = share / (b["picks"] if b["type"] == "momentum" else len(b["tickers"]))
        for t in held.get(b["name"], []):
            if mode == al.AGGRESSIVE and b["type"] == "long" and t == s["CORE"]:
                t = s["AGG_CORE"]
            out[t] = out.get(t, 0.0) + slot
    out = {k: round(v, 4) for k, v in out.items() if v > 0}
    rest = round(room - sum(out.values()), 4)
    if rest > 0.0005:
        out[s["SAFE"]] = round(out.get(s["SAFE"], 0.0) + rest, 4)
    return out


def block_of(symbol: str, s: dict) -> dict | None:
    for b in s.get("BLOCKS") or []:
        if symbol in b["tickers"] or (b["type"] == "long" and symbol == s.get("AGG_CORE") and s.get("CORE") in b["tickers"]):
            return b
    return None


def reason(order: dict, d: dict, state: dict, s: dict) -> str:
    """Why this order, in one plain sentence, for a custom plan."""
    sym, side, mode = order["symbol"], order["side"], d["mode"]
    tw = round(d["targets"].get(sym, 0) * 100, 1)
    if sym == s["SAFE"]:
        if mode == al.SAFETY:
            return "Safety mode: SPY ended the month below its trend line, so the money waits in T-bills until it recovers."
        if side == "sell":
            return "Back to investing: T-bills sold for your blocks." if d["switched"] else "Less waits in T-bills now: your blocks use it."
        return "What your blocks don't use waits in T-bills (short US government loans)."
    b = block_of(sym, s)
    if side == "sell" and order["all"]:
        if mode == al.SAFETY:
            return "Safety mode: SPY ended the month below its trend line, so Charles moved the money to T-bills."
        if sym == s.get("AGG_CORE") and b:
            return "Leaving aggressive mode: the 2× fund goes back to plain SPY."
        if sym == s["CORE"] and mode == al.AGGRESSIVE and b:
            return "Swapped for the 2× S&P 500 fund: aggressive mode is on."
        if not b:
            return "Not in your plan anymore."
        if b["type"] == "sector":
            return f"{b['name']}: {sym} closed below its {s['TREND_DAYS']}-day average, so its share waits in T-bills."
        if b["type"] == "momentum":
            r = next((x for x in d.get("ranked") or [] if x["symbol"] == sym), None)
            why = "it fell below its trend line" if r and not r["above_trend"] else f"it's now ranked {r['rank']}" if r else "it no longer qualifies"
            return f"{b['name']}: {sym} left the block's top {b['picks']} ({why})."
        return f"{b['name']}: {sym} sold."
    if not b:
        return f"Rebalance: {sym} {'topped up' if side == 'buy' else 'trimmed'} to its {tw}% target."
    if side == "buy" and not order.get("held"):
        if sym == s.get("AGG_CORE"):
            return f"{b['name']}: aggressive mode is on, so the S&P 500 part is held as the 2× fund ({tw}% of the account)."
        if b["type"] == "long":
            return f"{b['name']} (long term): {sym} is held all the time, {tw}% of the account."
        if b["type"] == "sector":
            return f"{b['name']} (sector): {sym} is above its {s['TREND_DAYS']}-day average, so it's held, {tw}% of the account."
        r = next((x for x in d.get("ranked") or [] if x["symbol"] == sym), None)
        up = f", up {r['momentum']:.0f}% over the past year" if r else ""
        return f"{b['name']} (momentum): {sym} is one of the block's {b['picks']} strongest{up}, {tw}% of the account."
    return f"{b['name']}: {sym} {'topped up' if side == 'buy' else 'trimmed'} to its {tw}% target."


def describe(mode: str | None, held: dict, s: dict) -> str:
    """One plain sentence for the dashboard."""
    if not mode:
        return "Waiting for enough price history to decide."
    if mode == al.SAFETY:
        return "Safety mode: SPY ended the month below its trend line, so the money waits in T-bills until a month ends back above it."
    inv = round(s.get("INVESTED", 1.0) * 100)
    parts = [f"{b['name']} {round(b['pct'] * s.get('INVESTED', 1.0) * 100)}%" for b in s["BLOCKS"]]
    lead = "Aggressive mode, your own plan: " if mode == al.AGGRESSIVE else "Your own plan: "
    return lead + ", ".join(parts) + (f", {inv}% invested in all." if inv < 100 else ".")


def from_default(s: dict) -> list[dict]:
    """The default plan's numbers as blocks, so a custom plan starts from what the user has (Charles's own list stays
    out: the user adds their own momentum tickers)."""
    return [{"name": "S&P 500", "type": "long", "pct": round(s["CORE_PCT"], 4), "tickers": [s["CORE"]]}]
