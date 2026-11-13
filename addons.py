"""
addons.py — Charles's optional add-ons: Crypto, Kalshi and Fetch (owner 2026-10-29: new installs run stocks only).

An add-on is on while its timer (Fetch, which runs all the time: its first service) is enabled in systemd, so nothing else records it:
    charles-addon               → which add-ons are on
    charles-addon on crypto     → install its services (only the missing ones) and start it
    charles-addon off kalshi    → stop it; its data stays
The dashboard shows a tab only for an add-on that's on (enabled(), read on every page).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.abspath(__file__))
SYSTEMD_DIR = os.environ.get("CHARLES_SYSTEMD_DIR", "/etc/systemd/system")
PY = sys.executable                                  # the charles env's python
_bin = Path(PY).parent
CONDA_DIR = str(_bin.parents[2]) if _bin.parent.parent.name == "envs" else os.path.expanduser("~/miniconda3")
KPY = os.path.join(CONDA_DIR, "envs", "kalshi", "bin", "python")

ADDONS = {
    "crypto": {"name": "Charles Crypto", "short": "Crypto", "pitch": "Bitcoin and ether, with practice money.", "timer": "charles-crypto.timer", "services": [],
               "about": "A $1,000 paper book of bitcoin and ether, each held only above its 200-day average. "
                        "Steps hourly on Coinbase's public prices; needs no account."},
    "kalshi": {"name": "Kalshi", "short": "Kalshi", "pitch": "Practice bets on prediction markets.", "timer": "kalshi-net.timer", "services": ["kalshi-web.service"],
               "about": "A paper prediction-market bot that scans Kalshi every 15 minutes. Its strategies "
                        "start by only predicting and bet (paper) once their record earns it."},
    "fetch": {"name": "Charles Fetch", "short": "Fetch", "pitch": "Watch Charles trade one coin or stock live, and why.", "timer": None,
              "services": ["charles-fetch.service", "charles-fetch-stocks.service"],
              "about": "Bitcoin and SPY watched trade by trade: a live chart, everyone's trades, and Charles saying what he sees "
                       "and why he buys or sells, with $1,000 of practice money each. Market data only; it never places an order."},
}


class AddonError(Exception):
    pass


def enabled(systemd_dir: str = SYSTEMD_DIR) -> dict[str, bool]:
    """{"crypto": bool, "kalshi": bool, "fetch": bool}: is each add-on's timer (or, with no timer, its first service) enabled?"""
    def on(a):
        if a["timer"]:
            return os.path.exists(os.path.join(systemd_dir, "timers.target.wants", a["timer"]))
        return os.path.exists(os.path.join(systemd_dir, "multi-user.target.wants", a["services"][0]))
    return {k: on(a) for k, a in ADDONS.items()}


def _switch(a: dict) -> list[str]:
    return [u for u in (a["timer"], *a["services"]) if u]


def status(systemd_dir: str = SYSTEMD_DIR) -> list[dict]:
    """One row per add-on for the Settings page, with the command that switches it."""
    on = enabled(systemd_dir)
    return [{"id": k, "name": a["name"], "short": a["short"], "about": a["about"], "pitch": a["pitch"], "on": on[k],
             "command": f"charles-addon {'off' if on[k] else 'on'} {k}"} for k, a in ADDONS.items()]


def units(name: str) -> dict[str, str]:
    """The systemd units an add-on needs (as the installer wrote them before add-ons)."""
    if name == "fetch":
        return {"charles-fetch.service": _fetch_unit("coin"), "charles-fetch-stocks.service": _fetch_unit("stocks")}
    if name == "crypto":
        return {
            "charles-crypto.service": f"""[Unit]
Description=Charles Crypto paper book, hourly step (practice money only)

[Service]
Type=oneshot
WorkingDirectory={ROOT}
ExecStart={PY} {ROOT}/crypto_paper.py
MemoryMax=300M
""",
            "charles-crypto.timer": """[Unit]
Description=Charles Crypto paper book, hourly

[Timer]
OnCalendar=*:05
Persistent=true

[Install]
WantedBy=timers.target
"""}
    kdir = os.path.join(ROOT, "kalshi")
    return {
        "kalshi-net.service": f"""[Unit]
Description=Kalshi paper bot scan (paper bets only)
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory={kdir}
ExecStart={KPY} {kdir}/run.py
TimeoutStartSec=600
""",
        "kalshi-net.timer": """[Unit]
Description=Run the Kalshi paper bot every 15 minutes

[Timer]
OnCalendar=*:0/15
Persistent=false

[Install]
WantedBy=timers.target
""",
        "kalshi-web.service": f"""[Unit]
Description=Kalshi dashboard (webdash.py, port 8503; writes only the Evil Charles switch)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={kdir}
ExecStart={KPY} {kdir}/webdash.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1
MemoryMax=300M

[Install]
WantedBy=multi-user.target
"""}


def _fetch_unit(market: str) -> str:
    """One Charles Fetch engine, sandboxed: it reads the code (the stock one also .env, for the Alpaca keys: market data only) and
    writes only its own data folder."""
    stock = market == "stocks"
    data = os.path.join(ROOT, "data", "fetch-stocks" if stock else "fetch")
    hidden = " ".join(f"-{p}" for p in ([] if stock else [os.path.join(ROOT, ".env")]) + [os.path.join(ROOT, "data", "launch_authorized.json")])
    env = f"Environment=FETCH_SETTINGS=fetch-stocks.json FETCH_DATA={data}\n" if stock else ""
    what = "one stock (SPY) on Alpaca's free IEX market data" if stock else "bitcoin on Coinbase's public feed"
    return f"""[Unit]
Description=Charles Fetch: {what}, trade by trade, with a practice book (no orders)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={ROOT}
ExecStartPre=+/bin/mkdir -p {data}
ExecStart={PY} {ROOT}/fetch_engine.py
{env}Restart=always
RestartSec=15
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=-{data}
InaccessiblePaths={hidden}
PrivateTmp=yes
NoNewPrivileges=yes
MemoryMax=150M
CPUQuota=25%

[Install]
WantedBy=multi-user.target
"""


def _run(cmd: list[str]):
    subprocess.run(cmd, check=True)


def on(name: str, run=_run, systemd_dir: str = SYSTEMD_DIR) -> list[str]:
    """Install the add-on's missing units and start it. Returns the unit files written. A unit that's
    already there is never replaced (the owner's server has its own, sandboxed ones)."""
    a = ADDONS[name]
    if name == "kalshi":
        kdir = os.path.join(ROOT, "kalshi")
        if not os.path.isdir(kdir):
            raise AddonError("This version of Charles has no kalshi/ folder. Run charles-update first.")
        if not os.path.exists(KPY):
            print("Setting up Kalshi's own Python (a few minutes)…")
            run([os.path.join(CONDA_DIR, "bin", "conda"), "create", "-y", "-q", "-n", "kalshi", "-c", "conda-forge",
                 "--override-channels", "python=3.11"])
        run([KPY, "-m", "pip", "install", "-q", "-r", os.path.join(kdir, "requirements.txt")])
        os.makedirs(os.path.join(kdir, "data"), exist_ok=True)
    wrote = []
    for unit, text in units(name).items():
        path = os.path.join(systemd_dir, unit)
        if not os.path.exists(path):
            with open(path, "w") as f:
                f.write(text)
            wrote.append(unit)
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", *_switch(a)])
    if name == "crypto":
        run(["systemctl", "start", "--no-block", "charles-crypto.service"])     # the first step now, not at :05
    return wrote


def off(name: str, run=_run):
    """Stop the add-on and keep it from starting again. Its data stays."""
    a = ADDONS[name]
    run(["systemctl", "disable", "--now", *_switch(a)])


def main(argv: list[str]) -> int:
    if not argv:
        for r in status():
            print(f"  {'●' if r['on'] else '○'} {r['id']:7s} {'on ' if r['on'] else 'off'}  {r['name']}: {r['about']}")
        print("\nTurn one on or off:  charles-addon on crypto   ·   charles-addon off kalshi")
        return 0
    if len(argv) != 2 or argv[0] not in ("on", "off", "enable", "disable") or argv[1] not in ADDONS:
        print("Usage: charles-addon [on|off crypto|kalshi|fetch]")
        return 2
    if os.geteuid() != 0:
        print("Run it as root (log in as root, or use sudo).")
        return 1
    try:
        if argv[0] in ("on", "enable"):
            on(argv[1])
            print(f"✅ {ADDONS[argv[1]]['name']} is on. Its tab shows on the dashboard now.")
        else:
            off(argv[1])
            print(f"🛑 {ADDONS[argv[1]]['name']} is off. Its data is kept; turn it back on any time.")
    except (AddonError, subprocess.CalledProcessError) as e:
        print(f"⛔ {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
