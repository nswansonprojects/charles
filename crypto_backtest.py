"""
crypto_backtest.py — the crypto demo's rules on history (Coinbase daily closes, 2016 on), with fees.

Runs the same crypto_plan.decide() / orders() / apply() the paper book uses, checked every
CHECK_DAYS days at that day's close, and compares it with simply holding bitcoin.
Writes docs/crypto_backtest.json (the dashboard's Crypto page draws it).
Run with: ops/heavy-run python crypto_backtest.py
"""
import bisect
import json
import os
from datetime import datetime, timezone

import crypto_market
import crypto_plan

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "docs", "crypto_backtest.json")
START_CASH = 1000.0


def simulate(days: list, closes: dict, settings: dict | None = None, hold: str | None = None) -> list:
    """[[day, value]] for the plan (or buy-and-hold `hold` from the first day). The plan needs TREND_DAYS of history
    before its first decision, so `days` should start after that. A coin with no close yet on a day (listed later,
    your own plan) isn't there: it waits in cash until its prices start; a gap of a day uses its last close."""
    s = {**crypto_plan.DEFAULTS, **(settings or {})}
    qty, cash, out = {}, START_CASH, []
    for n, day in enumerate(days):
        at = {}
        for p in closes:
            i = bisect.bisect_right(closes[p]["days"], day) - 1      # its last close on or before the day
            if i >= 0:
                at[p] = i
        prices = {p: closes[p]["close"][i] for p, i in at.items()}
        if hold:
            if n == 0:
                qty, cash = crypto_plan.apply({}, cash, prices, [{"product": hold, "side": "BUY", "value": cash,
                                                                   "qty": cash / prices[hold]}], s["FEE"])
        elif n % s["CHECK_DAYS"] == 0:
            hist = {p: closes[p]["close"][:i + 1] for p, i in at.items()}
            d = crypto_plan.decide(hist, s)
            targets = {**{p: 0.0 for p, q in qty.items() if q}, **d["targets"]}
            targets = {p: t for p, t in targets.items() if p in prices}
            qty, cash = crypto_plan.apply(qty, cash, prices, crypto_plan.orders(qty, prices, cash, targets, s), s["FEE"])
        out.append([day, round(crypto_plan.weights_now(qty, prices, cash)[0], 2)])
    return out


def stats(curve: list) -> dict:
    vals = [v for _, v in curve]
    peak, worst = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    years = (datetime.fromisoformat(curve[-1][0]) - datetime.fromisoformat(curve[0][0])).days / 365.25
    yearly, prev = {}, START_CASH
    for y in sorted({d[:4] for d, _ in curve}):
        end = [v for d, v in curve if d[:4] == y][-1]
        yearly[y] = round((end / prev - 1) * 100, 1)
        prev = end
    return {"final": vals[-1], "cagr_pct": round(((vals[-1] / START_CASH) ** (1 / years) - 1) * 100, 1),
            "max_drawdown_pct": round(worst * 100, 1), "best_year": max(yearly.values()),
            "worst_year": min(yearly.values()), "yearly": yearly}


def history(products: list, fetch=None) -> dict:
    """{product: {"days", "close"}} from Coinbase's daily closes (cached)."""
    fetch = fetch or crypto_market.daily_closes
    out = {}
    for p in products:
        rows = fetch(p)
        if rows:
            out[p] = {"days": [d for d, _ in rows], "close": [c for _, c in rows]}
    return out


def window(closes: dict, settings: dict) -> list:
    """The days a test runs on: bitcoin's days from when ether's trend line first exists (as the Plan tab's chart)."""
    btc = closes["BTC-USD"]["days"]
    eth = closes.get("ETH-USD", closes["BTC-USD"])["days"]
    first = eth[min(len(eth) - 1, settings["TREND_DAYS"])]
    return [d for d in btc if d >= first]


def compare(changes: dict, fetch=None, path: str | None = None) -> dict:
    """The Plan tab's "Test it on past prices" for crypto: the plan now and with `changes`, on the same days.
    {"history", "now": {"plan": stats}, "with_change": {"plan": stats}}. Coins listed later wait in cash until then."""
    import crypto_settings
    now_s = {**crypto_plan.DEFAULTS, **crypto_settings.load(path or crypto_settings.PATH)}
    new_s = {**now_s, **crypto_settings.to_plan({**crypto_settings.flat(now_s), **changes})}
    closes = history(list(dict.fromkeys(["BTC-USD", "ETH-USD"] + crypto_plan.products(now_s) + crypto_plan.products(new_s))), fetch)
    days = window(closes, {"TREND_DAYS": max(now_s["TREND_DAYS"], new_s["TREND_DAYS"])})
    pick = lambda s: {p: closes[p] for p in closes if p in crypto_plan.products(s)}
    keep = ("cagr_pct", "max_drawdown_pct", "final")
    run = lambda s: {k: stats(simulate(days, pick(s), s))[k] for k in keep}
    return {"history": f"{days[0][:7]} to {days[-1][:7]}", "now": {"plan": run(now_s)}, "with_change": {"plan": run(new_s)}}


def main():
    s = crypto_plan.DEFAULTS
    closes = {}
    for p in s["WEIGHTS"]:
        rows = crypto_market.daily_closes(p)
        closes[p] = {"days": [d for d, _ in rows], "close": [c for _, c in rows]}
    common = sorted(set.intersection(*(set(c["days"]) for c in closes.values())))
    first = closes["ETH-USD"]["days"][s["TREND_DAYS"]]      # ether's 200-day average exists from here
    days = [d for d in common if d >= first]
    runs = {"plan": ("Charles crypto: 60% bitcoin / 40% ether, each only above its 200-day average, checked weekly", None),
            "btc": ("Just hold bitcoin", "BTC-USD")}
    out = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "start": days[0], "end": days[-1],
           "fee_pct": s["FEE"] * 100, "start_cash": START_CASH, "runs": {}}
    for name, (about, hold) in runs.items():
        curve = simulate(days, closes, hold=hold)
        since = [p for p in curve if p[0] >= "2021-06-01"]
        out["runs"][name] = {"about": about, "stats": stats(curve),
                             "since_2021": stats([[d, v * START_CASH / since[0][1]] for d, v in since]),
                             "curve": curve[::7] + ([curve[-1]] if (len(curve) - 1) % 7 else [])}
    with open(OUT, "w") as f:
        json.dump(out, f)
    for name, r in out["runs"].items():
        st, h2 = r["stats"], r["since_2021"]
        print(f"{name:5s} {st['cagr_pct']:6.1f}%/yr worst {st['max_drawdown_pct']}% final ${st['final']:,.0f} | "
              f"since Jun 2021 {h2['cagr_pct']}%/yr worst {h2['max_drawdown_pct']}%")


if __name__ == "__main__":
    main()
