"""
update_keys.py — interactive API key update, run by `charles-keys`.

Shows the keys in .env (masked), lets you change them, then copies the new
values into data/launch_authorized.json and restarts the service.

That copy matters: main._apply_wizard_settings lets the Alpaca and Telegram
keys stored in launch_authorized.json override .env, so updating .env alone
would not take effect. Only the five credential fields are touched; sizing,
daily-loss and other settings in that file are left as they are.

Cancelling changes nothing and leaves the running bot alone.

Non-interactive: `update_keys.py --set GROQ_API_KEY` reads the new value from
stdin, saves it to .env, copies it into launch_authorized.json and restarts.
It never prints key values. Allowed keys: those in CRED_FIELDS.
Replaces start_server.py (2026-10-01), which stopped the service, rewrote
launch_authorized.json with old defaults and ran the bot outside systemd.
"""
import json
import os
import subprocess
import sys

import server_confirm

HERE = os.path.dirname(os.path.abspath(__file__))
FLAG = os.path.join(HERE, "data", "launch_authorized.json")

# .env key → launch_authorized.json field
CRED_FIELDS = {
    "ALPACA_API_KEY":     "alpaca_key",
    "ALPACA_SECRET_KEY":  "alpaca_secret",
    "TELEGRAM_BOT_TOKEN": "tg_token",
    "TELEGRAM_CHAT_ID":   "tg_chat_id",
    "GROQ_API_KEY":       "groq_key",
}


def sync_launch_flag(env_vals: dict, flag_path: str | None = None, only: str | None = None) -> list:
    """
    Copy credential values from .env into the launch flag. Returns changed field names.
    only: copy just this .env key (used by --set, so stale values elsewhere in .env
    can't overwrite working ones in the flag).
    """
    flag_path = flag_path or FLAG   # resolved at call time, not import time
    with open(flag_path) as f:
        data = json.load(f)
    changed = []
    for env_key, field in CRED_FIELDS.items():
        if only and env_key != only:
            continue
        val = env_vals.get(env_key, "")
        if val and data.get(field) != val:
            data[field] = val
            changed.append(field)
    if changed:
        tmp = flag_path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, flag_path)   # atomic; keeps the file root-only (600)
    return changed


def set_key(env_key: str, value: str):
    """Non-interactive update of one key in .env (other entries kept)."""
    if env_key not in CRED_FIELDS:
        print(f"⛔ Unknown key {env_key}. Allowed: {', '.join(CRED_FIELDS)}")
        sys.exit(2)
    if not value or any(c.isspace() for c in value):
        print("⛔ Empty value or value contains whitespace — nothing changed.")
        sys.exit(2)
    vals = server_confirm._load_env()
    vals[env_key] = value
    server_confirm._save_env(vals)
    print(f"✅ {env_key} saved to .env")


def main():
    only = None
    if len(sys.argv) == 3 and sys.argv[1] == "--set":
        only = sys.argv[2]
        set_key(only, sys.stdin.readline().strip())
    elif len(sys.argv) > 1:
        print("Usage: update_keys.py            (interactive)\n"
              "       update_keys.py --set KEY  (new value on stdin)")
        sys.exit(2)
    elif not server_confirm.run_confirmation():
        sys.exit(0)
    if os.path.exists(FLAG):   # only installs from the old setup wizard have this file
        changed = sync_launch_flag(server_confirm._load_env(), only=only)
        print(f"🔑 Updated in launch_authorized.json: {', '.join(changed)}" if changed
              else "🔑 launch_authorized.json already matches .env")
    print("🔄 Restarting the bot...")
    subprocess.run(["/usr/local/bin/charles-restart"], check=False)
    subprocess.run(["systemctl", "--no-pager", "--lines=0", "status", "charles"], check=False)


if __name__ == "__main__":
    main()
