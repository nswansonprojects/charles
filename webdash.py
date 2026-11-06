"""
webdash.py — Charles's mobile dashboard (port 8502): the pages in web/ + their JSON endpoints.

Read-only for trading: it reads the bot's data/ files, the Alpaca paper account and SPY closes, and
never trades or writes. The Settings page shows which broker accounts are connected and the SSH
steps to connect them (keys go in through the terminal, connections.py). It also shows the crypto
book (/crypto/, crypto_paper.py's paper book).
Python stdlib server, a few MB of memory. Runs as its own service (charles-web) so it keeps
working while the bot restarts. The Kalshi bot's page is passed through under /kalshi/.

Answers, in this order: how is my money doing (vs SPY), what is Charles doing and why (the plan),
what does it hold, what did it do, and how Claude is tuning the rules.
"""
import hmac
import html
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import addons
import auth
import allocator
import chat
import config
import connections
import control
import crypto_plan
import crypto_settings
import equity_tracker
import feed
import lessons
import live
import news_feed
import report_view
import state_io
import strategy_settings
import updates

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("WEBDASH_DATA") or os.path.join(ROOT, "data")     # the preview reads a copy
PAGES = {"/": "home.html", "/index.html": "home.html", "/stocks": "stocks.html", "/crypto/": "crypto.html",
         "/leaderboard": "leaderboard.html", "/settings": "settings.html"}
PAGE_TAB = {"/": "home", "/index.html": "home", "/stocks": "stocks", "/crypto/": "crypto", "/leaderboard": "leaderboard", "/settings": "settings"}
CRYPTO_BACKTEST = os.path.join(ROOT, "docs", "crypto_backtest.json")
BACKTEST = os.path.join(ROOT, "docs", "backtest.json")
PORT = int(os.environ.get("WEBDASH_PORT", "8502"))
STATIC = {"/style.css": "text/css; charset=utf-8", "/shell.css": "text/css; charset=utf-8", "/shell.js": "text/javascript; charset=utf-8", "/common.js": "text/javascript; charset=utf-8", "/board.js": "text/javascript; charset=utf-8", "/chat.js": "text/javascript; charset=utf-8", "/alerts.js": "text/javascript; charset=utf-8", "/tour.js": "text/javascript; charset=utf-8", "/feed.js": "text/javascript; charset=utf-8", "/report.js": "text/javascript; charset=utf-8", "/tune.js": "text/javascript; charset=utf-8", "/viz.js": "text/javascript; charset=utf-8", "/icon-180.png": "image/png", "/icon-192.png": "image/png", "/icon-512.png": "image/png"}
# The login (auth.py). Cookie names carry the port: browsers send a host's cookies to all its ports, so
# the dashboard (:8502) and the preview (:8504) would otherwise log each other out.
SESSION_COOKIE, CSRF_COOKIE = f"charles_session_{PORT}", f"charles_csrf_{PORT}"
AUTH_FREE = {"/login", "/logout", "/api/session", "/manifest.webmanifest", *STATIC}
_links_used: set = set()
CSRF_JS = ("<script>(function(){var f=window.fetch;window.fetch=function(u,o){o=o||{};"
           "var m=(o.method||'GET').toUpperCase(),c=document.cookie.match(/(?:^|; )%s=([^;]+)/);"
           "if(c&&m!=='GET'&&m!=='HEAD'){o.headers=new Headers(o.headers||{});o.headers.set('X-CSRF-Token',c[1]);}"
           "return f.call(this,u,o);};})();</script>" % CSRF_COOKIE).encode()
MANIFEST = json.dumps({
    "name": "Charles", "short_name": "Charles", "start_url": "/", "scope": "/", "display": "standalone",
    "background_color": "#ebedf0", "theme_color": "#2a78d6",
    "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
              {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
}).encode()
ET = ZoneInfo("America/New_York")
JOURNAL = os.path.join(ROOT, "ops", "journal")         # Claude's daily reports (check-up)
CHECKUP = os.path.join(ROOT, "ops", "run-checkup")      # Claude's daily check-up: the owner's server only
CLAUDE_PLAN = os.path.join(ROOT, "ops", "claude-plan.md")
REVIEW_DAYS = 182                                       # the first honest look at the new plan vs SPY
SPY_FROM = "2026-05-01"                                 # SPY history for the charts starts here
HOURLY_DAYS = 720                                       # Yahoo serves 60-minute bars only for the last 730 days
BACKTEST_SHOWN = ("charles", "charles_sectors", "spy")


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


def with_history(points: list, daily: list) -> list:
    """Prepend Alpaca's daily closes from before the equity curve began (it started Jun 3, 2026 with
    the account already at $1,042), so the chart starts at the real $1,000 start."""
    first = points[0][0] if points else "9999"
    return [p for p in daily if p[0] < first] + points


def daily_history(timestamps, equities) -> list:
    """Alpaca 1D portfolio history → [[iso UTC, equity]], from the first funded day. Each daily
    timestamp is 00:00 UTC after the session (8 PM ET), so it never sits before the close it reports."""
    out = []
    for t, e in zip(timestamps, equities):
        if e or out:
            out.append([datetime.fromtimestamp(t, timezone.utc).isoformat(), round(float(e or 0), 2)])
    return out


def with_live_point(points: list, value, now: datetime) -> list:
    """Append the live account value at `now`, so the chart ends where the header does."""
    if value is None or (points and points[-1][0] >= now.isoformat()):
        return points
    return points + [[now.isoformat(), round(float(value), 2)]]


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


BLOCK_ROLE = {"long": "core", "sector": "sector", "momentum": "pick"}      # a Custom Plan's block types, as holding roles


def holdings(positions: list, plan: dict, total: float) -> list:
    """Account positions with their role in the plan (core / pick / safety / leaving; with the user's own blocks also
    sector, and the block's name), their target and actual share of the account, and the pick's momentum rank. Biggest first."""
    import portfolio
    s = plan.get("settings") or {}
    core, safe, agg = s.get("CORE", "SPY"), s.get("SAFE", "SGOV"), s.get("AGG_CORE", "SSO")
    targets, picks = plan.get("targets") or {}, plan.get("picks") or []
    ranked = {r["symbol"]: r for r in plan.get("ranked") or []}
    out = []
    for p in positions:
        sym = p["symbol"]
        in_plan = sym in targets or not targets
        block = portfolio.block_of(sym, {"CORE": core, "AGG_CORE": agg, **s}) if s.get("BLOCKS") and sym != safe else None
        if s.get("BLOCKS"):
            role = "safety" if sym == safe else BLOCK_ROLE[block["type"]] if block and in_plan else "leaving"
        else:
            role = ("core" if sym in (core, agg) and in_plan else "safety" if sym == safe else
                    "pick" if sym in picks else "leaving")
        r = ranked.get(sym, {})
        out.append({**p, "role": role, "target_pct": round(targets.get(sym, 0) * 100, 1) if plan else None,
                    "actual_pct": round(p["market_value"] / total * 100, 1) if total else None,
                    "momentum": r.get("momentum"), "rank": r.get("rank"), **({"block": block["name"]} if block and role != "leaving" else {})})
    order = {"core": 0, "sector": 1, "pick": 2, "safety": 3, "leaving": 4}
    out.sort(key=lambda h: (order[h["role"]], -h["market_value"]))
    return out


def next_evaluation(now: datetime) -> datetime:
    """The rule check runs on the first Sunday of each month (charles-evaluate.timer, 3 AM ET)."""
    d = now.astimezone(ET).date()
    for months in (0, 1):
        y, m = d.year + (d.month + months - 1) // 12, (d.month + months - 1) % 12 + 1
        first = datetime(y, m, 1).date()
        sunday = first + timedelta(days=(6 - first.weekday()) % 7)
        if sunday >= d:
            return datetime(sunday.year, sunday.month, sunday.day, tzinfo=ET)
    return datetime(d.year, d.month, d.day, tzinfo=ET)


def evaluation_view(ev: dict | None) -> dict | None:
    """data/evaluation.json trimmed to the Self-Tune tab's one verdict."""
    if not ev:
        return None
    rows = ev.get("variants") or []
    return {"generated": ev.get("generated"), "tested": len(rows), "passing": sum(r.get("passes", False) for r in rows),
            "candidate": ev.get("candidate"), "live": ev.get("live") or {}, "custom": bool(ev.get("custom")),
            "self_tune_said": ev.get("self_tune_said")}


def evaluation_tests(ev: dict | None) -> list:
    """What the monthly rule check tried, for the Self-Tune tab's grid. A change has to win every test, so `worst` is what it gained or
    lost a year in the test it did worst in, against the current rules."""
    now = (ev or {}).get("current") or {}

    def worst(results):
        gaps = [r["cagr_pct"] - now[a][p]["cagr_pct"] for a, runs in (results or {}).items() for p, r in runs.items()
                if (now.get(a) or {}).get(p) and r.get("cagr_pct") is not None]
        return round(min(gaps), 1) if gaps else None
    return [{**{k: r.get(k) for k in ("setting", "label", "from", "to", "passes", "why")}, "worst": worst(r.get("results"))} for r in (ev or {}).get("variants") or []]


def milestones(plan: dict, now: datetime, ev: dict | None = None) -> list:
    """Progress bars: the next monthly re-pick, the next rule check, and the six-month check vs SPY."""
    started = plan.get("started")
    if not started:
        return [{"name": "First check", "about": "Charles sets up the plan at the first 10:00 AM ET check after the switch.",
                 "progress": 0.0, "needs": "Weekdays, 10 AM ET", "detail": "not yet"}]
    today = now.astimezone(ET).date()
    start = datetime.fromisoformat(started).date()
    out = []
    nxt = plan.get("next_rebalance")
    if nxt:
        n = datetime.fromisoformat(nxt).date()
        last = datetime.fromisoformat(plan.get("last_rebalance") or started).date()
        span = max(1, (n - last).days)
        left = max(0, (n - today).days)
        out.append({"name": "Next re-pick", "about": "Once a month Charles re-ranks the stocks and checks the safety switch.",
                    "progress": round(min(1.0, (today - last).days / span), 2), "needs": f"{n:%b} {n.day}",
                    "detail": "today" if left == 0 else f"in {left} day{'s' if left != 1 else ''}"})
    nxt_ev = next_evaluation(now).date()
    last_ev = datetime.fromisoformat(ev["generated"]).date() if ev and ev.get("generated") else start
    span = max(1, (nxt_ev - last_ev).days)
    left = (nxt_ev - today).days
    out.append({"name": "Next rule check", "about": "The monthly rule check tests small changes to the plan's numbers on 19 years of history.",
                "progress": round(min(1.0, max(0, (today - last_ev).days) / span), 2), "needs": f"{nxt_ev:%b} {nxt_ev.day}",
                "detail": "today" if left <= 0 else f"in {left} day{'s' if left != 1 else ''}"})
    review = start + timedelta(days=REVIEW_DAYS)
    left = (review - today).days
    out.append({"name": "Six-month check vs SPY", "about": "The first fair look at whether the new plan earns its keep.",
                "progress": round(min(1.0, max(0, (today - start).days) / REVIEW_DAYS), 2),
                "needs": f"{review:%b} {review.day}, {review.year}", "detail": f"{left} days to go" if left > 0 else "due"})
    return out


def backtest_view(bt: dict) -> dict | None:
    """The backtest file trimmed to what the Plan tab draws."""
    if not bt or "runs" not in bt:
        return None
    runs = {}
    for name in BACKTEST_SHOWN:
        r = bt["runs"].get(name)
        if r:
            st = r.get("stats", {})
            runs[name] = {"about": r.get("about"), "curve": r.get("curve", []),
                          **{k: st.get(k) for k in ("cagr_pct", "max_drawdown_pct", "final", "worst_year", "best_year")},
                          "yearly": st.get("yearly", {})}
    return {"start": bt.get("start"), "end": bt.get("end"), "runs": runs}


def checkup_installed(path: str = CHECKUP) -> bool:
    """Is Claude's daily check-up set up here? Public installs have only the monthly rule check (evaluate.py), which
    applies a passing change by itself while self-tune is on (SELF_TUNE), so the page mustn't say Claude does."""
    return os.path.exists(path)


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


def dividend_adjust(hourly: list, factors: dict) -> list:
    """Scale hourly closes by their day's dividend factor (adjusted / raw daily close), so the chart's
    SPY includes dividends like the daily closes and the headline do."""
    out = []
    for t, v in hourly:
        day = str(datetime.fromisoformat(t).astimezone(ET).date())
        out.append([t, round(v * factors.get(day, 1.0), 2)])
    return out


def hourly_closes(rows, now: datetime | None = None) -> list:
    """[(bar start, close)] hourly SPY bars → [[iso UTC time the bar ENDED, close]], dropping NaNs
    and the bar still forming, so the chart never uses a close that hadn't happened yet."""
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


def thin_older(curve: list, now: datetime, recent_days: int = 31) -> list:
    """Hourly [[ts, value]] → the first point, every point from the last `recent_days`, and one a day
    (its last) in between, so the "All" range still starts at the start without years of hourly points."""
    cutoff = (now - timedelta(days=recent_days)).isoformat()
    out = []
    for ts, v in curve:
        if ts < cutoff and len(out) > 1 and out[-1][0][:10] == ts[:10]:
            out[-1] = [ts, v]
        else:
            out.append([ts, v])
    return out


def crypto_shares(s: dict) -> dict:
    """{product: its share of the book while it's held}: bitcoin/ether's weights, or each block's share split over its coins."""
    inv = s.get("INVESTED", 1.0)
    if not s.get("BLOCKS"):
        return {p: w * inv for p, w in s["WEIGHTS"].items()}
    out = {}
    for b in s["BLOCKS"]:
        for p in b["tickers"]:
            out[p] = inv * b["pct"] / (b["picks"] if b["type"] == "momentum" else len(b["tickers"]))
    return out


def crypto_view(book: dict | None, prices: dict, now: datetime, bt: dict | None = None, settings: dict | None = None,
                names: dict | None = None) -> dict:
    """The crypto demo's paper book valued at live prices, each coin vs its trend line, the next
    weekly check, and the backtest. Missing prices (feed down) fall back to the book's last hourly price, then the last fill.
    `settings`: the plan as it stands (your own coins too); a coin the book still holds but the plan dropped is listed too."""
    s = {**crypto_plan.DEFAULTS, **(settings or {})}
    names = {**crypto_plan.NAMES, **(names or {})}
    shares = crypto_shares(s)
    out = {"generated_at": now.isoformat(), "settings": {k: s[k] for k in ("WEIGHTS", "TREND_DAYS", "CHECK_DAYS", "FEE", "INVESTED", "BLOCKS")},
           "names": names, "backtest": bt, "book": None, "custom": bool(s.get("BLOCKS")), "shares": shares,
           "mom_days": crypto_plan.MOM_DAYS, "types": crypto_plan.TYPE_NAMES}
    if not book:
        return out
    last = {**{t["product"]: t["price"] for t in book.get("trades", [])}, **(book.get("last_prices") or {})}   # the book's hourly price first
    qty = book.get("qty", {})
    listed = list(dict.fromkeys(crypto_plan.products(s) + [p for p, q in qty.items() if q > 1e-12]))
    px = {p: prices.get(p) or last.get(p) or (book.get("coins", {}).get(p) or {}).get("price") for p in listed}
    value = book["cash"] + sum(q * px[p] for p, q in qty.items() if q and px.get(p))
    start = book.get("start_cash", 1000.0)
    sp, btc = book.get("start_prices") or {}, prices.get("BTC-USD") or px.get("BTC-USD")
    coins = []
    for p in listed:
        c = (book.get("coins") or {}).get(p) or {}
        avg = c.get("average")
        held = qty.get(p, 0.0) * (px.get(p) or 0)
        coins.append({"product": p, "name": crypto_plan.name_of(p, names), "price": px.get(p), "average": avg,
                      "gap_pct": round((px[p] / avg - 1) * 100, 1) if avg and px.get(p) else None,
                      "holding": held > 1, "value": round(held, 2), "share_pct": round(held / value * 100, 1) if value else 0,
                      "target_pct": round((book.get("targets") or {}).get(p, 0) * 100), "weight_pct": round(shares.get(p, 0) * 100, 1),
                      "block": c.get("block"), "type": c.get("type"), "momentum": c.get("momentum"), "in_plan": p in shares})
    nxt = progress = None
    if book.get("last_check"):
        lc = datetime.fromisoformat(book["last_check"])
        nxt = lc + timedelta(days=s["CHECK_DAYS"])
        if book.get("sig") and book["sig"] != crypto_plan.signature(s):
            nxt = now + timedelta(minutes=60 - now.minute)          # a saved change: the next hourly step checks
        progress = round(min(1.0, max(0.0, (now - lc).total_seconds() / (s["CHECK_DAYS"] * 86400))), 2)
    curve = book.get("curve", [])
    day_ago = (now - timedelta(days=1)).isoformat()
    prior = [v for t, v in curve if t <= day_ago] or [start]
    curve = thin_older(curve, now)
    cost = cost_basis(book.get("trades", []))
    for c in coins:
        c["qty"] = qty.get(c["product"], 0.0)
        c["cost"] = cost.get(c["product"])
        c["gain"] = round(c["value"] - c["cost"], 2) if c["cost"] and c["holding"] else None
        c["gain_pct"] = round((c["value"] / c["cost"] - 1) * 100, 2) if c["cost"] and c["holding"] else None
    out["book"] = {
        "started": book.get("started"), "start_cash": start, "value": round(value, 2), "cash": round(book["cash"], 2),
        "return": round(value - start, 2), "return_pct": round((value / start - 1) * 100, 2),
        "day_change": round(value - prior[-1], 2),
        "btc_pct": round((btc / sp["BTC-USD"] - 1) * 100, 2) if btc and sp.get("BTC-USD") else None,
        "coins": coins, "last_check": book.get("last_check"), "next_check": nxt.isoformat() if nxt else None,
        "check_progress": progress, "curve": with_live_point(curve, value, now),
        "trades": book.get("trades", [])[-40:][::-1],
        "start_prices": book.get("start_prices") or {},
    }
    return out


def cost_basis(trades: list) -> dict:
    """{product: dollars paid for the coins still held}, fees included; a sell removes its share of the cost."""
    held, cost = {}, {}
    for t in trades:
        p, q = t["product"], t["value"] / t["price"]
        if t["side"] == "BUY":
            held[p], cost[p] = held.get(p, 0.0) + q * (1 - (t["fee"] / t["value"] if t["value"] else 0)), cost.get(p, 0.0) + t["value"]
        elif held.get(p):
            frac = min(1.0, q / held[p])
            held[p], cost[p] = held[p] * (1 - frac), cost[p] * (1 - frac)
    return {p: round(c, 2) for p, c in cost.items() if held.get(p, 0) > 0}


def crypto_backtest_view(bt: dict | None) -> dict | None:
    if not bt or "runs" not in bt:
        return None
    return {"start": bt.get("start"), "end": bt.get("end"), "fee_pct": bt.get("fee_pct"),
            "runs": {n: {"about": r["about"], "curve": r["curve"],
                         "all": {k: r["stats"].get(k) for k in ("cagr_pct", "max_drawdown_pct", "final")},
                         "since_2021": {k: (r.get("since_2021") or {}).get(k) for k in ("cagr_pct", "max_drawdown_pct")}}
                     for n, r in bt["runs"].items()}}


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


_soon_running = set()


def _soon(key, ttl, fn):
    """The last value at once (None the first time), with a stale one refreshed behind the scenes: a slow news site never holds up a page."""
    with _lock:
        hit = _cache.get(key)
        start = (not hit or time.time() - hit[0] >= ttl) and key not in _soon_running
        if start:
            _soon_running.add(key)
    if start:
        def work():
            try:
                _cached(key, 0, fn)
            finally:
                with _lock:
                    _soon_running.discard(key)
        threading.Thread(target=work, daemon=True).start()
    return hit[1] if hit else None


def _read(name, default=None, folder=None):
    try:
        with open(os.path.join(folder or DATA, name)) as f:
            return json.load(f)
    except Exception:
        return default


def _text(path):
    try:
        with open(path) as f:
            return f.read()[:20000]
    except FileNotFoundError:
        return None


def _account():
    import alpaca_executor
    return alpaca_executor.get_account_summary() or None


def _history():
    import alpaca_executor
    from alpaca.trading.requests import GetPortfolioHistoryRequest
    h = alpaca_executor._client().get_portfolio_history(GetPortfolioHistoryRequest(period="1A", timeframe="1D"))
    return daily_history(h.timestamp, h.equity)


def hourly_start(now: datetime) -> str:
    """First day of hourly SPY to ask for: SPY_FROM, or later once that's outside Yahoo's window."""
    return max(SPY_FROM, (now - timedelta(days=HOURLY_DAYS)).date().isoformat())


def _spy():
    import yfinance as yf
    return clean_closes(yf.Ticker("SPY").history(start=SPY_FROM)["Close"].items())


def _spy_hourly():
    import yfinance as yf
    t = yf.Ticker("SPY")
    start = hourly_start(datetime.now(timezone.utc))
    d = t.history(start=start, auto_adjust=False)
    factors = {str(i.date()): float(a / c) for i, a, c in zip(d.index, d["Adj Close"], d["Close"]) if c == c and a == a}
    return dividend_adjust(hourly_closes(t.history(start=start, interval="60m")["Close"].items()), factors)


def _rules() -> dict:
    """The plan's numbers now (strategy.json over the defaults) and the note on the last change."""
    try:
        with open(strategy_settings.PATH) as f:
            note = json.load(f).get("last_change")
    except Exception:
        note = None
    return {"settings": {**allocator.DEFAULTS, **strategy_settings.load()}, "last_change": note}


def _bot_running() -> bool:
    try:
        with open(os.path.join(DATA, ".bot.lock")) as f:
            pid = int(f.read().strip())
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return b"main.py" in f.read()
    except Exception:
        return os.environ.get("WEBDASH_PREVIEW") == "1"


def _crypto_settings() -> dict:
    return {**crypto_plan.DEFAULTS, **crypto_settings.load()}


def _crypto_prices() -> dict:
    import crypto_market
    book = _read("crypto_paper.json") or {}
    out = {}
    for p in dict.fromkeys(crypto_plan.products(_crypto_settings()) + [p for p, q in (book.get("qty") or {}).items() if q] + ["BTC-USD"]):
        try:
            out[p] = crypto_market.spot(p)
        except Exception as e:                     # one coin's feed down: the others still show
            log.warning(f"crypto price {p}: {e}")
    return out


def build_crypto_state() -> dict:
    book = _read("crypto_paper.json")
    out = crypto_view(book, _cached("crypto_prices", 30, _crypto_prices) or {},
                      datetime.now(timezone.utc), _cached("crypto_bt", 3600, lambda: crypto_backtest_view(_read(CRYPTO_BACKTEST))),
                      settings=_crypto_settings(), names=coin_names_known())
    out["evaluation"] = crypto_evaluation_view(_read("crypto_evaluation.json"))
    if book:          # bitcoin's hourly closes since the book started: the chart's "just hold bitcoin" line
        import crypto_market
        out["btc_hourly"] = _cached("btc_hourly", 900, lambda: crypto_market.hourly_closes("BTC-USD", book["started"])) or []
    return out


# A preview copy has no broker keys: it can show the live dashboard's stocks state instead.
STATE_FROM = os.environ.get("WEBDASH_STATE_FROM")


def _stocks_state() -> dict | None:
    if not STATE_FROM:
        return with_lessons(build_state())
    import urllib.request
    with urllib.request.urlopen(STATE_FROM + "/api/state", timeout=15) as r:
        return with_lessons(json.loads(r.read()))


def with_lessons(state: dict) -> dict:
    """The state plus the learning layer (lessons.py): its lessons, and why Charles owns each holding. Added
    here, not in build_state(), so a preview adds them to the live state it borrows."""
    if isinstance(state, dict) and not state.get("error"):
        numbers = _cached("bt_numbers", 3600, lambda: lessons.backtest_numbers(_read(BACKTEST))) or {}
        state["lessons"] = lessons.due(state, numbers)
        for h in state.get("positions") or []:
            h["why"] = lessons.holding_why(h, state.get("plan") or {})
        if isinstance(state.get("evaluation"), dict):
            state["evaluation"]["tests"] = evaluation_tests(_read("evaluation.json"))
    return state


def holdings_news() -> dict:
    """Headlines about the stocks held (news_feed.py) for the Holdings tab. News, not advice: nothing trades on it."""
    st = _cached("state", 10, _stocks_state) or {}
    return news_feed.holdings_news(st.get("positions") or [], st.get("names") or allocator.NAMES)


def accounts_state(on: dict | None = None, new: bool = False) -> dict:
    st = (_cached("state", 10, _stocks_state) or {}) if STATE_FROM else {}
    acct = st.get("account") if STATE_FROM else _cached("account", 20, _account)
    acct_ok = bool((acct or {}).get("total_value")) and not new
    view = accounts_view(connections.public_status(_read("connections.json", {}, connections.DIR), server_alpaca=acct_ok),
                         addons.enabled() if on is None else on, [{**r, "on": False, "command": f"charles-addon on {r['id']}"} if on is not None and not on[r["id"]] else r for r in addons.status()])
    view["trading"] = (st.get("trading") if STATE_FROM else None) or live.dashboard(DATA)
    if new:
        view["trading"] = {"mode": "paper", "since": None, "hold": None, "terms": "Not accepted yet. Run charles-terms to read and accept it."}
    return view


def control_post(body: bytes, cfg: dict | None) -> tuple[int, dict]:
    """POST /api/control {"action": "pause" | "resume" | "update" | "restore" | "check-updates" | "undo-settings"}:
    leaves a request for the root service (control.py), which checks it again. "apply-settings" comes through
    chat_apply. The gate has already checked the login and CSRF; with no login turned on, nobody may change anything."""
    if STATE_FROM:
        return 409, {"error": "This is a preview: it shows the live bot, so it can't change it."}
    if auth.access(cfg) == "open":
        return 403, {"error": "Changing things from the dashboard needs its login. Turn it on over SSH: charles-login"}
    try:
        req = json.loads(body)
        action = req.get("action")
    except (ValueError, AttributeError):
        req, action = {}, None
    if action not in control.ACTIONS or action in ("apply-settings", "copy-config"):
        return 400, {"error": "Unknown request."}
    extra = {}
    if action in ("update", "restore"):
        if not isinstance(req.get("version"), str) or not updates.VERSION_RE.match(req["version"]):
            return 400, {"error": "Unknown version."}
        extra = {"version": req["version"], "confirm": req.get("confirm") is True}
    try:
        control.request(action, "the dashboard", **extra)
    except OSError as e:
        log.warning(f"control request failed: {e}")
        return 503, {"error": "The dashboard can't leave requests on this server yet (data/requests isn't writable)."}
    return 202, {"ok": True, "action": action}


def chat_status(cfg: dict | None, cookie_header: str | None) -> dict:
    """The chat drawer's first look: can it be used, and if not, what's the one step missing."""
    saved = chat.demo_answers()
    if saved:                     # a preview replaying saved answers: no key, no spending, so it needs no login
        return {"enabled": True, "why": None, "demo": True, "starts": [a.get("q") for a in saved], "spend_usd": 0, "cap_usd": 0, "preview": True, "undo": False}
    sid = auth.check(cfg, auth.cookies(cookie_header).get(SESSION_COOKIE))
    return {**chat.status(auth.access(cfg), bool(sid), DATA), "preview": bool(STATE_FROM), "undo": bool(chat.last_change()) if not STATE_FROM else False}


def chat_state():
    return with_lessons(_cached("state", 10, _stocks_state) or {})


def chat_post(body: bytes, cfg: dict | None) -> tuple[int, dict]:
    """POST /api/chat {"messages": [...]}: one question, answered with the user's own Anthropic key."""
    if chat.demo_answers():
        try:
            return 200, chat.demo_reply(json.loads(body).get("messages"))
        except (ValueError, AttributeError):
            return 400, {"ok": False, "error": "Couldn't read that message. Refresh the page and try again."}
    if auth.access(cfg) == "open":
        return 403, {"ok": False, "error": "The chat needs the dashboard login, or anyone could spend your money. Turn it on over SSH: charles-login"}
    try:
        return 200, chat.reply(json.loads(body).get("messages"), chat_state, DATA)
    except chat.ChatError as e:
        return 200, {"ok": False, "error": str(e)}
    except (ValueError, AttributeError):
        return 400, {"ok": False, "error": "Couldn't read that message. Refresh the page and try again."}


def chat_apply_post(body: bytes, cfg: dict | None) -> tuple[int, dict]:
    """POST /api/chat/apply {"settings": {...}, "reason": "..."}, or with "backtest": true to run the backtest of it
    first. Checked here and again by the root service; the dashboard itself can't write the settings."""
    if STATE_FROM:
        return 409, {"ok": False, "error": "This is a preview: it shows the live bot, so it can't change its settings."}
    if auth.access(cfg) == "open":
        return 403, {"ok": False, "error": "Changing settings needs the dashboard login. Turn it on over SSH: charles-login"}
    try:
        req = json.loads(body)
        changes = chat.check_settings(req.get("settings"))
    except (ValueError, AttributeError) as e:
        return 400, {"ok": False, "error": str(e) or "Couldn't read that change."}
    action = "backtest-settings" if req.get("backtest") is True else "apply-settings"
    try:
        control.request(action, "the dashboard", settings=changes, reason=str(req.get("reason") or "")[:300])
    except OSError as e:
        log.warning(f"control request failed: {e}")
        return 503, {"ok": False, "error": "The dashboard can't leave requests on this server yet (data/requests isn't writable)."}
    return 202, {"ok": True, "action": action}


# ── The Plan tab's sliders: GET and POST /api/settings ──

def _test_files(bot: str) -> tuple[str, str]:
    """A preview changes only its test folder: the settings file there, and the record of changes that Undo reads."""
    return (os.path.join(LEADERBOARD_BOT_DIR, "strategy.json" if bot == "stocks" else "crypto.json"),
            os.path.join(LEADERBOARD_BOT_DIR, f"{bot}_settings_history.jsonl"))


PLAN_KEYS = tuple(strategy_settings.LIMITS) + strategy_settings.CUSTOM      # what the Plan tab moves; presets and self-tune have their own controls


def _own_file(bot: str) -> dict:
    """The stock plan's file as it stands (the preview's test copy on a preview), for the user's own choices."""
    test, (path, _) = LEADERBOARD_BOT_DIR != ROOT, _test_files(bot)
    try:
        return chat.current_settings(path if test and os.path.exists(path) else None)
    except (OSError, ValueError):
        return {}


def plan_now(bot: str) -> dict:
    """Every setting of a bot's plan as it stands. A preview starts from what the live bot uses, then keeps its own test copy."""
    test, (path, _) = LEADERBOARD_BOT_DIR != ROOT, _test_files(bot)
    if bot == "crypto":
        return {k: v for k, v in crypto_settings.current(path if test else crypto_settings.PATH).items() if k in crypto_settings.PLAN_KEYS}
    if test and os.path.exists(path):
        return {k: v for k, v in {**allocator.DEFAULTS, **chat.current_settings(path)}.items() if k in PLAN_KEYS}
    rules = ((_cached("state", 10, _stocks_state) or {}).get("rules") or {}).get("settings") if STATE_FROM else None
    return {k: v for k, v in {**allocator.DEFAULTS, **(rules or chat.current_settings())}.items() if k in PLAN_KEYS}


# ── The ticker search (the Custom Plan's "Add a ticker") ──
TICKERS_FILE = os.path.join(ROOT, "tickers.json")
_FUND_WORDS = re.compile(r"\b(ETF|ETN|Fund|Trust|iShares|SPDR|Vanguard|Invesco|ProShares)\b", re.I)
# A fund named for one part of the market goes in a sector block, any other fund in a long-term one, a single stock in a momentum
# one: only where the search puts it first (owner: "categorized by stock type"); the user can move it.
_SECTOR_WORDS = re.compile(r"\b(Sector|Semiconductors?|Biotech\w*|Bank\w*|Energy|Health ?Care|Technology|Tech|Software|Real Estate|REIT|"
                           r"Utilities|Financials?|Industrials?(?! Average)|Materials|Consumer|Aerospace|Defense|Oil|Gas|Mining|Metals|Solar|Uranium|"
                           r"Homebuilders|Retail|Internet|Cyber\w*|Robotics|Transport\w*|Communications?|Pharma\w*|Medical|Insurance|Jets|Airlines?)\b", re.I)


def block_type(row: dict) -> str:
    if row.get("kind") != "fund":
        return "momentum"
    return "sector" if _SECTOR_WORDS.search(row.get("name") or "") else "long"


_NAME_TAIL = re.compile(r"\s+(Class [A-C] )?(Common Stock|Ordinary Shares|American Depositary Shares?|Common Shares)\b.*$", re.I)


def plain_name(name: str) -> str:
    """'NVIDIA Corporation Common Stock' → 'NVIDIA Corporation'; an all-capitals name in ordinary capitals."""
    n = _NAME_TAIL.sub("", " ".join(str(name).split())) or str(name)
    return n.title() if n.replace("Class", "").isupper() and len(n) > 5 else n


def _alpaca_assets() -> list:
    """Every US stock and fund Alpaca can trade in dollar amounts (fractional), from the user's own account: [{symbol, name, kind}].
    Read raw: alpaca-py's own Asset model refuses the whole list when Alpaca adds an exchange it doesn't know (TXSE, 2026-11)."""
    import alpaca_executor
    rows = alpaca_executor._client().get("/assets", {"status": "active", "asset_class": "us_equity"})
    out = []
    for a in rows if isinstance(rows, list) else []:
        sym, name = a.get("symbol"), a.get("name")
        if a.get("tradable") and a.get("fractionable") and sym and name:
            out.append({"symbol": sym.replace("/", "."), "name": plain_name(name), "kind": "fund" if _FUND_WORDS.search(name) else "stock"})
    return out


_BUILTIN = []


def _builtin_tickers() -> list:
    """tickers.json, read once (it only changes with a release)."""
    if not _BUILTIN:
        try:
            with open(TICKERS_FILE) as f:
                _BUILTIN.extend(json.load(f)["tickers"])
        except (OSError, ValueError, KeyError) as e:
            log.warning(f"tickers.json unreadable: {e}")
    return _BUILTIN


def ticker_search(q: str, lists: tuple | None = None, limit: int = 12) -> list:
    """GET /api/tickers?q=: tickers matching the words typed, the symbol first, then names. Alpaca's own list when the keys
    work (so anything found can be bought), else the built-in one. Plain names from allocator.NAMES where it has one."""
    q = " ".join(str(q or "").split())[:40]
    if not q:
        return []
    if lists is None:
        lists = (_cached("alpaca_assets", 86400, _alpaca_assets) or [], _builtin_tickers())
    import portfolio
    rows = [r for r in _merged(lists) if portfolio.SYMBOL.match(r["symbol"])]      # only what a block can hold (no preferred shares)
    up, low = q.upper(), q.lower()
    known = {r["symbol"] for r in _builtin_tickers()} | set(allocator.NAMES)      # the familiar ones first among look-alikes

    def score(r):
        sym, name = r["symbol"], (allocator.NAMES.get(r["symbol"]) or r["name"]).lower()
        boost = 3 if sym in known else 0
        if sym == up:                         # typing "nvd" on the way to NVDA shouldn't put an obscure NVD first
            return 0 if boost else 2.5
        if sym.startswith(up):
            return 1 + len(sym) - boost
        if name.startswith(low):
            return 20 - 5 * boost
        if (" " + low) in (" " + name):       # a word of the name: "energy" finds XLE before small companies called Energy
            return 30 - 5 * boost
        return None
    hits = sorted(((score(r), r) for r in rows), key=lambda x: (x[0] if x[0] is not None else 99, x[1]["symbol"]))
    return [{"symbol": r["symbol"], "name": allocator.NAMES.get(r["symbol"]) or r["name"], "kind": r.get("kind", "stock"), "type": block_type(r)}
            for sc, r in hits if sc is not None][:limit]


def _merged(lists) -> list:
    seen, rows = set(), []
    for lst in lists:
        for r in lst:
            if r["symbol"] not in seen:
                seen.add(r["symbol"])
                rows.append(r)
    return rows


def ticker_names(symbols) -> dict:
    """{symbol: plain name} for the Plan tab's ticker chips. Never waits on Alpaca: its list counts only once a search loaded it."""
    with _lock:
        hit = _cache.get("alpaca_assets")
    rows = {r["symbol"]: r["name"] for r in _merged(((hit[1] or []) if hit and isinstance(hit[1], list) else [], _builtin_tickers()))}
    return {s: allocator.NAMES.get(s) or rows.get(s) or s for s in symbols}


# ── The coin search (the crypto plan's "My own"): Coinbase's public list of coins it trades in dollars, else coins.json ──
_STABLE = {"USDT", "USDC", "DAI", "PAX", "USDS", "PYUSD", "GUSD", "EURC", "FDUSD", "USD1"}      # cash already waits as cash
_BUILTIN_COINS = []


def _coinbase_coins() -> list:
    """[{product, name}] for every coin Coinbase trades in US dollars right now (public, no key)."""
    import crypto_market
    names = {c.get("id"): c.get("name") for c in crypto_market._get("/currencies")}
    out = []
    for p in crypto_market._get("/products"):
        pid, base = p.get("id") or "", p.get("base_currency")
        if (p.get("quote_currency") == "USD" and p.get("status") == "online" and not p.get("trading_disabled")
                and base not in _STABLE and crypto_plan.PRODUCT.match(pid)):
            out.append({"product": pid, "name": names.get(base) or base})
    return out


def _builtin_coins() -> list:
    if not _BUILTIN_COINS:
        try:
            with open(crypto_settings.COINS_FILE) as f:
                _BUILTIN_COINS.extend(json.load(f)["coins"])
        except (OSError, ValueError, KeyError) as e:
            log.warning(f"coins.json unreadable: {e}")
    return _BUILTIN_COINS


def coin_type(product: str) -> str:
    """Where the search suggests a coin goes: bitcoin and ether in a trend block (as in Charles's plan), any other in a
    momentum one, where only the strongest are held. The user picks."""
    return "trend" if product in crypto_plan.DEFAULTS["WEIGHTS"] else "momentum"


def coin_search(q: str, lists: tuple | None = None, limit: int = 12) -> list:
    """GET /api/tickers?bot=crypto&q=: coins by symbol first, then by name. Coinbase's own list when it answers."""
    q = " ".join(str(q or "").split())[:40]
    if not q:
        return []
    if lists is None:
        found = _cached("coinbase_coins", 86400, _coinbase_coins)
        if found is None:               # Coinbase didn't answer: ask again in 10 minutes, the built-in list meanwhile
            with _lock:
                _cache["coinbase_coins"] = (time.time() - 86400 + 600, None)
        lists = (found or [], _builtin_coins())
    seen, rows = set(), []
    for lst in lists:
        for r in lst:
            if r["product"] not in seen and crypto_plan.PRODUCT.match(r["product"]):
                seen.add(r["product"])
                rows.append(r)
    up, low = q.upper().removesuffix("-USD"), q.lower()
    known = {r["product"] for r in _builtin_coins()} | set(crypto_plan.NAMES)
    names = {**{r["product"]: r["name"] for r in rows}, **{r["product"]: r["name"] for r in _builtin_coins()}, **crypto_plan.NAMES}

    def score(r):
        sym, name = r["product"].split("-")[0], names[r["product"]].lower()
        boost = 3 if r["product"] in known else 0
        if sym == up:
            return 0 if boost else 2.5
        if sym.startswith(up):
            return 1 + len(sym) - boost
        if name.startswith(low):
            return 20 - 5 * boost
        if (" " + low) in (" " + name):
            return 30 - 5 * boost
        return None
    hits = sorted(((score(r), r) for r in rows), key=lambda x: (x[0] if x[0] is not None else 99, x[1]["product"]))
    return [{"symbol": r["product"], "name": names[r["product"]], "kind": "coin", "type": coin_type(r["product"])}
            for sc, r in hits if sc is not None][:limit]


def coin_names_known() -> dict:
    """{product: name} for every coin known without waiting on Coinbase: its list counts once a search loaded it."""
    with _lock:
        hit = _cache.get("coinbase_coins")
    rows = (hit[1] or []) if hit and isinstance(hit[1], list) else []
    return {**{r["product"]: r["name"] for r in rows}, **{r["product"]: r["name"] for r in _builtin_coins()}, **crypto_plan.NAMES}


def crypto_evaluation_view(ev: dict | None) -> dict | None:
    """The crypto plan's monthly check (evaluate.crypto_main), for its Self-Tune tab: each change tried, with what it did in its
    worse half (% a year against the plan now), like the stock plan's."""
    if not ev:
        return None
    base = ((ev.get("current") or {}).get("coins")) or {}

    def worst(v):
        try:
            r = v["results"]["coins"]
            return round(min(r[h]["cagr_pct"] - base[h]["cagr_pct"] for h in ("first", "second")), 1)
        except (KeyError, TypeError):
            return None
    tests = [{"setting": v.get("setting"), "label": v.get("label"), "from": v.get("from"), "to": v.get("to"), "passes": bool(v.get("passes")),
              "why": v.get("why"), "worst": worst(v)} for v in ev.get("variants") or []]
    return {k: ev.get(k) for k in ("generated", "history", "custom", "candidate", "self_tune", "self_tune_said", "rules")} | {
        "now": base.get("all"), "tests": tests, "tested": len(tests), "passing": sum(t["passes"] for t in tests)}


def _check_change(bot: str, changes) -> dict:
    test, path = LEADERBOARD_BOT_DIR != ROOT, _test_files(bot)[0]
    if bot == "crypto":
        return crypto_settings.check(changes, path if test else crypto_settings.PATH, custom=True)
    return chat.check_settings(changes, path if test and os.path.exists(path) else None, custom=True)


def settings_change_test(bot: str, changes, reason: str = "", source: str = "the sliders") -> tuple[bool, str]:
    """A change on a preview: checked the same way, written only to its test folder, and nobody is told."""
    path, history = _test_files(bot)
    if bot == "crypto":
        return crypto_settings.apply_settings(changes, "the preview", reason, path=path, source=source, history=history, tell=False,
                                              custom=source == "the sliders")
    if not os.path.exists(path):
        state_io.write_json(path, {"settings": plan_now("stocks")}, indent=2)
    return chat.apply_settings(changes, "the preview", reason, path=path, source=source, history=history, tell=False, custom=source == "the sliders")


def settings_view(bot: str, cfg: dict | None, cookie: str | None) -> dict:
    """What the sliders start from: the plan now, the default plan, each setting's limits, and whether this browser may change it."""
    if bot not in ("stocks", "crypto"):
        return {"ok": False, "error": "Unknown bot."}
    limits, defaults = (strategy_settings.LIMITS, {k: allocator.DEFAULTS[k] for k in PLAN_KEYS}) if bot == "stocks" \
        else (crypto_settings.LIMITS, crypto_settings.defaults())
    test, history = LEADERBOARD_BOT_DIR != ROOT, _test_files(bot)[1]
    changed = chat.last_change(history if test else None) if bot == "stocks" else crypto_settings.last_change(history if test else crypto_settings.HISTORY)
    why = None
    if not test:
        if STATE_FROM:
            why = "This is a preview: it shows the live bot, so it can't change its settings."
        elif auth.access(cfg) == "open":
            why = "Changing settings needs the dashboard login. Turn it on over SSH: charles-login"
        elif not auth.check(cfg, auth.cookies(cookie).get(SESSION_COOKIE)):
            why = "Log in to change settings: run charles-login on your server and open its link."
    out = {"ok": True, "bot": bot, "settings": plan_now(bot), "defaults": defaults, "test": test, "can": why is None, "why": why,
           "limits": {k: {"min": v[0], "max": v[1]} for k, v in limits.items() if v[2] in (int, float)},
           "can_undo": bool(changed), "changed_at": (changed or {}).get("at")}
    import portfolio
    if bot == "stocks":                  # the Custom Plan, the presets and self-tune (owner 2026-11-03)
        own = _own_file(bot)
        lo, hi = strategy_settings.INVESTED_RANGE
        out["limits"]["INVESTED"] = {"min": lo, "max": hi}
        presets = own.get("PRESETS") or []
        out.update(self_tune=own.get("SELF_TUNE", True) is not False, presets=presets, charles_presets=portfolio.PRESETS,
                   custom={"types": portfolio.TYPE_NAMES, "max_blocks": portfolio.MAX_BLOCKS, "max_tickers": portfolio.MAX_TICKERS,
                           "max_picks": portfolio.MAX_PICKS, "max_presets": portfolio.MAX_PRESETS, "name_max": portfolio.NAME_MAX,
                           "core": allocator.DEFAULTS["CORE"]},
                   names=ticker_names(portfolio.symbols(out["settings"]) + [t for p in presets for t in portfolio.symbols(p["settings"])]),
                   checkup=checkup_installed())
    else:                                # the crypto plan's own coins, presets and self-tune (owner 2026-11-03: "match the stock configuration")
        test_path = _test_files(bot)[0]
        own = crypto_settings.current(test_path if test else crypto_settings.PATH)
        lo, hi = crypto_settings.INVESTED_RANGE
        out["limits"]["INVESTED"] = {"min": lo, "max": hi}
        out.update(self_tune=own.get("SELF_TUNE", True) is not False, presets=own.get("PRESETS") or [], charles_presets=crypto_plan.PRESETS,
                   custom={"types": crypto_plan.TYPE_NAMES, "max_blocks": crypto_plan.MAX_BLOCKS, "max_tickers": crypto_plan.MAX_COINS,
                           "max_picks": crypto_plan.MAX_PICKS, "max_presets": portfolio.MAX_PRESETS, "name_max": portfolio.NAME_MAX,
                           "mom_days": crypto_plan.MOM_DAYS},
                   names=coin_names_known(), checkup=checkup_installed(), fee=crypto_plan.DEFAULTS["FEE"])
    return out


def settings_post(body: bytes, cfg: dict | None) -> tuple[int, dict]:
    """POST /api/settings {"bot", "settings", "backtest"?} or {"bot", "undo": true}. Checked here and again by the root job; the
    dashboard itself can't write the settings. The daily loss limit isn't a setting here at all."""
    try:
        req = json.loads(body)
        bot = req.get("bot")
        if bot not in ("stocks", "crypto"):
            raise ValueError("Unknown bot.")
        undo = req.get("undo") is True
        backtest = req.get("backtest") is True
        changes = None if undo else _check_change(bot, req.get("settings"))
    except (ValueError, AttributeError) as e:
        return 400, {"ok": False, "error": str(e) or "Couldn't read that change."}
    if LEADERBOARD_BOT_DIR != ROOT:
        if backtest:
            return 409, {"ok": False, "error": "The history test runs on a real install, not on this preview."}
        path, history = _test_files(bot)
        try:
            if undo:
                _, said = (chat.undo_settings("the preview", path=path, history=history, tell=False) if bot == "stocks"
                           else crypto_settings.undo_settings("the preview", path=path, history=history, tell=False))
            else:
                _, said = settings_change_test(bot, changes)
        except (ValueError, OSError) as e:
            log.warning(f"test settings change: {e}")
            return 400, {"ok": False, "error": str(e) or "Couldn't save that."}
        return 200, {"ok": True, "done": True, "test": True, "message": f"{'Undone' if undo else 'Saved'} in this preview's test folder. The live bot wasn't touched."}
    if STATE_FROM:
        return 409, {"ok": False, "error": "This is a preview: it shows the live bot, so it can't change its settings."}
    if auth.access(cfg) == "open":
        return 403, {"ok": False, "error": "Changing settings needs the dashboard login. Turn it on over SSH: charles-login"}
    action = "undo-settings" if undo else "backtest-settings" if backtest else "apply-settings"
    try:
        control.request(action, "the dashboard", bot=bot, source="the sliders", **({} if undo else {"settings": changes}))
    except OSError as e:
        log.warning(f"control request failed: {e}")
        return 503, {"ok": False, "error": "The dashboard can't leave requests on this server yet (data/requests isn't writable)."}
    return 202, {"ok": True, "action": action}


def updates_state() -> dict:
    """The Updates card: what the root check last found, the job under way or just finished, the history."""
    v = _read("updates.json") or {"state": "unknown", "versions": [], "pending": []}
    v = {**v, "summary": updates.summary(v.get("pending") or []), "job": _read("update_job.json"),
         "history": (_read("versions.json") or [])[:10], "preview": bool(STATE_FROM)}
    if isinstance(v["job"], dict):
        v["job"] = {k: v["job"].get(k) for k in ("action", "version", "status", "result", "at", "finished", "backtest")}
    return v


def accounts_view(rows: list, on: dict, addon_rows: list) -> dict:
    """The Settings page: the bots that run here (an add-on that's off has no account to connect) and the add-ons."""
    return {"bots": [b for b in rows if on.get(b["id"], True)], "addons": addon_rows}


FONTS = "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap"
_I = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">%s</svg>'
NAV = (("home", "/", "Home", '<path d="M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>'),
       ("stocks", "/stocks", "Stocks", '<path d="M4 14l6-6 4 4 6-6M14 6h6v6"/>'),
       ("crypto", "/crypto/", "Crypto", '<path d="M9 7h5a2.5 2.5 0 0 1 0 5H9zM9 12h6a2.5 2.5 0 0 1 0 5H9zM9 5v14M12 5v2M12 17v2"/>'),
       ("kalshi", "/kalshi/", "Kalshi", '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>'),
       ("leaderboard", "/leaderboard", "Leaderboard", '<path d="M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0zM7 6H4v2a3 3 0 0 0 3 3M17 6h3v2a3 3 0 0 1-3 3"/>'))
LOGO = ('<svg width="28" height="28" viewBox="0 0 32 32" aria-hidden="true"><path d="M7 12.5 L8.4 5 L13.5 8.6 Z M25 12.5 L23.6 5 L18.5 8.6 Z" fill="#fff" stroke="#fff" stroke-width="2.6" stroke-linejoin="round"/>'
        '<path class="ear-in" d="M9.2 10.4 L9.8 7.3 L11.8 8.8 Z M22.8 10.4 L22.2 7.3 L20.2 8.8 Z" fill="#f2b8c0" stroke="#f2b8c0" stroke-width=".6" stroke-linejoin="round"/>'
        '<path d="M16 7.4c3.2 0 5.6 1 7.4 3.2c1.6 1.9 2.3 4.3 2.1 6.9l1.4 1.6l-1.9.4c-.5 2.6-2 4.8-4 6.2l.4 1.9l-1.8-.9l-.6 2l-1.4-1.4l-1.6 1.8l-1.6-1.8l-1.4 1.4l-.6-2l-1.8.9l.4-1.9c-2-1.4-3.5-3.6-4-6.2l-1.9-.4l1.4-1.6c-.2-2.6.5-5 2.1-6.9C10.4 8.4 12.8 7.4 16 7.4Z" fill="#fff"/>'
        '<ellipse cx="16" cy="21" rx="5.6" ry="3.9" fill="#edf1f6"/><path class="brow" d="M10.4 13.1l1.2-.9l1 .6l1-.6l.9.7M21.6 13.1l-1.2-.9l-1 .6l-1-.6l-.9.7" stroke="#c3cedb" stroke-width="1" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
        '<circle class="eye" cx="12.6" cy="15.5" r="1.3" fill="#1f2937"/><circle class="eye" cx="19.4" cy="15.5" r="1.3" fill="#1f2937"/><circle class="glint" cx="13" cy="15.1" r=".4" fill="#fff"/><circle class="glint" cx="19.8" cy="15.1" r=".4" fill="#fff"/><path class="angry" d="M10 12.4 L14.4 14.3 M22 12.4 L17.6 14.3" stroke="#1f2937" stroke-width="1.5" fill="none" stroke-linecap="round"/>'
        '<path d="M16 19.9c-1.3.2-2.6.5-3.8 1.2c-1.4.8-2.6 1.8-3.4 3.2l1.3-.3l-.5 1.4l1.4-.6l.1 1.4l1.2-1l.6 1.2l.8-1.2l.9.9l.3-1.3c.5-.8 1.2-1.5 2-2.1Z M16 19.9c1.3.2 2.6.5 3.8 1.2c1.4.8 2.6 1.8 3.4 3.2l-1.3-.3l.5 1.4l-1.4-.6l-.1 1.4l-1.2-1l-.6 1.2l-.8-1.2l-.9.9l-.3-1.3c-.5-.8-1.2-1.5-2-2.1Z" fill="#fff" stroke="#d3dbe5" stroke-width=".45" stroke-linejoin="round"/>'
        '<path d="M11 20.6q-3.2-.9-6.2-.4M11.2 21.8q-3.2 0-5.9 1.3M21 20.6q3.2-.9 6.2-.4M20.8 21.8q3.2 0 5.9 1.3" stroke="#fff" stroke-width=".6" fill="none" stroke-linecap="round"/><ellipse cx="16" cy="18.7" rx="2" ry="1.4" fill="#1f2937"/></svg>')


def shell(page: bytes, on: dict, current: str) -> bytes:
    """Every page's header and navigation, in one place: the logo goes Home, and an add-on that's off has no tab.
    Added here so the Kalshi page (its own repo, also served alone) gets the same ones and only keeps its own header
    when it's served alone."""
    items = [n for n in NAV if on.get(n[0], True)]
    links = "".join(f'<a href="{h}"{" class=on aria-current=page" if k == current else ""}>{_I % d}<span>{t}</span></a>' for k, h, t, d in items)
    gear = '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.9 2.9l-.1-.1a1.7 1.7 0 0 0-2.9 1.2V21a2 2 0 0 1-4 0v-.1a1.7 1.7 0 0 0-2.9-1.2l-.1.1a2 2 0 1 1-2.9-2.9l.1-.1A1.7 1.7 0 0 0 3 14H3a2 2 0 0 1 0-4h.1a1.7 1.7 0 0 0 1.2-2.9l-.1-.1a2 2 0 1 1 2.9-2.9l.1.1A1.7 1.7 0 0 0 10 3V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 2.9 1.2l.1-.1a2 2 0 1 1 2.9 2.9l-.1.1a1.7 1.7 0 0 0 1.2 2.9H21a2 2 0 0 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>'
    chat_ic = '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/>'
    bell = '<path d="M6 9a6 6 0 0 1 12 0c0 6 2.5 8 2.5 8h-17S6 15 6 9zM10 20.5a2.2 2.2 0 0 0 4 0"/>'
    moon = '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>'     # light and dark in one tap (owner 2026-11-04); Settings still has all three
    head = (f'<header class="shell" data-page="{current}"><div class="bar">'
            f'<a class="brand" href="/" aria-label="Charles: go to Home"><span class="logo">{LOGO}</span><span class="bname">Charles</span></a>'
            f'<nav class="topnav" aria-label="Main">{links}</nav>'
            f'<div class="bar-end" id="t-help"><a class="status" id="st-bot" href="/#t-run"><span class="dot"></span><span>…</span></a>'
            f'<button class="ibtn" id="theme-tg" type="button" aria-label="Dark mode" title="Dark mode">{_I % moon}</button>'
            f'<button class="ibtn bell" id="bell" type="button" aria-label="Alerts" title="Alerts">{_I % bell}<span class="bell-n" id="bell-n" hidden></span></button>'
            f'<button class="ibtn" id="chat-open" type="button" aria-label="Ask Charles" title="Ask Charles">{_I % chat_ic}</button>'
            f'<a class="ibtn{" on" if current == "settings" else ""}" href="/settings" aria-label="Settings" title="Settings">{_I % gear}</a></div>'
            f'</div></header>').encode()
    page, found = re.subn(rb"<header\b.*?</header>", lambda _: head, page, count=1, flags=re.S)
    if not found:
        page = re.sub(rb"(<body\b[^>]*>)", lambda m: m.group(1) + head, page, count=1)
    # The theme is set before the page paints; it's chosen on Settings.
    extra = (f'<link href="{FONTS}" rel="stylesheet"><link rel="stylesheet" href="/shell.css">'
             '<script>try{var t=localStorage.getItem("charles-theme");if(t)document.documentElement.dataset.theme=t}catch(e){}</script>').encode()
    foot = (f'<nav class="appnav" aria-label="Main">{links}</nav><script src="/shell.js" defer></script><script src="/tour.js" defer></script><script src="/alerts.js" defer></script><script src="/chat.js" defer></script><script src="/feed.js" defer></script><script src="/report.js" defer></script>').encode()
    return page.replace(b"</head>", extra + b"</head>", 1).replace(b"</body>", foot + b"</body>", 1)


def _page(title: str, inner: str, head: str = "") -> bytes:
    """A small page in the dashboard's style: the add-on and login pages."""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{title}</title>{head}
<link rel="icon" href="/icon-192.png">
<link href="{FONTS}" rel="stylesheet">
<link rel="stylesheet" href="/style.css">
<script>try {{ const t = localStorage.getItem("charles-theme"); if (t) document.documentElement.dataset.theme = t; }} catch (e) {{}}</script>
</head>
<body>
<main style="max-width:600px">
  <h1 class="page-title" style="display:block">{title}</h1>
  <div class="card">
{inner}
  </div>
</main>
</body>
</html>
""".encode()


def _code(cmd: str) -> str:
    return f'<p><code style="font-size:14px;background:var(--surface-2);padding:6px 10px;border-radius:8px;display:inline-block">{html.escape(cmd)}</code></p>'


def addon_off_page(name: str) -> bytes:
    """/crypto/ or /kalshi/ while that add-on is off: what it is and the one command that turns it on."""
    a = addons.ADDONS[name]
    return _page(f"{html.escape(a['name'])} is off", f"""    <p style="margin-top:0">{html.escape(a["about"])}</p>
    <p>It's an add-on. To turn it on, log in to your server over SSH and run:</p>
    {_code(f"charles-addon on {name}")}
    <p class="note" style="margin-bottom:0">Then refresh this page. <a href="/" style="color:var(--series-1);font-weight:600">Back to Home</a></p>""")


def auth_page(kind: str, token: str = "") -> bytes:
    """The login's pages: "locked" (private, not logged in), "confirm" (a link was opened: tap to log in;
    a link checker or chat preview opening it doesn't use it up) and "expired"."""
    no_ref = '\n<meta name="referrer" content="no-referrer">'           # the link's token stays out of Referer headers
    if kind == "confirm":
        return _page("Log in to Charles", f"""    <p style="margin-top:0">Log in on this phone or computer? It stays logged in for 30 days.</p>
    <form method="post" action="/login"><input type="hidden" name="t" value="{html.escape(token)}">
      <button type="submit" style="font:inherit;font-weight:600;padding:10px 18px;border-radius:10px;border:0;background:var(--series-1);color:#fff;cursor:pointer">Log in</button></form>
    <p class="note" style="margin-bottom:0">To log every browser out later, run <code>charles-login --logout-all</code> on your server.</p>""", no_ref)
    if kind == "expired":
        return _page("This link has expired", f"""    <p style="margin-top:0">A login link works once, within 10 minutes. For a new one, log in to your server over SSH and run:</p>
    {_code("charles-login")}""", no_ref)
    return _page("This dashboard is private", f"""    <p style="margin-top:0">Charles keeps your portfolio private. To open it, log in to your server over SSH and run:</p>
    {_code("charles-login")}
    <p class="note" style="margin-bottom:0">It prints a link that works once, within 10 minutes. The phone or computer you open it on stays logged in for 30 days.</p>""", no_ref)


def gate(method: str, path: str, cfg: dict | None, cookie_header: str | None, csrf_header: str | None,
         now: float | None = None) -> tuple[int, str] | None:
    """None when the request may go ahead, else (status, "login" or "csrf").
    Private: everything needs the login. Public: looking is free, changes need the login and the page's
    CSRF token. Open (no data/auth.json): everything goes, as before the login existed."""
    mode = auth.access(cfg)
    if mode == "open" or path in AUTH_FREE:
        return None
    sid = auth.check(cfg, auth.cookies(cookie_header).get(SESSION_COOKIE), now)
    if method != "GET":
        if not sid:
            return 401, "login"
        return None if hmac.compare_digest(csrf_header or "", auth.csrf(cfg, sid)) else (403, "csrf")
    return (401, "login") if mode == "private" and not sid else None


def set_cookie(name: str, value: str, max_age: int, http_only: bool = True, secure: bool = False) -> str:
    return (f"{name}={value}; Path=/; Max-Age={max_age}; SameSite=Lax" + ("; HttpOnly" if http_only else "")
            + ("; Secure" if secure else ""))


def with_csrf(page: bytes) -> bytes:
    """Every change a page sends carries the CSRF token from its readable cookie. Added here, so the Kalshi
    page (a subtree this repo doesn't edit) gets it too."""
    return page.replace(b"</head>", CSRF_JS + b"</head>", 1)


def build_state() -> dict:
    now = datetime.now(timezone.utc)
    acct = _cached("account", 20, _account) or {}
    spy = _cached("spy", 3600, _spy) or []
    daily = _read("daily_loss.json", {})
    trades = _read("trades.json", [])
    plan = _read("plan.json", {})
    today = str(now.astimezone(ET).date())
    history = _cached("history", 21600, _history) or []
    start_day = str((datetime.fromisoformat(history[0][0]).astimezone(ET) + timedelta(days=1)).date()) \
        if history else None
    total = acct.get("total_value") or 0
    return {
        "generated_at": now.isoformat(),
        "preview": os.environ.get("WEBDASH_PREVIEW") == "1",
        "bot_running": _bot_running(),
        "paused": control.paused(),
        "trading": live.dashboard(DATA),            # paper, or live (charles-live) and whether it's on hold; never a key
        "market": market_status(now),
        "account": {k: acct.get(k) for k in ("total_value", "cash", "invested", "total_return",
                                              "total_return_pct", "open_positions", "day_change")} if acct else None,
        "start_cash": equity_tracker.start_cash(),
        "today": {"halted": bool(daily.get("halted")) and daily.get("date") == today,
                  "limit": daily.get("limit") or config.MAX_DAILY_LOSS},
        "equity": with_live_point(with_history(_cached("equity", 60, lambda: thin_hourly(_read("equity_curve.json", []))) or [],
                                               history), acct.get("total_value"), now),
        "since_start": {"day": start_day, "spy_pct": spy_return_since(spy, start_day) if start_day else None},
        "spy": spy,
        "spy_hourly": _cached("spy_hourly", 900, _spy_hourly) or [],
        "plan": plan or None,
        "positions": holdings(acct.get("positions", []), plan, total),
        "names": allocator.NAMES,
        "trades": [{k: t.get(k) for k in ("timestamp", "symbol", "action", "value", "pnl", "pnl_pct",
                                          "reasoning", "source")} for t in trades[-60:]][::-1],
        "backtest": _cached("backtest", 3600, lambda: backtest_view(_read(BACKTEST))),
        "milestones": milestones(plan, now, _read("evaluation.json")),
        "evaluation": evaluation_view(_read("evaluation.json")),
        "rules": _rules(),
        "claude": {"report": latest_journal(), "plan": _text(CLAUDE_PLAN), "checkup": checkup_installed()},
    }


def _thin(rows: list, keep: int = 90) -> list:
    """At most `keep` rows, evenly spaced, always ending on the last."""
    if len(rows) <= keep:
        return rows
    step = (len(rows) - 1) / (keep - 1)
    return [rows[round(i * step)] for i in range(keep)]


def earnings_series(books: list, now: datetime) -> list:
    """What all the bots have earned together over time, [[iso time, dollars], ...]. `books` is [(curve, start)], a curve
    being [[iso time, value], ...]. Hourly for the last 8 days, daily before that. A bot counts as $0 until it starts."""
    cut = now - timedelta(days=8)
    slots = []
    for curve, start in books:
        slot = {}
        for row in curve:
            t = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00")).astimezone(timezone.utc)
            slot[f"{t:%Y-%m-%dT00}" if t < cut else f"{t:%Y-%m-%dT%H}"] = row[1] - start
        slots.append(slot)
    out, last = [], [0.0] * len(books)
    for key in sorted(set().union(*slots)) if slots else []:
        for i, slot in enumerate(slots):
            if key in slot:
                last[i] = slot[key]
        out.append([key + ":00:00+00:00", round(sum(last), 2)])
    return out


# What a "Rebalanced" notice said about the plan's mode, as an alert of its own. Its orders are alerts already.
MODE_ALERTS = (("Safety mode on", "Safety switch: into T-bills"), ("Aggressive mode on", "Aggressive mode on"), ("plain SPY core", "Back to plain SPY"))
_NOT_WORDS = re.compile(r"^[^\w$+(−-]+")


ALERT_TEXT = 4000                                  # a row opens to the whole message; this only stops a runaway one


def _alert_text(title: str, text: str) -> str:
    """A notice's message for a row: without a report's ruled lines, or a first line that only repeats the title."""
    words = lambda t: re.sub(r"\W+", " ", t).strip().lower()
    lines = [ln for ln in text.splitlines() if not set(ln.strip()) <= set("─━-=_ ")]
    if lines and words(lines[0]) == words(title):
        lines = lines[1:]
    return "\n".join(lines).strip()


def alert_events(stocks: dict, crypto: dict, kalshi: dict, noted: list, keep: int = 30) -> list:
    """GET /api/alerts: what web/alerts.js pops up and the bell lists, newest first, in one shape:
    {"ts", "bot", "kind": buy|sell|won|lost|bet|note|error, "title", "sub", "amount", "pnl", "href"}."""
    ev = []
    trades = stocks.get("trades") or []
    for t in trades[:keep]:
        buy = str(t.get("action") or "").startswith("BUY")
        ev.append({"ts": t.get("timestamp"), "bot": "Stocks", "kind": "buy" if buy else "sell", "title": f"{'Bought' if buy else 'Sold'} {t.get('symbol')}",
                   "sub": str(t.get("reasoning") or "")[:ALERT_TEXT], "amount": t.get("value"), "pnl": None if buy else t.get("pnl"), "href": "/stocks"})
    plan = stocks.get("plan") or {}
    for v in plan.get("vetoes") or []:
        ev.append({"ts": plan.get("last_rebalance"), "bot": "Stocks", "kind": "note", "title": f"AI skipped {v.get('symbol')}", "sub": str(v.get("reason") or "")[:ALERT_TEXT],
                   "amount": None, "pnl": None, "href": "/stocks"})
    book = crypto.get("book") or {}
    for t in (book.get("trades") or [])[:keep]:
        buy = str(t.get("side")).upper() == "BUY"
        name = (crypto.get("names") or {}).get(t.get("product"), t.get("product"))
        ev.append({"ts": t.get("timestamp"), "bot": "Crypto", "kind": "buy" if buy else "sell", "title": f"{'Bought' if buy else 'Sold'} {name}",
                   "sub": str(t.get("reason") or "")[:ALERT_TEXT], "amount": t.get("value"), "pnl": None, "href": "/crypto/"})
    bets = kalshi.get("betting") or {}
    for b in sorted(bets.get("closed") or [], key=lambda x: str(x.get("closed")), reverse=True)[:keep]:
        how = b.get("how")
        word = {"won": "Won", "lost": "Lost", "sold": "Sold", "void": "Refunded"}.get(how, "Closed")
        ev.append({"ts": b.get("closed"), "bot": "Kalshi", "kind": how if how in ("won", "lost") else "note", "title": f"{word}: {b.get('sub') or b.get('title')}",
                   "sub": str(b.get("title") or "")[:ALERT_TEXT], "amount": None, "pnl": b.get("pnl"), "href": "/kalshi/"})
    for b in sorted(bets.get("open") or [], key=lambda x: str(x.get("opened")), reverse=True)[:keep]:
        ev.append({"ts": b.get("opened"), "bot": "Kalshi", "kind": "bet", "title": f"Bet {str(b.get('side') or '').upper()}: {b.get('sub') or b.get('title')}".replace("Bet : ", "Bet: "),
                   "sub": str(b.get("title") or "")[:ALERT_TEXT], "amount": b.get("cost"), "pnl": None, "href": "/kalshi/"})
    for n in noted:
        title, sub = _NOT_WORDS.sub("", str(n.get("title") or "")).strip(), _NOT_WORDS.sub("", str(n.get("message") or "")).strip()
        if title == "Rebalanced":
            said = [t for k, t in MODE_ALERTS if k in sub]
            if not said:
                continue
            title, sub = said[0], ""
        ev.append({"ts": n.get("ts"), "bot": "Charles", "kind": "error" if n.get("level") == "error" else "note", "title": title or "Note",
                   "sub": _alert_text(title, sub)[:ALERT_TEXT], "amount": None, "pnl": None, "href": "/" if n.get("level") == "error" else None})
    return sorted([e for e in ev if e["ts"]], key=lambda e: str(e["ts"]), reverse=True)[:keep]


MODE_NAMES = {"aggressive": "Aggressive mode", "invest": "Invest mode", "safety": "In T-bills"}


def home_view(stocks: dict | None, crypto: dict | None, kalshi: dict | None, noted: list, on: dict, now: datetime) -> dict:
    """GET /api/home: every bot's earnings and chart, the total, and whether Charles is running. The Kalshi number is its
    betting book: the arbitrage book's rewards are an estimate, and never shown as profit."""
    stocks, crypto, kalshi = stocks or {}, crypto or {}, kalshi or {}
    bots, books = [], []

    def add(bot_id, name, href, value, start, today, curve, note, late=False, bench=None):
        row = {"id": bot_id, "name": name, "href": href, "value": value, "start": start, "today": today, "note": note, "late": late,
               "bench": bench, "earned": None if value is None else round(value - start, 2),
               "earned_pct": None if value is None or not start else round((value / start - 1) * 100, 2),
               "curve": [r[:2] for r in _thin(curve or [])]}
        bots.append(row)
        if value is not None:
            books.append((curve or [], start))

    acct, plan = stocks.get("account") or {}, stocks.get("plan") or {}
    spy = (stocks.get("since_start") or {}).get("spy_pct")
    add("stocks", "Stocks", "/stocks", acct.get("total_value"), stocks.get("start_cash") or 1000, acct.get("day_change"), stocks.get("equity"),
        MODE_NAMES.get(plan.get("mode"), "Starts at its first check"), bool(stocks) and not stocks.get("bot_running"),
        {"name": "S&P 500", "pct": spy} if spy is not None else None)
    if on.get("crypto"):
        b = crypto.get("book") or {}
        held = [c["name"] if c["name"].isupper() else c["name"].lower() for c in b.get("coins") or [] if c.get("holding")]
        say = ("Holding " + (" and ".join(held) if len(held) <= 2 else f"{held[0]}, {held[1]} and {len(held) - 2} more")) if held else None
        add("crypto", "Crypto", "/crypto/", b.get("value"), b.get("start_cash") or 1000, b.get("day_change"), b.get("curve"),
            say or ("In cash" if b else "Starts at its first check"), False,
            {"name": "Bitcoin", "pct": b["btc_pct"]} if b.get("btc_pct") is not None else None)
    if on.get("kalshi"):
        k, a = kalshi.get("betting") or {}, (kalshi.get("betting") or {}).get("account") or {}
        n = a.get("open_bets")
        add("kalshi", "Kalshi", "/kalshi/", a.get("account_value"), a.get("start_cash") or 1000, a.get("day_pnl"), k.get("equity"),
            # Both bots always bet in their own books; the account copies the one the switch picks (Kalshi, owner 2026-11-03)
            (f"Following {'Evil Charles' if (k.get('mode') or {}).get('evil') else 'Charles'}" + (f" · {n} open bet{'' if n == 1 else 's'}" if n is not None else ""))
            if k else "No word from it yet",
            bool(kalshi) and not (kalshi.get("scanner") or {}).get("healthy", True))
    counted = [b for b in bots if b["value"] is not None]
    earned = round(sum(b["earned"] for b in counted), 2)
    start = sum(b["start"] for b in counted)
    series = earnings_series(books, now)
    if series:
        series.append([now.isoformat(timespec="seconds"), earned])
    return {"generated_at": now.isoformat(timespec="seconds"), "preview": bool(STATE_FROM), "running": stocks.get("bot_running"), "paused": stocks.get("paused"),
            "trading": stocks.get("trading") or {"mode": "paper"},
            "halted": {"limit": (stocks.get("today") or {}).get("limit")} if (stocks.get("today") or {}).get("halted") else None,
            "next_repick": plan.get("next_rebalance"), "total": {"earned": earned, "earned_pct": round(earned / start * 100, 2) if start else None,
            "today": round(sum(b["today"] or 0 for b in counted), 2), "value": round(sum(b["value"] for b in counted), 2), "start": start},
            "series": series, "bots": bots, "notices": noted,
            "can_add": [{"id": k, "name": a["short"], "about": a["pitch"]} for k, a in addons.ADDONS.items() if not on.get(k)]}


def _kalshi_state() -> dict | None:
    code, body, _ = kalshi_proxy("/kalshi/api/state")
    if code != 200:
        raise OSError("the Kalshi dashboard isn't answering")
    return json.loads(body)


_kalshi_refreshing = threading.Lock()


def kalshi_for_home() -> dict | None:
    """Kalshi's state takes a couple of seconds to build, so Home shows the last one and refreshes it behind the scenes.
    Only the first look after a restart waits for it."""
    with _lock:
        hit = _cache.get("kalshi_state")
    if not hit or hit[1] is None:                  # nothing to show yet: a failed first try is tried again soon
        return _cached("kalshi_state", 15, _kalshi_state)
    if time.time() - hit[0] >= 120 and _kalshi_refreshing.acquire(blocking=False):
        def refresh():
            try:
                _cached("kalshi_state", 120, _kalshi_state)
            finally:
                _kalshi_refreshing.release()
        threading.Thread(target=refresh, daemon=True).start()
    return hit[1]


def new_install_state(st: dict) -> dict:
    """The stock state as a new install has it for /preview-as-new: no account, plan, history or reports. What ships with the code stays."""
    return {**st, "bot_running": False, "paused": None, "account": None, "today": {**(st.get("today") or {}), "halted": False}, "equity": [],
            "since_start": {"day": None, "spy_pct": None}, "plan": None, "positions": [], "trades": [], "milestones": milestones({}, datetime.now(timezone.utc), None),
            "evaluation": None, "claude": {"report": None, "plan": "", "checkup": False}}


def as_new(headers) -> bool:
    """The preview pretending to be a new install (/preview-as-new). Never on a real install: it has no WEBDASH_STATE_FROM."""
    return bool(STATE_FROM) and "preview_new=1" in (headers.get("Cookie") or "")


def has_keys(stocks: dict) -> bool:
    """The account answers, or has once: a moment when Alpaca doesn't answer isn't a new install."""
    return bool((stocks.get("account") or {}).get("total_value") or stocks.get("equity"))


def setup_view(stocks: dict, changed: bool, now: datetime, new: bool = False) -> dict | None:
    """Home's first steps. None once an install is past them: its keys work and the plan was changed, or it's been running a week. So an
    update never greets an old install with them, and a moment when the state can't be read doesn't either."""
    if not stocks and not new:
        return None
    curve = stocks.get("equity") or []
    if not has_keys(stocks):
        return {"keys": False, "plan": changed}
    try:
        old = now - datetime.fromisoformat(curve[0][0]) >= timedelta(days=7) if curve else False
    except (TypeError, ValueError, IndexError):
        old = True
    return None if changed or old else {"keys": True, "plan": False}


def home_state(on: dict | None = None, new: bool = False) -> dict:
    """`new`: the preview pretending to be a new install (/preview-as-new), with no account, no history and no notices."""
    on = addons.enabled() if on is None else on
    now = datetime.now(timezone.utc)
    stocks = {} if new else _cached("state", 10, _stocks_state)
    view = home_view(stocks, _cached("crypto_state", 10, build_crypto_state) if on["crypto"] else None,
                     kalshi_for_home() if on["kalshi"] else None, [] if new else notices()["notices"], on, now)
    changed = not new and bool(chat.last_change(_test_files("stocks")[1] if LEADERBOARD_BOT_DIR != ROOT else None))
    return {**view, "setup": setup_view(stocks or {}, changed, now, new)}


def alerts_state(on: dict | None = None) -> dict:
    on = addons.enabled() if on is None else on
    return {"alerts": alert_events(_cached("state", 10, _stocks_state) or {}, (_cached("crypto_state", 10, build_crypto_state) or {}) if on["crypto"] else {},
                                   (kalshi_for_home() or {}) if on["kalshi"] else {}, notices()["notices"])}


def feed_state(bot: str, on: dict) -> dict:
    """GET /api/feed?bot=: the feed beside a bot's page (feed.py). The news comes from a slower place, so the page gets Charles's own
    posts at once and "waiting" says to ask again soon."""
    noted = notices()["notices"]
    if bot == "stocks":
        news = _soon("news", 1800, holdings_news)
        posts = feed.for_stocks(_cached("state", 10, _stocks_state) or {}, noted, (news or {}).get("items") or [])
    elif bot == "crypto" and on["crypto"]:
        news = _soon("news-crypto", 1800, news_feed.coin_news)
        posts = feed.for_crypto(_cached("crypto_state", 10, build_crypto_state) or {}, (news or {}).get("items") or [])
    elif bot == "kalshi" and on["kalshi"]:
        k = kalshi_for_home() or {}
        news = _soon("news-kalshi", 1800, lambda: news_feed.bet_news((k.get("betting") or {}).get("open") or []))
        posts = feed.for_kalshi(k, noted, (news or {}).get("items") or [])
    else:
        return {"posts": []}
    return {"posts": posts, "waiting": news is None, "note": feed.NOTE[bot]}


def _stock_line(symbol: str):
    """A stock's last month of closes for a finding's chart; None until the first fetch is back. Yahoo's chart address directly, because
    importing yfinance would add pandas to this small process for one little line."""
    def fetch():
        import requests
        got = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}", params={"range": "1mo", "interval": "1d"},
                           timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        got.raise_for_status()
        day = got.json()["chart"]["result"][0]
        return [[datetime.fromtimestamp(t, timezone.utc).date().isoformat(), round(c, 2)]
                for t, c in zip(day["timestamp"], day["indicators"]["quote"][0]["close"]) if c is not None]
    return _soon(f"px-{symbol}", 3600, fetch)


def findings_state(bot: str, on: dict) -> dict:
    """GET /api/findings?bot=: Claude's latest report as short findings with small charts (report_view.py)."""
    if bot == "kalshi" and on["kalshi"]:
        st = kalshi_for_home() or {}
        return report_view.view((st.get("review") or {}).get("report"), "kalshi", st)
    if bot != "stocks":
        return {"date": None, "items": [], "also": [], "text": ""}
    st = _cached("state", 10, _stocks_state) or {}
    asked = []

    def line(sym):
        rows = _stock_line(sym)
        asked.append(rows is None)
        return rows
    out = report_view.view((st.get("claude") or {}).get("report"), "stocks", st, stock_line=line)
    return {**out, "waiting": any(asked)}


def status_view(new: bool = False) -> dict:
    st = {} if new else _cached("state", 10, _stocks_state) or {}
    return {"running": False if new else st.get("bot_running"), "paused": st.get("paused"), "preview": bool(STATE_FROM),
            "waiting": new or (bool(st) and not has_keys(st))}


def board_view(bot: str, rng: str) -> dict:
    """GET /api/board: the leaderboard, for the dashboard's own Leaderboard page."""
    from leaderboard import client
    if bot not in ("stocks", "crypto") or rng not in ("week", "month", "all"):
        return {"ok": False, "message": "unknown board"}
    d = _cached(f"board-{bot}-{rng}", 60, lambda: client._get(f"{LEADERBOARD_URL}/api/leaderboard?bot={bot}&range={rng}"))
    if not isinstance(d, dict):
        return {"ok": False, "message": "The leaderboard isn't answering right now."}
    return {**d, "ok": True, "board_url": f"{LEADERBOARD_SITE}?bot={bot}"}


def leaderboard_copy(body: bytes, cfg: dict | None) -> tuple[int, dict]:
    """POST /api/leaderboard/copy {"id"}: copy a board entry's settings into this bot. Its signature is checked here and again
    by the root job, which fetches it itself: the dashboard only passes on an id. A preview writes only its test folder."""
    from leaderboard import client
    try:
        entry = json.loads(body).get("id")
        if LEADERBOARD_BOT_DIR != ROOT:
            c = client.fetch(entry, LEADERBOARD_URL)
            now_set = plan_now(c["bot"])
            changes = {k: v for k, v in c["settings"].items() if now_set.get(k) != v}
            if changes:
                settings_change_test(c["bot"], changes, f"copied {c['id']} ({c['name']})", "the leaderboard")
            return 200, {"ok": True, "done": True, "test": True, "message": "Copied into this preview's test folder. The live bot wasn't touched."}
        if auth.access(cfg) == "open":
            return 403, {"ok": False, "message": "Copying needs the dashboard login. Turn it on over SSH: charles-login"}
        client.fetch(entry, LEADERBOARD_URL)
        control.request("copy-config", "the dashboard", config=entry)
    except (ValueError, AttributeError, TypeError) as e:
        return 400, {"ok": False, "message": str(e) or "malformed request"}
    except OSError as e:
        log.warning(f"copy failed: {e}")
        return 502, {"ok": False, "message": "That didn't work: the leaderboard or this server's request folder isn't answering."}
    return 202, {"ok": True, "action": "copy-config"}


# ── HTTP ──────────────────────────────────────────────────────────────────────

# The Kalshi bot's dashboard (its own service, own env, port 8503) shown here under /kalshi/, so
# stocks and Kalshi share one address. Only its page, its read-only state and the Evil Charles
# switch (POST /kalshi/api/mode, owner 2026-10-26) pass through.
KALSHI_URL = os.environ.get("KALSHI_URL", "http://127.0.0.1:8503")
KALSHI_PATHS = {"/kalshi/": "/", "/kalshi/index.html": "/", "/kalshi/api/state": "/api/state"}
KALSHI_PAGE = os.environ.get("KALSHI_PAGE")      # preview: a branch copy of the Kalshi page (its state stays live)


def kalshi_proxy(path: str):
    """(status, body, content type) for a /kalshi/ path, fetched from the Kalshi dashboard."""
    import urllib.request
    if KALSHI_PAGE and KALSHI_PATHS[path] == "/":
        with open(KALSHI_PAGE, "rb") as f:
            return 200, f.read(), "text/html; charset=utf-8"
    try:
        with urllib.request.urlopen(KALSHI_URL + KALSHI_PATHS[path], timeout=10) as r:
            return 200, r.read(), r.headers.get("Content-Type", "application/octet-stream")
    except Exception as e:
        msg = f"The Kalshi dashboard isn't answering ({type(e).__name__}). Is kalshi-web running?"
        return 502, msg.encode(), "text/plain; charset=utf-8"


def kalshi_mode(body: bytes, ctype: str):
    """Pass the Evil Charles switch to the Kalshi dashboard, which checks it and saves it."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(KALSHI_URL + "/api/mode", data=body, method="POST", headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), "application/json"
    except urllib.error.HTTPError as e:
        return e.code, e.read(), "application/json"
    except Exception as e:
        return 502, json.dumps({"ok": False, "message": f"The Kalshi dashboard isn't answering ({type(e).__name__})."}).encode(), "application/json"


# The leaderboard (owner 2026-10-27; leaderboard/, docs/leaderboard.md): the Home cards' Submit button.
# Only this bot's settings and an optional name leave the server, signed with this install's key.
LEADERBOARD_URL = os.environ.get("LEADERBOARD_URL", "https://charles-bot.xyz")
LEADERBOARD_SITE = os.environ.get("LEADERBOARD_SITE", "https://charles-bot.xyz/leaderboard.html")
LEADERBOARD_BOT_DIR = os.environ.get("LEADERBOARD_BOT_DIR") or ROOT      # the preview submits a test copy's settings


def leaderboard_mine(bot: str) -> dict:
    from leaderboard import client
    if bot not in ("stocks", "crypto"):
        return {"ok": False, "message": "unknown bot"}
    out = {"ok": True, "board_url": f"{LEADERBOARD_SITE}?bot={bot}", "min_days": 20}
    try:
        out["preview"] = client.preview(bot, LEADERBOARD_BOT_DIR)
    except ValueError as e:
        return {**out, "ok": False, "custom": str(e) == client.NOT_SHARED,
                "message": str(e) if str(e) == client.NOT_SHARED else f"This bot's settings can't be shared as they are ({e})."}
    try:
        out["entries"] = [x for x in client.mine(DATA, LEADERBOARD_URL) if x["bot"] == bot]
    except Exception as e:
        return {**out, "ok": False, "message": f"The leaderboard isn't answering right now ({type(e).__name__})."}
    return out


def _board_changed():
    """Someone just joined or left the board: the next look shows it, not the last minute's copy."""
    with _lock:
        for k in [k for k in _cache if k.startswith("board-")]:
            del _cache[k]


def leaderboard_post(path: str, body: bytes):
    """(status, reply) for a Submit or Withdraw from the Home card."""
    from leaderboard import client
    try:
        req = json.loads(body)
        if path.endswith("/submit"):
            if req.get("bot") not in ("stocks", "crypto"):
                return 400, {"ok": False, "message": "unknown bot"}
            out = client.submit(req["bot"], req.get("name") or None, DATA, LEADERBOARD_BOT_DIR, LEADERBOARD_URL)
        else:
            out = client.withdraw(str(req["id"]), DATA, LEADERBOARD_URL)
        _board_changed()
        return out
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        return 400, {"ok": False, "message": str(e) or "malformed request"}
    except Exception as e:
        return 502, {"ok": False, "message": f"The leaderboard isn't answering right now ({type(e).__name__})."}


def _settled(path: str) -> tuple[set, set]:
    """(notices to hide, notices to show as plain notes), by their "ts", from notices_settled.json beside the notices:
    warnings Claude dealt with or the owner didn't need (owner 2026-11-06: warnings only for real bugs). The notices
    file itself is never edited."""
    try:
        with open(path) as f:
            s = json.load(f)
        return {str(t) for t in s.get("hide") or []}, {str(t) for t in s.get("note") or []}
    except (OSError, ValueError, AttributeError, TypeError):
        return set(), set()


def notices(path: str | None = None, now=None) -> dict:
    """GET /api/notices: what the bot noted in the last 7 days, newest first (alerts.py)."""
    now = now or datetime.now(timezone.utc)
    path = path or os.path.join(DATA, "notifications.json")
    try:
        with open(path) as f:
            rows = json.load(f)
    except (OSError, ValueError):
        return {"notices": []}
    rows = rows if isinstance(rows, list) else []
    hide, note = _settled(os.path.join(os.path.dirname(path), "notices_settled.json"))
    # A restart alarm stops counting once a later restart went clean or Claude checked it. The file itself is never edited.
    clear = [i for i, r in enumerate(rows) if isinstance(r, dict) and str(r.get("title", "")).startswith(("Charles restarted", "Charles restart checked"))]
    keep = []
    for i, r in enumerate(rows):
        try:
            if clear and i < clear[-1] and str(r.get("title", "")).startswith("Charles restart") and "needs a look" in str(r.get("title", "")):
                continue
            if str(r["ts"]) in hide:
                continue
            if (now - datetime.fromisoformat(r["ts"])).total_seconds() <= 7 * 86400:
                keep.append({"ts": r["ts"], "title": str(r.get("title", "")), "message": str(r.get("message", "")),
                             "level": "error" if r.get("level") == "error" and str(r["ts"]) not in note else "info"})
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return {"notices": keep[::-1][:20]}


def challenge_view(bot: str) -> dict:
    """GET /api/challenge?bot=: the challenge taking entries, if it's for this bot, and what entering would send."""
    from leaderboard import challenges, client
    if bot not in ("stocks", "crypto"):
        return {"ok": False, "message": "unknown bot"}
    try:
        c = client.challenges(LEADERBOARD_URL).get("open")
        if not c or c["bot"] != bot:
            return {"ok": True, "open": None}
        current = client.current_settings(bot, LEADERBOARD_BOT_DIR)
        fitted = challenges.fit_rules(current, c["rules"])
        mine = next((x for x in client.my_challenge_entries(DATA, LEADERBOARD_URL) if x["challenge"] == c["id"]), None)
    except Exception as e:
        return {"ok": False, "message": f"The leaderboard isn't answering right now ({type(e).__name__})."}
    return {"ok": True, "open": {k: c[k] for k in ("id", "title", "about", "rules", "metric", "starts", "ends", "n")}, "mine": mine, "board_url": f"{LEADERBOARD_SITE}?bot={bot}#challenges",
            "changes": {k: {"old": current.get(k), "new": v} for k, v in fitted.items() if current.get(k) != v}, "settings": fitted}


def challenge_post(body: bytes, withdraw: bool = False):
    """(status, reply) for Enter and Withdraw on the challenge card."""
    from leaderboard import client
    try:
        req = json.loads(body)
        c = client.challenges(LEADERBOARD_URL).get("open")
        if withdraw:
            return (409, {"ok": False, "message": "That challenge has started, so its entries stay."}) if not c else \
                client.withdraw_challenge(c["id"], DATA, LEADERBOARD_URL)
        if not c or c["bot"] != req.get("bot"):
            return 409, {"ok": False, "message": "That challenge isn't taking entries any more. Refresh the page."}
        return client.enter_challenge(c, req.get("name") or None, DATA, LEADERBOARD_BOT_DIR, LEADERBOARD_URL)
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        return 400, {"ok": False, "message": str(e) or "malformed request"}
    except Exception as e:
        return 502, {"ok": False, "message": f"The leaderboard isn't answering right now ({type(e).__name__})."}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body: bytes, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(json_safe(obj), default=str, allow_nan=False).encode(), "application/json")

    def _redirect(self, to: str, cookies=()):
        self.send_response(303)
        self.send_header("Location", to)
        for c in cookies:
            self.send_header("Set-Cookie", c)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _on(self) -> dict:
        """Which add-ons are on. The preview can pretend to be a new install, to see what a new user sees (/preview-as-new)."""
        on = addons.enabled()
        return {k: False for k in on} if as_new(self.headers) else on

    def _stopped(self, method: str, path: str) -> bool:
        """Answers a request the login doesn't allow and returns True; False lets it through."""
        stop = gate(method, path, _cached("auth", 5, auth.load), self.headers.get("Cookie"), self.headers.get("X-CSRF-Token"))
        if not stop:
            return False
        code, why = stop
        if method != "GET" or "/api/" in path:
            self._json(code, {"error": "Log in first: run charles-login on your server." if why == "login"
                              else "This page's login has changed. Refresh it and try again."})
        else:
            self._send(code, auth_page("locked"), "text/html; charset=utf-8")
        return True

    def _login(self, token: str, use: bool):
        """GET /login?t=… asks "Log in on this device?"; the POST from its button uses the link up."""
        cfg = auth.load()                      # not the cached copy: a link made a moment ago must work
        if auth.access(cfg) == "open":
            return self._redirect("/")
        if not use:
            ok = auth.link_ok(cfg, token, _links_used)
            return self._send(200 if ok else 410, auth_page("confirm" if ok else "expired", token), "text/html; charset=utf-8")
        if not auth.take_link(cfg, token, _links_used):
            return self._send(410, auth_page("expired"), "text/html; charset=utf-8")
        value, secure = auth.session(cfg), self.headers.get("X-Forwarded-Proto") == "https"
        self._redirect("/", [set_cookie(SESSION_COOKIE, value, auth.SESSION_SECONDS, True, secure),
                             set_cookie(CSRF_COOKIE, auth.csrf(cfg, value.split(".")[0]), auth.SESSION_SECONDS, False, secure)])

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/login":
            return self._login(parse_qs(query).get("t", [""])[0], use=False)
        if path == "/logout":
            return self._redirect("/", [set_cookie(SESSION_COOKIE, "", 0), set_cookie(CSRF_COOKIE, "", 0, http_only=False)])
        if path == "/api/chat/status":
            if self._stopped("GET", path):
                return
            return self._json(200, chat_status(_cached("auth", 5, auth.load), self.headers.get("Cookie")))
        if path == "/api/session":
            cfg = _cached("auth", 5, auth.load)
            return self._json(200, {"access": auth.access(cfg), "logged_in": bool(
                auth.check(cfg, auth.cookies(self.headers.get("Cookie")).get(SESSION_COOKIE)))})
        if self._stopped("GET", path):
            return
        if path == "/preview-as-new" and STATE_FROM:     # the preview only: look at the dashboard as a new install sees it (?off goes back)
            off = "off" in query
            return self._redirect("/", [f"preview_new={'0' if off else '1'}; Path=/; Max-Age={0 if off else 86400}; SameSite=Lax"])
        if path in PAGES:
            on = self._on()
            if path == "/crypto/" and not on["crypto"]:
                return self._send(200, addon_off_page("crypto"), "text/html; charset=utf-8")
            with open(os.path.join(ROOT, "web", PAGES[path]), "rb") as f:
                self._send(200, with_csrf(shell(f.read(), on, PAGE_TAB[path])), "text/html; charset=utf-8")
        elif path in ("/crypto", "/accounts", "/accounts/"):
            self.send_response(301)
            self.send_header("Location", "/crypto/" if path == "/crypto" else "/settings")
            self.end_headers()
        elif path == "/api/home":
            self._json(200, home_state(self._on(), as_new(self.headers)))
        elif path == "/api/status":
            self._json(200, status_view(as_new(self.headers)))
        elif path == "/api/board":
            q = parse_qs(query)
            self._json(200, board_view(q.get("bot", [""])[0], q.get("range", ["week"])[0]))
        elif path == "/crypto/api/state":
            self._json(200, _cached("crypto_state", 10, build_crypto_state) or {"error": "unavailable"})
        elif path == "/api/leaderboard/mine":
            bot = self.path.partition("bot=")[2].split("&")[0]
            mine = leaderboard_mine(bot)
            self._json(200, {**mine, "entries": []} if as_new(self.headers) else mine)
        elif path == "/api/challenge":
            self._json(200, challenge_view(self.path.partition("bot=")[2].split("&")[0]))
        elif path == "/api/settings":
            self._json(200, settings_view(parse_qs(query).get("bot", [""])[0], _cached("auth", 5, auth.load), self.headers.get("Cookie")))
        elif path == "/api/tickers":
            qs = parse_qs(query)
            q = qs.get("q", [""])[0]
            self._json(200, {"ok": True, "tickers": coin_search(q) if qs.get("bot", [""])[0] == "crypto" else ticker_search(q)})
        elif path == "/api/accounts":
            self._json(200, accounts_state(self._on(), as_new(self.headers)))
        elif path == "/api/updates":
            self._json(200, updates_state())
        elif path == "/api/notices":
            self._json(200, notices())
        elif path == "/api/alerts":
            self._json(200, {"alerts": []} if as_new(self.headers) else alerts_state(self._on()))
        elif path == "/api/findings":
            bot = parse_qs(query).get("bot", ["stocks"])[0]
            self._json(200, findings_state("" if as_new(self.headers) else bot, self._on()))
        elif path == "/api/feed":
            self._json(200, {"posts": []} if as_new(self.headers) else feed_state(parse_qs(query).get("bot", [""])[0], self._on()))
        elif path == "/api/news":
            self._json(200, _cached("news", 1800, holdings_news) or {"items": [], "by_symbol": {}, "failed": []})
        elif path == "/manifest.webmanifest":
            self._send(200, MANIFEST, "application/manifest+json")
        elif path in STATIC:
            with open(os.path.join(ROOT, "web", path[1:]), "rb") as f:
                self._send(200, f.read(), STATIC[path])
        elif path == "/kalshi":
            self.send_response(301)
            self.send_header("Location", "/kalshi/")
            self.end_headers()
        elif path in KALSHI_PATHS:
            on = self._on()
            if not on["kalshi"]:
                if KALSHI_PATHS[path] == "/":
                    return self._send(200, addon_off_page("kalshi"), "text/html; charset=utf-8")
                return self._json(404, {"error": "Kalshi is off"})
            code, body, ctype = kalshi_proxy(path)
            self._send(code, with_csrf(shell(body, on, "kalshi")) if ctype.startswith("text/html") else body, ctype)
        elif path == "/api/state":
            st = _cached("state", 10, _stocks_state) or {"error": "unavailable"}
            body = json.dumps(json_safe(new_install_state(st) if as_new(self.headers) and not st.get("error") else st), default=str, allow_nan=False).encode()
            self._send(200, body, "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        path = self.path.split("?")[0]
        if path not in ("/login", "/api/control", "/kalshi/api/mode", "/api/leaderboard/submit", "/api/leaderboard/withdraw",
                        "/api/chat", "/api/chat/apply", "/api/challenge/enter", "/api/challenge/withdraw", "/api/leaderboard/copy", "/api/settings"):
            return self._send(404, b"not found", "text/plain")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if not 0 < n <= {"/api/chat": 64_000, "/api/chat/apply": 2048, "/api/settings": 60_000}.get(path, 256):
            return self._send(413, b"too big", "text/plain")
        body = self.rfile.read(n)
        if path == "/login":
            return self._login(parse_qs(body.decode("utf-8", "replace")).get("t", [""])[0], use=True)
        if self._stopped("POST", path):
            return
        if path == "/api/control":
            return self._json(*control_post(body, _cached("auth", 5, auth.load)))
        if path == "/api/challenge/enter":
            return self._json(*challenge_post(body))
        if path == "/api/challenge/withdraw":
            return self._json(*challenge_post(body, withdraw=True))
        if path == "/api/leaderboard/copy":
            return self._json(*leaderboard_copy(body, _cached("auth", 5, auth.load)))
        if path == "/api/chat":
            return self._json(*chat_post(body, _cached("auth", 5, auth.load)))
        if path == "/api/chat/apply":
            return self._json(*chat_apply_post(body, _cached("auth", 5, auth.load)))
        if path == "/api/settings":
            return self._json(*settings_post(body, _cached("auth", 5, auth.load)))
        if path == "/kalshi/api/mode":
            return self._send(*kalshi_mode(body, self.headers.get("Content-Type", "")))
        self._json(*leaderboard_post(path, body))

    def log_message(self, *args):       # keep the journal quiet
        pass


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info(f"Charles web dashboard on :{PORT}")
    ThreadingHTTPServer((os.environ.get("WEBDASH_HOST", "0.0.0.0"), PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
