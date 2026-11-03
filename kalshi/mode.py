"""mode.py — which bot the account follows: Charles or Evil Charles (owner 2026-10-26, 2026-11-03).

Evil Charles bets the opposite side of every bet Charles decides to place: Charles still picks the
market and the size, then the bet goes on the other side for the same dollars. Since 2026-11-03 both
bots bet on every scan in their own books (betting.BOTS), whatever the switch says; the switch only
picks whose bets the account copies. Flipped from the dashboard's switch by anyone who can open it
(owner's choice: public, no PIN). The scan (betting.run) reads it each time, so a flip applies from the
next scan; open bets stay as placed. The flips are kept so the chart can color each stretch of the account.
"""
import json
import os
from datetime import datetime, timezone

PATH = os.environ.get("KALSHI_MODE_FILE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "switch", "mode.json")   # the one folder kalshi-web may write
KEEP = 1000                     # flips kept in the history: the chart colors each stretch by who the account followed


def load(path: str | None = None) -> dict:
    try:
        with open(path or PATH) as f:
            st = json.load(f)
    except (FileNotFoundError, ValueError):
        st = {}
    return {"evil": bool(st.get("evil")), "since": st.get("since"), "history": st.get("history", [])}


def is_evil(path: str | None = None) -> bool:
    return load(path)["evil"]


def set_evil(evil: bool, now: datetime | None = None, path: str | None = None) -> dict:
    path = path or PATH
    st = load(path)
    if st["evil"] == bool(evil):
        return st
    ts = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    st = {"evil": bool(evil), "since": ts, "history": (st["history"] + [{"ts": ts, "evil": bool(evil)}])[-KEEP:]}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(st, f, indent=1)
    os.replace(path + ".tmp", path)
    return st


def followed_at(ts: str, st: dict | None = None) -> str:
    """"evil" or "charles": who the account followed at ISO time `ts` (UTC, like every saved time), from the saved flips.
    Before the first flip, the opposite of that flip; with no flips at all, whatever the switch says now."""
    st = st if st is not None else load()
    last, hist = None, st.get("history") or []
    for h in hist:                          # oldest first, as saved
        if h["ts"] <= ts:
            last = h
    if last is None:
        return ("charles" if hist[0]["evil"] else "evil") if hist else ("evil" if st.get("evil") else "charles")
    return "evil" if last["evil"] else "charles"
