"""
crypto_settings.py — the crypto book's settings from crypto.json (git-tracked), over crypto_plan.DEFAULTS.

Same idea as strategy_settings.py: every value is clamped to the limits below and unknown keys are
ignored. The coin weights are flat keys (BTC_PCT, ETH_PCT) so a leaderboard config is a plain list
of numbers. FEE is not a setting: it's what Coinbase charges.
"""
import json
import logging
import os
from datetime import datetime, timezone

import state_io

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(ROOT, "crypto.json")
HISTORY = os.path.join(ROOT, "data", "crypto_settings_history.jsonl")

# name: (min, max, type)
LIMITS = {
    "BTC_PCT":    (0.0,  1.0,  float),
    "ETH_PCT":    (0.0,  1.0,  float),
    "TREND_DAYS": (50,   300,  int),
    "CHECK_DAYS": (1,    30,   int),
    "MIN_TRADE":  (0.01, 0.20, float),
}
WEIGHT_KEYS = {"BTC_PCT": "BTC-USD", "ETH_PCT": "ETH-USD"}


def flat(settings: dict) -> dict:
    """crypto_plan settings (WEIGHTS dict) → the flat keys used in crypto.json."""
    out = {k: v for k, v in settings.items() if k in LIMITS}
    for key, product in WEIGHT_KEYS.items():
        out[key] = settings.get("WEIGHTS", {}).get(product, 0.0)
    return out


def to_plan(flat_settings: dict) -> dict:
    """Flat crypto.json keys → crypto_plan settings (WEIGHTS dict)."""
    out = {k: v for k, v in flat_settings.items() if k in LIMITS and k not in WEIGHT_KEYS}
    if any(k in flat_settings for k in WEIGHT_KEYS):
        base = flat({"WEIGHTS": __import__("crypto_plan").DEFAULTS["WEIGHTS"]})
        out["WEIGHTS"] = {p: float(flat_settings.get(k, base[k])) for k, p in WEIGHT_KEYS.items()}
    return out


def load(path: str = PATH) -> dict:
    """crypto_plan settings from crypto.json, clamped to LIMITS (unknown keys ignored). {} without the file."""
    try:
        with open(path) as f:
            raw = json.load(f).get("settings", {})
    except FileNotFoundError:
        return {}
    out = {}
    for name, value in raw.items():
        if name not in LIMITS:
            log.warning(f"🪙 crypto.json: unknown setting {name} ignored")
            continue
        lo, hi, typ = LIMITS[name]
        if isinstance(value, bool):
            log.warning(f"🪙 crypto.json: {name}={value!r} is not a number — ignored")
            continue
        try:
            v = typ(value)
        except (TypeError, ValueError):
            log.warning(f"🪙 crypto.json: {name}={value!r} is not a number — ignored")
            continue
        out[name] = min(max(v, lo), hi)
    if out.get("BTC_PCT", 0) + out.get("ETH_PCT", 0) > 1:
        total = out["BTC_PCT"] + out["ETH_PCT"]
        out["BTC_PCT"], out["ETH_PCT"] = out["BTC_PCT"] / total, out["ETH_PCT"] / total
        log.warning("🪙 crypto.json: coin shares add up to more than 100% — scaled down")
    return to_plan(out)


# ── Changing them: the sliders and Copy (charles-updater.service writes the file; the dashboard can't) ──

LABELS = {"BTC_PCT": "Bitcoin's share", "ETH_PCT": "Ether's share", "TREND_DAYS": "Trend line (days)",
          "CHECK_DAYS": "Days between checks", "MIN_TRADE": "Skip moves under"}


def defaults() -> dict:
    import crypto_plan
    return flat(crypto_plan.DEFAULTS)


def current(path: str = PATH) -> dict:
    """Every setting as it stands, in the flat keys: crypto.json over the defaults."""
    import crypto_plan
    return flat({**crypto_plan.DEFAULTS, **load(path)})


def check(changes, path: str = PATH) -> dict:
    """The changes exactly as given if the plan is valid with them in; ValueError saying why otherwise. Strict: a bad value is
    refused, never clamped."""
    from leaderboard import common
    if not isinstance(changes, dict) or not changes:
        raise ValueError("no settings to change")
    merged = common.validate_settings("crypto", {**current(path), **changes})
    return {k: merged[k] for k in changes}


def _write(new: dict, note: str, path: str):
    state_io.write_json(path, {"settings": new, "last_change": note[:300]}, indent=2)


def _tell(text: str):
    try:
        import alerts
        alerts.control(text)
    except Exception as e:
        log.warning(f"Notice: {e}")


def apply_settings(changes, by: str, reason: str = "", now: datetime | None = None, path: str = PATH, source: str = "the sliders",
                   history: str = HISTORY, tell: bool = True) -> tuple[bool, str]:
    """Apply a confirmed change. The book reads crypto.json at its next hourly step."""
    now = now or datetime.now(timezone.utc)
    changes = check(changes, path)
    before = current(path)
    after = {**before, **changes}
    if after == before:
        return True, "Those settings were already in place."
    words = ", ".join(f"{LABELS.get(k, k)} {before.get(k)} → {v}" for k, v in changes.items())
    _write(after, f"{now:%Y-%m-%d}: from {source}: {words}" + (f" ({reason})" if reason else ""), path)
    with open(history, "a") as f:
        f.write(json.dumps({"at": now.isoformat(timespec="seconds"), "by": by, "reason": reason, "changes": changes,
                            "before": before, "after": after}) + "\n")
    said = f"🎛️ Crypto settings changed: {words}. The book uses them from its next check. You can undo it on the dashboard."
    if tell:
        _tell(said)
    return True, said


def last_change(history: str = HISTORY) -> dict | None:
    """The newest change that can still be undone: one applied after the last undo."""
    try:
        with open(history) as f:
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


def undo_settings(by: str, now: datetime | None = None, path: str = PATH, history: str = HISTORY, tell: bool = True) -> tuple[bool, str]:
    """Put the settings back as they were before the last applied change."""
    from leaderboard import common
    now = now or datetime.now(timezone.utc)
    row = last_change(history)
    if not row:
        return True, "There's no change to undo."
    common.validate_settings("crypto", row["before"])
    _write(row["before"], f"{now:%Y-%m-%d}: undid the change of {row['at'][:10]}", path)
    with open(history, "a") as f:
        f.write(json.dumps({"at": now.isoformat(timespec="seconds"), "by": by, "undo": True, "before": row["after"],
                            "after": row["before"]}) + "\n")
    said = "↩️ The last crypto settings change was undone. The book uses the old settings from its next check."
    if tell:
        _tell(said)
    return True, said
