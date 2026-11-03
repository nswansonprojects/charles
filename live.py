"""
live.py — live trading for the stock bot (owner 2026-11-03: "live trading mode available for authorized users").

Paper is the default and stays the default. Live means Charles trades the user's own Alpaca live account, and getting
there takes every step below, over SSH on the user's own server (never the browser):
  1. confirm they're 18 or older and accept the License Agreement (terms.py; asked here if it isn't on file);
  2. add their live Alpaca keys, checked read-only with Alpaca and kept in .env as ALPACA_LIVE_API_KEY /
     ALPACA_LIVE_SECRET_KEY, apart from the paper pair;
  3. read what live trading means and type LIVE.
Then the paper account's history is put aside in data/paper-<when>/ (a new account starts a new history),
data/trading_mode.json says live, and the bot restarts. `charles-live off` puts the live history aside in
data/live-<when>/, brings the paper history back and restarts on paper. Positions stay in the Alpaca account either way.

While live, the rebalancer places no order when problem() finds one (the agreement has a new version, the live keys are
gone): it fails closed and says why. Only the stock bot can go live; Kalshi and crypto trade practice money only.

    charles-live            what's on now (same as: charles-live status)
    charles-live on         every step, then live
    charles-live off        back to paper
"""
from __future__ import annotations

import getpass
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

import state_io
import terms

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
LIVE_API = "https://api.alpaca.markets"
KEY_VARS = ("ALPACA_LIVE_API_KEY", "ALPACA_LIVE_SECRET_KEY")
# A brokerage account's own record: a new account starts these afresh, and the old ones are kept to come back to.
HISTORY_FILES = ("start.json", "equity_curve.json", "peak_equity.json", "plan.json", "trades.json", "daily_loss.json")
CONFIRM = "LIVE"
RISKS = [
    "Every order Charles places is your order, in your own Alpaca account, with real money.",
    "You can lose money, including all of it. The 2x fund in aggressive mode falls twice as fast as the market.",
    "Charles promises no gains and gives no advice. Fees, taxes and your broker's rules are yours.",
    "Pause on the dashboard stops new orders at once; charles-live off goes back to paper.",
    "Charles buys only with the account's cash, never margin, at its daily check (10:00 AM ET, 2:00 PM retry).",
]


def _file(data_dir: str | None) -> str:
    return os.path.join(data_dir or DATA, "trading_mode.json")


def state(data_dir: str | None = None) -> dict:
    """{"mode": "paper"|"live", "since", "by"}. Anything unreadable counts as paper: live is never assumed."""
    try:
        with open(_file(data_dir)) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {"mode": "paper"}
    if not isinstance(d, dict) or d.get("mode") != "live":
        return {"mode": "paper", **({"since": d.get("since")} if isinstance(d, dict) and d.get("since") else {})}
    return {"mode": "live", "since": d.get("since"), "by": d.get("by")}


def is_live(data_dir: str | None = None) -> bool:
    return state(data_dir)["mode"] == "live"


def keys(env: dict | None = None) -> tuple[str, str]:
    """The live pair, from the environment (.env is loaded by credential_manager) or `env`."""
    src = env if env is not None else os.environ
    if env is None:
        import credential_manager  # noqa: F401  (loads .env into the environment)
    return (src.get(KEY_VARS[0]) or "").strip(), (src.get(KEY_VARS[1]) or "").strip()


def problem(data_dir: str | None = None, env: dict | None = None) -> str | None:
    """Why a live install must not place orders right now, or None. Paper never has one."""
    if not is_live(data_dir):
        return None
    if not terms.current(data_dir):
        return "the License Agreement has a version you haven't accepted. Run charles-terms over SSH"
    key, secret = keys(env)
    if not key or not secret:
        return "the live Alpaca keys are missing. Run charles-live on over SSH"
    return None


def dashboard(data_dir: str | None = None) -> dict:
    """What the dashboard shows: the mode, since when, and whether live is on hold (never a key)."""
    s = state(data_dir)
    return {"mode": s["mode"], "since": s.get("since"), "hold": problem(data_dir) if s["mode"] == "live" else None,
            "terms": terms.status_line(data_dir)}


def check_keys(key: str, secret: str, opener=urllib.request.urlopen) -> tuple[bool, str, dict]:
    """Ask Alpaca's live API for the account these keys open (read-only). (ok, sentence, account)."""
    if not re.fullmatch(r"AK[A-Z0-9]{8,40}", key or ""):
        return False, "That isn't a live Alpaca key: live keys start with AK (paper keys start with PK).", {}
    if not secret or len(secret) < 20 or any(c.isspace() for c in secret):
        return False, "That secret doesn't look right. Copy it again from Alpaca.", {}
    req = urllib.request.Request(LIVE_API + "/v2/account", headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
    try:
        with opener(req, timeout=15) as r:
            acct = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return False, ("Alpaca refused those keys. Check that they're the live ones, and that the account is open."
                       if e.code in (401, 403) else f"Alpaca answered {e.code}. Try again in a minute."), {}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return False, f"Couldn't reach Alpaca ({e}). Try again in a minute.", {}
    if acct.get("status") != "ACTIVE" or acct.get("trading_blocked") or acct.get("account_blocked"):
        return False, f"Alpaca says this account can't trade yet (status {acct.get('status')}).", acct
    return True, f"Live account found: ${float(acct.get('equity') or 0):,.2f}, cash ${float(acct.get('cash') or 0):,.2f}.", acct


def _stamp(now: datetime) -> str:
    return now.strftime("%Y%m%d-%H%M%S")


def put_aside(kind: str, data_dir: str | None = None, now: datetime | None = None) -> str | None:
    """Move this account's history into data/<kind>-<when>/ (nothing is deleted). Returns the folder, or None if empty."""
    folder = data_dir or DATA
    now = now or datetime.now(timezone.utc)
    present = [n for n in HISTORY_FILES if os.path.exists(os.path.join(folder, n))]
    if not present:
        return None
    dest = os.path.join(folder, f"{kind}-{_stamp(now)}")
    os.makedirs(dest, exist_ok=False)
    for n in present:
        os.replace(os.path.join(folder, n), os.path.join(dest, n))
    return dest


def bring_back(kind: str, data_dir: str | None = None) -> str | None:
    """Move the newest data/<kind>-<when>/ history back, file by file, never over a file that's there. Returns the folder."""
    folder = data_dir or DATA
    try:
        found = sorted(n for n in os.listdir(folder) if re.fullmatch(rf"{kind}-\d{{8}}-\d{{6}}", n)
                       and os.path.isdir(os.path.join(folder, n)))
    except FileNotFoundError:
        return None
    if not found:
        return None
    src = os.path.join(folder, found[-1])
    for n in HISTORY_FILES:
        if os.path.exists(os.path.join(src, n)) and not os.path.exists(os.path.join(folder, n)):
            os.replace(os.path.join(src, n), os.path.join(folder, n))
    return src


def set_mode(mode: str, by: str, data_dir: str | None = None, now: datetime | None = None):
    now = now or datetime.now(timezone.utc)
    state_io.write_json(_file(data_dir), {"mode": mode, "since": now.isoformat(timespec="seconds"), "by": by,
                                          "terms_version": terms.VERSION}, indent=1)
    with open(os.path.join(data_dir or DATA, "control.jsonl"), "a") as f:
        f.write(json.dumps({"at": now.isoformat(timespec="seconds"), "action": f"trading-{mode}", "by": by}) + "\n")


def _save_keys(key: str, secret: str):
    import server_confirm
    vals = server_confirm._load_env()
    vals[KEY_VARS[0]], vals[KEY_VARS[1]] = key, secret
    server_confirm._save_env(vals)


def _service(action: str):
    """stop / start the bot; "web" restarts the dashboard, so it reads the new account's history at once."""
    cmd = ["systemctl", "try-restart", "charles-web"] if action == "web" else ["systemctl", action, "charles"]
    subprocess.run(cmd, check=False, timeout=120)


def turn_on(read=input, secret_read=getpass.getpass, say=print, opener=urllib.request.urlopen, service=_service,
            data_dir: str | None = None, env: dict | None = None, save_keys=_save_keys) -> bool:
    """Every step, in order; any "no" stops with nothing changed."""
    if is_live(data_dir):
        say("Live trading is already on. To go back to paper: charles-live off")
        return True
    say("\nLive trading: Charles trades your own Alpaca live account with real money.\n")
    if not terms.current(data_dir) and not terms.ask("charles-live", data_dir, read=read, say=say):
        return False
    key, secret = keys(env)
    if key and secret:
        ok, said, _ = check_keys(key, secret, opener)
        say(said)
        if not ok:
            key = secret = ""
    if not key or not secret:
        say("\nYour live Alpaca keys: in Alpaca, switch to your live account, then API Keys → Generate. They stay on this server.")
        key = read("Live API key (starts with AK): ").strip()
        secret = secret_read("Live secret (hidden as you type): ").strip()
        ok, said, _ = check_keys(key, secret, opener)
        say(said)
        if not ok:
            return False
        save_keys(key, secret)
    say("\nBefore you turn it on:")
    for r in RISKS:
        say(f"  • {r}")
    if read(f"\nType {CONFIRM} to trade real money from the next daily check (10:00 AM ET): ").strip() != CONFIRM:
        say("Not turned on. Charles stays on paper.")
        return False
    service("stop")
    try:
        kept = put_aside("paper", data_dir)
        set_mode("live", "charles-live", data_dir)
    finally:
        service("start")
        service("web")
    say(f"🟢 Live trading is on.{' Your paper history is kept in ' + kept + '.' if kept else ''}")
    say("Charles trades the live account at its next daily check. The dashboard shows Live. To stop: charles-live off")
    return True


def turn_off(read=input, say=print, service=_service, data_dir: str | None = None) -> bool:
    if not is_live(data_dir):
        say("Charles is already on paper.")
        return True
    if read("Go back to paper trading? Your live positions stay in your Alpaca account. [y/N] ").strip().lower() not in ("y", "yes"):
        say("Nothing changed: live trading is still on.")
        return False
    service("stop")
    try:
        kept = put_aside("live", data_dir)
        back = bring_back("paper", data_dir)
        set_mode("paper", "charles-live", data_dir)
    finally:
        service("start")
        service("web")
    say("📄 Back to paper trading." + (f" The live history is kept in {kept}." if kept else "") +
        (" The paper history is back." if back else ""))
    say("Charles no longer trades the live account. Anything it holds there stays until you sell it in Alpaca.")
    return True


def status(data_dir: str | None = None) -> str:
    s, hold = state(data_dir), problem(data_dir)
    if s["mode"] != "live":
        return "📄 Paper trading (practice money). To trade real money: charles-live on\nLicense Agreement: " + terms.status_line(data_dir)
    return (f"🟢 Live trading since {str(s.get('since'))[:10]}." + (f"\n⛔ On hold: {hold}." if hold else "") +
            "\nLicense Agreement: " + terms.status_line(data_dir))


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        print(status())
        return 0
    if os.geteuid() != 0:
        print("Run this as root (it restarts the bot).")
        return 1
    if cmd == "on":
        return 0 if turn_on() else 1
    if cmd == "off":
        return 0 if turn_off() else 1
    print("Usage: charles-live [status|on|off]")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
