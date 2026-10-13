"""webdash.py — Kalshi paper-account dashboard (port 8503): the betting book on top,
the arbitrage scanner below it.

One page (web/index.html) plus one JSON endpoint (/api/state). Read-only: it reads
data/ files written by run.py and never writes, trades or calls Kalshi.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

import betting
import evaluate
import paper

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
PAGE = os.path.join(ROOT, "web", "index.html")
JOURNAL = os.path.join(ROOT, "ops", "journal")      # Claude's daily reviews (ops/run-review)
PLAN = os.path.join(ROOT, "ops", "claude-plan.md")  # Claude's own plan for the bot
PORT = int(os.environ.get("KALSHI_WEB_PORT", "8503"))
SCAN_EVERY_MIN = 15


def _jsonl(path: str, last: int | None = None) -> list:
    try:
        with open(path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    out = []
    for line in lines[-last:] if last else lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def _json(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def review() -> dict:
    """Newest ops/journal/YYYY-MM-DD.md and the plan, for the Self-Tuning tab."""
    try:
        names = sorted(n for n in os.listdir(JOURNAL) if len(n) == 13 and n.endswith(".md"))
    except FileNotFoundError:
        names = []
    report = None
    if names:
        with open(os.path.join(JOURNAL, names[-1])) as f:
            report = {"date": names[-1][:10], "text": f.read()[:20000], "count": len(names)}
    try:
        with open(PLAN) as f:
            plan = f.read()[:20000]
    except FileNotFoundError:
        plan = None
    return {"report": report, "plan": plan}


STRATEGY_INFO = {
    "weather": ("Weather", "Daily high/low temperatures, 24 cities. NWS forecast + 80-run ensemble spread."),
    "sports": ("Sports", "Game winners (NFL, college football, NHL, NBA, MLB) vs DraftKings odds with the book's cut removed."),
    "econ": ("Inflation", "CPI markets vs the Cleveland Fed's inflation nowcast."),
    "longshot": ("Longshot bias", "Bets NO on 3-10c long shots, which win less often than their price says."),
}


GRADED_NEEDED = 30  # settled bets before a strategy's forecast score says much (bar on the Self-Tuning tab)
# No bar for these: the owner's 2x-payout rule (2026-10-12) leaves longshot nothing it may buy.
CANT_BET = {"longshot"}
TUNE_SETTING = {"trust_model": "forecast trust", "min_edge": "smallest gap it bets on",
                "strategy_cap_pct": "share of the book", "kelly_fraction": "bet size", "max_bet_pct": "biggest bet"}


def next_tune(now: datetime) -> datetime:
    """Start of the next Monday review (9:15 AM ET), the one that may tune settings."""
    et = now.astimezone(ZoneInfo("America/New_York"))
    mon = (et - timedelta(days=et.weekday())).replace(hour=9, minute=15, second=0, microsecond=0)
    return mon if mon > et else mon + timedelta(days=7)


def _change_text(h: dict) -> str:
    key, _, sub = h["setting"].partition(".")
    name = (STRATEGY_INFO[sub][0] + " " if sub in STRATEGY_INFO else "") + TUNE_SETTING.get(key, key)
    fmt = (lambda v: f"{round(v * 100)}%") if key != "min_edge" else (lambda v: f"{round(v * 100)}¢")
    when = datetime.fromisoformat(h["date"]).strftime("%b %-d")
    return f"Last change {when}: {name} {fmt(h['from'])} → {fmt(h['to'])}."


def milestones(now: datetime, led: dict, st: dict) -> list:
    """What is still collecting data before it can be judged: [{key, name, about, progress, needs, detail}].
    One bar per strategy still betting (not paused or CANT_BET, with a bet open or in the last week),
    plus the weekly tune."""
    week = (now - timedelta(days=7)).isoformat()
    out = []
    for k, (name, _) in STRATEGY_INFO.items():
        s = st[k]
        if k in CANT_BET or not betting.RULES["strategy_cap_pct"][k]:
            continue
        if not s["open"] and not any(b["strategy"] == k and b["opened"] >= week for b in led["closed"]):
            continue
        g, bm, bk = s["graded"], s["brier_model"], s["brier_market"]
        detail = f"{g} settled" + ("" if bm is None else " · beating the market so far" if bm < bk
                                    else " · behind the market so far")
        out.append({"key": k, "name": f"{name} forecasts", "progress": round(min(1.0, g / GRADED_NEEDED), 2),
                    "about": "Are its odds better than Kalshi's own price? Its say in each bet grows only if so.",
                    "needs": f"{GRADED_NEEDED} settled bets", "detail": detail})
    tune = next_tune(now)
    hist = _json(betting.TUNING, {}).get("history") or []
    left = (tune - now).total_seconds() / 86400
    out.append({"key": "tune", "name": "Next weekly tune",
                "about": "Claude may move up to two settings a small step, if the results back it. "
                         + (_change_text(hist[-1]) if hist else ""),
                "progress": round(min(1.0, max(0.0, 1 - left / 7)), 2), "needs": "a week",
                "detail": tune.strftime("%a %b %-d") + (f" · in {max(1, round(left * 24))} h" if left < 1 else "")})
    return out


def betting_state(now: datetime) -> dict:
    betting.apply_tuning()  # this process runs for days: show the rules the scans use now
    led = betting.load(os.path.join(DATA, "bets.json"))
    eq = _jsonl(os.path.join(DATA, "bets_equity.jsonl"))
    latest = _json(os.path.join(DATA, "bets_latest.json"), {})
    start = led.get("start_cash", betting.START_CASH)
    acct = eq[-1] if eq else {"ts": None, "cash": led["cash"], "positions_value": 0.0,
                              "account_value": led["cash"], "open_bets": 0}
    day_ago = (now - timedelta(days=1)).isoformat()
    prev = next((e for e in reversed(eq) if e["ts"] <= day_ago), eq[0] if eq else acct)
    at_cost = round(sum(b["cost"] for b in led["open"]), 2)
    closed = led["closed"]
    st = betting.stats(led)
    status = (latest.get("summary") or {}).get("strategies", {})
    strategies = [{"key": k, "name": n, "about": d, **st[k], **{"data": status.get(k, {})},
                   "min_edge": betting.RULES["min_edge"][k], "trust": betting.RULES["trust_model"][k],
                   "cap_pct": betting.RULES["strategy_cap_pct"][k]} for k, (n, d) in STRATEGY_INFO.items()]
    wins = [b["pnl"] for b in closed if b["pnl"] > 0]
    losses = [b["pnl"] for b in closed if b["pnl"] < 0]
    return {
        "account": {**acct, "start_cash": start, "at_cost": at_cost,
                    "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
                    "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
                    "can_bet": round(acct["account_value"] * (1 - betting.RULES["cash_reserve_pct"]), 2),
                    "all_time_pnl": round(acct["account_value"] - start, 2),
                    "all_time_pct": round((acct["account_value"] / start - 1) * 100, 2),
                    "day_pnl": round(acct["account_value"] - prev["account_value"], 2),
                    "realised_pnl": round(sum(b["pnl"] for b in closed), 2),
                    "unrealised_pnl": round(acct["positions_value"] - at_cost, 2),
                    "placed": len(led["open"]) + len(closed),
                    "won": sum(b["how"] == "won" for b in closed), "lost": sum(b["how"] == "lost" for b in closed),
                    "sold": sum(b["how"] == "sold" for b in closed)},
        "equity": [[e["ts"], e["account_value"]] for e in eq[-3000:]],
        "open": sorted(led["open"], key=lambda b: b["opened"], reverse=True),
        "closed": list(reversed(closed[-50:])),
        "strategies": strategies,
        "watching": latest.get("top_edges", [])[:12],
        "last": latest.get("summary"),
        "rules": {k: v for k, v in betting.RULES.items() if not isinstance(v, dict)},
        "milestones": milestones(now, led, st),
    }


def state(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    led = paper.load(os.path.join(DATA, "ledger.json"))
    equity = _jsonl(os.path.join(DATA, "equity.jsonl"))
    scans = _jsonl(os.path.join(DATA, "scans.jsonl"))
    latest = _json(os.path.join(DATA, "network_latest.json"), {})

    start = led.get("start_cash", paper.START_CASH)
    h = paper.held(led)
    acct = equity[-1] if equity else {"ts": None, "cash": led["cash"], "held": h, "positions_value": 0.0,
                                      "account_value": led["cash"] + h, "settlement_value": led["cash"] + h,
                                      "open_bets": 0}
    acct = {"held": 0.0, **acct}
    day_ago = (now - timedelta(days=1)).isoformat()
    prev = next((e for e in reversed(equity) if e["ts"] <= day_ago), equity[0] if equity else acct)
    realised = round(sum(b.get("pnl", 0) for b in led["closed"]), 2)
    open_cost = round(sum(b["cost"] for b in led["open"]), 2)

    fills = []
    for b in led["open"] + led["closed"]:
        for l in b["legs"]:
            fills.append({"ts": b["opened"], "kind": "fill", "ticker": l["ticker"], "side": l["side"],
                          "contracts": l["contracts"], "price": l["avg_price"], "fee": l["fee"],
                          "event": b["event"]})
        if b.get("closed"):
            fills.append({"ts": b["closed"], "kind": "settled", "event": b["event"],
                          "received": b["received"], "pnl": b["pnl"]})
    for f in led.get("maker_log", []):
        fills.append({"ts": f["ts"], "kind": "maker_fill", "ticker": f["ticker"], "side": f["side"],
                      "contracts": f["filled"], "size": f["size"], "price": f["price"]})
    for p in led.get("maker_closed", []):
        if p.get("outcome") != "filled":
            fills.append({"ts": p["ended"], "kind": "maker_end", "event": p["event"], "title": p["title"],
                          "outcome": p["outcome"], "pnl": p.get("pnl"), "rewards": p.get("rewards", 0)})
    fills.sort(key=lambda x: x["ts"], reverse=True)

    last_scan = scans[-1] if scans else None
    today = now.date().isoformat()
    scans_today = [s for s in scans if s["ts"][:10] == today]
    age_min = None
    if last_scan:
        age_min = round((now - datetime.fromisoformat(last_scan["ts"])).total_seconds() / 60, 1)

    return {
        "now": now.isoformat(timespec="seconds"),
        "account": {**acct, "start_cash": start,
                    "all_time_pnl": round(acct["account_value"] - start, 2),
                    "all_time_pct": round((acct["account_value"] / start - 1) * 100, 2),
                    "day_pnl": round(acct["account_value"] - prev["account_value"], 2),
                    "realised_pnl": round(realised + sum(p.get("pnl") or 0 for p in led.get("maker_closed", [])), 2),
                    "open_cost": open_cost, "rewards_total": round(led.get("rewards_total", 0.0), 2),
                    "resting": len(led.get("resting", [])),
                    "unrealised_pnl": round(acct["positions_value"] - open_cost, 2),
                    "locked_profit": round(sum(b["payout"] * b["sets"] - b["cost"] for b in led["open"]), 2)},
        "equity": [[e["ts"], e["account_value"], e["settlement_value"]] for e in equity[-3000:]],
        "positions": led["open"],
        "resting": led.get("resting", []),
        "closed": list(reversed(led["closed"][-50:])),
        "activity": fills[:40],
        "scanner": {"last": last_scan, "age_min": age_min, "every_min": SCAN_EVERY_MIN,
                    "healthy": age_min is not None and age_min < SCAN_EVERY_MIN * 2 + 5,
                    "total": len(scans), "with_bets": sum(1 for s in scans if s["placed"]),
                    "today": len(scans_today), "today_with_bets": sum(1 for s in scans_today if s["placed"]),
                    "history": [[s["ts"], s["candidates"], s["placed"]] for s in scans[-96:]]},
        "near_misses": latest.get("near_misses", []),
        "rejected": latest.get("rejected", [])[:15],
        "rules": evaluate.RULES,
        "betting": betting_state(now),
        "review": review(),
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/state":
            self._send(200, json.dumps(state()).encode(), "application/json")
        elif path in ("/", "/index.html"):
            with open(PAGE, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
