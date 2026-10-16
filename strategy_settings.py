"""
strategy_settings.py — the plan's settings from strategy.json (git-tracked), over allocator.DEFAULTS.

Every value is clamped to the hard limits below. Changes are slow and evidence-based: evaluate.py
tests one-step changes every month, and at most one change that passes every test is applied a
quarter. MAX_DAILY_LOSS is not here on purpose: the kill switch stays the owner's (config.py /
data/launch_authorized.json).
"""
import json
import logging
import os

log = logging.getLogger(__name__)

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "strategy.json")

# name: (min, max, type) — for str settings, (allowed values, None, str)
LIMITS = {
    "CORE_PCT":      (0.50, 0.95, float),
    "PICKS":         (0,    8,    int),
    "PICK_PCT":      (0.0,  0.10, float),
    "DRIFT_PCT":     (0.01, 0.10, float),
    "TREND_DAYS":    (100,  300,  int),
    "SAFETY_SWITCH": (None, None, bool),
    "SWITCH_CHECK":  (("monthly", "daily"), None, str),
    "AGGRESSIVE":    (None, None, bool),
    "AGG_CORE":      (("SSO", "UPRO"), None, str),        # 2x or 3x S&P fund — the owner's call
    "AGG_VOL_MAX":   (0.10, 0.25, float),
    "AGG_VOL_EXIT":  (0.15, 0.35, float),
}


def load(path: str = PATH) -> dict:
    """The settings from strategy.json, clamped to LIMITS (unknown keys ignored)."""
    try:
        with open(path) as f:
            raw = json.load(f).get("settings", {})
    except FileNotFoundError:
        return {}
    out = {}
    for name, value in raw.items():
        if name not in LIMITS:
            log.warning(f"🎛️  strategy.json: unknown setting {name} ignored")
            continue
        lo, hi, typ = LIMITS[name]
        if typ is bool:
            if isinstance(value, bool):
                out[name] = value
            else:
                log.warning(f"🎛️  strategy.json: {name}={value!r} is not true/false — ignored")
            continue
        if typ is str:
            if value in lo:
                out[name] = value
            else:
                log.warning(f"🎛️  strategy.json: {name}={value!r} not one of {lo} — ignored")
            continue
        try:
            v = typ(value)
        except (TypeError, ValueError):
            log.warning(f"🎛️  strategy.json: {name}={value!r} is not a number — ignored")
            continue
        clamped = min(max(v, lo), hi)
        if clamped != v:
            log.warning(f"🎛️  strategy.json: {name}={v} outside {lo}..{hi} — using {clamped}")
        out[name] = clamped
    import allocator
    s = {**allocator.DEFAULTS, **out}
    if s["AGG_VOL_EXIT"] < s["AGG_VOL_MAX"]:
        out["AGG_VOL_EXIT"] = s["AGG_VOL_MAX"]
        log.warning("🎛️  strategy.json: AGG_VOL_EXIT below AGG_VOL_MAX — raised to match")
    room = 1 - s["CASH_PCT"] - s["CORE_PCT"]
    if s["PICKS"] * s["PICK_PCT"] > room + 1e-9:
        out["PICK_PCT"] = round(room / s["PICKS"], 4) if s["PICKS"] else 0.0
        log.warning(f"🎛️  strategy.json: picks don't fit next to the core — PICK_PCT {out['PICK_PCT']}")
    return out


def apply() -> dict:
    """Load and log the settings (the rebalancer reads them itself on every run)."""
    try:
        settings = load()
    except Exception as e:
        log.error(f"🎛️  strategy.json unreadable ({e}) — using the defaults")
        return {}
    if settings:
        log.info("🎛️  Strategy settings: " + ", ".join(f"{k}={v}" for k, v in settings.items()))
    return settings
