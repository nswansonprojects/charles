"""One scan: map the network of related Kalshi markets, evaluate every bet in it,
then place the chosen ones on the PAPER ledger; then run the betting book (betting.py),
which takes directional paper bets from the strategies. Never sends real orders.

    python3 run.py            # scan + paper bets + settle
    python3 run.py --report   # print the ledger and the last scan
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone

import betting
import evaluate
import kalshi_api
import maker
import network
import paper

DATA = paper.DATA
LATEST = os.path.join(DATA, "network_latest.json")
SCANS = os.path.join(DATA, "scans.jsonl")
EQUITY = os.path.join(DATA, "equity.jsonl")
NEAR_MISSES = 15  # closest relations kept for the dashboard, profitable or not
MAX_BOOKS = 80  # order books fetched per scan (best-looking candidates first)

log = logging.getLogger("kalshi-net")


def _brief(b: dict) -> dict:
    keep = ("type", "event", "category", "title", "why", "reason", "sets", "cost", "profit",
            "return_pct", "annual_return_pct", "days", "confidence", "expected_profit",
            "worst_case", "quick_edge")
    out = {k: b[k] for k in keep if k in b}
    out["legs"] = [f"{l['side'].upper()} {l['ticker']}" for l in b["legs"]]
    return out


def _interesting(r: dict, by_ticker: dict) -> bool:
    """Ladders with a leg at 1-3c or 97-99c are 'near' only on paper: nothing to win."""
    if r["type"] == "me_all_no":
        return True
    return all(0.03 < network.top_ask(by_ticker[l["ticker"]], l["side"]) < 0.97 for l in r["legs"])


def _near(r: dict, by_ticker: dict, now: datetime) -> dict:
    """A relation as the dashboard shows it: legs with their asks, and the edge per set."""
    return {"type": r["type"], "event": r["event"], "category": r["category"], "title": r["title"],
            "why": r["why"], "payout": r["payout"], "edge": r["quick_edge"],
            "days": round(evaluate.days_left(r["close_time"], now), 1),
            "confidence": network.CONFIDENCE[r["type"]],
            "legs": [{"ticker": l["ticker"], "side": l["side"], "title": l["title"], "sub": l.get("sub", ""),
                      "ask": network.top_ask(by_ticker[l["ticker"]], l["side"])} for l in r["legs"][:6]],
            "leg_count": len(r["legs"])}


def run_maker(led: dict, by_ticker: dict, scored: list, now: datetime) -> dict:
    """Update resting plans from real trades, accrue rewards, finish expired plans,
    then rest new plans on the best near-miss sets. Returns counts for the scan log."""
    ev = {"filled_sets": 0, "fills": 0, "expired": 0, "new": 0, "rewards": 0.0}
    try:
        programs = {p["market_ticker"]: p for p in kalshi_api.incentive_programs()
                    if p.get("start_date", "") <= now.isoformat() <= p.get("end_date", "")}
    except Exception as e:
        log.warning(f"incentive programs: {e}")
        programs = {}
    ev["programs"] = len(programs)

    for plan in list(led["resting"]):
        since = int(datetime.fromisoformat(plan["last_check"]).timestamp())
        books = {}
        for leg in plan["legs"]:
            try:
                if leg["filled"] < leg["size"]:
                    if maker.apply_trades(leg, kalshi_api.trades(leg["ticker"], since)):
                        ev["fills"] += 1
                        led.setdefault("maker_log", []).append(
                            {"ts": now.isoformat(timespec="seconds"), "ticker": leg["ticker"], "side": leg["side"],
                             "filled": leg["filled"], "size": leg["size"], "price": leg["price"]})
                if leg["ticker"] in programs:
                    books[leg["ticker"]] = kalshi_api.orderbook(leg["ticker"])
            except Exception as e:
                log.warning(f"maker {leg['ticker']}: {e}")
        got = maker.accrue_rewards(plan, books, programs, now)
        led["cash"] = round(led["cash"] + got, 4)
        led["rewards_total"] = round(led.get("rewards_total", 0.0) + got, 4)
        ev["rewards"] += got
        plan["last_check"] = now.isoformat(timespec="seconds")

        done = None
        if maker.complete(plan):
            done = "filled"
        elif now >= maker.ends_at(plan):
            res = maker.resolve_expired(plan, by_ticker)
            done = res["action"]
            if done == "complete":
                maker.finish_with_buys(plan, res["buys"])
        if not done:
            continue
        led["resting"].remove(plan)
        led["cash"] = round(led["cash"] + plan["held"], 2)
        plan["ended"], plan["outcome"] = now.isoformat(timespec="seconds"), done
        if done in ("filled", "complete"):
            bet = maker.to_bet(plan, now)
            led["cash"] = round(led["cash"] - bet["cost"], 2)
            led["open"].append(bet)
            ev["filled_sets"] += 1
            plan["pnl"] = None  # settles with the bet
            log.info(f"MAKER SET LOCKED ({done}) {plan['event']}: cost ${bet['cost']} pays ${plan['payout'] * plan['sets']}")
        elif done == "unwind":
            res_cost = sum(l["filled"] * l["price"] + l["fee"] for l in plan["legs"])
            led["cash"] = round(led["cash"] - res_cost + res["proceeds"], 2)
            plan["pnl"] = res["pnl"]
            log.info(f"MAKER UNWIND {plan['event']}: pnl ${res['pnl']:+.2f}")
        else:
            plan["pnl"] = 0.0
        ev["expired"] += done != "filled"
        led["maker_closed"].append(plan)

    room = min(maker.MAKER["max_plans"] - len(led["resting"]), maker.MAKER["max_new_per_scan"])
    if room > 0:
        used = {l["ticker"] for b in led["open"] for l in b["legs"]} | \
               {l["ticker"] for p in led["resting"] for l in p["legs"]}
        equity = paper.equity(led)
        viable = []
        for r in scored:
            if len(viable) >= 300:
                break
            if r["quick_edge"] < -0.10:
                break  # sorted: the rest are further away still
            if evaluate.days_left(r["close_time"], now) > maker.MAKER["max_days"]:
                continue
            if any(l["ticker"] in used for l in r["legs"]):
                continue
            p = maker.plan_for(r, by_ticker, equity, now)
            if p:
                p["rewarded"] = sum(l["ticker"] in programs for l in p["legs"])
                viable.append(p)
        viable.sort(key=lambda p: (p["rewarded"] > 0, p["edge_per_set"] * p["sets"]), reverse=True)
        spendable = led["cash"] - equity * evaluate.RULES["cash_reserve_pct"]
        for p in viable:
            if room <= 0:
                break
            tickers = {l["ticker"] for l in p["legs"]}
            if tickers & used or p["held"] > spendable:
                continue
            led["cash"] = round(led["cash"] - p["held"], 2)
            spendable -= p["held"]
            used |= tickers
            p["id"] = f"{now:%Y%m%d%H%M%S}-{p['event']}"
            led["resting"].append(p)
            room -= 1
            ev["new"] += 1
            log.info(f"MAKER REST {p['type']} {p['event']}: {p['sets']} sets, edge {p['edge_per_set']}/set, "
                     f"holds ${p['held']}, rewarded legs {p['rewarded']}")
    ev["rewards"] = round(ev["rewards"], 4)
    return ev


def scan(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    led = paper.load()

    settled = paper.settle(led, kalshi_api.market)
    for b in settled:
        log.info(f"settled {b['event']} {b['type']}: pnl ${b['pnl']:+.2f}")

    events = kalshi_api.open_events()
    by_ticker = {m["ticker"]: m for e in events for m in e.get("markets", [])}
    rels = network.relations(events)

    scored = []
    for r in rels:
        q = network.quick_edge(r, by_ticker)
        if q is not None and evaluate.days_left(r["close_time"], now) <= evaluate.RULES["max_days"]:
            scored.append({**r, "quick_edge": round(q, 4)})
    scored.sort(key=lambda r: r["quick_edge"], reverse=True)
    near = [_near(r, by_ticker, now) for r in scored if _interesting(r, by_ticker)][:NEAR_MISSES]
    cands = [r for r in scored if r["quick_edge"] >= evaluate.RULES["min_edge_per_set"]][:MAX_BOOKS]

    books = {}
    for t in {l["ticker"] for r in cands for l in r["legs"]}:
        try:
            books[t] = kalshi_api.orderbook(t)
        except Exception as e:  # one bad book shouldn't stop the scan
            log.warning(f"orderbook {t}: {e}")
    cands = [r for r in cands if all(l["ticker"] in books for l in r["legs"])]

    held_tickers = {l["ticker"] for b in led["open"] for l in b["legs"]}
    chosen, rejected = evaluate.choose(cands, books, paper.equity(led), led["cash"],
                                       paper.held_by_event(led), held_tickers, now)
    placed = [paper.place(led, b, now) for b in chosen]
    maker_events = run_maker(led, by_ticker, scored, now)
    account = paper.mark(led, by_ticker, now)
    paper.save(led)
    with open(EQUITY, "a") as f:
        f.write(json.dumps(account) + "\n")

    summary = {
        "ts": now.isoformat(timespec="seconds"),
        "events": len(events), "markets": len(by_ticker), "relations": len(rels),
        "by_type": {t: sum(r["type"] == t for r in rels) for t in network.CONFIDENCE},
        "candidates": len(cands), "placed": len(placed), "settled": len(settled),
        "maker": maker_events,
        "best_quick_edge": cands[0]["quick_edge"] if cands else None,
        "equity": paper.equity(led), "cash": led["cash"], "open_bets": len(led["open"]),
    }
    with open(SCANS, "a") as f:
        f.write(json.dumps(summary) + "\n")
    rejected.sort(key=lambda b: b.get("expected_profit", b.get("quick_edge", 0)), reverse=True)
    with open(LATEST + ".tmp", "w") as f:
        json.dump({"summary": summary, "near_misses": near, "chosen": [_brief(b) for b in chosen],
                   "rejected": [_brief(b) for b in rejected[:40]]}, f, indent=1)
    os.replace(LATEST + ".tmp", LATEST)
    log.info(f"scan: {summary}")
    for b in chosen:
        log.info(f"PAPER BET {b['type']} {b['event']}: cost ${b['cost']} -> profit ${b['profit']} "
                 f"({b['annual_return_pct']}%/yr, conf {b['confidence']}) {b['why']}")
    try:
        betting.run(events, by_ticker, now)
    except Exception:  # the betting book must never stop the scanner
        log.exception("betting book")
    return summary


def report():
    led = paper.load()
    pnl = sum(b["pnl"] for b in led["closed"])
    print(f"Paper book: equity ${paper.equity(led):,.2f}  cash ${led['cash']:,.2f}  "
          f"open {len(led['open'])}  closed {len(led['closed'])}  realised P&L ${pnl:+.2f}")
    for b in led["open"]:
        print(f"  OPEN  {b['opened'][:16]}  {b['type']:13} {b['event']:30} cost ${b['cost']:>7.2f}"
              f"  locked profit ${b['profit']:>6.2f}  conf {b['confidence']}  closes {b['close_time'][:10]}")
    for b in led["closed"][-10:]:
        print(f"  DONE  {b['closed'][:16]}  {b['type']:13} {b['event']:30} pnl ${b['pnl']:+.2f}")
    try:
        last = json.load(open(LATEST))
    except FileNotFoundError:
        return
    s = last["summary"]
    print(f"\nLast scan {s['ts']}: {s['markets']:,} markets, {s['relations']:,} linked bets checked, "
          f"{s['candidates']} looked profitable at top of book, {s['placed']} placed")
    for b in last["chosen"]:
        print(f"  BET   {b['type']:13} {' + '.join(b['legs'])}\n        cost ${b['cost']}  profit ${b['profit']}"
              f"  {b['annual_return_pct']}%/yr  conf {b['confidence']}  — {b['why']}")
    for b in last["rejected"][:10]:
        print(f"  SKIP  {b['type']:13} {b['event']:30} {b['reason']}")
    try:
        scans = [json.loads(l) for l in open(SCANS)]
        hits = sum(1 for x in scans if x["placed"])
        print(f"\n{len(scans)} scans so far; {hits} found at least one bet worth placing.")
    except FileNotFoundError:
        pass
    print()
    betting.report()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    report() if "--report" in sys.argv else scan()
