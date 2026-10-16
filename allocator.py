"""
allocator.py — what Charles should own. Pure functions only (no network, no files), so the live
rebalancer (rebalancer.py) and the backtest (backtest_core.py) run exactly the same rules.

The plan (owner-approved redesign, 2026-10-16; docs/redesign-plan.md):
  1. Core: CORE_PCT of the account in SPY.
  2. Momentum picks: the PICKS strongest stocks of UNIVERSE, PICK_PCT each, ranked by their
     12-month return skipping the latest month. A pick must have positive momentum and close above
     its own 200-day average; a slot nobody qualifies for goes to safety. Re-picked monthly.
  3. Safety switch: when SPY closes a month below its 200-day average, everything goes to SAFE
     (T-bills) until a month closes back above it.
  4. Aggressive mode (owner 2026-10-16: "a toggle the bot decides"): while SPY is above its average
     AND calm (20-day swings below AGG_VOL_MAX a year), the core is held in a 2x S&P fund (AGG_CORE)
     instead of SPY. It starts only on a monthly check, and ends on ANY day the swings pass
     AGG_VOL_EXIT or SPY closes below its average: leverage is kept to calm, rising markets.
"""
from __future__ import annotations

# The 100 liquid US stocks the old screener used (screener.py, retired with the redesign).
# Survivor bias: this is today's list, so backtests of the momentum part look better than live.
UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "ORCL", "CRM", "ADBE",
    "AMD", "INTC", "QCOM", "MU", "AMAT", "LRCX", "KLAC", "ASML", "MRVL", "ARM",
    "AVGO", "TXN", "ON", "WOLF", "CRUS",
    "PLTR", "SNOW", "NET", "DDOG", "ZS", "CRWD", "S", "GTLB", "HUBS", "NOW",
    "SMCI", "DELL", "HPE", "WDC", "STX",
    "V", "MA", "PYPL", "XYZ", "COIN", "SOFI", "AFRM", "BILL", "HOOD", "NU",
    "WMT", "COST", "HD", "TGT", "SHOP", "ETSY", "UBER", "LYFT", "ABNB",
    "JPM", "BAC", "GS", "MS", "C", "WFC", "BX", "KKR", "APO", "SCHW",
    "LLY", "NVO", "ABBV", "MRK", "PFE", "AMGN", "GILD", "VRTX", "REGN", "MRNA",
    "XOM", "CVX", "OXY", "COP", "PSX",
    "CAT", "DE", "GE", "BA", "LMT", "RTX", "NOC", "HON", "MMM", "UNP",
    "NKE", "LULU", "RH", "DECK", "ONON",
]

# Plain names for the dashboard (an average user knows "Nvidia", not always "NVDA").
NAMES = {
    "SPY": "S&P 500 index fund", "SSO": "2x S&P 500 fund", "UPRO": "3x S&P 500 fund", "SGOV": "0-3 month T-bills", "BIL": "1-3 month T-bills",
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "Nvidia", "GOOGL": "Alphabet (Google)", "AMZN": "Amazon",
    "META": "Meta", "TSLA": "Tesla", "ORCL": "Oracle", "CRM": "Salesforce", "ADBE": "Adobe",
    "AMD": "AMD", "INTC": "Intel", "QCOM": "Qualcomm", "MU": "Micron", "AMAT": "Applied Materials",
    "LRCX": "Lam Research", "KLAC": "KLA", "ASML": "ASML", "MRVL": "Marvell", "ARM": "Arm",
    "AVGO": "Broadcom", "TXN": "Texas Instruments", "ON": "onsemi", "WOLF": "Wolfspeed", "CRUS": "Cirrus Logic",
    "PLTR": "Palantir", "SNOW": "Snowflake", "NET": "Cloudflare", "DDOG": "Datadog", "ZS": "Zscaler",
    "CRWD": "CrowdStrike", "S": "SentinelOne", "GTLB": "GitLab", "HUBS": "HubSpot", "NOW": "ServiceNow",
    "SMCI": "Super Micro", "DELL": "Dell", "HPE": "Hewlett Packard Enterprise", "WDC": "Western Digital",
    "STX": "Seagate", "V": "Visa", "MA": "Mastercard", "PYPL": "PayPal", "XYZ": "Block", "COIN": "Coinbase",
    "SOFI": "SoFi", "AFRM": "Affirm", "BILL": "BILL", "HOOD": "Robinhood", "NU": "Nu Holdings",
    "WMT": "Walmart", "COST": "Costco", "HD": "Home Depot", "TGT": "Target", "SHOP": "Shopify",
    "ETSY": "Etsy", "UBER": "Uber", "LYFT": "Lyft", "ABNB": "Airbnb", "JPM": "JPMorgan Chase",
    "BAC": "Bank of America", "GS": "Goldman Sachs", "MS": "Morgan Stanley", "C": "Citigroup",
    "WFC": "Wells Fargo", "BX": "Blackstone", "KKR": "KKR", "APO": "Apollo", "SCHW": "Charles Schwab",
    "LLY": "Eli Lilly", "NVO": "Novo Nordisk", "ABBV": "AbbVie", "MRK": "Merck", "PFE": "Pfizer",
    "AMGN": "Amgen", "GILD": "Gilead", "VRTX": "Vertex", "REGN": "Regeneron", "MRNA": "Moderna",
    "XOM": "ExxonMobil", "CVX": "Chevron", "OXY": "Occidental", "COP": "ConocoPhillips", "PSX": "Phillips 66",
    "CAT": "Caterpillar", "DE": "Deere", "GE": "GE Aerospace", "BA": "Boeing", "LMT": "Lockheed Martin",
    "RTX": "RTX", "NOC": "Northrop Grumman", "HON": "Honeywell", "MMM": "3M", "UNP": "Union Pacific",
    "NKE": "Nike", "LULU": "Lululemon", "RH": "RH", "DECK": "Deckers", "ONON": "On Running",
}

DEFAULTS = {
    "CORE": "SPY",
    "SAFE": "SGOV",
    "CORE_PCT": 0.80,       # share of the account in SPY while investing
    "PICKS": 4,             # momentum picks
    "PICK_PCT": 0.05,       # share of the account per pick
    "CASH_PCT": 0.0,        # left uninvested (notional orders fill to the cent, so none is needed)
    "TREND_DAYS": 200,      # the safety switch's moving average (trading days)
    "SAFETY_SWITCH": True,  # False = always invest (no T-bill switch)
    "SWITCH_CHECK": "monthly",  # look at the switch only on rebalance days (backtest: 30 switches
                                # in 19 years vs 112 checking daily, better return; docs/backtest.json)
    "MOM_DAYS": 252,        # momentum look-back (~12 months)
    "MOM_SKIP": 21,         # ...skipping the latest ~month (short-term reversal)
    "AGGRESSIVE": True,     # let the bot use aggressive mode when the market is calm and rising
    "AGG_CORE": "SSO",      # 2x S&P 500 fund held as the core in aggressive mode
    "VOL_DAYS": 20,         # the calm meter: SPY's daily swings over this many days, as % a year
    "AGG_VOL_MAX": 0.20,    # aggressive mode may start (monthly) only below this
    "AGG_VOL_EXIT": 0.25,   # ...and ends on any day above this, or when SPY closes below its average
                            # (backtest 2007-now: 18.9%/yr stocks, 11.7% sectors vs 15.5%/8.4% without;
                            # worst drop -39%/-35% vs -29%/-23%; better in both halves of history)
    "DRIFT_PCT": 0.03,      # between rebalances, fix a holding only when it's this far off target
    "MIN_ORDER": 1.00,      # Alpaca's minimum notional order ($)
}

INVEST, SAFETY, AGGRESSIVE = "invest", "safety", "aggressive"


def sma(closes: list[float], days: int) -> float | None:
    return sum(closes[-days:]) / days if len(closes) >= days else None


def trend(spy_closes: list[float], days: int = DEFAULTS["TREND_DAYS"]) -> dict | None:
    """The safety switch: {"mode", "price", "average", "gap_pct"} from SPY's daily closes
    (latest last). None without enough history."""
    avg = sma(spy_closes, days)
    if avg is None:
        return None
    price = spy_closes[-1]
    return {"mode": INVEST if price > avg else SAFETY, "price": round(price, 2),
            "average": round(avg, 2), "gap_pct": round((price / avg - 1) * 100, 2)}


def volatility(closes: list[float], days: int = DEFAULTS["VOL_DAYS"]) -> float | None:
    """SPY's recent swings: the standard deviation of its last `days` daily returns, annualised
    (0.15 = 15% a year). None without enough history."""
    if len(closes) < days + 1:
        return None
    rets = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - days, len(closes))]
    mean = sum(rets) / days
    return round((sum((r - mean) ** 2 for r in rets) / (days - 1)) ** 0.5 * 252 ** 0.5, 4)


def momentum(closes: list[float], days: int = DEFAULTS["MOM_DAYS"], skip: int = DEFAULTS["MOM_SKIP"]) -> float | None:
    """Return from `days` trading days ago to `skip` days ago, in %. None without enough history."""
    if len(closes) < days + 1 or skip >= days:
        return None
    start, end = closes[-1 - days], closes[-1 - skip]
    return round((end / start - 1) * 100, 2) if start > 0 else None


def rank(closes_by_symbol: dict[str, list[float]], s: dict = DEFAULTS) -> list[dict]:
    """Every symbol with enough history, strongest momentum first:
    [{"symbol", "momentum", "above_trend", "eligible"}]."""
    rows = []
    for sym, closes in closes_by_symbol.items():
        m = momentum(closes, s["MOM_DAYS"], s["MOM_SKIP"])
        avg = sma(closes, s["TREND_DAYS"])
        if m is None or avg is None:
            continue
        above = closes[-1] > avg
        rows.append({"symbol": sym, "momentum": m, "above_trend": above, "eligible": above and m > 0})
    rows.sort(key=lambda r: r["momentum"], reverse=True)
    return rows


def pick(ranked: list[dict], n: int, vetoed: set | frozenset = frozenset()) -> list[str]:
    """The top `n` eligible symbols that weren't vetoed (fewer if not enough qualify)."""
    return [r["symbol"] for r in ranked if r["eligible"] and r["symbol"] not in vetoed][:n]


def targets(mode: str, picks: list[str], s: dict = DEFAULTS) -> dict[str, float]:
    """Target weights (fractions of the account). Weights sum to 1 − CASH_PCT."""
    invested = 1 - s["CASH_PCT"]
    if mode not in (INVEST, AGGRESSIVE):
        return {s["SAFE"]: round(invested, 4)}
    out = {(s["AGG_CORE"] if mode == AGGRESSIVE else s["CORE"]): s["CORE_PCT"]}
    for sym in picks[: s["PICKS"]]:
        out[sym] = s["PICK_PCT"]
    rest = round(invested - sum(out.values()), 4)    # empty pick slots
    if rest > 0.0005:
        out[s["SAFE"]] = rest
    return out


def is_rebalance_day(today: str, last_rebalance: str | None) -> bool:
    """First run in a new calendar month (dates as YYYY-MM-DD)."""
    return not last_rebalance or today[:7] != last_rebalance[:7]


def orders(target: dict[str, float], held: dict[str, float], equity: float, full: bool,
           s: dict = DEFAULTS) -> list[dict]:
    """Orders that move `held` ({symbol: market value $}) toward `target` weights.

    full=True (a rebalance day or a mode switch): every holding is set to its target.
    full=False (an ordinary day): only holdings that drifted more than DRIFT_PCT of the account,
    plus anything not in the plan (sold) and anything in the plan not held at all (bought).
    Sells come first: [{"symbol", "side": "sell"|"buy", "amount": $, "all": bool}].
    """
    sells, buys = [], []
    for sym, value in held.items():
        if sym not in target and value > 0:
            sells.append({"symbol": sym, "side": "sell", "amount": round(value, 2), "all": True})
    for sym, w in target.items():
        want, have = w * equity, held.get(sym, 0.0)
        diff = want - have
        if abs(diff) < s["MIN_ORDER"]:
            continue
        if not full and have > 0 and abs(diff) < s["DRIFT_PCT"] * equity:
            continue
        if diff < 0:
            sells.append({"symbol": sym, "side": "sell", "amount": round(-diff, 2), "all": False})
        else:
            buys.append({"symbol": sym, "side": "buy", "amount": round(diff, 2), "all": False})
    buys.sort(key=lambda o: -o["amount"])
    return sells + buys


def describe(mode_info: dict | None, picks: list[str], s: dict = DEFAULTS) -> str:
    """One plain sentence for the dashboard and Telegram."""
    if not mode_info:
        return "Waiting for enough price history to decide."
    if mode_info["mode"] == SAFETY:
        return (f"Safety mode: SPY ended the month below its 200-day average, so the money waits in "
                f"T-bills ({s['SAFE']}) until a month ends back above it.")
    named = ", ".join(picks) if picks else "none qualify right now"
    if mode_info["mode"] == AGGRESSIVE:
        return (f"Aggressive mode: the market is rising calmly, so {round(s['CORE_PCT'] * 100)}% sits in a 2x S&P 500 "
                f"fund ({s['AGG_CORE']}) and {round(s['PICK_PCT'] * 100)}% each in the strongest stocks ({named}).")
    return (f"Invest mode: {round(s['CORE_PCT'] * 100)}% in the whole market ({s['CORE']}) and "
            f"{round(s['PICK_PCT'] * 100)}% each in the strongest stocks ({named}).")
