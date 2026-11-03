"""
leaderboard/common.py — what the leaderboard server, the dashboard's Submit button and `charles-copy`
all agree on: the config schema per bot, signing, and the display-name rules.

Trust model (docs/leaderboard.md):
- A submission is only a config. The server never takes anyone's P/L: it runs each config forward
  itself from the day after it was submitted, on public prices (leaderboard/sim.py).
- Each install signs with its own Ed25519 key (no accounts). The public key is the identity: only it
  can withdraw that entry. A display name is optional; without one the entry is "Anonymous · <id>".
- Configs going out are signed by the server's key; `charles-copy` refuses anything that doesn't
  verify against the pinned public key, or that fails the same strict schema check used here.
"""
import base64
import hashlib
import json
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

BOTS = ("stocks", "crypto")
MAX_BODY = 4096                    # bytes; a config is a dozen numbers
CLOCK_SKEW = 300                   # seconds a signed request may be old or early


def _limits(bot: str) -> dict:
    if bot == "stocks":
        import strategy_settings
        return strategy_settings.LIMITS
    if bot == "crypto":
        import crypto_settings
        return crypto_settings.LIMITS
    raise ValueError(f"unknown bot {bot!r}")


def _custom_keys(bot: str) -> tuple:
    if bot == "stocks":
        import strategy_settings
        return strategy_settings.CUSTOM + strategy_settings.OWN
    import crypto_settings
    return crypto_settings.CUSTOM + crypto_settings.OWN


def _custom_value(name: str, value, bot: str = "stocks"):
    """Your own plan and the user's own choices (strategy_settings / crypto_settings CUSTOM + OWN), as strictly as the rest."""
    import portfolio
    import strategy_settings
    if name == "INVESTED":
        lo, hi = strategy_settings.INVESTED_RANGE
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
            raise ValueError(f"INVESTED must be between {lo} and {hi}")
        return float(value)
    if name == "SELF_TUNE":
        if not isinstance(value, bool):
            raise ValueError("SELF_TUNE must be true or false")
        return value
    if name == "BLOCKS":
        if bot == "crypto":
            import crypto_plan
            return crypto_plan.check_blocks(value)
        return portfolio.check_blocks(value)
    return portfolio.check_presets(value, lambda s: validate_settings(bot, s, custom=True))


def validate_settings(bot: str, settings, custom: bool = False) -> dict:
    """The settings exactly as given if every key is known and every value is the right type and in
    range; raises ValueError otherwise. Stricter than strategy_settings.load(), which clamps: a shared
    config is rejected, never quietly changed. `custom` also allows the Custom Plan and the user's own
    choices (the Plan tab's sliders, undo); the leaderboard never does."""
    if bot not in BOTS:
        raise ValueError(f"unknown bot {bot!r}")
    if not isinstance(settings, dict) or not settings:
        raise ValueError("settings must be a non-empty object")
    limits = _limits(bot)
    out = {}
    for name, value in settings.items():
        if custom and name in _custom_keys(bot):
            out[name] = _custom_value(name, value, bot)
            continue
        if name not in limits:
            raise ValueError(f"unknown setting {name!r}")
        lo, hi, typ = limits[name]
        if typ is bool:
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be true or false")
        elif typ is str:
            if value not in lo:
                raise ValueError(f"{name} must be one of {', '.join(lo)}")
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a number")
            if typ is int and value != int(value):
                raise ValueError(f"{name} must be a whole number")
            if not lo <= value <= hi:
                raise ValueError(f"{name} must be between {lo} and {hi}")
            value = typ(value)
        out[name] = value
    if bot == "stocks":
        import allocator
        s = {**allocator.DEFAULTS, **out}
        if s["PICKS"] * s["PICK_PCT"] > 1 - s["CASH_PCT"] - s["CORE_PCT"] + 1e-9:
            raise ValueError("the picks don't fit next to the core (PICKS × PICK_PCT + CORE_PCT > 100%)")
        if s["AGG_VOL_EXIT"] < s["AGG_VOL_MAX"]:
            raise ValueError("AGG_VOL_EXIT must be at least AGG_VOL_MAX")
    if bot == "crypto" and out.get("BTC_PCT", 0) + out.get("ETH_PCT", 0) > 1 + 1e-9:
        raise ValueError("BTC_PCT + ETH_PCT must be at most 1")
    return out


def canonical(obj) -> bytes:
    """The exact bytes that get signed: sorted keys, no spaces."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def config_hash(bot: str, settings: dict) -> str:
    return hashlib.sha256(canonical({"bot": bot, "settings": settings})).hexdigest()


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def new_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def key_pem(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def load_key(pem: bytes) -> Ed25519PrivateKey:
    return serialization.load_pem_private_key(pem, password=None)


def public_b64(key: Ed25519PrivateKey) -> str:
    return b64(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def sign(key: Ed25519PrivateKey, payload: dict) -> str:
    return b64(key.sign(canonical(payload)))


def verify(public_key_b64: str, payload: dict, signature_b64: str) -> bool:
    try:
        raw = base64.b64decode(public_key_b64, validate=True)
        sig = base64.b64decode(signature_b64, validate=True)
        if len(raw) != 32 or len(sig) != 64:
            return False
        Ed25519PublicKey.from_public_bytes(raw).verify(sig, canonical(payload))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def short_id(public_key_b64: str) -> str:
    """A stable 6-character tag for an anonymous submitter."""
    return hashlib.sha256(public_key_b64.encode()).hexdigest()[:6]


NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,19}$")
RESERVED = ("charles", "admin", "official", "moderator", "support", "anthropic", "claude", "alpaca",
            "kalshi", "coinbase", "anonymous")
BLOCKED = ("fuck", "shit", "cunt", "nigg", "fag", "retard", "rape", "nazi")
# everyday words with a blocked one inside: removed before the check, so the blocked word alone still fails
INNOCENT = ("grape", "drape", "scrape", "trapez", "therap", "scunthorpe", "retardant", "ashkenazi")


def check_name(name) -> str | None:
    """A display name, or None for anonymous; raises ValueError for one that isn't allowed."""
    if name is None or name == "":
        return None
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError("names are 3–20 letters, numbers, _ or -")
    low = name.lower().replace("_", "").replace("-", "")
    words = low
    for w in INNOCENT:
        words = words.replace(w, " ")
    if any(r in low for r in RESERVED) or any(b in words for b in BLOCKED):
        raise ValueError("that name isn't available")
    return name
