"""
lessons.py — the dashboard's learning layer (owner's Phase 3 plan, 2026-10-29): short lessons that show up
when the thing they explain happens, and a plain "why Charles owns this" for each holding.

Everything here explains how Charles works or what a term means. Nothing says what anyone should buy:
that would be advice, and the mission is no advice. The texts are fixed (no AI), and the numbers they
quote come from the bot's own settings and docs/backtest.json, so they stay true when either changes.
due() reads the dashboard's state, so the preview (which borrows the live state) shows the same lessons.
Which ones a viewer has read stays in their browser: the dashboard has no login, so it writes nothing.
"""
from __future__ import annotations

from datetime import date

import allocator

# "event": shows up first when it happens. "evidence": a sentence with backtest numbers, left out when
# this server has no backtest file.
LESSONS = [
    {"id": "loss-limit", "event": True, "title": "The daily loss limit",
     "body": "If the account falls more than its daily limit in one day, Charles stops buying until the next "
             "trading day. Sales still go through. The limit is a brake, not a forecast: a big down day is usually "
             "just the market moving, and the plan itself stays the same."},
    {"id": "safety", "event": True, "title": "Why Charles moved to T-bills",
     "body": "SPY {ended} below its {trend}-day average, so Charles moved everything into T-bills. It moves back "
             "into stocks once SPY {ends} above the line again. In long falls like 2008's, SPY dropped below "
             "this line early and stayed there, which is what the switch watches for. Many short dips cross it too, "
             "so the switch sometimes sits out a rally.",
     "evidence": "In the {years}-year backtest it cut the plan's worst fall from {nosw_dd}% to {plan_dd}%. It wasn't "
                 "free: without it the plan made more a year ({nosw_cagr}% against {plan_cagr}%)."},
    {"id": "drawdown", "event": True, "title": "Down days are normal",
     "body": "Even a sound plan has bad days and bad months. Since 1980 the S&P 500 has dropped about 14% from its "
             "high at some point in an average year, yet it ended most of those years higher (J.P. Morgan's Guide "
             "to the Markets). One bad day says little about whether the rules work, so Charles doesn't change the "
             "plan after one.",
     "evidence": "In Charles's own {years}-year backtest, its worst fall from a high was {plan_dd}%."},
    {"id": "veto", "event": True, "title": "The AI news check",
     "body": "Before a new pick is bought, an AI reads its recent headlines and skips it only for serious company "
             "news, like a fraud probe, a failed drug trial or a sudden CEO exit. Ordinary price drops and analysts' "
             "opinions don't count. It never chooses stocks and never sells one Charles already holds. If the "
             "headlines or the AI aren't available, the pick goes ahead."},
    {"id": "repick", "event": True, "title": "The monthly re-pick",
     "body": "On the first trading day of each month, Charles ranks {universe} big US companies by their gain over "
             "the past year, leaving out the latest month. Only stocks above their own {trend}-day average count, "
             "and the top {picks} become the picks. A pick that drops out of the top {picks} is sold, and the new "
             "one is bought after the AI news check. Momentum tends to play out over months, so re-picking more "
             "often would mostly add trading costs."},
    {"id": "paper", "title": "Paper money vs real money",
     "body": "Charles trades practice money in Alpaca's paper account: real prices, simulated orders. It's a safe "
             "way to watch how the plan behaves, but paper results aren't a promise. Real orders can fill at "
             "slightly worse prices, and losing real money feels very different from watching a number fall."},
    {"id": "aggressive", "title": "{x}x funds, and why only in calm markets",
     "body": "{agg} aims to move {x_word} as much as the S&P 500 each day: up {x}% on a day the index rises 1%, "
             "down {x}% when it falls 1%. Because it resets every day, choppy markets wear it down. If the index "
             "rises 10% one day and falls 10% the next, it's down 1%, but {agg} is down about {decay}%. In calm, "
             "rising markets the daily multiplying works in its favor, so Charles holds it only while SPY is above "
             "its {trend}-day average and calm, and goes back to plain SPY on any choppy day.",
     "evidence": "In the {years}-year backtest it raised the plan's yearly gain from {steady_cagr}% to {plan_cagr}% "
                 "and deepened its worst fall from {steady_dd}% to {plan_dd}%."},
    {"id": "index-fund", "title": "What's an index fund?",
     "body": "An index fund owns every company in a list, in proportion to its size. SPY follows the S&P 500: "
             "about 500 of the biggest US companies. Owning it means owning a small slice of all of them, so one "
             "company's bad year barely matters. Over long stretches most professionally run US stock funds have "
             "trailed the S&P 500 (S&P's SPIVA reports), which is why it's the core of Charles's plan. It still "
             "falls when the whole market falls: from 2007 to 2009 it lost more than half its value before recovering.",
     "evidence": "Over the {years}-year backtest, holding SPY alone made {spy_cagr}% a year."},
    {"id": "momentum", "title": "Why buy what already went up?",
     "body": "Momentum is the tendency of the stocks that rose most over the past year to keep doing a little "
             "better than average for the next few months. Researchers have found it in many countries and over "
             "many decades, which makes it one of the best-documented patterns in markets. It also fails, sometimes "
             "sharply: when a falling market suddenly turns up, last year's losers can rebound far faster, as in "
             "2009. That's why each pick is only {pick_pct}% of the account and the picks are re-checked every "
             "month. The latest month is left out because very short-term moves tend to partly reverse."},
    {"id": "costs", "title": "What trading costs",
     "body": "Alpaca charges no commission on US stocks and funds, only tiny regulatory fees on sales, but trading "
             "still costs a little. Each order pays the spread, the gap between the buying and the selling price, "
             "and the price can move between the decision and the fill (slippage). Funds also take a yearly fee "
             "out of their price: under 0.1% for SPY, close to 1% for 2x funds like SSO. So Charles trades rarely: "
             "in a normal month only on the re-pick day, plus any holding that drifts more than {drift} points "
             "from its target.",
     "evidence": "The backtest charges {slip} on every trade for these costs."},
    {"id": "tbills", "title": "What are T-bills?",
     "body": "Treasury bills are short loans to the US government, repaid within a year. A T-bill fund such as "
             "{safe} holds them and trades like a stock. Its price barely moves, and it pays interest close to the "
             "Federal Reserve's short-term rate. Charles uses it as a waiting room: for everything in safety mode, "
             "and for any pick slot no stock qualifies for."},
    {"id": "trend-line", "title": "The {trend}-day average",
     "body": "The {trend}-day average is a stock's or fund's average closing price over its last {trend} trading "
             "days, about {months} months. A price above it has been rising on balance; below it, falling. In the "
             "long falls of 2008 and 2022, SPY stayed below it for most of the way down. In Charles's plan, {uses}. "
             "It's a blunt rule: when the market dips below the line and quickly bounces back, it gives up some gains."},
    {"id": "backtest", "title": "What a backtest can and can't tell you",
     "body": "A backtest runs the rules on past prices to see how they would have done. It's the best evidence "
             "available before anything is risked, but it has blind spots. Charles's list of {universe} companies "
             "is today's, so the test leaves out companies that were big back then and later collapsed (survivor "
             "bias). And history won't repeat exactly.",
     "evidence": "That's why the plan is also tested with sector funds, which have no such bias: {sect_cagr}% a year "
                 "instead of {plan_cagr}%, with SPY alone at {spy_cagr}%."},
]
BY_ID = {x["id"]: x for x in LESSONS}
X_WORD = {2: "twice", 3: "three times"}
RUNS = {"plan": "charles", "nosw": "charles_no_switch", "steady": "charles_steady", "sect": "charles_sectors", "spy": "spy"}


def _usd(v: float) -> str:
    return f"${v:,.0f}" if float(v).is_integer() else f"${v:,.2f}"


def _whole(x: float) -> int:
    return int(abs(x) + 0.5)                     # 56.5 → 57 (round() would give 56)


def _day(iso: str) -> str:
    d = date.fromisoformat(iso[:10])
    return f"{d:%b} {d.day}"


def backtest_numbers(bt: dict | None) -> dict:
    """The backtest numbers the lessons quote, from docs/backtest.json: {"years", "plan_cagr", "plan_dd", …}
    (whole percents), or {} without the file."""
    try:
        runs = {k: v["stats"] for k, v in bt["runs"].items() if v.get("stats")}
        out = {"years": int((date.fromisoformat(bt["end"]) - date.fromisoformat(bt["start"])).days / 365.25)}
    except (TypeError, KeyError, ValueError, AttributeError):
        return {}
    for short, run in RUNS.items():
        st = runs.get(run) or {}
        if st.get("cagr_pct") is not None and st.get("max_drawdown_pct") is not None:
            out[f"{short}_cagr"], out[f"{short}_dd"] = _whole(st["cagr_pct"]), _whole(st["max_drawdown_pct"])
    if bt.get("slippage_bps") is not None:
        out["slip"] = f"{bt['slippage_bps'] / 100:g}%"
    return out


def _fill(state: dict, numbers: dict) -> dict:
    s = (state.get("plan") or {}).get("settings") or {}
    agg = s.get("AGG_CORE") or "SSO"
    x = 3 if agg in ("UPRO", "SPXL") else 2
    trend = s.get("TREND_DAYS", 200)
    daily = s.get("SWITCH_CHECK") == "daily"
    ends = "closes a day" if daily else "ends a month"
    uses = ["picks must be above their own line"] + (["aggressive mode needs SPY above it"] if s.get("AGGRESSIVE", True) else []) \
        + ([f"if SPY {ends} below it, everything moves to T-bills"] if s.get("SAFETY_SWITCH", True) else [])
    return {**numbers, "trend": trend, "ends": ends, "ended": "closed a day" if daily else "ended a month",
            "uses": uses[0] if len(uses) == 1 else ", ".join(uses[:-1]) + ", and " + uses[-1], "months": round(trend / 21), "picks": s.get("PICKS", 4),
            "pick_pct": round(s.get("PICK_PCT", 0.05) * 100), "drift": round(s.get("DRIFT_PCT", 0.03) * 100),
            "agg": agg, "x": x, "x_word": X_WORD[x], "safe": s.get("SAFE") or "SGOV",
            "universe": len(allocator.UNIVERSE),
            "decay": round((1 - (1 + x / 10) * (1 - x / 10)) * 100)}    # up 10%, down 10%, at x times


def _text(lesson: dict, fill: dict) -> tuple[str, str]:
    body = lesson["body"].format(**fill)
    if lesson.get("evidence"):
        try:
            body += " " + lesson["evidence"].format(**fill)
        except KeyError:
            pass                                 # no backtest numbers here: the lesson stands without them
    return lesson["title"].format(**fill), body


def due(state: dict, numbers: dict | None = None) -> list[dict]:
    """Every lesson, the ones that fit what Charles is doing right now first (events first, each with `why`,
    a line saying why now), then the rest (why None): [{id, title, body, why, event}].
    `state` is the dashboard's /api/state; `numbers` is backtest_numbers()."""
    plan = state.get("plan") or {}
    s = plan.get("settings") or {}
    acct = state.get("account") or {}
    today_ = state.get("today") or {}
    mode = plan.get("mode")
    agg = s.get("AGG_CORE") or "SSO"
    roles = {}
    for p in state.get("positions") or []:
        roles.setdefault(p.get("role"), []).append(p.get("symbol"))
    today = (state.get("generated_at") or "")[:10]

    def recent(iso, days=3):
        try:
            return 0 <= (date.fromisoformat(today) - date.fromisoformat((iso or "")[:10])).days <= days
        except ValueError:
            return False
    out = []
    if today_.get("halted"):
        limit = today_.get("limit")
        out.append(("loss-limit", f"Buys are paused today: the account passed its {_usd(limit)} limit." if limit
                    else "Buys are paused today."))
    if mode == "safety":
        out.append(("safety", "Charles is in safety mode."))
    total, day = acct.get("total_value") or 0, acct.get("day_change") or 0
    if total and total - day > 0 and day / (total - day) <= -0.02:
        out.append(("drawdown", f"The account is down {abs(day / (total - day)) * 100:.1f}% today."))
    if plan.get("vetoes"):
        out.append(("veto", f"It skipped {plan['vetoes'][0].get('symbol', 'a pick')} this month."))
    if recent(plan.get("last_rebalance")):
        out.append(("repick", f"Charles re-picked on {_day(plan['last_rebalance'])}."))
    out.append(("paper", "Stocks trade real money here (live trading is on); crypto and Kalshi stay practice money."
                if (state.get("trading") or {}).get("mode") == "live" else "Every bot here runs on practice money."))
    if mode == "aggressive" or agg in roles.get("core", []):
        out.append(("aggressive", f"The core is {agg} right now."))
    if roles.get("core"):
        out.append(("index-fund", "The core of the plan follows the S&P 500."))
    if roles.get("pick"):
        out.append(("momentum", f"This month's picks: {', '.join(roles['pick'])}."))
    trades = state.get("trades") or []
    if trades and recent(trades[0].get("timestamp")):
        n = sum(1 for t in trades if (t.get("timestamp") or "")[:10] == trades[0]["timestamp"][:10])
        out.append(("costs", f"Charles placed {n} order{'' if n == 1 else 's'} on {_day(trades[0]['timestamp'])}."))
    if roles.get("safety"):
        out.append(("tbills", f"Charles holds {', '.join(roles['safety'])}."))
    gap = (plan.get("trend") or {}).get("gap_pct")
    out.append(("trend-line", f"SPY is {abs(gap):.1f}% {'above' if gap >= 0 else 'below'} its line." if gap is not None
                else "It guards the whole plan."))
    out.append(("backtest", "The chart on the Plan tab is a backtest."))
    out += [(x["id"], None) for x in LESSONS if x["id"] not in {i for i, _ in out}]

    fill = _fill(state, numbers or {})
    rows = []
    for i, why in out:
        title, body = _text(BY_ID[i], fill)
        rows.append({"id": i, "title": title, "body": body, "why": why, "event": bool(BY_ID[i].get("event"))})
    return rows


def holding_why(h: dict, plan: dict) -> str | None:
    """Why Charles owns this holding, from the plan's own numbers, e.g. "Momentum pick #2 of 99: up 384%
    over the past year (not counting the latest month) and above its 200-day average, as of Oct 28. …"."""
    s = plan.get("settings") or {}
    trend, sym = s.get("TREND_DAYS", 200), h.get("symbol")
    nxt = f" on {_day(plan['next_rebalance'])}" if plan.get("next_rebalance") else " at the next monthly re-pick"
    gap = (plan.get("trend") or {}).get("gap_pct")
    line = f"SPY is {abs(gap):.1f}% {'above' if gap >= 0 else 'below'} its {trend}-day average" if gap is not None else None
    role = h.get("role")
    if role == "core" and sym == s.get("AGG_CORE"):
        return (f"Aggressive mode's core: a fund that moves about {X_WORD[3 if sym in ('UPRO', 'SPXL') else 2]} as much "
                f"as the S&P 500 each day. Charles holds it while the market is calm and SPY is above its {trend}-day "
                f"average{f' ({gap:.1f}% above now)' if gap is not None and gap >= 0 else ''}, and goes back to "
                f"{s.get('CORE') or 'SPY'} on the first choppy day.")
    if role == "core":
        return (f"The core of the plan: the whole S&P 500 in one fund. Charles keeps it near its "
                f"{round(s.get('CORE_PCT', 0.8) * 100)}% share and checks the safety switch at each month's end"
                f"{f'; {line} now' if line else ''}.")
    if role == "pick":
        r = next((x for x in plan.get("ranked") or [] if x.get("symbol") == sym), None)
        asof = f", as of {_day(plan['as_of'])}" if plan.get("as_of") else ""
        if r and r.get("momentum") is not None:
            above = r.get("above_trend")
            return (f"Momentum pick #{r['rank']} of {plan.get('universe_n') or len(allocator.UNIVERSE)}: "
                    f"{'up' if r['momentum'] >= 0 else 'down'} {abs(r['momentum']):,.0f}% over the past year (not "
                    f"counting the latest month) and {'above' if above else 'now below'} its {trend}-day "
                    f"average{asof}. Charles checks it again{nxt}: it stays if it's still in the top "
                    f"{s.get('PICKS', 4)}{'' if above else ' and back above that line'}.")
        return f"One of this month's {s.get('PICKS', 4)} momentum picks. Charles checks it again{nxt}."
    if role == "safety":
        return ("T-bills, the plan's waiting room: Charles parks money here in safety mode, and for any pick slot "
                "no stock qualifies for.")
    if role == "leaving":
        return "Bought by Charles's old trading rules. It isn't part of the plan, so Charles sells it at its next check."
    return None
