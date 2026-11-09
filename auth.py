"""
auth.py — the dashboard login (owner's Phase 3 plan, 2026-10-29; charles-audit/phase3-design.md §3).

No passwords and no account with us. Over SSH, `charles-login` prints a link that works once, within 10
minutes. Opening it and tapping "Log in" keeps that browser logged in for 30 days, renewed while it's used.

    charles-login                a login link (the first run turns the login on, in public mode)
    charles-login --private      only a logged-in browser can see the dashboard (new installs)
    charles-login --public       anyone can look; changes (Evil Charles, the leaderboard, later Pause and
                                 Update) need the login
    charles-login --off          no login: anyone can see and change things (as before the login existed)
    charles-login --logout-all   log out every browser
    charles-login --status

data/auth.json (600) holds the access mode, the secret that signs session cookies, and the hashes of
links not used yet. Only this command writes it. With no file, the dashboard is open as it always was.
The dashboard's sandbox is read-only, so it remembers in memory which links it has accepted, and a link
expires after 10 minutes anyway. Over plain http:// someone on the same network could copy a session
cookie, which is why a real install gets HTTPS first (design §3).
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from http.cookies import CookieError, SimpleCookie

import state_io

ROOT = os.path.dirname(os.path.abspath(__file__))
AUTH_FILE = os.environ.get("CHARLES_AUTH_FILE") or os.path.join(ROOT, "data", "auth.json")
MODES = {"private": "only a logged-in browser can see it",
         "public": "anyone can look; changes need the login",
         "open": "no login: anyone can see and change things"}
LINK_SECONDS = 10 * 60
SESSION_SECONDS = 30 * 86400
RENEW_AFTER = 24 * 3600            # a session used a day or more after it began is renewed for 30 days from then
_lock = threading.Lock()


def load(path: str | None = None) -> dict | None:
    """The login settings, or None when the login was never turned on. A damaged file locks the dashboard
    (private, no valid session) until charles-login writes a new one: it never fails open."""
    path = path or AUTH_FILE
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            cfg = json.load(f)
        if isinstance(cfg, dict) and cfg.get("access") in MODES and len(str(cfg.get("secret") or "")) >= 32:
            return cfg
    except (OSError, ValueError):
        pass
    return {"access": "private", "secret": None, "links": []}


def access(cfg: dict | None) -> str:
    return cfg["access"] if cfg else "open"


def _sign(secret: str, msg: str) -> str:
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_link(cfg: dict, now: float | None = None) -> str:
    """Add a one-time link to cfg (only its hash is kept; expired ones are dropped) and return its token."""
    now = time.time() if now is None else now
    token = secrets.token_urlsafe(24)
    cfg["links"] = [x for x in cfg.get("links", []) if x.get("expires", 0) > now] + [
        {"hash": _hash(token), "expires": int(now + LINK_SECONDS)}]
    return token


def link_ok(cfg: dict | None, token: str, used: set, now: float | None = None) -> bool:
    """Did charles-login make this link, under 10 minutes ago, and it isn't used yet?"""
    if not cfg or not cfg.get("secret") or not token:
        return False
    now = time.time() if now is None else now
    h = _hash(token)
    return h not in used and any(hmac.compare_digest(str(x.get("hash", "")), h) and x.get("expires", 0) > now
                                 for x in cfg.get("links", []))


def take_link(cfg: dict | None, token: str, used: set, now: float | None = None) -> bool:
    """link_ok(), and the link can't be used again."""
    with _lock:
        if not link_ok(cfg, token, used, now):
            return False
        used.add(_hash(token))
        return True


def session(cfg: dict, now: float | None = None) -> str:
    """A new session cookie value: id.expiry.signature."""
    now = time.time() if now is None else now
    sid, exp = secrets.token_urlsafe(16), int(now + SESSION_SECONDS)
    return f"{sid}.{exp}.{_sign(cfg['secret'], f's|{sid}|{exp}')}"


def check(cfg: dict | None, value: str | None, now: float | None = None) -> str | None:
    """The session id of a valid, unexpired session cookie, else None."""
    if not cfg or not cfg.get("secret") or not value or value.count(".") != 2:
        return None
    sid, exp, sig = value.split(".")
    now = time.time() if now is None else now
    if not exp.isdigit() or int(exp) <= now:
        return None
    return sid if hmac.compare_digest(sig, _sign(cfg["secret"], f"s|{sid}|{exp}")) else None


def renew(cfg: dict | None, value: str | None, now: float | None = None) -> str | None:
    """A browser that keeps using the dashboard stays logged in: once a session is RENEW_AFTER old, the same session gets
    a fresh 30 days (same id, so the page's CSRF token still works). None when it's not due, or not valid."""
    sid = check(cfg, value, now)
    if not sid:
        return None
    now = time.time() if now is None else now
    if int(value.split(".")[1]) - now > SESSION_SECONDS - RENEW_AFTER:
        return None
    exp = int(now + SESSION_SECONDS)
    return f"{sid}.{exp}.{_sign(cfg['secret'], f's|{sid}|{exp}')}"


def csrf(cfg: dict, sid: str) -> str:
    """The token a logged-in page sends with every change (X-CSRF-Token), tied to its session."""
    return _sign(cfg["secret"], f"c|{sid}")


def cookies(header: str | None) -> dict:
    c = SimpleCookie()
    try:
        c.load(header or "")
    except CookieError:
        return {}
    return {k: m.value for k, m in c.items()}


def base_url(port: int) -> str:
    """Where the dashboard is: CHARLES_URL if the installer set one (HTTPS), else http://<this server>:port."""
    if os.environ.get("CHARLES_URL"):
        return os.environ["CHARLES_URL"].rstrip("/")
    try:
        ip = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5).stdout.split()[0]
    except (OSError, IndexError, subprocess.SubprocessError):
        ip = "YOUR-SERVER-IP"
    return f"http://{ip}:{port}"


def _save(cfg: dict, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    old = os.umask(0o077)
    try:
        state_io.write_json(path, cfg, indent=2)
    finally:
        os.umask(old)
    os.chmod(path, 0o600)


def main(argv: list[str], path: str | None = None) -> int:
    path = path or AUTH_FILE
    ap = argparse.ArgumentParser(prog="charles-login", description="A one-time login link for the Charles dashboard.")
    g = ap.add_mutually_exclusive_group()
    for flag, about in (("--private", MODES["private"]), ("--public", MODES["public"]), ("--off", MODES["open"]),
                        ("--logout-all", "log out every browser"), ("--status", "show the access mode")):
        g.add_argument(flag, action="store_true", help=about)
    ap.add_argument("--port", type=int, default=int(os.environ.get("WEBDASH_PORT", "8502")), help="the dashboard's port")
    a = ap.parse_args(argv)
    cfg = load(path)
    if a.status:
        print(f"Dashboard: {access(cfg)} ({MODES[access(cfg)]}).")
        return 0
    if not cfg or not cfg.get("secret"):
        cfg = {"access": "public", "secret": secrets.token_hex(32), "links": []}
    if a.private or a.public or a.off:
        cfg["access"] = "private" if a.private else "public" if a.public else "open"
    if a.logout_all:
        cfg["secret"], cfg["links"] = secrets.token_hex(32), []
    token = None if a.off or a.logout_all or cfg["access"] == "open" else new_link(cfg)
    _save(cfg, path)
    if a.logout_all:
        print("👋 Every browser is logged out. For a new link: charles-login")
    print(f"Dashboard: {cfg['access']} ({MODES[cfg['access']]}).")
    if token:
        url = base_url(a.port)
        print(f"\n🔑 Open this within 10 minutes on the phone or computer you'll use. It works once:\n\n   {url}/login?t={token}\n")
        print("That browser stays logged in while it's used (30 days from its last visit). To log every browser out: charles-login --logout-all")
        if url.startswith("http://"):
            print("Note: over http://, someone on the same Wi-Fi could copy the login. HTTPS fixes that.")
    return 0


if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Run it as root (log in as root, or use sudo).")
        sys.exit(1)
    sys.exit(main(sys.argv[1:]))
