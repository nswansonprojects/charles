"""
leaderboard/sim.py — the leaderboard's referee: runs every submitted config forward on public prices.

Nobody's own P/L is trusted. A config's record starts the trading day after it was submitted and
uses the same rules the bots use (backtest_core.simulate → rebalancer.decide for stocks,
crypto_backtest.simulate → crypto_plan for crypto), from $10,000 of simulated money, so every entry
is compared like for like. Retired entries keep the record they had on the day they were withdrawn.

Store: one SQLite file (LEADERBOARD_DB). Run nightly (leaderboard-sim.timer):
    python -m leaderboard.sim             update every entry
    python -m leaderboard.sim --seed-demo add made-up TEST entries, back-dated, for trying the page out
"""
import argparse
import json
import logging
import os
import random
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from leaderboard import common  # noqa: E402

log = logging.getLogger(__name__)

DB = os.environ.get("LEADERBOARD_DB") or os.path.join(ROOT, "data", "leaderboard", "leaderboard.db")
START_CASH = 10_000.0
WARMUP_DAYS = 460          # calendar days of history before the first start: ~316 trading days, enough for the
                           # longest trend line allowed (TREND_DAYS 300) and 252+21 days of momentum
BENCH = {"stocks": "SPY", "crypto": "BTC-USD"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
  id TEXT PRIMARY KEY, bot TEXT NOT NULL, pubkey TEXT NOT NULL, name TEXT,
  settings TEXT NOT NULL, hash TEXT NOT NULL, submitted TEXT NOT NULL, start_day TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active', retired TEXT, demo INTEGER NOT NULL DEFAULT 0,
  copies INTEGER NOT NULL DEFAULT 0, curve TEXT NOT NULL DEFAULT '[]', updated TEXT);
CREATE INDEX IF NOT EXISTS entries_bot ON entries(bot, status);
CREATE TABLE IF NOT EXISTS bench (bot TEXT PRIMARY KEY, curve TEXT NOT NULL, updated TEXT);
"""


def connect(path: str = DB) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    return db


def start_day(submitted: datetime) -> str:
    """Records start the day after submission (so nobody submits with today's move already known)."""
    return (submitted.date() + timedelta(days=1)).isoformat()


# ── Stocks ────────────────────────────────────────────────────────────────────

def stock_prices(first_start: str):
    """(dates, opens, closes) for everything the plan can hold, from enough history before first_start."""
    import allocator
    import backtest_core
    syms = sorted(set(allocator.UNIVERSE) | {"SPY", "SGOV", "BIL", "SSO", "UPRO"})
    begin = (date.fromisoformat(first_start) - timedelta(days=WARMUP_DAYS)).isoformat()
    old = backtest_core.WARMUP_FROM
    backtest_core.WARMUP_FROM = begin
    try:
        return backtest_core._download(syms)
    finally:
        backtest_core.WARMUP_FROM = old


def run_stocks(settings: dict, start: str, prices, end: str | None = None, universe: list | None = None) -> list:
    """[[day, value]] for one config from `start` (inclusive) to `end` (inclusive, default the latest day).
    `universe` limits the picks (a one-sector challenge), else it's the plan's whole list."""
    import allocator
    import backtest_core
    dates, opens, closes = prices
    if end:
        k = sum(1 for d in dates if d <= end)
        dates, opens, closes = dates[:k], {s: v[:k] for s, v in opens.items()}, {s: v[:k] for s, v in closes.items()}
    if not any(d >= start for d in dates):
        return []
    s = {**allocator.DEFAULTS, **settings}
    names = [k for k in universe or allocator.UNIVERSE if k in closes]
    r = backtest_core.simulate(dates, opens, closes, s, start_cash=START_CASH, start=start, universe=names)
    return [[d, v] for d, v in zip(r["dates"], r["values"])]


def bench_stocks(prices) -> list:
    dates, _, closes = prices
    return [[d, c] for d, c in zip(dates, closes["SPY"]) if c is not None]


# ── Crypto ────────────────────────────────────────────────────────────────────

def crypto_prices():
    import crypto_market
    import crypto_plan
    out = {}
    for p in crypto_plan.DEFAULTS["WEIGHTS"]:
        rows = crypto_market.daily_closes(p)
        out[p] = {"days": [d for d, _ in rows], "close": [c for _, c in rows]}
    return out


def run_crypto(settings: dict, start: str, closes: dict, end: str | None = None) -> list:
    import crypto_backtest
    import crypto_settings
    common_days = sorted(set.intersection(*(set(c["days"]) for c in closes.values())))
    days = [d for d in common_days if d >= start and (not end or d <= end)]
    if not days:
        return []
    scale = START_CASH / crypto_backtest.START_CASH
    curve = crypto_backtest.simulate(days, closes, crypto_settings.to_plan(settings))
    return [[d, round(v * scale, 2)] for d, v in curve]


def bench_crypto(closes: dict) -> list:
    c = closes[BENCH["crypto"]]
    return [[d, v] for d, v in zip(c["days"], c["close"])]


# ── Update ────────────────────────────────────────────────────────────────────

def update(db: sqlite3.Connection, bots=common.BOTS):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for bot in bots:
        rows = db.execute("SELECT * FROM entries WHERE bot=?", (bot,)).fetchall()
        if not rows:
            continue
        first = min(r["start_day"] for r in rows)
        if bot == "stocks":
            prices = stock_prices(first)
            run, bench = run_stocks, bench_stocks(prices)
        else:
            prices = crypto_prices()
            run, bench = run_crypto, bench_crypto(prices)
        db.execute("INSERT OR REPLACE INTO bench VALUES (?,?,?)",
                   (bot, json.dumps([p for p in bench if p[0] >= (date.fromisoformat(first) - timedelta(days=7)).isoformat()]), now))
        for r in rows:
            end = r["retired"][:10] if r["status"] == "retired" and r["retired"] else None
            if end and r["updated"] and r["updated"][:10] > end:
                continue                       # a retired record is frozen
            try:
                curve = run(json.loads(r["settings"]), r["start_day"], prices, end)
            except Exception as e:
                log.warning(f"{r['id']}: simulation failed ({e})")
                continue
            db.execute("UPDATE entries SET curve=?, updated=? WHERE id=?", (json.dumps(curve), now, r["id"]))
        db.commit()
        log.info(f"{bot}: {len(rows)} entries updated")


# ── Made-up test entries (preview only) ───────────────────────────────────────

DEMO_NAMES = ["trendrider", "momo_mike", None, "safe_sally", None, "bullbear", "quietcompounder", None,
              "spy_maxi", "leverage_lou", None, "night_owl"]


def demo_settings(bot: str, rng: random.Random) -> dict:
    if bot == "stocks":
        picks = rng.choice([0, 2, 4, 4, 6, 8])
        core = rng.choice([0.6, 0.7, 0.8, 0.8, 0.9])
        pick_pct = min(0.10, int((1 - core) / picks * 1000) / 1000) if picks else 0.0
        return {"CORE_PCT": core, "PICKS": picks, "PICK_PCT": pick_pct, "TREND_DAYS": rng.choice([150, 200, 200, 250]),
                "SAFETY_SWITCH": rng.random() > 0.2, "SWITCH_CHECK": rng.choice(["monthly", "monthly", "daily"]),
                "AGGRESSIVE": rng.random() > 0.4, "AGG_CORE": rng.choice(["SSO", "SSO", "UPRO"]),
                "AGG_VOL_MAX": 0.2, "AGG_VOL_EXIT": 0.25, "DRIFT_PCT": rng.choice([0.02, 0.03, 0.05])}
    btc = rng.choice([0.3, 0.5, 0.6, 0.7, 1.0])
    return {"BTC_PCT": btc, "ETH_PCT": round(rng.choice([0.0, 1 - btc, (1 - btc) / 2]), 2),
            "TREND_DAYS": rng.choice([50, 100, 150, 200]), "CHECK_DAYS": rng.choice([1, 7, 7, 14]), "MIN_TRADE": 0.05}


def seed_demo(db: sqlite3.Connection, per_bot: int = 12, seed: int = 7):
    """Back-dated, made-up entries flagged demo=1 so the page has something to show. Never on a real board."""
    rng = random.Random(seed)
    now = datetime.now(timezone.utc)
    for bot in common.BOTS:
        for k in range(per_bot):
            settings = common.validate_settings(bot, demo_settings(bot, rng))
            key = common.new_key()
            submitted = now - timedelta(days=rng.choice([3, 9, 15, 25, 40, 60, 90, 120, 180, 240, 300, 365]))
            status, retired = "active", None
            if k == per_bot - 1:
                submitted = now - timedelta(days=120)
                status, retired = "retired", (submitted + timedelta(days=45)).isoformat(timespec="seconds")
            db.execute("INSERT INTO entries (id, bot, pubkey, name, settings, hash, submitted, start_day, status, retired, demo, copies)"
                       " VALUES (?,?,?,?,?,?,?,?,?,?,1,?)",
                       ("lb-" + os.urandom(3).hex(), bot, common.public_b64(key), DEMO_NAMES[k % len(DEMO_NAMES)],
                        json.dumps(settings), common.config_hash(bot, settings), submitted.isoformat(timespec="seconds"),
                        start_day(submitted), status, retired, rng.choice([0, 0, 1, 3, 8, 21])))
    db.commit()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Run every leaderboard config forward on public prices.")
    ap.add_argument("--seed-demo", action="store_true", help="first add made-up TEST entries (preview only)")
    ap.add_argument("--clear-demo", action="store_true", help="delete the made-up TEST entries")
    a = ap.parse_args()
    db = connect()
    if a.clear_demo:
        db.execute("DELETE FROM entries WHERE demo=1")
        db.commit()
    if a.seed_demo:
        seed_demo(db)
    update(db)
    from leaderboard import challenges
    challenges.update(db)


if __name__ == "__main__":
    main()
