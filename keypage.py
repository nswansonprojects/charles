"""
keypage.py — the paid setup's "Add your keys" page (owner 2026-11-08: "build it for the paid setup only").

Someone who paid for "Set it up for me" never needs a terminal: when their server is ready, the order page sends their
browser to https://<their server>/keys with a one-time code, they paste their Alpaca paper keys, and Charles starts.
The keys go from their browser to their own server only: charles-bot.xyz never sees them.

How it's locked:
  - Only a server the setup service made has the page. Its first-boot script passes CHARLES_KEYS_CODE_HASH (a SHA-256 of
    the code: the code itself is never in DigitalOcean's records) to install.sh, which runs `keypage.py arm <hash>`.
    A free install never runs that, so it has no page.
  - The code works for KEY_HOURS after the install, only over HTTPS, and only while .env has no Alpaca keys.
    Once the keys work, the page is closed for good.
  - The dashboard (a read-only sandbox) only checks the code and leaves a request, readable by root only, in
    data/requests. control.py (root) checks the code again, tries the keys on Alpaca's paper API, writes .env and
    starts Charles. The request file is deleted as soon as it's read, and nothing logs a key.
  - Then that browser is logged in to the dashboard, once, like a charles-login link.

    keypage.py arm <sha256 hex>     what install.sh runs on a paid-setup server
    keypage.py status               open, done or off
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

import state_io

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(ROOT, "data", "keypage.json")
ENV_FILE = os.path.join(ROOT, ".env")
BOT_LOG = os.path.join(ROOT, "logs", "bot.log")
KEY_HOURS = 26                       # the order page shows the link for 24 hours after the server is ready
LOGIN_MINUTES = 15                   # how long after the keys work that browser can still be logged in
HASH_RE = re.compile(r"[0-9a-f]{64}")
CODE_RE = re.compile(r"[A-Za-z0-9_-]{20,100}")
KEY_RE = re.compile(r"[A-Z0-9]{16,40}")
SECRET_RE = re.compile(r"[A-Za-z0-9/+=]{30,100}")
ATTEMPT_RE = re.compile(r"[0-9a-f]{16}")
ALPACA_PAPER = "https://paper-api.alpaca.markets"


def code_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def read(path: str | None = None) -> dict | None:
    try:
        with open(path or STATE) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and HASH_RE.fullmatch(str(d.get("hash", ""))) else None


def _write(d: dict, path: str | None = None):
    path = path or STATE
    if not os.path.exists(path):        # made root-only from the start: state_io keeps a file's mode
        os.makedirs(os.path.dirname(path), exist_ok=True)
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    state_io.write_json(path, d, indent=1)


def env_has_keys(env_file: str | None = None) -> bool:
    """Is there an Alpaca key in .env already? (Only whether, never what.)"""
    try:
        with open(env_file or ENV_FILE) as f:
            return any(re.match(r"\s*ALPACA_API_KEY\s*=\s*\S", line) for line in f)
    except OSError:
        return False


def arm(h: str, now: float | None = None, path: str | None = None, env_file: str | None = None) -> str:
    """install.sh on a paid-setup server. A re-run of the installer, or a server that has keys already, changes nothing."""
    if not HASH_RE.fullmatch(h or ""):
        raise ValueError("not a SHA-256")
    if read(path):
        return "The key page was already set up."
    if env_has_keys(env_file):
        return "This server has keys already, so it doesn't need the key page."
    now = time.time() if now is None else now
    _write({"hash": h, "expires": int(now + KEY_HOURS * 3600), "done": None, "last": None}, path)
    return f"The key page is open for {KEY_HOURS} hours."


def state(code: str, now: float | None = None, path: str | None = None) -> str:
    """What the page should show for this code: "open", "done" (its keys are in), or "closed"
    (wrong code, too late, or a server that never had the page)."""
    d = read(path)
    if not d or not CODE_RE.fullmatch(code or "") or not hmac.compare_digest(d["hash"], code_hash(code)):
        return "closed"
    if d.get("done"):
        return "done"
    now = time.time() if now is None else now
    return "open" if now < float(d.get("expires") or 0) else "closed"


def tidy(key, secret) -> tuple[str, str]:
    """The two keys as pasted, with stray spaces or quotes off, or ValueError saying what's wrong."""
    key = str(key or "").strip().strip("\"'").strip()
    secret = str(secret or "").strip().strip("\"'").strip()
    if not key or not secret:
        raise ValueError("Paste both keys: the API Key and the Secret Key.")
    if key.startswith("AK"):
        raise ValueError("That's a live-money key (it starts with AK). Use your paper account's keys: the API Key starts with PK.")
    if not KEY_RE.fullmatch(key):
        raise ValueError("That API Key doesn't look right. It's one word of capital letters and numbers, starting with PK.")
    if not SECRET_RE.fullmatch(secret):
        raise ValueError("That Secret Key doesn't look right. It's one long word of letters and numbers.")
    if key == secret:
        raise ValueError("Both boxes have the same key. The Secret Key is the longer one.")
    return key, secret


def outcome(attempt: str, path: str | None = None) -> dict | None:
    """What came of one try, once control.py has looked at it: {"ok", "message", "at"}."""
    d = read(path) or {}
    last = d.get("last") if isinstance(d.get("last"), dict) else None
    return last if last and last.get("attempt") == attempt else None


def login_due(code: str, given: set, now: float | None = None, path: str | None = None) -> bool:
    """May this browser be logged in now? Once per code, only within LOGIN_MINUTES of the keys working."""
    d = read(path)
    if not d or state(code, now, path) != "done" or d["hash"] in given:
        return False
    now = time.time() if now is None else now
    return now - float(d.get("done_ts") or 0) < LOGIN_MINUTES * 60


# ── The root side (control.py) ────────────────────────────────────────────────

def check_with_alpaca(key: str, secret: str, timeout: int = 15) -> str:
    """"ok", "rejected" or "unreachable": one read-only call to Alpaca's paper API."""
    base = os.environ.get("CHARLES_ALPACA_CHECK_URL") or ALPACA_PAPER
    req = urllib.request.Request(base.rstrip("/") + "/v2/account", headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return "ok" if r.status == 200 else "unreachable"
    except urllib.error.HTTPError as e:
        return "rejected" if e.code in (401, 403) else "unreachable"
    except (OSError, ValueError):
        return "unreachable"


def write_env(key: str, secret: str, env_file: str | None = None):
    """Set the two Alpaca lines in .env and keep every other line. Written in place: in charles-requests' sandbox .env is
    the one file outside data/ it may write, so it can't be swapped for a new file."""
    env_file = env_file or ENV_FILE
    try:
        with open(env_file) as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = ["# Trading Bot Credentials"]
    want, seen, out = {"ALPACA_API_KEY": key, "ALPACA_SECRET_KEY": secret}, set(), []
    for line in lines:
        name = line.split("=", 1)[0].strip()
        if name in want and "=" in line and not line.lstrip().startswith("#"):
            if name not in seen:
                out.append(f"{name}={want[name]}")
                seen.add(name)
            continue
        out.append(line)
    out += [f"{k}={v}" for k, v in want.items() if k not in seen]
    fd = os.open(env_file, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, "r+") as f:
        f.seek(0)
        f.write("\n".join(out) + "\n")
        f.truncate()
        f.flush()
        os.fsync(f.fileno())
    os.chmod(env_file, 0o600)


def start_bot(wait: float = 40, log_file: str | None = None, sleep=time.sleep) -> bool:
    """systemctl start charles, then True once its log says the dashboard part came up (as charles-start waits for)."""
    log_file = log_file or BOT_LOG
    try:
        before = os.path.getsize(log_file)
    except OSError:
        before = 0
    if subprocess.run(["systemctl", "start", "charles"], capture_output=True, timeout=60).returncode:
        return False
    end = time.monotonic() + wait
    while time.monotonic() < end:
        sleep(2)
        try:
            with open(log_file, "rb") as f:
                f.seek(before)
                if b"Dashboard" in f.read():
                    return True
        except OSError:
            pass
        if subprocess.run(["systemctl", "is-active", "-q", "charles"], timeout=30).returncode:
            return False
    return subprocess.run(["systemctl", "is-active", "-q", "charles"], timeout=30).returncode == 0


def apply(r: dict, now: float | None = None, path: str | None = None, env_file: str | None = None,
          check=None, start=None) -> str:
    """One "set-keys" request: check the code again, try the keys, save them, start Charles. Returns a sentence for the
    log (never a key), and leaves {"attempt", "ok", "message"} in data/keypage.json for the page."""
    now = time.time() if now is None else now
    check, start = check or check_with_alpaca, start or start_bot
    attempt = str(r.get("attempt") or "")
    if not ATTEMPT_RE.fullmatch(attempt):
        return "Ignored a key-page request with no attempt id."
    d = read(path)

    def answer(ok: bool, message: str, done: bool = False) -> str:
        if d:
            d["last"] = {"attempt": attempt, "ok": ok, "message": message, "at": int(now)}
            if done:
                d["done"], d["done_ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), now
            _write(d, path)
        return message

    st = state(str(r.get("code") or ""), now, path)
    if st != "open":
        return answer(False, "This key page has closed. Add your keys over SSH instead: nano ~/charles/.env, then charles-start."
                      if st == "closed" else "Your keys are already in.")
    if env_has_keys(env_file):
        return answer(False, "This server has Alpaca keys already, so the key page is closed. To change them: charles-keys over SSH.", done=True)
    try:
        key, secret = tidy(r.get("key"), r.get("secret"))
    except ValueError as e:
        return answer(False, str(e))
    said = check(key, secret)
    if said == "rejected":
        return answer(False, "Alpaca didn't accept those keys. Copy both again from your paper account (the API Key starts with PK), "
                             "or press Regenerate there and paste the new pair.")
    if said != "ok":
        return answer(False, "Couldn't reach Alpaca to check your keys. Try again in a minute.")
    try:
        write_env(key, secret, env_file)
    except OSError:
        return answer(False, "Your server couldn't save the keys. Add them over SSH instead: nano ~/charles/.env, then charles-start.")
    if start():
        return answer(True, "Your keys work and Charles is running.", done=True)
    return answer(True, "Your keys are saved, but Charles didn't start yet. The dashboard's Home says what's wrong.", done=True)


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "arm":
        try:
            print(arm(argv[1]))
        except ValueError as e:
            print(f"keypage: {e}")
            return 2
        return 0
    if argv == ["status"]:
        d = read()
        print("No key page on this server." if not d else "Key page: done, the keys are in." if d.get("done") else
              "Key page: open." if time.time() < float(d.get("expires") or 0) else "Key page: closed (too late).")
        return 0
    print("Usage: keypage.py arm <sha256 hex> | status")
    return 2


if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Run it as root (log in as root, or use sudo).")
        sys.exit(1)
    sys.exit(main(sys.argv[1:]))
