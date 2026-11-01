"""
leaderboard/challenges.py — monthly challenges on the leaderboard (owner's Phase 3 plan, 2026-10-29; design §8).

One theme a month, with rules the server enforces (for example "at most 2 momentum picks"). An entry is a settings
config, signed by the install's key, and the bot's own settings aren't touched. Entries close when the month starts,
so everyone runs the same days. leaderboard/sim.py's nightly run does the rest here; nobody's own P/L is accepted.

Ranking that discounts luck: each entry's month is compared with 1,000 random portfolios built under the same rules,
and the rank is the share of them it beat. The 19-year backtest of the same settings shows next to it, so a lucky
month looks like one. No cash prizes (decision F): the winner gets a badge.

Limits: one entry per key per challenge (entering again replaces it), at most 3 per address per challenge, and keys
first seen in the last 30 days are marked "new". Addresses are kept only as a salted hash, and only until the month
starts. Without accounts, fake identities can't be stopped completely, which is one more reason for no prizes.

Open data: each challenge's results are a CSV with no names and no keys (/api/challenges/<id>/results.csv).
"""
import csv
import hashlib
import io
import json
import logging
import os
import secrets
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from leaderboard import common, sim  # noqa: E402

log = logging.getLogger(__name__)

RANDOM_N = 1000
MAX_PER_ADDRESS = 3
NEW_KEY_DAYS = 30
MAX_BACKTESTS_A_NIGHT = 50     # keeps the nightly run bounded; the rest wait for the next night

# The themes, in rotation. Rules: "max" caps a setting, "fixed" pins one, both checked against the entry's settings.
# "universe" limits the momentum picks to those stocks: every entry runs on them, so it's nothing an entry has to set.
# metric: "return" (highest gain wins) or "drop" (the shallowest worst fall wins).
THEMES = [
    {"key": "two-picks", "bot": "stocks", "title": "Two picks only", "metric": "return", "rules": {"max": {"PICKS": 2}},
     "about": "At most 2 momentum picks. Is a tighter list better, or just riskier?"},
    {"key": "no-leverage", "bot": "stocks", "title": "No 2x funds", "metric": "return", "rules": {"fixed": {"AGGRESSIVE": False}},
     "about": "Aggressive mode off. What's the best plan without borrowed speed?"},
    {"key": "calm-crypto", "bot": "crypto", "title": "The calmest crypto", "metric": "drop", "rules": {},
     "about": "Crypto settings ranked by the shallowest worst fall of the month, not by gain."},
    {"key": "no-net", "bot": "stocks", "title": "No safety switch", "metric": "return", "rules": {"fixed": {"SAFETY_SWITCH": False}},
     "about": "The switch to T-bills is off. How do you handle a bad month without it?"},
    {"key": "one-sector", "bot": "stocks", "title": "Health care only", "metric": "return",
     "rules": {"universe": ["LLY", "NVO", "ABBV", "MRK", "PFE", "AMGN", "GILD", "VRTX", "REGN", "MRNA"]},
     "about": "Every momentum pick comes from health-care stocks. Does a narrow list help or hurt?"},
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS challenges (
  id TEXT PRIMARY KEY, theme TEXT NOT NULL, bot TEXT NOT NULL, title TEXT NOT NULL, about TEXT NOT NULL,
  rules TEXT NOT NULL, metric TEXT NOT NULL, starts TEXT NOT NULL, ends TEXT NOT NULL,
  salt TEXT, baseline TEXT, updated TEXT, demo INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS challenge_entries (
  id TEXT PRIMARY KEY, challenge TEXT NOT NULL, pubkey TEXT NOT NULL, name TEXT, settings TEXT NOT NULL,
  hash TEXT NOT NULL, submitted TEXT NOT NULL, ip_tag TEXT, return_pct REAL, max_drop_pct REAL, beat_pct REAL,
  bt_cagr_pct REAL, bt_drop_pct REAL, days INTEGER NOT NULL DEFAULT 0, demo INTEGER NOT NULL DEFAULT 0,
  UNIQUE (challenge, pubkey));
CREATE INDEX IF NOT EXISTS challenge_entries_c ON challenge_entries(challenge);
"""


def ensure(db):
    db.executescript(SCHEMA)


# ── The calendar ──────────────────────────────────────────────────────────────

def month_bounds(month: str) -> tuple[str, str]:
    y, m = int(month[:4]), int(month[5:7])
    first = date(y, m, 1)
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return first.isoformat(), (nxt - timedelta(days=1)).isoformat()


def theme_for(month: str) -> dict:
    return THEMES[(int(month[:4]) * 12 + int(month[5:7]) - 1 - (2026 * 12 + 10)) % len(THEMES)]      # November 2026 is the first


def next_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y + (m == 12)}-{m % 12 + 1:02d}"


def ensure_months(db, now: datetime):
    """This month's challenge and next month's, which is the one taking entries."""
    this = f"{now:%Y-%m}"
    for month in (this, next_month(this)):
        t = theme_for(month)
        starts, ends = month_bounds(month)
        db.execute("INSERT OR IGNORE INTO challenges (id, theme, bot, title, about, rules, metric, starts, ends, salt) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (month, t["key"], t["bot"], t["title"], t["about"], json.dumps(t["rules"]), t["metric"], starts, ends, secrets.token_hex(16)))
    db.commit()


# ── The rules ─────────────────────────────────────────────────────────────────

def check_rules(settings: dict, rules: dict) -> list[str]:
    """What this config breaks, in plain words (empty: it fits)."""
    from updates import LABELS
    out = []
    for k, cap in (rules.get("max") or {}).items():
        if k in settings and settings[k] > cap:
            out.append(f"{LABELS.get(k, k)} can be at most {cap}")
        if k not in settings:
            out.append(f"{LABELS.get(k, k)} has to be set, at most {cap}")
    for k, want in (rules.get("fixed") or {}).items():
        if settings.get(k) != want:
            out.append(f"{LABELS.get(k, k)} has to be {'on' if want is True else 'off' if want is False else want}")
    return out


def fit_rules(settings: dict, rules: dict) -> dict:
    """The nearest config that fits: what the Enter button offers when the bot's own settings don't."""
    s = dict(settings)
    for k, cap in (rules.get("max") or {}).items():
        s[k] = min(s.get(k, cap), cap)
    s.update(rules.get("fixed") or {})
    return s


# ── Entering ──────────────────────────────────────────────────────────────────

def _ip_tag(salt: str, addr: str) -> str:
    return hashlib.sha256(f"{salt}|{addr}".encode()).hexdigest()[:16]


def enter(db, body: dict, addr: str, now: datetime) -> dict:
    from leaderboard.server import Reject, check_signed
    ensure(db)
    ensure_months(db, now)
    p = check_signed(body, "enter", {"challenge", "settings", "name"}, now.timestamp())
    c = db.execute("SELECT * FROM challenges WHERE id=?", (str(p["challenge"]),)).fetchone()
    if not c:
        raise Reject(404, "there's no such challenge")
    if now.date().isoformat() >= c["starts"]:
        raise Reject(409, "entries for this challenge have closed: everyone runs the same days")
    try:
        settings = common.validate_settings(c["bot"], p["settings"])
        name = common.check_name(p["name"])
    except ValueError as e:
        raise Reject(400, str(e))
    broken = check_rules(settings, json.loads(c["rules"]))
    if broken:
        raise Reject(400, "this month's rules: " + "; ".join(broken))
    if name:
        other = db.execute("SELECT 1 FROM entries WHERE lower(name)=lower(?) AND pubkey!=? UNION "
                           "SELECT 1 FROM challenge_entries WHERE lower(name)=lower(?) AND pubkey!=? LIMIT 1",
                           (name, p["pubkey"], name, p["pubkey"])).fetchone()
        if other:
            raise Reject(409, "that name is already used by someone else")
    tag = _ip_tag(c["salt"] or "", addr)
    n = db.execute("SELECT count(*) FROM challenge_entries WHERE challenge=? AND ip_tag=? AND pubkey!=?",
                   (c["id"], tag, p["pubkey"])).fetchone()[0]
    if n >= MAX_PER_ADDRESS:
        raise Reject(429, f"at most {MAX_PER_ADDRESS} entries per address in a challenge")
    h = common.config_hash(c["bot"], settings)
    stamp = now.isoformat(timespec="seconds")
    mine = db.execute("SELECT id FROM challenge_entries WHERE challenge=? AND pubkey=?", (c["id"], p["pubkey"])).fetchone()
    eid = mine["id"] if mine else "ch-" + uuid.uuid4().hex[:6]
    db.execute("INSERT OR REPLACE INTO challenge_entries (id, challenge, pubkey, name, settings, hash, submitted, ip_tag) VALUES (?,?,?,?,?,?,?,?)",
               (eid, c["id"], p["pubkey"], name, json.dumps(settings), h, stamp, tag))
    db.commit()
    return {"ok": True, "id": eid, "replaced": bool(mine),
            "message": f"You're in “{c['title']}”. It starts {c['starts']}. Entering again before then replaces this entry."}


def withdraw(db, body: dict, now: datetime) -> dict:
    from leaderboard.server import Reject, check_signed
    ensure(db)
    p = check_signed(body, "withdraw_entry", {"challenge"}, now.timestamp())
    c = db.execute("SELECT starts FROM challenges WHERE id=?", (str(p["challenge"]),)).fetchone()
    if not c or now.date().isoformat() >= c["starts"]:
        raise Reject(409, "only entries for a challenge that hasn't started can be withdrawn")
    gone = db.execute("DELETE FROM challenge_entries WHERE challenge=? AND pubkey=?", (p["challenge"], p["pubkey"])).rowcount
    db.commit()
    if not gone:
        raise Reject(404, "You have no entry in that challenge.")
    return {"ok": True, "message": "Your entry is withdrawn."}


# ── The referee ───────────────────────────────────────────────────────────────

def month_stats(curve: list) -> dict | None:
    """% return and worst fall over a month's curve, from the $10,000 start."""
    if not curve:
        return None
    peak, worst = sim.START_CASH, 0.0
    for _, v in curve:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    return {"return_pct": round((curve[-1][1] / sim.START_CASH - 1) * 100, 2), "max_drop_pct": round(worst * 100, 2), "days": len(curve)}


def _paths(days: list, series: dict, starts: str, ends: str):
    """(days in the month, {symbol: each day's price over its first}) for the symbols priced on the month's first day."""
    idx = [i for i, d in enumerate(days) if starts <= d <= ends]
    out = {}
    for sym, vals in series.items():
        if not idx or vals[idx[0]] in (None, 0):
            continue
        last, row = vals[idx[0]], []
        for i in idx:
            last = vals[i] if vals[i] is not None else last
            row.append(last / vals[idx[0]])
        out[sym] = row
    return [days[i] for i in idx], out


def random_weights(bot: str, rules: dict, rng, syms: set) -> dict:
    """One random portfolio built under the challenge's rules: {symbol: share}. The rest stays in cash or T-bills."""
    fixed, cap = rules.get("fixed") or {}, rules.get("max") or {}
    if bot == "crypto":
        btc = float(fixed.get("BTC_PCT", rng.uniform(0, min(cap.get("BTC_PCT", 1.0), 1.0))))
        eth = float(fixed.get("ETH_PCT", rng.uniform(0, min(cap.get("ETH_PCT", 1.0), 1.0 - btc))))
        return {"BTC-USD": btc, "ETH-USD": eth}
    import allocator
    core = float(fixed.get("CORE_PCT", rng.uniform(0.5, min(cap.get("CORE_PCT", 0.95), 0.95))))
    picks = int(fixed.get("PICKS", rng.integers(0, min(cap.get("PICKS", 8), 8) + 1)))
    aggressive = fixed.get("AGGRESSIVE", bool(rng.integers(0, 2)))
    core_sym = "SSO" if aggressive and "SSO" in syms else "SPY"
    w = {core_sym: core}
    names = [k for k in rules.get("universe") or allocator.UNIVERSE if k in syms]
    if picks and names:
        each = min(0.10, (1 - core) / picks)
        for k in rng.choice(names, size=min(picks, len(names)), replace=False):
            w[str(k)] = each
    return w


def random_scores(c, days: list, series: dict, n: int = RANDOM_N) -> tuple[list, list]:
    """Each random portfolio's (return %, worst fall %) over the month. Seeded by the challenge's id, so anyone can
    recompute them. Bought on the first day and held; what's left over earns T-bills (stocks) or nothing (crypto)."""
    import numpy as np
    rng = np.random.default_rng(int(hashlib.sha256(c["id"].encode()).hexdigest()[:8], 16))
    rules = json.loads(c["rules"])
    d, paths = _paths(days, series, c["starts"], c["ends"])
    if not d:
        return [], []
    flat = paths.get("BIL") or [1.0] * len(d)
    rets, drops = [], []
    for _ in range(n):
        w = random_weights(c["bot"], rules, rng, set(paths))
        v = np.full(len(d), max(0.0, 1 - sum(w.values()))) * np.array(flat)
        for sym, share in w.items():
            if sym in paths:
                v = v + share * np.array(paths[sym])
        rets.append(round(float((v[-1] - 1) * 100), 2))
        drops.append(round(float(np.min(v / np.maximum.accumulate(np.concatenate(([1.0], v)))[1:] - 1) * 100), 2))
    return rets, drops


def beat_share(score: float, others: list) -> float:
    """The share of random portfolios this entry beat. Both metrics: a bigger number is better (a drop is negative)."""
    return round(100.0 * sum(score > o for o in others) / len(others), 1) if others else 0.0


def settle(db, c, days, series, run, prices):
    """One challenge's results, as of the latest prices."""
    entries = db.execute("SELECT * FROM challenge_entries WHERE challenge=?", (c["id"],)).fetchall()
    if not entries:
        return
    rets, drops = random_scores(c, days, series)
    others = drops if c["metric"] == "drop" else rets
    sector = json.loads(c["rules"]).get("universe")
    for e in entries:
        try:
            st = month_stats(run(json.loads(e["settings"]), c["starts"], prices, c["ends"], **({"universe": sector} if sector else {})))
        except Exception as x:
            log.warning(f"{e['id']}: simulation failed ({x})")
            continue
        if not st:
            continue
        score = st["max_drop_pct"] if c["metric"] == "drop" else st["return_pct"]
        db.execute("UPDATE challenge_entries SET return_pct=?, max_drop_pct=?, beat_pct=?, days=? WHERE id=?",
                   (st["return_pct"], st["max_drop_pct"], beat_share(score, others), st["days"], e["id"]))
    if others:
        s = sorted(others)
        base = {"n": len(s), "median": s[len(s) // 2], "p10": s[len(s) // 10], "p90": s[len(s) * 9 // 10]}
        db.execute("UPDATE challenges SET baseline=? WHERE id=?", (json.dumps(base), c["id"]))


def backtest_entries(db, bot: str, limit: int = MAX_BACKTESTS_A_NIGHT):
    """The long backtest of the entries that have none yet (the same settings over all the history there is)."""
    rows = db.execute("SELECT e.id, e.settings, c.rules FROM challenge_entries e JOIN challenges c ON c.id=e.challenge "
                      "WHERE c.bot=? AND e.bt_cagr_pct IS NULL LIMIT ?", (bot, limit)).fetchall()
    if not rows:
        return
    if bot == "stocks":
        import allocator
        import backtest_core as bc
        syms = sorted(set(allocator.UNIVERSE) | {"SPY", "BIL", "SSO", "UPRO"})
        dates, opens, closes = bc._download(syms)
        universe = [k for k in allocator.UNIVERSE if k in closes]

        def one(s, rules):
            sector = rules.get("universe")
            r = bc.simulate(dates, opens, closes, {**allocator.DEFAULTS, **s, "SAFE": "BIL"}, universe=[k for k in universe if k in sector] if sector else universe)
            st = bc.stats(r["dates"], r["values"])
            return st["cagr_pct"], st["max_drawdown_pct"]
    else:
        import crypto_backtest
        import crypto_settings
        closes = sim.crypto_prices()
        days = sorted(set.intersection(*(set(c["days"]) for c in closes.values())))

        def one(s, rules):
            curve = crypto_backtest.simulate(days, closes, crypto_settings.to_plan(s))
            years = (date.fromisoformat(curve[-1][0]) - date.fromisoformat(curve[0][0])).days / 365.25
            st = month_stats([[d, v * sim.START_CASH / curve[0][1]] for d, v in curve])
            return round(((curve[-1][1] / curve[0][1]) ** (1 / years) - 1) * 100, 1), st["max_drop_pct"]
    for r in rows:
        try:
            cagr, dd = one(json.loads(r["settings"]), json.loads(r["rules"]))
        except Exception as x:
            log.warning(f"{r['id']}: backtest failed ({x})")
            continue
        db.execute("UPDATE challenge_entries SET bt_cagr_pct=?, bt_drop_pct=? WHERE id=?", (cagr, dd, r["id"]))


def update(db, now: datetime | None = None):
    """The nightly run: settle every challenge under way, forget the addresses of those that have started."""
    now = now or datetime.now(timezone.utc)
    ensure(db)
    ensure_months(db, now)
    today = now.date().isoformat()
    db.execute("UPDATE challenge_entries SET ip_tag=NULL WHERE challenge IN (SELECT id FROM challenges WHERE starts<=?)", (today,))
    db.execute("UPDATE challenges SET salt=NULL WHERE starts<=?", (today,))
    live = [c for c in db.execute("SELECT * FROM challenges WHERE starts<=? ORDER BY id", (today,)).fetchall()
            if not (c["updated"] and c["updated"][:10] > c["ends"])]              # a finished one is final
    cache = {}
    for c in live:
        if not db.execute("SELECT 1 FROM challenge_entries WHERE challenge=? LIMIT 1", (c["id"],)).fetchone():
            db.execute("UPDATE challenges SET updated=? WHERE id=?", (now.isoformat(timespec="seconds"), c["id"]))
            continue
        if c["bot"] not in cache:
            if c["bot"] == "stocks":
                dates, opens, closes = sim.stock_prices(min(x["starts"] for x in live if x["bot"] == "stocks"))
                cache["stocks"] = (dates, closes, sim.run_stocks, (dates, opens, closes))
            else:
                p = sim.crypto_prices()
                days = sorted(set.intersection(*(set(v["days"]) for v in p.values())))
                series = {k: [dict(zip(v["days"], v["close"])).get(d) for d in days] for k, v in p.items()}
                cache["crypto"] = (days, series, sim.run_crypto, p)
        settle(db, c, *cache[c["bot"]])
        db.execute("UPDATE challenges SET updated=? WHERE id=?", (now.isoformat(timespec="seconds"), c["id"]))
    for bot in common.BOTS:
        backtest_entries(db, bot)
    db.commit()


# ── The page ──────────────────────────────────────────────────────────────────

def first_seen(db) -> dict:
    out = {}
    for table in ("entries", "challenge_entries"):
        for r in db.execute(f"SELECT pubkey, min(submitted) AS first FROM {table} GROUP BY pubkey"):
            out[r["pubkey"]] = min(out.get(r["pubkey"], r["first"]), r["first"])
    return out


def _standing(c, rows, seen: dict, now: datetime) -> list[dict]:
    """The ranked entries: the share of random portfolios beaten, then the month's own number."""
    done, waiting = [], []
    for e in rows:
        name = e["name"] or f"Anonymous · {common.short_id(e['pubkey'])}"
        fresh = (now - datetime.fromisoformat(seen.get(e["pubkey"], e["submitted"]))).days < NEW_KEY_DAYS
        row = {"id": e["id"], "name": name, "anonymous": not e["name"], "settings": json.loads(e["settings"]), "new": fresh, "demo": bool(e["demo"]),
               "submitted": e["submitted"][:10], "return_pct": e["return_pct"], "max_drop_pct": e["max_drop_pct"], "beat_pct": e["beat_pct"],
               "days": e["days"], "backtest": {"cagr_pct": e["bt_cagr_pct"], "max_drop_pct": e["bt_drop_pct"]} if e["bt_cagr_pct"] is not None else None}
        (waiting if e["beat_pct"] is None else done).append(row)
    key = "max_drop_pct" if c["metric"] == "drop" else "return_pct"
    done.sort(key=lambda r: (-r["beat_pct"], -r[key], r["submitted"]))
    for k, r in enumerate(done, 1):
        r["rank"] = k
    return done + waiting


def view(db, now: datetime | None = None) -> dict:
    """GET /api/challenges: the one taking entries, the one under way, the finished ones."""
    now = now or datetime.now(timezone.utc)
    ensure(db)
    ensure_months(db, now)
    today, seen = now.date().isoformat(), first_seen(db)
    out = {"open": None, "running": None, "finished": []}
    for c in db.execute("SELECT * FROM challenges ORDER BY id DESC").fetchall():
        rows = db.execute("SELECT * FROM challenge_entries WHERE challenge=?", (c["id"],)).fetchall()
        one = {"id": c["id"], "bot": c["bot"], "title": c["title"], "about": c["about"], "rules": json.loads(c["rules"]), "metric": c["metric"],
               "starts": c["starts"], "ends": c["ends"], "baseline": json.loads(c["baseline"]) if c["baseline"] else None, "updated": c["updated"],
               "demo": bool(c["demo"]) or any(r["demo"] for r in rows), "n": len(rows), "csv": f"/api/challenges/{c['id']}/results.csv"}
        if today < c["starts"]:
            out["open"] = {**one, "entries": _standing(c, rows, seen, now)}       # the nearest month that hasn't started
        elif today <= c["ends"]:
            out["running"] = {**one, "entries": _standing(c, rows, seen, now)}
        elif len(out["finished"]) < 12:
            out["finished"].append({**one, "entries": _standing(c, rows, seen, now)[:10]})
    return out


def results_csv(db, challenge: str) -> str | None:
    """A finished or running challenge's results as open data: settings and numbers, no names, no keys."""
    ensure(db)
    c = db.execute("SELECT * FROM challenges WHERE id=?", (challenge,)).fetchone()
    if not c:
        return None
    rows = db.execute("SELECT settings, return_pct, max_drop_pct, beat_pct, days, bt_cagr_pct, bt_drop_pct FROM challenge_entries "
                      "WHERE challenge=? AND beat_pct IS NOT NULL ORDER BY beat_pct DESC", (challenge,)).fetchall()
    names = sorted({k for r in rows for k in json.loads(r["settings"])})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["challenge", "metric", "days", "return_pct", "max_drop_pct", "beat_random_pct", "backtest_cagr_pct", "backtest_max_drop_pct", *names])
    for r in rows:
        s = json.loads(r["settings"])
        w.writerow([challenge, c["metric"], r["days"], r["return_pct"], r["max_drop_pct"], r["beat_pct"], r["bt_cagr_pct"], r["bt_drop_pct"],
                    *[s.get(k, "") for k in names]])
    return buf.getvalue()


# ── Made-up test entries (preview only) ───────────────────────────────────────

def seed_demo(db, month: str, per: int = 8, seed: int = 11):
    """Made-up entries (demo=1) in `month`'s challenge, so the page has something to show. Real prices, fake people."""
    import random
    ensure(db)
    rng = random.Random(seed)
    t = theme_for(month)
    starts, ends = month_bounds(month)
    db.execute("INSERT OR IGNORE INTO challenges (id, theme, bot, title, about, rules, metric, starts, ends, demo) VALUES (?,?,?,?,?,?,?,?,?,1)",
               (month, t["key"], t["bot"], t["title"], t["about"], json.dumps(t["rules"]), t["metric"], starts, ends))
    for k in range(per):
        s = fit_rules(sim.demo_settings(t["bot"], rng), t["rules"])
        settings = common.validate_settings(t["bot"], s)
        key = common.new_key()
        when = (date.fromisoformat(starts) - timedelta(days=rng.randint(1, 12))).isoformat() + "T12:00:00+00:00"
        db.execute("INSERT INTO challenge_entries (id, challenge, pubkey, name, settings, hash, submitted, demo) VALUES (?,?,?,?,?,?,?,1)",
                   ("ch-" + os.urandom(3).hex(), month, common.public_b64(key), sim.DEMO_NAMES[k % len(sim.DEMO_NAMES)], json.dumps(settings),
                    common.config_hash(t["bot"], settings), when))
    db.commit()


def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Monthly challenges: settle them, or add made-up TEST entries.")
    ap.add_argument("--seed-demo", metavar="YYYY-MM", help="first add made-up TEST entries to that month (preview only)")
    ap.add_argument("--clear-demo", action="store_true", help="delete the made-up TEST entries")
    a = ap.parse_args()
    db = sim.connect()
    ensure(db)
    if a.clear_demo:
        db.execute("DELETE FROM challenge_entries WHERE demo=1")
        db.execute("DELETE FROM challenges WHERE demo=1")
        db.commit()
    if a.seed_demo:
        seed_demo(db, a.seed_demo)
    update(db)


if __name__ == "__main__":
    main()
