"""
terms.py — the Charles Terms of Service and License Agreement (LICENSE.md, with the Privacy Policy and the Risk Disclosure that
are part of it): who accepted which version, and when (owner 2026-11-03: "a user license agreement ... that a user must accept
before installing and setting up themselves or having us set up for them"; version 2.0, owner 2026-11-07: "make sure i am within
legal and regulatory guidelines, i want to sell charles"). The wording comes from ops/legal/ (render.py), the version from here.

It's accepted before installing (install.sh asks, or the paid setup's checkbox does) and again before live trading
(live.py). The record is data/terms_accepted.json; every acceptance is also added to data/terms_log.jsonl, the
acceptance log. A new VERSION puts live trading on hold until it's accepted (rebalancer.run asks live.problem()).

    charles-terms                   show the short version and ask (python terms.py)
    charles-terms --status          what's on file
    python terms.py --check         exit 0 when the current version is on file (install.sh: don't ask twice)
    python terms.py --record HOW    record an acceptance made elsewhere (install.sh after its own prompt)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

import state_io

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
VERSION = "2.1"
EFFECTIVE = "November 9, 2026"
URL = "https://charles-bot.xyz/terms.html"
PRIVACY_URL = "https://charles-bot.xyz/privacy.html"
RISK_URL = "https://charles-bot.xyz/risk.html"
# The agreement's own short version (LICENSE.md, and the installer's prompt): keep the three the same.
SHORT = [
    "You must be 18 or older (or the age of majority where you live) and allowed to trade where you live.",
    "Charles is free to use. You run it on your own server, with your own accounts and keys. We never hold your money or your keys.",
    "It trades paper (practice) money by default. Live trading happens only if you turn it on yourself.",
    "Charles is a tool, not investment advice. It promises no gains, and you can lose money, including everything you put in.",
    "You're responsible for your accounts, your trades and your taxes. The software comes as is, with no warranty.",
    "Disputes go to individual arbitration, not class actions (section 18), unless you opt out within 30 days.",
]
HOW = ("installer", "charles-terms", "setup service", "charles-live")


def _file(data_dir: str | None) -> str:
    return os.path.join(data_dir or DATA, "terms_accepted.json")


def accepted(data_dir: str | None = None) -> dict | None:
    """What's on file: {"version", "at", "how", "age_confirmed"}, or None."""
    try:
        with open(_file(data_dir)) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and isinstance(d.get("version"), str) else None


def current(data_dir: str | None = None) -> bool:
    """True when this install accepted the agreement's current version, as an adult."""
    d = accepted(data_dir)
    return bool(d) and d.get("version") == VERSION and d.get("age_confirmed") is True


def record(how: str, data_dir: str | None = None, now: datetime | None = None) -> dict:
    """Write the acceptance and add it to the log. `how` says where it was given."""
    if how not in HOW:
        raise ValueError(f"unknown way to accept: {how}")
    now = now or datetime.now(timezone.utc)
    row = {"version": VERSION, "at": now.isoformat(timespec="seconds"), "how": how, "age_confirmed": True}
    folder = data_dir or DATA
    os.makedirs(folder, exist_ok=True)
    state_io.write_json(_file(data_dir), row, indent=1)
    with open(os.path.join(folder, "terms_log.jsonl"), "a") as f:
        f.write(json.dumps(row) + "\n")
    return row


def status_line(data_dir: str | None = None) -> str:
    d = accepted(data_dir)
    if not d:
        return f"Not accepted yet. Run charles-terms to read and accept it ({URL})."
    if d.get("version") != VERSION:
        return f"You accepted version {d['version']}; version {VERSION} is new. Run charles-terms to accept it."
    return f"Version {VERSION} accepted on {d['at'][:10]} ({d.get('how')})."


def ask(how: str = "charles-terms", data_dir: str | None = None, read=input, say=print) -> bool:
    """The terminal prompt: the short version, the link, then the two answers. Records on yes; changes nothing on no."""
    say(f"\nCharles Terms of Service and License Agreement (version {VERSION}, {EFFECTIVE})")
    for line in SHORT:
        say(f"  • {line}")
    say(f"\nRead all of it: {URL}  (also LICENSE.md in {ROOT})")
    say(f"It includes the Risk Disclosure ({RISK_URL}) and the Privacy Policy ({PRIVACY_URL}).\n")
    if read("Are you 18 or older (or the age of majority where you live)? [y/N] ").strip().lower() not in ("y", "yes"):
        say("Charles is only for adults. Nothing was changed.")
        return False
    if read('Type "I agree" to accept the Terms and License Agreement: ').strip().lower().rstrip(".") != "i agree":
        say("Not accepted. Nothing was changed.")
        return False
    record(how, data_dir)
    say(f"✅ Accepted version {VERSION}. Thank you.")
    return True


def main(argv: list[str], data_dir: str | None = None) -> int:
    if argv[:1] == ["--status"]:
        print(status_line(data_dir))
        return 0
    if argv == ["--check"]:
        return 0 if current(data_dir) else 1
    if argv[:1] == ["--record"] and len(argv) == 2:
        record(argv[1], data_dir)
        print(status_line(data_dir))
        return 0
    if argv:
        print("Usage: charles-terms [--status]")
        return 2
    if current(data_dir):
        print(status_line(data_dir))
        return 0
    return 0 if ask(data_dir=data_dir) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
