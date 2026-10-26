"""
connections.py — the bots' broker accounts, connected from a terminal over SSH (`charles-connect`).

  Charles (stocks)  → Alpaca         uses the keys installed by `charles-keys` (update_keys.py)
  Kalshi            → Kalshi         API key ID + RSA private key
  Charles Crypto    → Coinbase       Advanced Trade API key name + EC private key

Keys are typed into the terminal (hidden), checked, and written to data/accounts/connections.json
(mode 600). The dashboard's Accounts page only reads the status (connected, last 4 characters of the
key ID, last check) and shows the SSH steps for each account; it never receives keys.

  charles-connect kalshi            connect (or replace) the Kalshi keys
  charles-connect crypto --remove   delete the saved Coinbase keys
  charles-connect --status          which accounts are connected

The bots don't read this file yet: Kalshi trades on paper with public data and the crypto book
needs no account.
"""
import argparse
import getpass
import json
import os
import re
import sys
import termios
import urllib.error
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("WEBDASH_DATA") or os.path.join(ROOT, "data")
DIR = os.path.join(DATA, "accounts")
STORE = os.path.join(DIR, "connections.json")

PEM = r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+-----END [A-Z ]*PRIVATE KEY-----"
BOTS = [
    {"id": "charles", "name": "Charles", "what": "Stocks", "broker": "Alpaca",
     "command": "charles-keys", "help": "Alpaca dashboard → Paper Trading → API Keys → Generate.", "url": "https://app.alpaca.markets/paper/dashboard/overview",
     "fields": [{"key": "key_id", "label": "API key ID", "secret": False, "pattern": r"[A-Z0-9]{16,32}"},
                {"key": "secret", "label": "Secret key", "secret": True, "pattern": r"[A-Za-z0-9/+=]{30,64}"}]},
    {"id": "kalshi", "name": "Kalshi", "what": "Prediction markets", "broker": "Kalshi",
     "command": "charles-connect kalshi", "help": "Kalshi → Account & security → API keys → Create key; paste the key ID and the private key file's text.",
     "url": "https://kalshi.com/account/profile",
     "fields": [{"key": "key_id", "label": "API key ID", "secret": False, "pattern": r"[0-9a-fA-F-]{36}"},
                {"key": "private_key", "label": "Private key (PEM)", "secret": True, "multiline": True, "pattern": PEM}]},
    {"id": "crypto", "name": "Charles Crypto", "what": "Bitcoin and ether", "broker": "Coinbase",
     "command": "charles-connect crypto", "help": "Coinbase Developer Platform → API keys → Create (Advanced Trade, view + trade, no transfers).",
     "url": "https://portal.cdp.coinbase.com/access/api",
     "fields": [{"key": "key_id", "label": "API key name", "secret": False, "pattern": r"organizations/[0-9a-f-]+/apiKeys/[0-9a-f-]+"},
                {"key": "private_key", "label": "Private key (PEM)", "secret": True, "multiline": True, "pattern": PEM}]},
]
BY_ID = {b["id"]: b for b in BOTS}


class ConnectError(Exception):
    """A refusal shown as is (status code, plain-English message)."""
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# ── Storage ───────────────────────────────────────────────────────────────────

def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_private(path, obj):
    """Write JSON readable by root only (the file holds keys)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# ── Checking keys ─────────────────────────────────────────────────────────────

def validate(bot_id: str, fields: dict) -> dict:
    """Trim and format-check the fields → the clean values. Raises ConnectError with what's wrong."""
    bot = BY_ID.get(bot_id)
    if not bot:
        raise ConnectError(404, "Unknown bot.")
    clean = {}
    for f in bot["fields"]:
        v = str(fields.get(f["key"]) or "").strip()
        if f.get("multiline"):
            v = v.replace("\\n", "\n").replace("\r\n", "\n")
        if not v:
            raise ConnectError(400, f"{f['label']} is empty.")
        if not re.fullmatch(f["pattern"], v):
            raise ConnectError(400, f"That doesn't look like a {bot['broker']} {f['label'].lower()}.")
        clean[f["key"]] = v
    if bot_id == "charles" and not clean["key_id"].startswith("PK"):
        raise ConnectError(400, "That's a live-money Alpaca key. Charles trades practice money only for now: use a paper key (starts with PK).")
    return clean


def check_alpaca(key_id: str, secret: str, timeout: float = 10) -> tuple[bool, str]:
    """Ask Alpaca's paper API for the account with these keys (read-only)."""
    req = urllib.request.Request("https://paper-api.alpaca.markets/v2/account",
                                 headers={"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            acct = json.loads(r.read())
        return True, f"Paper account found, ${float(acct.get('equity') or 0):,.2f}."
    except urllib.error.HTTPError as e:
        return False, "Alpaca turned these keys down." if e.code in (401, 403) else f"Alpaca answered {e.code}."
    except Exception as e:
        return False, f"Couldn't reach Alpaca ({type(e).__name__})."


def check(bot_id: str, clean: dict, alpaca=check_alpaca) -> tuple[bool, str]:
    """Live check where it can be done without extra libraries; the others are format-checked only
    (signing Kalshi/Coinbase requests needs the `cryptography` package, added when live trading is)."""
    if bot_id == "charles":
        return alpaca(clean["key_id"], clean["secret"])
    return True, "Format looks right. Checked with the exchange once live trading is turned on."


# ── What the page sees ───────────────────────────────────────────────────────

def public_status(store: dict, server_alpaca: bool | None = None) -> list:
    """One row per bot without any secret: connected, key tail, last check. `server_alpaca` says
    whether Charles's server-installed keys are working (so Charles shows connected without a save)."""
    out = []
    for b in BOTS:
        saved = store.get(b["id"]) or {}
        row = {k: b[k] for k in ("id", "name", "what", "broker", "help", "url", "command")}
        row["fields"] = [{k: f.get(k) for k in ("key", "label", "secret", "multiline")} for f in b["fields"]]
        if saved:
            row.update(connected=True, source="dashboard", tail=saved["key_id"][-4:], saved_at=saved.get("saved_at"),
                       check_ok=saved.get("check_ok"), check_msg=saved.get("check_msg"))
        elif b["id"] == "charles" and server_alpaca:
            row.update(connected=True, source="server", tail=None, check_ok=True,
                       check_msg="Using the paper keys installed on the server.")
        else:
            row.update(connected=False, source=None, tail=None)
        row["mode"] = "Practice money"
        out.append(row)
    return out


def connect(bot_id: str, fields: dict, path: str = STORE, alpaca=check_alpaca) -> dict:
    clean = validate(bot_id, fields)
    ok, msg = check(bot_id, clean, alpaca)
    if not ok:
        raise ConnectError(400, msg)
    store = _load(path)
    store[bot_id] = {**clean, "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "check_ok": ok, "check_msg": msg}
    _write_private(path, store)
    return {"ok": True, "message": msg}


def disconnect(bot_id: str, path: str = STORE) -> dict:
    if bot_id not in BY_ID:
        raise ConnectError(404, "Unknown bot.")
    store = _load(path)
    store.pop(bot_id, None)
    _write_private(path, store)
    return {"ok": True, "message": "Disconnected. The keys were deleted from this server. You can also revoke them on the broker's site."}


def _ask_pem(label: str) -> str:
    """Read a pasted multi-line private key without echoing it; stops at the -----END line."""
    print(f"{label}: paste it, then press Enter (it won't show).")
    fd, old = None, None
    if sys.stdin.isatty():
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        new = termios.tcgetattr(fd)
        new[3] &= ~termios.ECHO
        termios.tcsetattr(fd, termios.TCSADRAIN, new)
    lines = []
    try:
        for line in sys.stdin:
            lines.append(line.rstrip("\r\n"))
            if "-----END" in line:
                break
    finally:
        if old:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return "\n".join(lines)


def _ask(bot: dict) -> dict:
    fields = {}
    for f in bot["fields"]:
        if f.get("multiline"):
            fields[f["key"]] = _ask_pem(f["label"])
        elif f["secret"]:
            fields[f["key"]] = getpass.getpass(f"{f['label']} (hidden): ")
        else:
            fields[f["key"]] = input(f"{f['label']}: ")
    return fields


def main():
    ap = argparse.ArgumentParser(prog="charles-connect", description="Connect a bot's broker account (keys stay on this server).")
    ap.add_argument("bot", nargs="?", choices=[b["id"] for b in BOTS if b["id"] != "charles"],
                    help="kalshi or crypto (Charles's Alpaca keys: run charles-keys)")
    ap.add_argument("--remove", action="store_true", help="delete this account's saved keys")
    ap.add_argument("--status", action="store_true", help="show which accounts are connected (no secrets)")
    a = ap.parse_args()
    if a.bot and not a.status:
        bot = BY_ID[a.bot]
        try:
            if a.remove:
                print(disconnect(a.bot)["message"])
                return
            print(f"{bot['name']} → {bot['broker']}. Where to find the keys: {bot['help']}")
            print(connect(a.bot, _ask(bot))["message"])
        except ConnectError as e:
            raise SystemExit(f"Not saved: {e}")
        except (KeyboardInterrupt, EOFError):
            raise SystemExit("\nCancelled, nothing changed.")
        return
    for r in public_status(_load(STORE)):
        if r["id"] == "charles":
            print(f"{r['name']:15s} uses the Alpaca paper keys installed by charles-keys")
            continue
        print(f"{r['name']:15s} {'connected' if r['connected'] else 'not connected':14s} "
              f"{('key …' + r['tail']) if r['tail'] else ''}")


if __name__ == "__main__":
    main()
