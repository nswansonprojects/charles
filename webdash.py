"""
webdash.py — Charles's mobile dashboard (port 8502): one page (web/index.html) + one JSON endpoint.

Read-only for trading: it reads the bot's data/ files, the Alpaca paper account and SPY closes, and
never trades or writes. The Accounts page shows which broker accounts are connected and the SSH
steps to connect them (keys go in through the terminal, connections.py). It also shows the crypto
book (/crypto/, crypto_paper.py's paper book).
Python stdlib server, a few MB of memory. Runs as its own service (charles-web) so it keeps
working while the bot restarts. The Kalshi bot's page is passed through under /kalshi/.

Answers, in this order: how is my money doing (vs SPY), what is Charles doing and why (the plan),
what does it hold, what did it do, and how Claude is tuning the rules.
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

import allocator
import config
import connections
import crypto_plan

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("WEBDASH_DATA") or os.path.join(ROOT, "data")     # the preview reads a copy
PAGES = {"/": "index.html", "/index.html": "index.html", "/crypto/": "crypto.html", "/accounts": "accounts.html"}
CRYPTO_BACKTEST = os.path.join(ROOT, "docs", "crypto_backtest.json")
BACKTEST = os.path.join(ROOT, "docs", "backtest.json")
PORT = int(os.environ.get("WEBDASH_PORT", "8502"))
STATIC = {"/style.css": "text/css; charset=utf-8", "/icon-180.png": "image/png", "/icon-192.png": "image/png", "/icon-512.png": "image/png"}
MANIFEST = json.dumps({
    "name": "Charles", "short_name": "Charles", "start_url": "/", "scope": "/", "display": "standalone",
    "background_color": "#f4f4f2", "theme_color": "#2a78d6",
    "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
              {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
}).encode()
ET = ZoneInfo("America/New_York")
JOURNAL = os.path.join(ROOT, "ops", "journal")         # Claude's daily reports (check-up)
CLAUDE_PLAN = os.path.join(ROOT, "ops", "claude-plan.md")
REVIEW_DAYS = 182                                       # the first honest look at the new plan vs SPY
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


def holdings(positions: list, plan: dict, total: float) -> list:
    """Account positions with their role in the plan (core / pick / safety / leaving), their target
    and actual share of the account, and the pick's momentum rank. Biggest first."""
    s = plan.get("settings") or {}
    core, safe, agg = s.get("CORE", "SPY"), s.get("SAFE", "SGOV"), s.get("AGG_CORE", "SSO")
    targets, picks = plan.get("targets") or {}, plan.get("picks") or []
    ranked = {r["symbol"]: r for r in plan.get("ranked") or []}
    out = []
    for p in positions:
        sym = p["symbol"]
        in_plan = sym in targets or not targets
        role = ("core" if sym in (core, agg) and in_plan else "safety" if sym == safe else
                "pick" if sym in picks else "leaving")
        r = ranked.get(sym, {})
        out.append({**p, "role": role, "target_pct": round(targets.get(sym, 0) * 100, 1) if plan else None,
                    "actual_pct": round(p["market_value"] / total * 100, 1) if total else None,
                    "momentum": r.get("momentum"), "rank": r.get("rank")})
    order = {"core": 0, "pick": 1, "safety": 2, "leaving": 3}
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
            "candidate": ev.get("candidate"), "live": ev.get("live") or {}}


def milestones(plan: dict, now: datetime, ev: dict | None = None) -> list:
    """Progress bars: the next monthly re-pick, the next rule check, and the six-month check vs SPY."""
    started = plan.get("started")
    if not started:
        return [{"name": "First check", "about": "Charles sets up the plan at the first 10:00 AM ET check after the switch.",
                 "progress": 0.0, "needs": "the first check", "detail": "not yet"}]
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
    out.append({"name": "Next rule check", "about": "Claude tests small changes to the plan's numbers on 19 years of history.",
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


def crypto_view(book: dict | None, prices: dict, now: datetime, bt: dict | None = None) -> dict:
    """The crypto demo's paper book valued at live prices, each coin vs its trend line, the next
    weekly check, and the backtest. Missing prices (feed down) fall back to the last fill price."""
    s = crypto_plan.DEFAULTS
    out = {"generated_at": now.isoformat(), "settings": {k: s[k] for k in ("WEIGHTS", "TREND_DAYS", "CHECK_DAYS", "FEE")},
           "names": crypto_plan.NAMES, "backtest": bt, "book": None}
    if not book:
        return out
    last = {t["product"]: t["price"] for t in book.get("trades", [])}
    px = {p: prices.get(p) or last.get(p) or (book.get("coins", {}).get(p) or {}).get("price") for p in s["WEIGHTS"]}
    qty = book.get("qty", {})
    value = book["cash"] + sum(q * px[p] for p, q in qty.items() if q and px.get(p))
    start = book.get("start_cash", 1000.0)
    sp, btc = book.get("start_prices") or {}, px.get("BTC-USD")
    coins = []
    for p, w in s["WEIGHTS"].items():
        c = (book.get("coins") or {}).get(p) or {}
        avg = c.get("average")
        held = qty.get(p, 0.0) * (px.get(p) or 0)
        coins.append({"product": p, "name": crypto_plan.NAMES.get(p, p), "price": px.get(p), "average": avg,
                      "gap_pct": round((px[p] / avg - 1) * 100, 1) if avg and px.get(p) else None,
                      "holding": held > 1, "value": round(held, 2), "share_pct": round(held / value * 100, 1) if value else 0,
                      "target_pct": round((book.get("targets") or {}).get(p, 0) * 100), "weight_pct": round(w * 100)})
    nxt = progress = None
    if book.get("last_check"):
        lc = datetime.fromisoformat(book["last_check"])
        nxt = lc + timedelta(days=s["CHECK_DAYS"])
        progress = round(min(1.0, max(0.0, (now - lc).total_seconds() / (s["CHECK_DAYS"] * 86400))), 2)
    curve = book.get("curve", [])[-2000:]
    day_ago = (now - timedelta(days=1)).isoformat()
    prior = [v for t, v in curve if t <= day_ago] or [start]
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


def _spy():
    import yfinance as yf
    return clean_closes(yf.Ticker("SPY").history(start="2026-05-01")["Close"].items())


def _spy_hourly():
    import yfinance as yf
    t = yf.Ticker("SPY")
    d = t.history(start="2026-05-01", auto_adjust=False)
    factors = {str(i.date()): float(a / c) for i, a, c in zip(d.index, d["Adj Close"], d["Close"]) if c == c and a == a}
    return dividend_adjust(hourly_closes(t.history(start="2026-05-01", interval="60m")["Close"].items()), factors)


def _rules() -> dict:
    """The plan's numbers now (strategy.json over the defaults) and the note on the last change."""
    import strategy_settings
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


def _crypto_prices() -> dict:
    import crypto_market
    return {p: crypto_market.spot(p) for p in crypto_plan.DEFAULTS["WEIGHTS"]}


def build_crypto_state() -> dict:
    book = _read("crypto_paper.json")
    out = crypto_view(book, _cached("crypto_prices", 30, _crypto_prices) or {},
                      datetime.now(timezone.utc), _cached("crypto_bt", 3600, lambda: crypto_backtest_view(_read(CRYPTO_BACKTEST))))
    if book:          # bitcoin's hourly closes since the book started: the chart's "just hold bitcoin" line
        import crypto_market
        out["btc_hourly"] = _cached("btc_hourly", 900, lambda: crypto_market.hourly_closes("BTC-USD", book["started"])) or []
    return out


# A preview copy has no broker keys: it can show the live dashboard's stocks state instead.
STATE_FROM = os.environ.get("WEBDASH_STATE_FROM")


def _stocks_state() -> dict | None:
    if not STATE_FROM:
        return build_state()
    import urllib.request
    with urllib.request.urlopen(STATE_FROM + "/api/state", timeout=15) as r:
        return json.loads(r.read())


def accounts_state() -> dict:
    acct = (_cached("state", 10, _stocks_state) or {}).get("account") if STATE_FROM else _cached("account", 20, _account)
    acct_ok = bool((acct or {}).get("total_value"))
    return {"bots": connections.public_status(_read("connections.json", {}, connections.DIR), server_alpaca=acct_ok)}


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
        "market": market_status(now),
        "account": {k: acct.get(k) for k in ("total_value", "cash", "invested", "total_return",
                                              "total_return_pct", "open_positions", "day_change")} if acct else None,
        "start_cash": config.VIRTUAL_CASH,
        "today": {"halted": bool(daily.get("halted")) and daily.get("date") == today, "limit": config.MAX_DAILY_LOSS},
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
        "claude": {"report": latest_journal(), "plan": _text(CLAUDE_PLAN)},
    }


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

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in PAGES:
            with open(os.path.join(ROOT, "web", PAGES[path]), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif path in ("/crypto", "/accounts/"):
            self.send_response(301)
            self.send_header("Location", "/crypto/" if path == "/crypto" else "/accounts")
            self.end_headers()
        elif path == "/crypto/api/state":
            self._json(200, _cached("crypto_state", 10, build_crypto_state) or {"error": "unavailable"})
        elif path == "/api/accounts":
            self._json(200, accounts_state())
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
            self._send(*kalshi_proxy(path))
        elif path == "/api/state":
            body = json.dumps(json_safe(_cached("state", 10, _stocks_state) or {"error": "unavailable"}),
                              default=str, allow_nan=False).encode()
            self._send(200, body, "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path.split("?")[0] != "/kalshi/api/mode":
            return self._send(404, b"not found", "text/plain")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if not 0 < n <= 256:
            return self._send(413, b"too big", "text/plain")
        self._send(*kalshi_mode(self.rfile.read(n), self.headers.get("Content-Type", "")))

    def log_message(self, *args):       # keep the journal quiet
        pass


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info(f"Charles web dashboard on :{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
