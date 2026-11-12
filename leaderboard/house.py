"""House bots: entries the leaderboard runs itself, one per preset, so a new board has something to compare with.

Each is named "House bot: ..." and flagged `house` on the board, so nobody takes one for a person. They are run forward
on real prices exactly like every other entry (sim.update): nothing about them is made up, and they never enter a
challenge. Their "pubkey" is the text house:<bot>:<id>, which no signature can match, so nobody can withdraw or replace
one over the API. A house bot never blocks a person: when someone shares the same settings, server.submit retires the
house entry and the person's takes its place; ensure() then leaves that preset alone.

Only where LEADERBOARD_HOUSE=1 (the real board's nightly run; the test board has its made-up entries instead).
"""
import json
import os
from datetime import datetime, timezone

from leaderboard import common, sim

PREFIX = "house:"
HOUSE = {
    "stocks": [
        ("default", "House bot: Default plan", {}),
        ("steady", "House bot: Steady, no 2x fund", {"AGGRESSIVE": False}),
        ("aggressive", "House bot: 2x aggressive", {"AGGRESSIVE": True, "AGG_CORE": "SSO", "AGG_VOL_MAX": 0.25, "AGG_VOL_EXIT": 0.30}),
        ("index", "House bot: Index only, no picks", {"PICKS": 0, "PICK_PCT": 0.0, "CORE_PCT": 0.95}),
        ("always-in", "House bot: No safety switch", {"SAFETY_SWITCH": False}),
    ],
    "crypto": [
        ("default", "House bot: Default plan", {}),
        ("aggressive", "House bot: 2x aggressive", {"TREND_DAYS": 100, "CHECK_DAYS": 3}),
        ("bitcoin", "House bot: Bitcoin only", {"BTC_PCT": 1.0, "ETH_PCT": 0.0}),
        ("even", "House bot: Half and half", {"BTC_PCT": 0.5, "ETH_PCT": 0.5}),
    ],
}


def is_house(pubkey) -> bool:
    return isinstance(pubkey, str) and pubkey.startswith(PREFIX)


def defaults(bot: str) -> dict:
    """Charles's own plan in the board's shareable keys (as leaderboard.client.current_settings builds it)."""
    if bot == "stocks":
        import allocator
        import strategy_settings
        return {k: allocator.DEFAULTS[k] for k in strategy_settings.LIMITS}
    import crypto_plan
    import crypto_settings
    return {k: v for k, v in crypto_settings.flat(dict(crypto_plan.DEFAULTS)).items() if k in crypto_settings.LIMITS}


def ensure(db, now: datetime | None = None) -> list:
    """Add every house bot that isn't there. Skips a preset someone already shares (active, same settings) and one whose
    house entry exists, active or retired: a retired one means a person took its place once. Returns the ids added."""
    now = now or datetime.now(timezone.utc)
    added = []
    for bot, presets in HOUSE.items():
        for key, name, change in presets:
            pubkey = f"{PREFIX}{bot}:{key}"
            settings = common.validate_settings(bot, {**defaults(bot), **change})
            h = common.config_hash(bot, settings)
            if db.execute("SELECT 1 FROM entries WHERE pubkey=? LIMIT 1", (pubkey,)).fetchone():
                continue
            if db.execute("SELECT 1 FROM entries WHERE bot=? AND hash=? AND status='active' LIMIT 1", (bot, h)).fetchone():
                continue
            eid = "lb-" + os.urandom(3).hex()
            db.execute("INSERT INTO entries (id, bot, pubkey, name, settings, hash, submitted, start_day) VALUES (?,?,?,?,?,?,?,?)",
                       (eid, bot, pubkey, name, json.dumps(settings), h, now.isoformat(timespec="seconds"), sim.start_day(now)))
            added.append(eid)
    db.commit()
    return added
