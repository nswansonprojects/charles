"""
updates.py — updates, version history and Restore (owner's Phase 3 plan, 2026-10-29; charles-audit/phase3-design.md §4).

Releases are git tags (v1.4.2) on the public repo. CHANGELOG.json says what each one changed and whether it is
a fix or a rule change. The update check asks GitHub directly, so nobody learns who is checking.

    charles-update              install the newest release (shows what changes first; a rule change must be confirmed)
    charles-update --check      only look for a new release
    charles-restore             list the versions; charles-restore v1.4.1 goes back to one

The dashboard's sandbox can't touch the code. It leaves a request in data/requests/; control.py checks it and
queues a job here (data/update_job.json); charles-updater.service, the only unit that may write the code folder,
carries it out. An update or restore never touches data/ and keeps settings the user changed. It installs the
requirements, runs the tests, restarts and watches the bot for up to 2 minutes; on any failure it goes back to
the version it came from. Nothing here is automatic: the daily timer only looks.

A copy that didn't come from the release repo (the owner's server runs the private repo) isn't touched: it
updates through git, and the dashboard says so.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import state_io

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
RELEASE_REPO = os.environ.get("CHARLES_RELEASE_REPO", "https://github.com/nswansonprojects/charles.git")
UPDATER_UNIT = "charles-updater.service"
SERVICES = ("charles", "charles-web")
PORT = int(os.environ.get("WEBDASH_PORT", "8502"))
ET = ZoneInfo("America/New_York")
VERSION_RE = re.compile(r"^v(\d{1,4})\.(\d{1,4})\.(\d{1,4})\Z")
SETTINGS_FILES = ("strategy.json", "crypto.json")     # the user's own settings: an update keeps their changes
SOURCES = ("the chat", "the sliders", "self-tune")    # where a settings change came from, for its note in the file
CUSTOM_SOURCES = ("the sliders", "self-tune")         # these may change the Custom Plan; the chat never can
# Anything here can change what gets bought or sold, so moving across it needs the confirm screen,
# whatever the changelog calls the release.
RULE_FILES = ("allocator.py", "rebalancer.py", "risk_manager.py", "news_veto.py", "alpaca_executor.py", "config.py",
              "strategy_settings.py", "strategy.json", "crypto_plan.py", "crypto_paper.py", "crypto.json")
JOB_STALE = 30 * 60                                   # seconds: a job this old died with its service
HEALTH_WAIT, HEALTH_SETTLE = 120, 20                  # watch for 2 minutes; healthy once steady for 20 s
LABELS = {"CORE_PCT": "Share in the S&P 500 core", "PICKS": "Momentum picks", "PICK_PCT": "Size of each pick",
          "DRIFT_PCT": "Fix drift over", "TREND_DAYS": "Trend line (days)", "SAFETY_SWITCH": "Safety switch",
          "SWITCH_CHECK": "Safety switch checked", "AGGRESSIVE": "Aggressive mode", "AGG_CORE": "Aggressive fund",
          "AGG_VOL_MAX": "Aggressive mode's calm limit", "AGG_VOL_EXIT": "Aggressive mode's exit limit",
          "INVESTED": "Invested", "BLOCKS": "Your blocks", "SELF_TUNE": "Self-tune", "PRESETS": "Saved presets"}

log = logging.getLogger(__name__)


class UpdateError(Exception):
    """A step failed, or the request can't be carried out. The message is shown to the user."""


# ── Git ───────────────────────────────────────────────────────────────────────

def git(*args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True, timeout=120)
    if check and r.returncode:
        raise UpdateError(f"git {args[0]} failed: {r.stderr.strip()[:300]}")
    return r.stdout.rstrip() if r.returncode == 0 else ""    # not strip(): `git status` lines start with a meaningful space


def _key(version: str) -> tuple:
    return tuple(int(x) for x in VERSION_RE.match(version).groups())


def tags() -> list[str]:
    """Every release, newest first."""
    return sorted((t for t in git("tag", "--list", "v*", check=False).split() if VERSION_RE.match(t)), key=_key, reverse=True)


def current() -> str | None:
    """The release this copy is on, or None when it isn't exactly on one."""
    here = git("rev-parse", "HEAD")
    for t in tags():
        if git("rev-list", "-n", "1", t) == here:
            return t
    return None


def from_release_repo() -> bool:
    def norm(url: str) -> str:
        return re.sub(r"\.git$", "", url.strip().rstrip("/")).lower()
    return norm(git("remote", "get-url", "origin", check=False)) == norm(RELEASE_REPO)


def local_changes() -> tuple[list[str], list[str]]:
    """(the settings files the user changed, every other tracked file that was changed)."""
    settings, other = [], []
    for line in git("status", "--porcelain", "--untracked-files=no").splitlines():
        path = line[3:].strip()
        (settings if path in SETTINGS_FILES else other).append(path)
    return settings, other


def show(rev: str, path: str) -> str | None:
    r = subprocess.run(["git", "-C", ROOT, "show", f"{rev}:{path}"], capture_output=True, text=True, timeout=60)
    return r.stdout if r.returncode == 0 else None


def _json_at(rev: str, path: str):
    text = show(rev, path)
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


# ── What a release changes ────────────────────────────────────────────────────

def changelog() -> dict:
    """{version: entry} from the newest release's CHANGELOG.json: it carries the notes of all the older ones."""
    all_tags = tags()
    out = {}
    for rev in ([all_tags[0]] if all_tags else []):
        for e in (_json_at(rev, "CHANGELOG.json") or {}).get("releases", []):
            if isinstance(e, dict) and isinstance(e.get("version"), str):
                out[e["version"]] = e
    return out


def entry(version: str, log_: dict) -> dict:
    """One release for the page. A release with no notes counts as a rule change: nobody said it's only a fix."""
    e = log_.get(version) or {}
    notes = [str(n) for n in e.get("notes") or []][:20]
    return {"version": version, "date": e.get("date"), "kind": "fix" if e.get("kind") == "fix" else "rule",
            "notes": notes or ["No notes for this release."]}


def rule_diff(target: str) -> dict:
    """What moving from this copy to `target` changes in how Charles trades: the settings (old → new, in plain
    English), the trading files, and the backtest's yearly gain and worst fall when they differ."""
    now_s = (_json_at("HEAD", "strategy.json") or {}).get("settings") or {}
    to_s = (_json_at(target, "strategy.json") or {}).get("settings") or {}
    settings = [{"name": k, "label": LABELS.get(k, k), "old": now_s.get(k), "new": to_s.get(k)}
                for k in sorted(set(now_s) | set(to_s)) if now_s.get(k) != to_s.get(k)]
    files = [f for f in git("diff", "--name-only", "HEAD", target, "--", *RULE_FILES, check=False).splitlines() if f]

    def bt(rev):
        st = (((_json_at(rev, "docs/backtest.json") or {}).get("runs") or {}).get("charles") or {}).get("stats") or {}
        return {k: st.get(k) for k in ("cagr_pct", "max_drawdown_pct")}
    a, b = bt("HEAD"), bt(target)
    backtest = {"old": a, "new": b} if a != b and all(v is not None for v in (*a.values(), *b.values())) else None
    return {"settings": settings, "files": files, "backtest": backtest}


def view(now: datetime | None = None) -> dict:
    """What the dashboard's Updates card shows; written to data/updates.json by the check."""
    now = now or datetime.now(timezone.utc)
    out = {"checked": now.isoformat(timespec="seconds"), "current": current(), "managed": from_release_repo(),
           "state": "unmanaged", "versions": [], "pending": [], "latest": None}
    if not out["managed"]:
        return out
    all_tags, log_ = tags(), changelog()
    if not all_tags:
        return {**out, "state": "no_releases"}
    settings, other = local_changes()
    cur = out["current"]
    pending = [t for t in all_tags if cur is None or _key(t) > _key(cur)]
    versions = []
    for t in all_tags:
        e = entry(t, log_)
        if t != cur:
            d = rule_diff(t)
            e["diff"] = d
            e["needs_confirm"] = bool(d["settings"] or d["files"])
        versions.append(e)
    by = {e["version"]: e for e in versions}
    # a rule change in any release crossed on the way needs the confirm too, forward or back
    for t in pending:
        if any(by[p]["kind"] != "fix" for p in pending if _key(p) <= _key(t)):
            by[t]["needs_confirm"] = True
    if cur:
        for t in all_tags:
            if _key(t) < _key(cur) and any(by[p]["kind"] != "fix" for p in all_tags if _key(t) < _key(p) <= _key(cur)):
                by[t]["needs_confirm"] = True
    out.update(state="available" if pending else "up_to_date", versions=versions, latest=all_tags[0],
               pending=[{"version": t, "kind": by[t]["kind"]} for t in pending],
               local_settings=settings, local_code=other)
    return out


def summary(pending: list[dict]) -> str:
    fixes, rules = sum(p["kind"] == "fix" for p in pending), sum(p["kind"] != "fix" for p in pending)
    return " and ".join(x for x in (f"{fixes} fix{'es' if fixes != 1 else ''}" if fixes else "",
                                    f"{rules} rule change{'s' if rules != 1 else ''}" if rules else "") if x)


def check(now: datetime | None = None, fetch: bool = True) -> dict:
    """Look for new releases (asks GitHub, nothing else) and leave the answer for the dashboard."""
    if fetch and from_release_repo():
        git("fetch", "-q", "--tags", "origin")      # a tag already here is never moved: git refuses without --force
    out = view(now)
    state_io.write_json(os.path.join(DATA, "updates.json"), out, indent=1)
    return out


# ── Queueing (control.py, on behalf of the dashboard) ─────────────────────────

def busy_now(now: datetime | None = None) -> bool:
    """During the daily checks (10:00 and 14:00 ET on weekdays) a restart could cut off an order."""
    t = (now or datetime.now(timezone.utc)).astimezone(ET)
    return t.weekday() < 5 and any(h * 60 - 5 <= t.hour * 60 + t.minute < h * 60 + 20 for h in (10, 14))


def job_path() -> str:
    return os.path.join(DATA, "update_job.json")


def read_job() -> dict | None:
    try:
        with open(job_path()) as f:
            j = json.load(f)
    except (OSError, ValueError):
        return None
    return j if isinstance(j, dict) else None


def job_active(now: float | None = None) -> bool:
    j = read_job()
    now = time.time() if now is None else now
    return bool(j and j.get("status") in ("queued", "running") and now - float(j.get("at_ts") or 0) < JOB_STALE)


def _write_job(job: dict):
    state_io.write_json(job_path(), job, indent=1)


def _check_target(action: str, version, confirm: bool, now: datetime | None) -> dict:
    """The release an update or restore is going to, or UpdateError saying why not."""
    if not from_release_repo():
        raise UpdateError("This copy didn't come from the release repo, so it updates through git.")
    if not isinstance(version, str) or not VERSION_RE.match(version) or version not in tags():
        raise UpdateError("That version doesn't exist.")
    if busy_now(now):
        raise UpdateError("Charles is at its daily check right now. Try again in about 20 minutes.")
    _, other = local_changes()
    if other:
        raise UpdateError("This copy has code changes of its own (" + ", ".join(other[:3]) + "). Nothing was changed.")
    v = view(now)
    e = next((x for x in v["versions"] if x["version"] == version), None)
    if not e or version == v["current"]:
        raise UpdateError(f"Charles is already on {version}.")
    if e.get("needs_confirm") and not confirm:
        raise UpdateError(f"{version} changes how Charles trades, so it needs the confirm box ticked.")
    return e


def queue(action: str, by: str, version=None, confirm: bool = False, settings=None, reason: str = "",
          now: datetime | None = None, start=None, config=None, bot: str = "stocks", source: str = "the chat") -> str:
    """Check a request and leave it for charles-updater.service. Returns what happened, in a sentence."""
    now = now or datetime.now(timezone.utc)
    if job_active(now.timestamp()):
        return "Another update is still running. Try again when it's done."
    job = {"action": action, "by": by, "status": "queued", "at": now.isoformat(timespec="seconds"), "at_ts": now.timestamp()}
    try:
        if action in ("update", "restore"):
            _check_target(action, version, confirm, now)
            job.update(version=version, confirm=bool(confirm))
        elif action in ("apply-settings", "backtest-settings", "undo-settings"):
            if bot not in ("stocks", "crypto") or (bot == "crypto" and action == "backtest-settings"):
                raise UpdateError("That can't be done for this bot.")
            if source not in SOURCES:
                raise UpdateError("Unknown source of a settings change.")
            job.update(bot=bot, source=source)
            if action != "undo-settings":
                import chat
                import crypto_settings
                job.update(settings=chat.check_settings(settings, custom=source in CUSTOM_SOURCES) if bot == "stocks" else crypto_settings.check(settings),
                           reason=str(reason or "")[:300])
        elif action == "copy-config":
            if not isinstance(config, str) or not re.fullmatch(r"lb-[0-9a-f]{6}", config):
                raise UpdateError("Leaderboard ids look like lb-1a2b3c.")
            job.update(config=config)
        elif action != "check-updates":
            raise UpdateError(f"Unknown request: {action}")
    except (UpdateError, ValueError) as e:
        _write_job({**job, "status": "refused", "result": str(e)})
        return str(e)
    _write_job(job)
    (start or _start_updater)()
    return f"Queued: {action} {version or ''}".strip()


def _start_updater():
    subprocess.run(["systemctl", "start", "--no-block", UPDATER_UNIT], check=True, timeout=30)


def copy_config(entry: str, by: str, now: datetime | None = None, fetch=None, root: str = ROOT) -> tuple[bool, str]:
    """Copy a leaderboard entry into this bot. It's fetched and its signature checked here, whatever the dashboard saw.
    It goes through the same path as the sliders, so it can be undone."""
    from leaderboard import client
    cfg = (fetch or client.fetch)(entry)
    reason = f"copied {cfg['id']} ({cfg['name']})"
    if cfg["bot"] == "stocks":
        import chat
        now_set = chat.current_settings()
        changes = {k: v for k, v in cfg["settings"].items() if now_set.get(k) != v}
        # a copy is that plan: the user's own blocks and the money they kept aside go (Undo brings them back)
        if now_set.get("BLOCKS"):
            changes["BLOCKS"] = None
        if now_set.get("INVESTED", 1.0) != 1.0:
            changes["INVESTED"] = 1.0
        if not changes:
            return True, "You already use these settings."
        return chat.apply_settings(changes, by, reason, now, source="the leaderboard", custom=True)
    import crypto_settings
    path, history = os.path.join(root, "crypto.json"), os.path.join(root, "data", "crypto_settings_history.jsonl")
    now_set = crypto_settings.current(path)
    changes = {k: v for k, v in cfg["settings"].items() if now_set.get(k) != v}
    if not changes:
        return True, "You already use these settings."
    crypto_settings.apply_settings(changes, by, reason, now, path=path, source="the leaderboard", history=history)
    return True, f"Copied {cfg['name']}'s settings. The crypto book uses them from its next check."


# ── Carrying it out (charles-updater.service, or charles-update over SSH) ─────

def switch(rev: str):
    """Put the code on `rev`. Settings the user changed are kept: data/ and untracked files aren't touched at all."""
    settings, _ = local_changes()
    saved = {}
    for name in settings:
        with open(os.path.join(ROOT, name)) as f:
            saved[name] = f.read()
    if settings:
        git("checkout", "-q", "--", *settings)
    try:
        git("checkout", "-q", "--detach", rev)
    finally:
        for name, text in saved.items():
            state_io.write_text(os.path.join(ROOT, name), text)


def install_requirements():
    py = sys.executable
    subprocess.run([py, "-m", "pip", "install", "-q", "--no-cache-dir", "-r", os.path.join(ROOT, "requirements.txt")],
                   check=True, timeout=600, capture_output=True, text=True)
    kalshi = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(py))), "kalshi", "bin", "python")
    reqs = os.path.join(ROOT, "kalshi", "requirements.txt")
    if os.path.exists(kalshi) and os.path.exists(reqs):
        subprocess.run([kalshi, "-m", "pip", "install", "-q", "--no-cache-dir", "-r", reqs], check=True, timeout=600,
                       capture_output=True, text=True)


def run_tests():
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", ".", "-t", "."],
                       cwd=os.path.join(ROOT, "tests"), capture_output=True, text=True, timeout=300)
    if r.returncode:
        raise UpdateError("The tests failed on this version: " + (r.stderr.strip().splitlines() or ["no output"])[-1][:200])


def restart():
    subprocess.run(["systemctl", "restart", *SERVICES], check=True, timeout=120)
    subprocess.run(["systemctl", "try-restart", "kalshi-web"], check=False, timeout=60, capture_output=True)    # an add-on most installs don't have


def _probe() -> tuple[bool, int]:
    """(both services active and the dashboard answering, how many times they've restarted so far)."""
    restarts, active = 0, True
    for s in SERVICES:
        r = subprocess.run(["systemctl", "show", s, "-p", "ActiveState", "-p", "NRestarts"], capture_output=True, text=True, timeout=30)
        vals = dict(x.split("=", 1) for x in r.stdout.splitlines() if "=" in x)
        active = active and vals.get("ActiveState") == "active"
        restarts += int(vals.get("NRestarts") or 0)
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/session", timeout=5) as resp:
            active = active and resp.status == 200
    except OSError:
        active = False
    return active, restarts


def healthy(probe=_probe, wait: int = HEALTH_WAIT, settle: int = HEALTH_SETTLE, sleep=time.sleep, clock=time.monotonic) -> bool:
    """True once the bot and dashboard have stayed up, with no restart, for `settle` seconds within `wait`."""
    end, since, base = clock() + wait, None, None
    while clock() < end:
        ok, restarts = probe()
        if base is None:
            base = restarts
        if not ok or restarts != base:
            since, base = None, restarts
        elif since is None:
            since = clock()
        elif clock() - since >= settle:
            return True
        sleep(5)
    return False


def _tell(text: str):
    try:
        import alerts
        alerts.control(text)
    except Exception as e:
        log.warning(f"Notice: {e}")


def _record(row: dict):
    path = os.path.join(DATA, "versions.json")
    try:
        with open(path) as f:
            rows = json.load(f)
    except (OSError, ValueError):
        rows = []
    state_io.write_json(path, ([row] + (rows if isinstance(rows, list) else []))[:50], indent=1)
    with open(os.path.join(DATA, "control.jsonl"), "a") as f:
        f.write(json.dumps({"at": row["at"], "action": "version", "by": row["by"], "result": row["result"],
                            "from": row["from"], "to": row["to"]}) + "\n")


def move(version: str, by: str, confirm: bool, now: datetime | None = None, *, install=install_requirements, tests=run_tests,
         restart_=restart, health=healthy) -> tuple[bool, str]:
    """Go to `version`, keeping data/ and the user's settings. Any failure goes back to where it started."""
    now = now or datetime.now(timezone.utc)
    e = _check_target("update", version, confirm, now)          # checked again: the tags may have moved since the request
    from_rev, from_name = git("rev-parse", "HEAD"), current() or "the previous code"
    restarted = False

    def finish(ok: bool, said: str) -> tuple[bool, str]:
        _record({"at": now.isoformat(timespec="seconds"), "by": by, "from": from_name, "to": version,
                 "kind": e["kind"], "ok": ok, "result": said})
        _tell(said)
        return ok, said

    def back(why: str) -> tuple[bool, str]:
        try:
            switch(from_rev)
            if restarted:
                restart_()
        except (UpdateError, subprocess.SubprocessError, OSError) as err:
            return finish(False, f"🚨 {version} failed ({why}) and going back to {from_name} failed too ({err}). "
                                 f"Over SSH, run: charles-restore")
        return finish(False, f"⚠️ {version} wasn't installed: {why} Charles is back on {from_name}.")

    try:
        switch(version)
        install()
        tests()
        restarted = True
        restart_()
    except (UpdateError, subprocess.SubprocessError, OSError) as err:
        return back(f"{err}")
    if not health():
        return back("the bot wasn't healthy within 2 minutes of the restart.")
    return finish(True, f"⬆️ Charles is now on {version} (it was on {from_name}).")


def run_job(now: datetime | None = None, **steps) -> str | None:
    """Carry out the queued job (what charles-updater.service runs). No job: just look for new releases."""
    now = now or datetime.now(timezone.utc)
    job = read_job()
    if not job or job.get("status") != "queued":
        check(now)
        return None
    _write_job({**job, "status": "running"})
    action = job.get("action")
    extra = {}
    try:
        if action in ("update", "restore"):
            ok, said = move(job.get("version"), job.get("by") or "the dashboard", bool(job.get("confirm")), now, **steps)
        elif action in ("apply-settings", "undo-settings"):
            who = job.get("by") or "the dashboard"
            if job.get("bot") == "crypto":
                import crypto_settings
                ok, said = crypto_settings.apply_settings(job.get("settings"), who, job.get("reason") or "", now, source=job.get("source") or "the sliders") \
                    if action == "apply-settings" else crypto_settings.undo_settings(who, now)
            else:
                import chat
                ok, said = chat.apply_settings(job.get("settings"), who, job.get("reason") or "", now, source=job.get("source") or "the chat",
                                               custom=job.get("source") in CUSTOM_SOURCES) \
                    if action == "apply-settings" else chat.undo_settings(who, now)
        elif action == "copy-config":
            ok, said = copy_config(job.get("config"), job.get("by") or "the dashboard", now)
        elif action == "backtest-settings":
            import chat
            extra["backtest"] = chat.backtest(job.get("settings"), custom=job.get("source") in CUSTOM_SOURCES)
            ok, said = True, "The backtest is done."
        else:
            ok, said = True, "Checked for updates."
    except (UpdateError, ValueError, OSError, subprocess.SubprocessError) as e:
        ok, said = False, f"Nothing was changed: {e}"
    _write_job({**job, **extra, "status": "done" if ok else "failed", "result": said, "finished": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    try:
        check(now, fetch=action == "check-updates")
    except (UpdateError, OSError, subprocess.SubprocessError) as e:
        log.warning(f"update check: {e}")
    return said


# ── SSH ───────────────────────────────────────────────────────────────────────

def _pull_without_releases() -> int:
    """No releases yet, or a private copy: the old charles-update (git pull, requirements, restart)."""
    print(git("pull", "--ff-only") or "Up to date.")
    install_requirements()
    restart()
    print("⬆️  Charles updated and restarted")
    return 0


def _show_target(e: dict):
    print(f"\n{e['version']} ({'rule change' if e['kind'] != 'fix' else 'fix'})")
    for n in e["notes"]:
        print(f"  • {n}")
    d = e.get("diff") or {}
    for s in d.get("settings") or []:
        print(f"  {s['label']}: {s['old']} → {s['new']}")
    if d.get("backtest"):
        o, n = d["backtest"]["old"], d["backtest"]["new"]
        print(f"  Backtest: {o['cagr_pct']}% a year → {n['cagr_pct']}%; worst fall {o['max_drawdown_pct']}% → {n['max_drawdown_pct']}%")
    if d.get("files"):
        print("  Trading code that changes: " + ", ".join(d["files"]))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="charles-update", description="Updates and Restore for Charles.")
    ap.add_argument("command", choices=("update", "restore", "check", "run-job"))
    ap.add_argument("version", nargs="?")
    ap.add_argument("--check", action="store_true", help="only look for a new release")
    ap.add_argument("--yes", action="store_true", help="skip the question (a rule change still counts as confirmed)")
    a = ap.parse_args(argv)
    if a.command == "run-job":
        said = run_job()
        if said:
            print(said)
        return 0
    v = check()
    if a.command == "check" or a.check:
        print({"unmanaged": "This copy didn't come from the release repo: update it with git.",
               "no_releases": "No releases yet.", "up_to_date": f"✅ Up to date · {v['current']}",
               "available": f"Update available: {summary(v['pending'])} (newest {v['latest']})"}[v["state"]])
        return 0
    if v["state"] in ("unmanaged", "no_releases") and a.command == "update":
        return _pull_without_releases()
    if a.command == "restore" and not a.version:
        print("Versions:" if v["versions"] else "No releases yet.")
        for e in v["versions"]:
            print(f"  {e['version']}{'  ← this one' if e['version'] == v['current'] else ''}  {e.get('date') or ''}  {e['notes'][0]}")
        print("\nTo go back to one: charles-restore <version>")
        return 0
    version = a.version or v["latest"]
    if a.command == "update" and v["state"] == "up_to_date" and not a.version:
        print(f"✅ Up to date · {v['current']}")
        return 0
    e = next((x for x in v["versions"] if x["version"] == version), None)
    if not e:
        print("That version doesn't exist.")
        return 2
    _show_target(e)
    if a.yes or not sys.stdin.isatty():
        answer = "y" if a.yes else "n"
    elif e.get("needs_confirm"):
        answer = "y" if input("\nThis changes how Charles trades. Type 'I understand' to go on: ").strip().lower() == "i understand" else "n"
    else:
        answer = input("\nGo on? [y/N] ").strip().lower()
    if answer != "y":
        print("Nothing was changed.")
        return 1
    try:
        ok, said = move(version, "SSH", True)
    except UpdateError as err:
        print(f"⛔ {err}")
        return 1
    print(said)
    check(fetch=False)
    return 0 if ok else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if os.geteuid() != 0:
        print("Run it as root (log in as root, or use sudo).")
        sys.exit(1)
    sys.exit(main(sys.argv[1:]))
