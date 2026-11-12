"""market_types.py — what kind of market each day belonged to, and how a plan did in each kind.

Pure functions, no I/O. Four kinds, the ones the plan's rules are built around (ops/board/plan.md):

  fall      from a high down to the low of a drop of `drop` or more (10% for stocks)
  rebound   the first `rebound_days` trading days after that low
  calm      any other day whose surrounding window rose in a fairly straight line
  choppy    everything else: sideways or zigzagging (the window's net move is under `steady` of the distance it travelled)

The labels look both ways in time (a fall is only a fall once its low is known), so they describe the past: they are
for scoring a plan after the fact, never for deciding a trade. The last days of a series are provisional for the same
reason, and `label` says how many.
"""
import math

FALL, REBOUND, CALM, CHOPPY = "fall", "rebound", "calm", "choppy"
KINDS = (CALM, FALL, REBOUND, CHOPPY)
STOCKS = {"drop": 0.10, "rebound_days": 63, "window": 63, "steady": 0.10}
CRYPTO = {"drop": 0.30, "rebound_days": 45, "window": 61, "steady": 0.10}


def falls(closes: list, drop: float) -> list:
    """Every drop of `drop` or more → [(high index, low index, over)]. A fall is over once the price is `drop` above
    its low again; the last one may still be running (over False)."""
    out, high, low, falling = [], 0, 0, False
    for i in range(1, len(closes)):
        c = closes[i]
        if not falling:
            if c >= closes[high]:
                high = i
            elif c <= closes[high] * (1 - drop):
                falling, low = True, i
        elif c < closes[low]:
            low = i
        elif c >= closes[low] * (1 + drop):
            out.append((high, low, True))
            falling, high = False, i
    if falling:
        out.append((high, low, False))
    return out


def label(closes: list, drop=0.10, rebound_days=63, window=63, steady=0.10) -> dict:
    """→ {"labels": one kind per close, "provisional": how many of the last days may still change}."""
    n = len(closes)
    labels = [None] * n
    eps = falls(closes, drop)
    for high, low, _ in eps:
        for i in range(high + 1, low + 1):
            labels[i] = FALL
    for high, low, _ in eps:
        for i in range(low + 1, min(n, low + 1 + rebound_days)):
            if labels[i] is None:
                labels[i] = REBOUND
    half = window // 2
    for i in range(n):
        if labels[i] is not None:
            continue
        a, b = max(0, i - half), min(n - 1, i + half)
        travelled = sum(abs(closes[k] - closes[k - 1]) for k in range(a + 1, b + 1))
        labels[i] = CALM if travelled and (closes[b] - closes[a]) / travelled > steady else CHOPPY
    # A day can still turn into a fall until the price has made a new high, and its window isn't full for `half` days.
    last_high = max(range(n), key=lambda i: (closes[i], i)) if n else 0
    return {"labels": labels, "provisional": max(n - 1 - last_high, min(half, n))}


def score(values: list, labels: list, year=252) -> dict:
    """How a daily value series did in each kind of market → {kind: {"days", "share_pct", "total_pct", "yearly_pct",
    "monthly_pct", "worst_day_pct"}}. `values` and `labels` are the same days; the first day has no return."""
    out = {}
    n = len(values) - 1
    for kind in KINDS:
        r = [values[i] / values[i - 1] - 1 for i in range(1, len(values)) if labels[i] == kind and values[i - 1]]
        if not r:
            out[kind] = {"days": 0, "share_pct": 0.0, "total_pct": 0.0, "yearly_pct": None, "monthly_pct": None, "worst_day_pct": None}
            continue
        growth = math.prod(1 + x for x in r)
        out[kind] = {"days": len(r), "share_pct": round(100 * len(r) / n, 1), "total_pct": round((growth - 1) * 100, 1),
                     "yearly_pct": round((growth ** (year / len(r)) - 1) * 100, 1) if growth > 0 else -100.0,
                     "monthly_pct": round((growth ** (year / 12 / len(r)) - 1) * 100, 2) if growth > 0 else -100.0,
                     "worst_day_pct": round(min(r) * 100, 1)}
    return out


def weakest(plan: dict, market: dict, min_days: int = 60) -> str | None:
    """The kind of market where the plan falls furthest behind the market itself, per month (two `score` results on the same
    days). None when it is behind in none, or no kind has `min_days` of history yet."""
    gaps = {k: plan[k]["monthly_pct"] - market[k]["monthly_pct"] for k in KINDS
            if plan[k]["days"] >= min_days and plan[k]["monthly_pct"] is not None and market[k]["monthly_pct"] is not None}
    worst = min(gaps, key=gaps.get, default=None)
    return worst if worst is not None and gaps[worst] < 0 else None
