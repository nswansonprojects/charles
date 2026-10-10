"""
strategy_settings.py — tunable strategy/sizing settings from strategy.json.

strategy.json (git-tracked) is the one place the daily check-up may tune the
strategy to market conditions. apply() runs at startup after
main._apply_wizard_settings, so it overrides config.py and the sizing in
data/launch_authorized.json. Every value is clamped to the hard limits below,
which the check-up is not allowed to edit. MAX_DAILY_LOSS is deliberately not
tunable here: the kill switch stays with the owner.
"""
import json
import logging
import os

import config

log = logging.getLogger(__name__)

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "strategy.json")

# name: (min, max, max change per check-up, type)
LIMITS = {
    "MIN_POSITION_SIZE_PCT":   (0.05, 0.15, 0.025, float),
    "MAX_POSITION_SIZE_PCT":   (0.08, 0.20, 0.025, float),
    "MAX_OPEN_POSITIONS":      (2,    6,    1,     int),
    "STOP_LOSS_PCT":           (0.03, 0.08, 0.01,  float),
    "TAKE_PROFIT_PCT":         (0.08, 0.30, 0.03,  float),
    "TRAILING_STOP_ATR_MULT":  (1.5,  3.5,  0.5,   float),
    "MIN_CONFIDENCE_FLOOR":    (0.60, 0.80, 0.05,  float),
    "LLM_SELL_MIN_CONFIDENCE": (0.65, 0.90, 0.05,  float),
    "TRADE_COOLDOWN_HOURS":    (2,    24,   4,     int),
    "PROFIT_LOCK_TRIGGER":     (0.02, 0.08, 0.01,  float),
    "PROFIT_LOCK_KEEP":        (0.3,  0.8,  0.1,   float),
}


def load() -> dict:
    """The settings from strategy.json, clamped to LIMITS (unknown keys ignored)."""
    try:
        with open(PATH) as f:
            raw = json.load(f).get("settings", {})
    except FileNotFoundError:
        return {}
    out = {}
    for name, value in raw.items():
        if name not in LIMITS:
            log.warning(f"🎛️  strategy.json: unknown setting {name} ignored")
            continue
        lo, hi, _, typ = LIMITS[name]
        try:
            v = typ(value)
        except (TypeError, ValueError):
            log.warning(f"🎛️  strategy.json: {name}={value!r} is not a number — ignored")
            continue
        clamped = min(max(v, lo), hi)
        if clamped != v:
            log.warning(f"🎛️  strategy.json: {name}={v} outside {lo}..{hi} — using {clamped}")
        out[name] = clamped
    lo_pct = out.get("MIN_POSITION_SIZE_PCT", config.MIN_POSITION_SIZE_PCT)
    if out.get("MAX_POSITION_SIZE_PCT", config.MAX_POSITION_SIZE_PCT) < lo_pct:
        log.warning("🎛️  strategy.json: max position size below min — raising max to min")
        out["MAX_POSITION_SIZE_PCT"] = lo_pct
    return out


def apply() -> dict:
    """Load strategy.json and set the values on config. Returns what was applied."""
    try:
        settings = load()
    except Exception as e:
        log.error(f"🎛️  strategy.json unreadable ({e}) — keeping config/launch settings")
        return {}
    for name, value in settings.items():
        setattr(config, name, value)
    if "MIN_CONFIDENCE_FLOOR" in settings:
        # the auto-tuner may raise MIN_CONFIDENCE later, never below the floor
        config.MIN_CONFIDENCE = max(config.MIN_CONFIDENCE, config.MIN_CONFIDENCE_FLOOR)
    if settings:
        log.info("🎛️  Strategy settings: " + ", ".join(f"{k}={v}" for k, v in settings.items()))
    return settings
