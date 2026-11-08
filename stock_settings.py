"""
stock_settings.py — the stock plan's settings (strategy.json): check a change, apply it, undo it, backtest it.
The stock twin of crypto_settings.py. The dashboard's sliders, Copy from the leaderboard and self-tune all change the
plan through here (charles-updater.service carries the change out), so every change is checked the same way, noted in
data/settings_history.jsonl and can be undone.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import state_io
import strategy_settings
from updates import LABELS

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
log = logging.getLogger(__name__)


def current_settings(path: str | None = None) -> dict:
    with open(path or strategy_settings.PATH) as f:
        return json.load(f).get("settings", {})


def check_settings(changes, path: str | None = None, custom: bool = False) -> dict:
    """The changes exactly as given if the plan is valid with them in; ValueError saying why otherwise. Strict: a
    bad value is refused, never clamped, and the daily loss limit isn't a setting here at all. `custom` (the Plan
    tab's sliders only) also allows the Custom Plan, the presets and self-tune: the chat never picks tickers."""
    from leaderboard import common
    if not isinstance(changes, dict) or not changes:
        raise ValueError("no settings to change")
    allowed = strategy_settings.KEYS if custom else strategy_settings.LIMITS
    unknown = [k for k in changes if k not in allowed]
    if unknown:
        raise ValueError(f"{unknown[0]} isn't a setting that can be changed here")
    merged = common.validate_settings("stocks", {**current_settings(path), **changes}, custom=True)
    return {k: merged[k] for k in changes}


def _word(k: str, v) -> str:
    """A setting's value in the change note and the notice."""
    if k == "BLOCKS":
        return "the default plan" if not v else f"{len(v)} block{'' if len(v) == 1 else 's'} ({', '.join(b['name'] for b in v)})"
    if k == "PRESETS":
        return f"{len(v or [])} saved" if v is not None else "none"
    if k == "SELF_TUNE":
        return "on" if v or v is None else "off"
    if k == "INVESTED":
        return f"{round((1.0 if v is None else v) * 100)}%"
    return str(v)


def _history_path() -> str:
    return os.path.join(DATA, "settings_history.jsonl")


def _write_settings(new: dict, note: str, path: str | None = None):
    path = path or strategy_settings.PATH
    with open(path) as f:
        doc = json.load(f)
    doc["settings"] = new
    doc["last_change"] = note[:300]
    state_io.write_json(path, doc, indent=2)


def _tell(text: str):
    try:
        import alerts
        alerts.control(text)
    except Exception as e:
        log.warning(f"Notice: {e}")


def apply_settings(changes, by: str, reason: str = "", now: datetime | None = None, path: str | None = None, source: str = "the dashboard",
                   history: str | None = None, tell: bool = True, custom: bool = False) -> tuple[bool, str]:
    """Apply a confirmed change (charles-updater.service). The bot reads strategy.json at its next daily check.
    `history` and `tell` are for a preview's test folder: its own record, and no notice about a change the bot never sees.
    `custom`: the change may hold the Custom Plan, the presets or self-tune (from the sliders, or a copy clearing them)."""
    now = now or datetime.now(timezone.utc)
    changes = check_settings(changes, path, custom)
    before = current_settings(path)
    after = {**before, **changes}
    if after == before:
        return True, "Those settings were already in place."
    words = ", ".join(f"{LABELS.get(k, k)} {_word(k, before.get(k))} → {_word(k, v)}" for k, v in changes.items())
    _write_settings(after, f"{now:%Y-%m-%d}: from {source}: {words}" + (f" ({reason})" if reason else ""), path)
    with open(history or _history_path(), "a") as f:
        f.write(json.dumps({"at": now.isoformat(timespec="seconds"), "by": by, "reason": reason, "changes": changes,
                            "before": before, "after": after}) + "\n")
    said = f"🎛️ Settings changed: {words}. Charles uses them at its next daily check. You can undo it on the dashboard."
    if tell:
        _tell(said)
    return True, said


def last_change(history: str | None = None) -> dict | None:
    """The newest change that can still be undone: one applied after the last undo."""
    try:
        with open(history or _history_path()) as f:
            rows = [json.loads(x) for x in f if x.strip()]
    except (OSError, ValueError):
        return None
    pending = []
    for r in rows:
        if r.get("undo"):
            if pending:
                pending.pop()
        else:
            pending.append(r)
    return pending[-1] if pending else None


def undo_settings(by: str, now: datetime | None = None, path: str | None = None, history: str | None = None, tell: bool = True) -> tuple[bool, str]:
    """Put the settings back as they were before the last applied change."""
    now = now or datetime.now(timezone.utc)
    row = last_change(history)
    if not row:
        return True, "There's no change to undo."
    from leaderboard import common
    common.validate_settings("stocks", row["before"], custom=True)
    _write_settings(row["before"], f"{now:%Y-%m-%d}: undid the change of {row['at'][:10]}", path)
    with open(history or _history_path(), "a") as f:
        f.write(json.dumps({"at": now.isoformat(timespec="seconds"), "by": by, "undo": True, "before": row["after"],
                            "after": row["before"]}) + "\n")
    said = "↩️ The last settings change was undone. Charles uses the old settings at its next daily check."
    if tell:
        _tell(said)
    return True, said


def backtest(changes, path: str | None = None, custom: bool = False) -> dict:
    """The 19-year backtest of the plan now and with the change, on today's companies and on sector funds (which have
    no survivor bias). A Custom Plan runs on its own tickers only (each from when it has prices). Heavy: it downloads
    the prices, so it runs in charles-updater.service, on request."""
    import allocator as al
    import backtest_core as bc
    import portfolio
    changes = check_settings(changes, path, custom)
    cur = {**al.DEFAULTS, **strategy_settings.load(path or strategy_settings.PATH)}
    new = {**cur, **changes}
    syms = sorted(set(al.UNIVERSE) | set(bc.SECTORS) | {"SPY", "BIL", cur["AGG_CORE"], new["AGG_CORE"]}
                  | set(portfolio.symbols(cur)) | set(portfolio.symbols(new)))
    dates, opens, closes = bc._download(syms)
    stocks = [k for k in al.UNIVERSE if k in closes]

    def run(s, universe):
        r = bc.simulate(dates, opens, closes, dict(s, SAFE="BIL"), universe=universe)
        st = bc.stats(r["dates"], r["values"])
        return {"cagr_pct": st["cagr_pct"], "max_drawdown_pct": st["max_drawdown_pct"]}
    out = {"history": f"{bc.START[:4]} to {dates[-1]}", "changes": changes,
           "now": {"stocks": run(cur, stocks)}, "with_change": {"stocks": run(new, stocks)}}
    if not cur.get("BLOCKS") and not new.get("BLOCKS"):         # the sector-fund check is for Charles's own stock list
        out["now"]["sectors"], out["with_change"]["sectors"] = run(cur, bc.SECTORS), run(new, bc.SECTORS)
    return out
