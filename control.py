"""
control.py — changes the dashboard asks for (owner's Phase 3 plan, 2026-10-29; design §3 and §5).

The dashboard's sandbox can write only data/requests/. A logged-in POST drops a small request file there;
charles-requests.path notices it and runs `control.py run` as root (charles-requests.service), which checks
each request, carries it out, logs it to data/control.jsonl and posts a notice on the dashboard. Pause and resume
happen here. Update, Restore and settings changes need more than this sandbox allows, so they're checked here and
queued for charles-updater.service (updates.py).

    python control.py run          carry out every waiting request (what the service runs)
    charles-pause / charles-resume  the same from SSH
    python control.py status

Pause stops every new stock order, buys and sells, until resume. The daily check still runs and keeps the
plan up to date, and the first check after resume places what it needs. Pause never sells anything: to
sell everything, use Alpaca's own app.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import sys
import time
from datetime import datetime, timezone

import state_io

ROOT = os.path.dirname(os.path.abspath(__file__))
REQ_DIR = os.path.join(ROOT, "data", "requests")
PAUSE_FILE = os.path.join(ROOT, "data", "paused.json")
LOG_FILE = os.path.join(ROOT, "data", "control.jsonl")
ACTIONS = ("pause", "resume", "update", "restore", "check-updates", "apply-settings", "undo-settings", "backtest-settings", "copy-config",
           "set-keys", "fetch-pause", "fetch-resume", "fetch-ticker")
MAX_REQUEST = 65536                      # bytes; most are a few dozen, a custom plan with its saved presets a few thousand

log = logging.getLogger(__name__)


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def paused() -> dict | None:
    """{"since", "by"} while Charles is paused, else None. A pause file that can't be read counts as paused:
    when the owner's wish is unclear, no orders (charles-resume writes a good file)."""
    try:
        with open(PAUSE_FILE) as f:
            d = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"since": None, "by": "a damaged data/paused.json"}
    if not isinstance(d, dict):
        return {"since": None, "by": "a damaged data/paused.json"}
    return {"since": d.get("since"), "by": d.get("by")} if d.get("paused") else None


def request(action: str, by: str, now: datetime | None = None, **extra) -> str:
    """The dashboard's side: leave a request for the root service. Returns the file's name."""
    if action not in ACTIONS:
        raise ValueError(f"unknown request: {action}")
    now = _now(now)
    name = f"{now:%Y%m%dT%H%M%S}-{secrets.token_hex(4)}-{action}.json"
    path = os.path.join(REQ_DIR, name)
    if action == "set-keys":            # it carries keys: root-only from the first byte (state_io keeps an existing file's mode)
        os.makedirs(REQ_DIR, exist_ok=True)
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    state_io.write_json(path, {**extra, "action": action, "by": by, "at": now.isoformat()})
    return name


def apply(action: str, by: str, now: datetime | None = None) -> str:
    """Carry out one request, log it and tell the owner. Returns what happened, in a sentence."""
    now = _now(now)
    was = paused()
    if action == "pause" and was:
        said = "Charles was already paused."
    elif action == "resume" and not was:
        said = "Charles wasn't paused."
    else:
        state_io.write_json(PAUSE_FILE, {"paused": action == "pause", "since": now.isoformat(), "by": by})
        said = ("⏸️ Charles is paused. No new stock orders until you resume (on the dashboard, or charles-resume "
                "over SSH). Nothing was sold." if action == "pause" else
                "▶️ Charles resumed. The next daily check (10:00 AM ET on weekdays) places any orders the plan needs.")
        try:
            import alerts
            alerts.control(f"{said}\nAsked from {by}.")
        except Exception as e:
            log.warning(f"Notice: {e}")
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps({"at": now.isoformat(), "action": action, "by": by, "result": said}) + "\n")
    return said


def dispatch(r: dict) -> str:
    """One checked-in-shape request: pause and resume happen now, the rest are queued for the updater."""
    if r["action"] in ("pause", "resume"):
        return apply(r["action"], "the dashboard")
    if r["action"] == "set-keys":
        return set_keys(r)
    if r["action"].startswith("fetch-"):
        return fetch(r)
    import updates
    said = updates.queue(r["action"], "the dashboard", version=r.get("version"), confirm=r.get("confirm") is True,
                         settings=r.get("settings"), reason=r.get("reason") or "", config=r.get("config"),
                         bot=r.get("bot") or "stocks", source=r.get("source") or "the dashboard")
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps({"at": _now(None).isoformat(), "action": r["action"], "by": "the dashboard",
                            "version": r.get("version"), "result": said}) + "\n")
    return said


def fetch(r: dict) -> str:
    """Charles Fetch's pause and ticker (fetch_control.py): practice money only, so nothing here can reach an account."""
    import fetch_control
    market = r.get("market") if r.get("market") in fetch_control.MARKETS else None
    if not market:
        said = "Unknown Fetch market."
    elif r["action"] == "fetch-ticker":
        said = fetch_control.set_ticker(market, str(r.get("product") or ""))
    else:
        said = fetch_control.set_paused(market, r["action"] == "fetch-pause", "the dashboard")
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps({"at": _now(None).isoformat(), "action": r["action"], "market": market, "product": r.get("product"),
                            "by": "the dashboard", "result": said}) + "\n")
    return said


def set_keys(r: dict) -> str:
    """The paid setup's key page (keypage.py): check the code, try the keys, save them, start Charles. Only the outcome is
    logged, never a key."""
    import keypage
    said = keypage.apply(r)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps({"at": _now(None).isoformat(), "action": "set-keys", "by": "the key page", "result": said}) + "\n")
    if said.startswith("Your keys"):
        try:
            import alerts
            alerts.control(f"🔑 {said}\nAdded on the key page from the paid setup.")
        except Exception as e:
            log.warning(f"Notice: {e}")
    return said


def run(wait: float = 10.0) -> list[str]:
    """Carry out every waiting request, oldest first. Each file is removed once read; anything that isn't a
    known request is logged and dropped. A request is written as a temp file and then renamed, so while a
    fresh temp file is there this waits (up to `wait` seconds) for it; older leftovers are removed, since
    any file left in the folder would wake the service again (charles-requests.path)."""
    out, deadline = [], time.time() + wait
    while True:
        try:
            names = sorted(os.listdir(REQ_DIR))
        except FileNotFoundError:
            return out
        for name in [n for n in names if n.endswith(".json")]:
            path = os.path.join(REQ_DIR, name)
            try:
                with open(path) as f:
                    raw = f.read(MAX_REQUEST + 1)
                r = json.loads(raw) if len(raw) <= MAX_REQUEST else None
            except (OSError, ValueError):
                r = None
            _remove(path)
            if not isinstance(r, dict) or r.get("action") not in ACTIONS:
                log.warning(f"ignored request {name}")
                continue
            out.append(dispatch(r))
        writing = False
        for name in [n for n in names if not n.endswith(".json")]:
            path = os.path.join(REQ_DIR, name)
            try:
                age = time.time() - os.path.getmtime(path)
            except OSError:
                continue
            if age > 60:
                _remove(path)
            else:
                writing = True
        if not writing or time.time() >= deadline:
            return out
        time.sleep(0.5)


def _remove(path: str):
    try:
        os.remove(path)
    except OSError:
        pass


def main(argv: list[str]) -> int:
    if argv == ["status"]:
        p = paused()
        print(f"⏸️ Paused since {p['since']} (by {p['by']})." if p else "▶️ Running: Charles places orders as planned.")
        return 0
    if argv == ["run"]:
        for said in run():
            print(said)
        return 0
    if len(argv) == 1 and argv[0] in ("pause", "resume"):
        print(apply(argv[0], "SSH"))
        return 0
    print("Usage: control.py pause | resume | status | run")
    return 2


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if os.geteuid() != 0:
        print("Run it as root (log in as root, or use sudo).")
        sys.exit(1)
    sys.exit(main(sys.argv[1:]))
