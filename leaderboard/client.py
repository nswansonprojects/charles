"""
leaderboard/client.py — this install's side of the leaderboard: submit / withdraw (the dashboard's
button) and `charles-copy <id>` (copy a config from the board into this bot).

What is sent on submit is only the bot type, its settings and an optional name, signed with this
install's own key (data/leaderboard/identity.pem, made on first use, 600). No balances, keys,
positions or logs ever leave the server.

charles-copy checks the config's signature against the leaderboard's pinned public key
(leaderboard/server_pubkey.txt), checks it with the same strict schema the server uses, shows what
would change, asks, backs up the current file and only then writes strategy.json / crypto.json.
The rebalancer and the crypto book re-read those files at their next run: nothing restarts.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from leaderboard import common  # noqa: E402

URL = os.environ.get("LEADERBOARD_URL", "https://charles-bot.xyz")
PINNED = os.environ.get("LEADERBOARD_PUBKEY_FILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_pubkey.txt")   # a test board pins its own key
FILES = {"stocks": "strategy.json", "crypto": "crypto.json"}


def identity(data_dir: str):
    """This install's signing key, made on first use."""
    folder = os.path.join(data_dir, "leaderboard")
    path = os.path.join(folder, "identity.pem")
    try:
        with open(path, "rb") as f:
            return common.load_key(f.read())
    except FileNotFoundError:
        pass
    os.makedirs(folder, mode=0o700, exist_ok=True)
    key = common.new_key()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:                       # made by another request a moment ago
        with open(path, "rb") as f:
            return common.load_key(f.read())
    with os.fdopen(fd, "wb") as f:
        f.write(common.key_pem(key))
    return key


NOT_SHARED = ("A plan with your own tickers, or with part of the money kept aside, can't be shared: anyone can copy what's "
              "on the board, and a list of tickers there could be used to push their prices. To share, set the Plan tab "
              "to Charles's picks with 100% invested.")


def current_settings(bot: str, root: str = ROOT) -> dict:
    """Every setting the bot is using now (file over defaults), limited to the shareable keys. A Custom Plan
    (portfolio.py) isn't shareable at all: ValueError."""
    if bot == "stocks":
        import allocator
        import strategy_settings
        s = {**allocator.DEFAULTS, **strategy_settings.load(os.path.join(root, FILES[bot]))}
        if s.get("BLOCKS") or s.get("INVESTED", 1.0) != 1.0:
            raise ValueError(NOT_SHARED)
        return {k: s[k] for k in strategy_settings.LIMITS}
    import crypto_plan
    import crypto_settings
    s = {**crypto_plan.DEFAULTS, **crypto_settings.load(os.path.join(root, FILES[bot]))}
    return crypto_settings.flat(s)


def _post(url: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=common.canonical(body), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except ValueError:
            return e.code, {"ok": False, "message": f"leaderboard answered {e.code}"}


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read())


def preview(bot: str, root: str = ROOT) -> dict:
    """Exactly what Submit would send (minus the signature)."""
    return {"bot": bot, "settings": common.validate_settings(bot, current_settings(bot, root))}


def submit(bot: str, name, data_dir: str, root: str = ROOT, url: str = URL) -> tuple[int, dict]:
    key = identity(data_dir)
    payload = {"v": 1, "action": "submit", "bot": bot, "settings": preview(bot, root)["settings"],
               "name": common.check_name(name), "pubkey": common.public_b64(key), "ts": int(time.time())}
    return _post(url + "/api/leaderboard/submit", {"payload": payload, "sig": common.sign(key, payload)})


def withdraw(entry_id: str, data_dir: str, url: str = URL) -> tuple[int, dict]:
    key = identity(data_dir)
    payload = {"v": 1, "action": "withdraw", "id": entry_id, "pubkey": common.public_b64(key), "ts": int(time.time())}
    return _post(url + "/api/leaderboard/withdraw", {"payload": payload, "sig": common.sign(key, payload)})


def challenges(url: str = URL) -> dict:
    return _get(url + "/api/challenges")


def enter_challenge(challenge: dict, name, data_dir: str, root: str = ROOT, url: str = URL) -> tuple[int, dict]:
    """Enter a challenge with this bot's settings, changed only as far as its rules need. The bot itself isn't touched."""
    from leaderboard.challenges import fit_rules
    key = identity(data_dir)
    settings = fit_rules(current_settings(challenge["bot"], root), challenge["rules"])
    payload = {"v": 1, "action": "enter", "challenge": challenge["id"], "settings": settings, "name": common.check_name(name),
               "pubkey": common.public_b64(key), "ts": int(time.time())}
    return _post(url + "/api/challenges/enter", {"payload": payload, "sig": common.sign(key, payload)})


def withdraw_challenge(challenge_id: str, data_dir: str, url: str = URL) -> tuple[int, dict]:
    key = identity(data_dir)
    payload = {"v": 1, "action": "withdraw_entry", "challenge": challenge_id, "pubkey": common.public_b64(key), "ts": int(time.time())}
    return _post(url + "/api/challenges/withdraw", {"payload": payload, "sig": common.sign(key, payload)})


def my_challenge_entries(data_dir: str, url: str = URL) -> list:
    if not os.path.exists(os.path.join(data_dir, "leaderboard", "identity.pem")):
        return []
    pub = common.public_b64(identity(data_dir))
    return _get(url + "/api/challenges/mine?pubkey=" + urllib.parse.quote(pub, safe=""))["entries"]


def mine(data_dir: str, url: str = URL) -> list:
    """This install's entries (no key is made just to look)."""
    if not os.path.exists(os.path.join(data_dir, "leaderboard", "identity.pem")):
        return []
    pub = common.public_b64(identity(data_dir))
    return _get(url + "/api/leaderboard/mine?pubkey=" + urllib.parse.quote(pub, safe=""))["entries"]


# ── charles-copy ──────────────────────────────────────────────────────────────

def pinned_key(path: str = PINNED) -> str:
    with open(path) as f:
        return next(line.strip() for line in f if line.strip() and not line.startswith("#"))


def fetch(entry_id: str, url: str = URL, pubkey: str | None = None) -> dict:
    """The verified config for `entry_id`, or ValueError saying why it can't be trusted."""
    if not common.re.fullmatch(r"lb-[0-9a-f]{6}", entry_id):
        raise ValueError("leaderboard ids look like lb-1a2b3c")
    m = _get(url + "/api/leaderboard/config/" + entry_id)
    p = m.get("payload") if isinstance(m, dict) else None
    if not isinstance(p, dict) or not common.verify(pubkey or pinned_key(), p, m.get("sig", "")):
        raise ValueError("the config's signature doesn't match the leaderboard's key — not copying it")
    if p.get("id") != entry_id:
        raise ValueError("the leaderboard sent a different config than the one asked for — not copying it")
    settings = common.validate_settings(p["bot"], p["settings"])
    if common.config_hash(p["bot"], settings) != p.get("hash"):
        raise ValueError("the config doesn't match its fingerprint — not copying it")
    return {**p, "settings": settings}


def diff(old: dict, new: dict) -> list:
    return [(k, old.get(k), new[k]) for k in new if old.get(k) != new[k]]


def install(cfg: dict, root: str = ROOT, now: datetime | None = None) -> str:
    """Back up the bot's settings file and write the copied config. Returns the backup path."""
    path = os.path.join(root, FILES[cfg["bot"]])
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    backup = f"{path}.bak-{stamp}"
    try:
        with open(path) as f:
            old_text = f.read()
    except FileNotFoundError:
        old_text = "{}"
    with open(backup, "w") as f:
        f.write(old_text)
    note = f"{(now or datetime.now()).date()}: copied leaderboard config {cfg['id']} ({cfg['name']})."
    try:                                # the user's own choices stay; their own blocks go (a copy is that plan)
        own = {k: v for k, v in (json.loads(old_text).get("settings") or {}).items() if k in ("SELF_TUNE", "PRESETS")}
    except (ValueError, AttributeError):
        own = {}
    with open(path + ".tmp", "w") as f:
        json.dump({"settings": {**cfg["settings"], **own}, "last_change": note}, f, indent=2)
        f.write("\n")
    os.replace(path + ".tmp", path)
    return backup


def main(argv=None):
    ap = argparse.ArgumentParser(prog="charles-copy", description="Copy a config from the Charles leaderboard into this bot.")
    ap.add_argument("id", help="the leaderboard id, like lb-1a2b3c")
    ap.add_argument("--dir", default=ROOT, help="the bot folder to change (default: this one)")
    ap.add_argument("--yes", action="store_true", help="don't ask")
    a = ap.parse_args(argv)
    try:
        cfg = fetch(a.id)
    except urllib.error.HTTPError as e:
        print(f"✗ {'No config with that id on the leaderboard.' if e.code == 404 else f'The leaderboard answered {e.code}.'}")
        return 1
    except (ValueError, urllib.error.URLError, OSError) as e:
        print(f"✗ {e}")
        return 1
    old = current_settings(cfg["bot"], a.dir)
    changes = diff(old, cfg["settings"])
    print(f"✓ {cfg['id']} by {cfg['name']} — {cfg['bot']} config, signature checked")
    if not changes:
        print("Your bot already uses exactly these settings. Nothing to do.")
        return 0
    print(f"\nChanges to {os.path.join(a.dir, FILES[cfg['bot']])}:")
    for k, was, now in changes:
        print(f"  {k:14s} {was!s:>8} → {now}")
    print("\nPast results don't predict future ones. This is not financial advice.")
    if not a.yes and input("Copy these settings? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Nothing changed.")
        return 0
    backup = install(cfg, a.dir)
    print(f"✓ Copied. Your old settings are saved in {backup}")
    print("The bot uses them from its next check; no restart needed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
