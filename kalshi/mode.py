"""mode.py — Charles or Evil Charles (owner 2026-10-26).

Evil Charles bets the opposite side of every bet Charles decides to place: Charles still picks the
market, the size and the levels (gates.py), then the bet goes on the other side for the same dollars.
Flipped from the dashboard's switch by anyone who can open it (owner's choice: public, no PIN).
The scan (betting.run) reads it each time, so a flip applies from the next scan; open bets stay as placed.
"""
import json
import os
from datetime import datetime, timezone

PATH = os.environ.get("KALSHI_MODE_FILE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "switch", "mode.json")   # the one folder kalshi-web may write
KEEP = 20                       # flips kept in the history


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
