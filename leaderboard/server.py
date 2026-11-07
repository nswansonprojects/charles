"""
leaderboard/server.py — the public leaderboard API (and, for the preview, the website next to it).

    GET  /api/leaderboard?bot=stocks|crypto&range=week|month|all   the board
    GET  /api/leaderboard/config/<id>    one config, signed by this server's key (what charles-copy fetches)
    GET  /api/leaderboard/pubkey         this server's public key
    GET  /api/leaderboard/mine?pubkey=…  the entries one install has submitted
    POST /api/leaderboard/submit         {"payload": {...}, "sig": "..."} signed by the install's key
    POST /api/leaderboard/withdraw       same, for one entry it owns
    GET  /api/challenges                 this month's challenge, the next one, the finished ones (challenges.py)
    GET  /api/challenges/<id>/results.csv  a challenge's results as open data, with no names and no keys
    GET  /api/challenges/mine?pubkey=…   the challenges one install has entered
    POST /api/challenges/enter           {"payload": {...}, "sig": "..."} signed by the install's key
    POST /api/challenges/withdraw        same, before the challenge starts

Every write is signed, size-capped, rate-limited per address and schema-checked (leaderboard/common.py).
Results are never accepted from anyone: leaderboard/sim.py makes them. Python stdlib server, like webdash.py.
SITE_DIR (preview only) also serves a copy of the website from the same address.
"""
import hashlib
import json
import logging
import mimetypes
import os
import secrets
import sys
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from leaderboard import challenges, common, sim  # noqa: E402

log = logging.getLogger(__name__)

PORT = int(os.environ.get("LEADERBOARD_PORT", "8506"))
KEY_PATH = os.environ.get("LEADERBOARD_KEY") or os.path.join(os.path.dirname(sim.DB), "server_key.pem")
SITE_DIR = os.environ.get("SITE_DIR")
COPY_CMD = os.environ.get("LEADERBOARD_COPY_CMD", "charles-copy")
TEST_MODE = os.environ.get("LEADERBOARD_TEST") == "1"
TRUST_PROXY = os.environ.get("LEADERBOARD_TRUST_PROXY") == "1"     # behind nginx: use X-Forwarded-For
HOST = os.environ.get("LEADERBOARD_HOST", "0.0.0.0")     # the real board sits behind nginx: 127.0.0.1
SETUP_DEMO = None
if os.environ.get("SETUP_DEMO") == "1":     # the test site's "Set it up for me" demo: nothing real. setupservice/ isn't in the public release
    from setupservice import service as setup_service
    SETUP_DEMO = setup_service.demo_service()
MIN_DAYS = 20                 # days of results before an entry is ranked
RANGES = {"week": 7, "month": 30, "all": None}
MAX_ACTIVE_PER_BOT = 2000     # keeps the nightly simulation bounded
LIMITS = {"submit": (5, 3600), "withdraw": (20, 3600), "enter": (10, 3600), "read": (240, 60)}   # (requests, per seconds) per address


# ── The board (pure; tested in tests/test_leaderboard.py) ─────────────────────

def display_name(row) -> str:
    return row["name"] or f"Anonymous · {common.short_id(row['pubkey'])}"


def value_before(curve: list, day: str, default: float):
    """The last value strictly before `day`, else `default`."""
    out = default
    for d, v in curve:
        if d >= day:
            break
        out = v
    return out


def window_stats(curve: list, since: str | None, start_value: float) -> dict | None:
    """% return and worst drop over the points on/after `since` (all of them when None)."""
    pts = [p for p in curve if since is None or p[0] >= since]
    if not pts:
        return None
    base = start_value if since is None else value_before(curve, since, start_value)
    peak, worst = base, 0.0
    for _, v in pts:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    return {"return_pct": round((pts[-1][1] / base - 1) * 100, 2), "max_drop_pct": round(worst * 100, 1),
            "points": pts}


def bench_return(bench: list, since: str) -> float | None:
    pts = [p for p in bench if p[0] >= since]
    base = value_before(bench, since, None)
    if not pts or not base:
        return None
    return round((pts[-1][1] / base - 1) * 100, 2)


def spark(points: list, n: int = 40) -> list:
    if len(points) <= n:
        return [v for _, v in points]
    step = (len(points) - 1) / (n - 1)
    return [points[round(i * step)][1] for i in range(n)]


def board(rows: list, bench: list, rng: str, today: str) -> dict:
    days = RANGES[rng]
    since = (date.fromisoformat(today) - timedelta(days=days)).isoformat() if days else None
    ranked, warming, retired = [], [], []
    for r in rows:
        curve = json.loads(r["curve"])
        base = {"id": r["id"], "name": display_name(r), "anonymous": not r["name"], "submitted": r["submitted"][:10],
                "days": len(curve), "copies": r["copies"], "demo": bool(r["demo"]), "settings": json.loads(r["settings"])}
        if r["status"] == "retired":
            st = window_stats(curve, None, sim.START_CASH)
            retired.append({**base, "retired": (r["retired"] or "")[:10],
                            "return_pct": st["return_pct"] if st else None})
            continue
        if len(curve) < MIN_DAYS:
            warming.append({**base, "need": MIN_DAYS})
            continue
        win_since = max(since, r["start_day"]) if since else None
        st = window_stats(curve, win_since, sim.START_CASH)
        if not st:
            continue
        b = bench_return(bench, win_since or r["start_day"])
        ranked.append({**base, "return_pct": st["return_pct"], "max_drop_pct": st["max_drop_pct"],
                       "bench_pct": b, "spark": spark(st["points"])})
    ranked.sort(key=lambda e: (-e["return_pct"], e["max_drop_pct"] * -1, e["submitted"]))
    for k, e in enumerate(ranked, 1):
        e["rank"] = k
    warming.sort(key=lambda e: -e["days"])
    retired.sort(key=lambda e: e["retired"], reverse=True)
    return {"range": rng, "since": since, "bench": sim.BENCH[rows[0]["bot"]] if rows else None,
            "bench_pct": bench_return(bench, since) if since else None,
            "ranked": ranked, "warming": warming, "retired": retired[:20]}


# ── Writes ────────────────────────────────────────────────────────────────────

class Reject(Exception):
    def __init__(self, code: int, message: str, **extra):
        super().__init__(message)
        self.code, self.message, self.extra = code, message, extra


def check_signed(body: dict, action: str, fields: set, now: float) -> dict:
    """The payload of a signed request, or Reject."""
    if not isinstance(body, dict) or set(body) != {"payload", "sig"}:
        raise Reject(400, "expected {payload, sig}")
    p = body["payload"]
    if not isinstance(p, dict) or set(p) != fields | {"v", "action", "pubkey", "ts"} or p["action"] != action or p["v"] != 1:
        raise Reject(400, "malformed request")
    if not isinstance(p["ts"], int) or abs(now - p["ts"]) > common.CLOCK_SKEW:
        raise Reject(400, "the request's clock is off by more than 5 minutes")
    if not isinstance(p["pubkey"], str) or not common.verify(p["pubkey"], p, body["sig"]):
        raise Reject(403, "signature doesn't verify")
    return p


def submit(db, body: dict, now: datetime) -> dict:
    p = check_signed(body, "submit", {"bot", "settings", "name"}, now.timestamp())
    try:
        settings = common.validate_settings(p["bot"], p["settings"])
        name = common.check_name(p["name"])
    except ValueError as e:
        raise Reject(400, str(e))
    h = common.config_hash(p["bot"], settings)
    if name:
        other = db.execute("SELECT 1 FROM entries WHERE lower(name)=lower(?) AND pubkey!=? LIMIT 1", (name, p["pubkey"])).fetchone()
        if other:
            raise Reject(409, "that name is already used by someone else")
    same = db.execute("SELECT id, pubkey FROM entries WHERE bot=? AND hash=? AND status='active'", (p["bot"], h)).fetchone()
    if same and same["pubkey"] == p["pubkey"]:
        return {"ok": True, "id": same["id"], "message": "Already on the leaderboard."}
    if same:
        raise Reject(409, f"This exact config is already on the leaderboard as {same['id']}.", id=same["id"])
    n = db.execute("SELECT count(*) FROM entries WHERE bot=? AND status='active'", (p["bot"],)).fetchone()[0]
    if n >= MAX_ACTIVE_PER_BOT:
        raise Reject(503, "the leaderboard is full right now; try again later")
    stamp = now.isoformat(timespec="seconds")
    replaced = [r["id"] for r in db.execute("SELECT id FROM entries WHERE bot=? AND pubkey=? AND status='active'",
                                             (p["bot"], p["pubkey"]))]
    db.execute("UPDATE entries SET status='retired', retired=? WHERE bot=? AND pubkey=? AND status='active'",
               (stamp, p["bot"], p["pubkey"]))
    eid = "lb-" + uuid.uuid4().hex[:6]
    db.execute("INSERT INTO entries (id, bot, pubkey, name, settings, hash, submitted, start_day) VALUES (?,?,?,?,?,?,?,?)",
               (eid, p["bot"], p["pubkey"], name, json.dumps(settings), h, stamp, sim.start_day(now)))
    db.commit()
    return {"ok": True, "id": eid, "start_day": sim.start_day(now), "replaced": replaced,
            "message": f"Submitted as {eid}. Its record starts {sim.start_day(now)} and it's ranked after {MIN_DAYS} days."}


def withdraw(db, body: dict, now: datetime) -> dict:
    p = check_signed(body, "withdraw", {"id"}, now.timestamp())
    row = db.execute("SELECT pubkey, status FROM entries WHERE id=?", (str(p["id"]),)).fetchone()
    if not row or row["pubkey"] != p["pubkey"]:
        raise Reject(404, "no entry of yours with that id")
    if row["status"] == "active":
        db.execute("UPDATE entries SET status='retired', retired=? WHERE id=?", (now.isoformat(timespec="seconds"), p["id"]))
        db.commit()
    return {"ok": True, "id": p["id"], "message": "Withdrawn. It stays on the page as retired, with the record it had."}


# ── Server key and signed configs ─────────────────────────────────────────────

def server_key(path: str = KEY_PATH):
    try:
        with open(path, "rb") as f:
            return common.load_key(f.read())
    except FileNotFoundError:
        if not TEST_MODE:
            raise SystemExit(f"no server key at {path} (generate it offline; see docs/leaderboard.md)")
    key = common.new_key()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(common.key_pem(key))
    log.warning(f"made a new TEST server key at {path}")
    return key


def manifest(row, key) -> dict:
    payload = {"v": 1, "id": row["id"], "bot": row["bot"], "settings": json.loads(row["settings"]), "hash": row["hash"],
               "name": display_name(row), "issued": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    return {"payload": payload, "sig": common.sign(key, payload)}


# ── HTTP ──────────────────────────────────────────────────────────────────────

_lock = threading.Lock()
_hits: dict = {}
_copied: set = set()
_SALT = secrets.token_hex(16)        # this process's: the copy counter remembers a hash of each address for a day, never the address


def allowed(kind: str, addr: str, now: float) -> bool:
    n, per = LIMITS[kind]
    with _lock:
        recent = [t for t in _hits.get((kind, addr), []) if now - t < per]
        if len(recent) >= n:
            _hits[(kind, addr)] = recent
            return False
        recent.append(now)
        _hits[(kind, addr)] = recent
        if len(_hits) > 50_000:
            _hits.clear()
        return True


class Handler(BaseHTTPRequestHandler):
    db_path = sim.DB
    key = None

    def _addr(self):
        if TRUST_PROXY and self.headers.get("X-Forwarded-For"):
            return self.headers["X-Forwarded-For"].split(",")[-1].strip()
        return self.client_address[0]

    def _send(self, code, body: bytes, ctype, cache="no-store"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, allow_nan=False).encode(), "application/json")

    def _db(self):
        return sim.connect(self.db_path)

    def _setup_demo(self, path: str) -> bool:
        """Answers it and returns True when it's a request for the demo."""
        if not SETUP_DEMO or not (path.rstrip("/") == "/set-up-for-me" or path.startswith("/api/setup")):
            return False
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if not 0 <= n <= setup_service.MAX_BODY:
            self._json(413, {"error": "too big"})
            return True
        code, ctype, body = setup_service.handle(SETUP_DEMO, self.command, self.path, self.headers, self.rfile.read(n), self._addr())
        self._send(code, body, ctype, cache="no-store")
        return True

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if self._setup_demo(path):
            return
        q = {k: v[0] for k, v in parse_qs(query).items()}
        if path.startswith("/api/") and not allowed("read", self._addr(), time.time()):
            return self._json(429, {"ok": False, "message": "slow down"})
        if path == "/api/leaderboard":
            bot, rng = q.get("bot", "stocks"), q.get("range", "week")
            if bot not in common.BOTS or rng not in RANGES:
                return self._json(400, {"ok": False, "message": "bad bot or range"})
            db = self._db()
            rows = db.execute("SELECT * FROM entries WHERE bot=?", (bot,)).fetchall()
            b = db.execute("SELECT curve, updated FROM bench WHERE bot=?", (bot,)).fetchone()
            out = board(rows, json.loads(b["curve"]) if b else [], rng, datetime.now(timezone.utc).date().isoformat())
            out.update(bot=bot, updated=b["updated"] if b else None, min_days=MIN_DAYS, copy_cmd=COPY_CMD,
                       test_mode=TEST_MODE, start_cash=sim.START_CASH)
            return self._json(200, out)
        if path.startswith("/api/leaderboard/config/"):
            eid = path.rsplit("/", 1)[1]
            row = self._db().execute("SELECT * FROM entries WHERE id=?", (eid,)).fetchone()
            if not row:
                return self._json(404, {"ok": False, "message": "no such config"})
            today = date.today().isoformat()
            seen = (hashlib.sha256((_SALT + self._addr()).encode()).hexdigest()[:20], eid, today)   # one copy a day per address, as a hash
            if seen not in _copied:
                if any(s[2] != today for s in _copied):
                    _copied.difference_update({s for s in _copied if s[2] != today})
                _copied.add(seen)
                db = self._db()
                db.execute("UPDATE entries SET copies=copies+1 WHERE id=?", (eid,))
                db.commit()
            return self._json(200, manifest(row, self.key))
        if path == "/api/challenges":
            return self._json(200, challenges.view(self._db()))
        if path == "/api/challenges/mine":
            db = self._db()
            challenges.ensure(db)
            rows = db.execute("SELECT id, challenge, name, submitted, settings FROM challenge_entries WHERE pubkey=? ORDER BY challenge DESC LIMIT 24",
                              (q.get("pubkey", ""),)).fetchall()
            return self._json(200, {"entries": [dict(r, settings=json.loads(r["settings"])) for r in rows]})
        if path.startswith("/api/challenges/") and path.endswith("/results.csv"):
            text = challenges.results_csv(self._db(), path.split("/")[3])
            if text is None:
                return self._json(404, {"ok": False, "message": "no such challenge"})
            return self._send(200, text.encode(), "text/csv; charset=utf-8")
        if path == "/api/leaderboard/pubkey":
            return self._json(200, {"pubkey": common.public_b64(self.key)})
        if path == "/api/leaderboard/mine":
            rows = self._db().execute("SELECT id, bot, name, submitted, status, settings FROM entries WHERE pubkey=? "
                                      "ORDER BY submitted DESC LIMIT 20", (q.get("pubkey", ""),)).fetchall()
            return self._json(200, {"entries": [dict(r, settings=json.loads(r["settings"])) for r in rows]})
        if SITE_DIR:
            return self._static(path)
        self._send(404, b"not found", "text/plain")

    def _static(self, path):
        rel = path.lstrip("/") or "index.html"
        full = os.path.realpath(os.path.join(SITE_DIR, rel))
        if os.path.isdir(full):
            full = os.path.join(full, "index.html")
        if not full.startswith(os.path.realpath(SITE_DIR) + os.sep) or "/." in full or not os.path.isfile(full):
            return self._send(404, b"not found", "text/plain")
        with open(full, "rb") as f:
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(200, f.read(), ctype, cache="no-cache")

    def do_POST(self):
        path = self.path.split("?")[0]
        if self._setup_demo(path):
            return
        kind = {"/api/leaderboard/submit": "submit", "/api/leaderboard/withdraw": "withdraw",
                "/api/challenges/enter": "enter", "/api/challenges/withdraw": "withdraw"}.get(path)
        if not kind:
            return self._json(404, {"ok": False, "message": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if not 0 < n <= common.MAX_BODY:
            return self._json(413, {"ok": False, "message": "request too big"})
        raw = self.rfile.read(n)
        if not allowed(kind, self._addr(), time.time()):
            return self._json(429, {"ok": False, "message": "too many submissions from this address; try again in an hour"})
        try:
            body = json.loads(raw)
            db = self._db()
            now = datetime.now(timezone.utc)
            if kind == "enter":
                out = challenges.enter(db, body, self._addr(), now)
            elif path == "/api/challenges/withdraw":
                out = challenges.withdraw(db, body, now)
            else:
                out = (submit if kind == "submit" else withdraw)(db, body, now)
            log.info(f"{kind} {out.get('id')}")                     # never the address (the Privacy Policy: web logs only, 14 days)
            self._json(200, out)
        except Reject as e:
            self._json(e.code, {"ok": False, "message": e.message, **e.extra})
        except (ValueError, TypeError, KeyError):
            self._json(400, {"ok": False, "message": "malformed request"})

    def log_message(self, *args):
        pass


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    Handler.key = server_key()
    db = sim.connect()
    challenges.ensure(db)
    db.close()
    log.info(f"leaderboard on :{PORT}{' (TEST)' if TEST_MODE else ''}, site {SITE_DIR or '-'}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
