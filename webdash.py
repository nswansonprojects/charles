"""
webdash.py — Charles mobile dashboard (port 8502), the lightweight successor to dashboard.py.

One page (web/index.html) plus one JSON endpoint (/api/state). Read-only: it reads the
bot's data/ files, the Alpaca paper account and SPY closes, and never trades. The one write is
the ORB on/off button (POST /api/orb → data/control/orb_switch.json; anyone who can open the dashboard can use it).
Python stdlib server, no Streamlit, so it uses a few MB of memory instead of ~1 GB.
Runs as its own service (charles-web) so it keeps working while the bot restarts.

Answers, in this order: is the bot healthy, how is it doing vs SPY (return AND worst
drop, the roadmap goal), what does it hold, how are the experiments doing, what happened.
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

import config

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
PAGE = os.path.join(ROOT, "web", "index.html")
PORT = int(os.environ.get("WEBDASH_PORT", "8502"))
ET = ZoneInfo("America/New_York")
EVIDENCE_TRADES = 30          # roadmap task 8: closed trades a book needs before promotion
REVIEW_FROM = "2026-11-02"    # roadmap task 8: first promotion review
DATA_FROM = "2026-10-01"      # measurement started (shadow books, scorecard)
JOURNAL = os.path.join(ROOT, "ops", "journal")         # Claude's daily reports (check-up)
PLAN = os.path.join(ROOT, "ops", "claude-plan.md")     # Claude's own project plan
MODEL_B_GRADED = 100          # second-model decisions graded at 5 days before comparing


# ── Pure helpers (tested in tests/test_webdash.py) ────────────────────────────

def thin_hourly(curve: list) -> list:
    """Equity points [{ts, value}] → [[ts, value]], keeping the last point of each hour."""
    out, last_hour = [], None
    for p in curve:
        hour = p["ts"][:13]
        if hour == last_hour:
            out[-1] = [p["ts"], p["value"]]
        else:
            out.append([p["ts"], p["value"]])
            last_hour = hour
    return out


def market_status(now: datetime) -> str:
    """'open', 'pre', 'after' or 'closed' (weekends; exchange holidays are not known here)."""
    et = now.astimezone(ET)
    if et.weekday() >= 5:
        return "closed"
    minutes = et.hour * 60 + et.minute
    if minutes < 4 * 60:
        return "closed"
    if minutes < 9 * 60 + 30:
        return "pre"
    if minutes < 16 * 60:
        return "open"
    return "after" if minutes < 20 * 60 else "closed"


def spy_return_since(spy: list, day: str):
    """% change of SPY from the last close before `day` (or the first on/after it) to the latest."""
    if not spy:
        return None
    before = [c for d, c in spy if d < day[:10]]
    start = before[-1] if before else next((c for d, c in spy if d >= day[:10]), None)
    return round((spy[-1][1] / start - 1) * 100, 2) if start else None


def experiments(summary: dict, spy: list, now: datetime) -> list:
    """Shadow books as rows, best return first, each with baseline and SPY over its own life."""
    import shadow_daily
    base = summary.get("baseline", {})
    rows = []
    for name, s in summary.items():
        daily = name in shadow_daily.STRATEGIES
        since = (s.get("since") or "")[:10]
        if daily:      # judged on 3 months of data (roadmap task 8)
            started = datetime.fromisoformat(since).replace(tzinfo=timezone.utc) if since else now
            progress = min(1.0, (now - started).days / 91)
            needs = "3 months of data"
        else:
            progress = min(1.0, s.get("closed", 0) / EVIDENCE_TRADES)
            needs = f"{EVIDENCE_TRADES} closed trades"
        rows.append({
            "name": name, "about": s.get("about", ""), "kind": "daily" if daily else "intraday",
            "since": since, "return_pct": s.get("return_pct"), "max_drawdown_pct": s.get("max_drawdown_pct"),
            "closed": s.get("closed", 0), "open": s.get("open", 0), "win_pct": s.get("win_pct"),
            "vs_baseline": (round(s["return_pct"] - base["return_pct"], 2)
                            if base and name != "baseline" and s.get("return_pct") is not None else None),
            "spy_pct": spy_return_since(spy, since) if since else None,
            "progress": round(progress, 2), "needs": needs,
        })
    rows.sort(key=lambda r: r["return_pct"] if r["return_pct"] is not None else -1e9, reverse=True)
    return rows


def ai_check(scorecard: dict) -> list:
    """AI BUY picks vs every candidate the bot looked at, per horizon (from decision_scorecard.json)."""
    groups = scorecard.get("groups", {})
    ai, allc = groups.get("AI BUY", {}), groups.get("all candidates (baseline)", {})
    out = []
    for h in ("1d", "3d", "5d"):
        if h in ai and h in allc:
            out.append({"horizon": h, "ai_mean": ai[h]["mean_pct"], "ai_n": ai[h]["n"],
                        "ai_up": ai[h]["up_pct"], "all_mean": allc[h]["mean_pct"],
                        "all_n": allc[h]["n"], "all_up": allc[h]["up_pct"]})
    return out


def milestones(now: datetime, scorecard: dict, learning: dict, summary: dict) -> list:
    """What is still collecting data before it can be judged: [{name, about, progress, needs, detail}]."""
    import promotion
    day = now.date()
    start, review = datetime.fromisoformat(DATA_FROM).date(), datetime.fromisoformat(REVIEW_FROM).date()
    span = max(1, (review - start).days)
    books = [v for n, v in promotion.review(summary, [], [], now)["books"].items() if n != "baseline"] \
        if summary else []
    enough = sum(v["checks"][0]["passed"] for v in books)          # first check = amount of evidence
    left = (review - day).days
    out = [{"name": "First promotion review", "about": "Shadow books that beat the live rules can go live, one at a time.",
            "progress": round(min(1.0, max(0, (day - start).days) / span), 2),
            "needs": "Nov 2", "detail": (f"{left} days to go" if left > 0 else "open")
                                        + f" · {enough} of {len(books)} books have enough data"}]
    model_b = scorecard.get("groups", {}).get("model B · all candidates (baseline)", {}).get("5d", {}).get("n", 0)
    out.append({"name": "Second AI model", "about": "A bigger model answers the same questions; it replaces the "
                "live AI only if its picks do better.", "progress": round(min(1.0, model_b / MODEL_B_GRADED), 2),
                "needs": f"{MODEL_B_GRADED} graded picks", "detail": f"{model_b} graded"})
    lw, nw = learning.get("labelled_weeks", 0), learning.get("needs_weeks", 12)
    res = learning.get("result") or {}
    out.append({"name": "Learning model", "about": "Charles learns from its own past decisions which setups "
                "actually went up.", "progress": learning.get("progress", 0.0), "needs": f"{nw} weeks of decisions",
                "detail": ("beats the AI and 'always up'" if res.get("beats_both") else
                           "trained, not better yet" if res else f"{lw} of {nw} weeks")})
    return out


def latest_journal(folder: str = JOURNAL) -> dict | None:
    """Newest ops/journal/YYYY-MM-DD.md as {"date", "text"}."""
    try:
        names = sorted(n for n in os.listdir(folder) if len(n) == 13 and n.endswith(".md"))
    except FileNotFoundError:
        return None
    if not names:
        return None
    with open(os.path.join(folder, names[-1])) as f:
        return {"date": names[-1][:10], "text": f.read()[:20000], "count": len(names)}


def _text(path):
    try:
        with open(path) as f:
            return f.read()[:20000]
    except FileNotFoundError:
        return None


# ── Data sources (cached; any failure becomes a missing section, never a crash) ──

_cache: dict = {}
_lock = threading.Lock()


def _cached(key, ttl, fn):
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    try:
        val = fn()
    except Exception as e:
        log.warning(f"{key} failed: {e}")
        val = hit[1] if hit else None          # keep showing the last good value
    with _lock:
        _cache[key] = (time.time(), val)
    return val


def _read(name, default=None):
    try:
        with open(os.path.join(DATA, name)) as f:
            return json.load(f)
    except Exception:
        return default


def _account():
    import alpaca_executor
    acct = alpaca_executor.get_account_summary()
    return acct or None


def exits_summary(history: list) -> dict:
    """How much of their best gain closed trades kept (from position_tracker's history)."""
    rows = [h for h in history if h.get("gave_back") is not None]
    return {"rows": [{k: h.get(k) for k in ("symbol", "closed", "peak_pnl", "peak_pct", "exit_pnl", "gave_back",
                                             "exit_source")} for h in rows[-40:]][::-1],
            "n": len(rows), "peak_total": round(sum(h["peak_pnl"] for h in rows), 2),
            "exit_total": round(sum(h["exit_pnl"] for h in rows), 2),
            "gave_back_total": round(sum(max(0, h["gave_back"]) for h in rows), 2)}


def json_safe(x):
    """NaN/inf → None anywhere in the state: browsers reject NaN in JSON (the whole page goes offline)."""
    if isinstance(x, float):
        return x if x == x and x not in (float("inf"), float("-inf")) else None
    if isinstance(x, dict):
        return {k: json_safe(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [json_safe(v) for v in x]
    return x


def clean_closes(pairs) -> list:
    """[(date, close)] → [[day, close]] without missing (NaN) closes, which are not valid JSON."""
    return [[str(d.date()), round(float(v), 2)] for d, v in pairs if v == v and v is not None]


def _spy():
    import yfinance as yf
    return clean_closes(yf.Ticker("SPY").history(start="2026-05-01")["Close"].items())


def hourly_closes(rows, now: datetime | None = None) -> list:
    """[(bar start, close)] hourly SPY bars → [[iso UTC time the bar ENDED, close]] (bars end on the
    hour or at the 4 PM close), dropping NaNs and the bar still forming. The chart prices SPY as of
    each Charles point from these, so it never uses a close that hadn't happened yet."""
    now = now or datetime.now(timezone.utc)
    out = []
    for start, v in rows:
        if v != v or v is None:
            continue
        start = start.astimezone(ET)
        end = min(start + timedelta(hours=1), start.replace(hour=16, minute=0, second=0, microsecond=0))
        if end <= now:
            out.append([end.astimezone(timezone.utc).isoformat(timespec="minutes"), round(float(v), 2)])
    return out


def _spy_hourly():
    import yfinance as yf
    return hourly_closes(yf.Ticker("SPY").history(start="2026-05-01", interval="60m")["Close"].items())


def _bot_running() -> bool:
    try:
        with open(os.path.join(DATA, ".bot.lock")) as f:
            pid = int(f.read().strip())
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return b"main.py" in f.read()
    except Exception:
        return False


def _options():
    import options_bot
    return options_bot.summary()


def _orb():
    import orb
    return orb.summary()


def _shadow_summary():
    import shadow
    return shadow.summary()


def build_state() -> dict:
    now = datetime.now(timezone.utc)
    acct = _cached("account", 20, _account) or {}
    spy = _cached("spy", 3600, _spy) or []
    spy_hourly = _cached("spy_hourly", 900, _spy_hourly) or []
    stops = _read("trailing_stops.json", {})
    daily = _read("daily_loss.json", {})
    perf = _read("performance.json", {})
    trades = _read("trades.json", [])
    latest = _read("latest.json", {})
    watch = _read("watchlist.json", {})

    import position_tracker
    paths = position_tracker.current()
    positions = []
    for p in acct.get("positions", []):
        st = stops.get(p["symbol"], {})
        stop = st.get("stop")
        path = paths.get(p["symbol"], {})
        positions.append({**p, "stop": stop, "peak_pnl": path.get("peak_pnl"), "peak_pct": path.get("peak_pct"),
                          "peak_ts": path.get("peak_ts"), "trough_pnl": path.get("trough_pnl"),
                          "stop_dist_pct": round((p["current_price"] / stop - 1) * 100, 1) if stop else None})
    positions.sort(key=lambda p: p["market_value"], reverse=True)

    today = str(now.astimezone(ET).date())
    return {
        "generated_at": now.isoformat(),
        "bot_running": _bot_running(),
        "market": market_status(now),
        "last_cycle": latest.get("collected_at"),
        "account": {k: acct.get(k) for k in ("total_value", "cash", "invested", "total_return",
                                              "total_return_pct", "open_positions")} if acct else None,
        "start_cash": config.VIRTUAL_CASH,
        "today": {"pnl": daily.get("loss", 0) if daily.get("date") == today else 0.0,
                  "halted": bool(daily.get("halted")) and daily.get("date") == today,
                  "limit": config.MAX_DAILY_LOSS},
        "stats": {k: perf.get(k) for k in ("win_rate", "total_closed", "total_pnl", "avg_win", "avg_loss")},
        "equity": _cached("equity", 60, lambda: thin_hourly(_read("equity_curve.json", []))) or [],
        "spy": spy,
        "spy_hourly": spy_hourly,
        "positions": positions,
        "experiments": experiments(_cached("shadow", 60, _shadow_summary) or {}, spy, now),
        "review_from": REVIEW_FROM,
        "ai_check": ai_check(_read("decision_scorecard.json", {})),
        "trades": [{k: t.get(k) for k in ("timestamp", "symbol", "action", "value", "pnl", "pnl_pct",
                                          "reasoning", "source")} for t in trades[-40:]][::-1],
        "watchlist": watch.get("stocks", []),
        "options": _cached("options", 30, _options) or {},
        "orb": _cached("orb", 20, _orb) or {},
        "exits": exits_summary(position_tracker.history()),
        "claude": {"report": latest_journal(), "plan": _text(PLAN),
                   "milestones": milestones(now, _read("decision_scorecard.json", {}),
                                            _read("learning_model.json", {}),
                                            _cached("shadow", 60, _shadow_summary) or {})},
    }


# ── HTTP ──────────────────────────────────────────────────────────────────────

# The Kalshi bot's dashboard (its own service, own env, port 8503) shown here under /kalshi/, so
# stocks and Kalshi share one address. Only its page and its read-only state pass through.
KALSHI_URL = os.environ.get("KALSHI_URL", "http://127.0.0.1:8503")
KALSHI_PATHS = {"/kalshi/": "/", "/kalshi/index.html": "/", "/kalshi/api/state": "/api/state"}


def kalshi_proxy(path: str):
    """(status, body, content type) for a /kalshi/ path, fetched from the Kalshi dashboard."""
    import urllib.request
    try:
        with urllib.request.urlopen(KALSHI_URL + KALSHI_PATHS[path], timeout=10) as r:
            return 200, r.read(), r.headers.get("Content-Type", "application/octet-stream")
    except Exception as e:
        msg = f"The Kalshi dashboard isn't answering ({type(e).__name__}). Is kalshi-web running?"
        return 502, msg.encode(), "text/plain; charset=utf-8"


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body: bytes, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            with open(PAGE, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif path == "/kalshi":
            self.send_response(301)
            self.send_header("Location", "/kalshi/")
            self.end_headers()
        elif path in KALSHI_PATHS:
            self._send(*kalshi_proxy(path))
        elif path == "/api/state":
            body = json.dumps(json_safe(_cached("state", 10, build_state) or {"error": "unavailable"}),
                              default=str, allow_nan=False).encode()
            self._send(200, body, "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path.split("?")[0] != "/api/orb":
            return self._send(404, b"not found", "text/plain")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if not self.headers.get("Content-Type", "").startswith("application/json") or not 0 < n <= 200:
                raise ValueError("expected a small JSON body")
            on = json.loads(self.rfile.read(n))["on"]
            if not isinstance(on, bool):
                raise ValueError("'on' must be true or false")
        except (ValueError, KeyError, TypeError) as e:
            return self._send(400, json.dumps({"error": str(e)}).encode(), "application/json")
        import orb
        rec = orb.set_switch(on, "dashboard")
        with _lock:
            _cache.pop("orb", None)
            _cache.pop("state", None)
        self._send(200, json.dumps(rec).encode(), "application/json")

    def log_message(self, *args):       # keep the journal quiet
        pass


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info(f"Charles web dashboard on :{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
