"""Directional betting strategies: each turns open Kalshi markets plus an outside source
into picks = our own probability that a market resolves YES.

Pure functions: outside data comes in already fetched (forecasts.py). Nothing here bets.
A pick: {"strategy", "ticker", "event", "category", "title", "sub", "fair_yes", "source"}.
"""
import math
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}
ET, PT = ZoneInfo("America/New_York"), ZoneInfo("America/Los_Angeles")


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def phi(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def clip(p: float) -> float:
    return min(max(p, 0.01), 0.99)


def event_date(event_ticker: str):
    """'KXHIGHNY-26OCT10' or 'KXNFLGAME-26OCT19WASSF' -> date(2026, 10, 19)."""
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", event_ticker)
    if not m or m.group(2) not in MONTHS:
        return None
    return datetime(2000 + int(m.group(1)), MONTHS[m.group(2)], int(m.group(3))).date()


def _pick(strategy, e, m, fair, source) -> dict:
    return {"strategy": strategy, "ticker": m["ticker"], "event": e["event_ticker"],
            "category": e.get("category", ""), "title": e.get("title", ""),
            "sub": m.get("yes_sub_title", ""), "fair_yes": round(clip(fair), 4), "source": source}


# ---------------------------------------------------------------- weather
# NWS climate stations Kalshi settles on: (lat, lon, UTC offset of local STANDARD time).
# The climate day runs midnight to midnight local standard time, all year.
STATIONS = {
    "CLINYC": (40.7789, -73.9692, -5), "CLIMIA": (25.7906, -80.3164, -5), "CLIPHL": (39.8733, -75.2268, -5),
    "CLIATL": (33.6297, -84.4422, -5), "CLIBOS": (42.3606, -71.0097, -5), "CLIDCA": (38.8483, -77.0342, -5),
    "CLIEWR": (40.6825, -74.1694, -5), "CLISDF": (38.1811, -85.7392, -5), "CLITTN": (40.2767, -74.8133, -5),
    "CLIMDW": (41.7861, -87.7522, -6), "CLIAUS": (30.1831, -97.6799, -6), "CLIDFW": (32.8998, -97.0403, -6),
    "CLIHOU": (29.6375, -95.2825, -6), "CLIMSP": (44.8831, -93.2289, -6), "CLIMSY": (29.9933, -90.2511, -6),
    "CLIOKC": (35.3889, -97.6006, -6), "CLISAT": (29.5443, -98.4839, -6), "CLIDEN": (39.8466, -104.6562, -7),
    "CLIPHX": (33.4278, -112.0036, -7), "CLILAX": (33.9382, -118.3866, -8), "CLILAS": (36.0719, -115.1634, -8),
    "CLISAN": (32.7336, -117.1831, -8), "CLISEA": (47.4447, -122.3144, -8), "CLISFO": (37.6197, -122.3656, -8),
}
WEATHER = {"sd": 1.6, "sd_per_extra_day": 0.4, "max_days_ahead": 3,
           # a low comes around dawn, hours after an evening scan: by then traders watch temperatures
           # actually falling and beat any forecast (2026-10-09: three such bets went badly wrong)
           "min_hours_before_low_day": 12}


def weather_kind(e: dict) -> str | None:
    s = e.get("series_ticker", "")
    if s.startswith("KXHIGH") and s != "KXHIGHINFLATION":
        return "max"
    if s.startswith("KXLOWT"):
        return "min"
    return None


def station_of(m: dict) -> str | None:
    hit = re.search(r"\((CLI[A-Z]{3})\)", m.get("rules_primary", ""))
    return hit.group(1) if hit and hit.group(1) in STATIONS else None


def temp_range(m: dict):
    """Reported whole-degree temperatures that make the market YES, as (lo, hi), either may be None.
    'less' 66 = 65 or below; 'between' 66/67 = 66 or 67; 'greater' 73 = 74 or above."""
    st, fl, cap = m.get("strike_type"), m.get("floor_strike"), m.get("cap_strike")
    if st == "less" and cap is not None:
        rng = None, cap - 1
    elif st == "between" and fl is not None and cap is not None:
        rng = fl, cap
    elif st == "greater" and fl is not None:
        rng = fl + 1, None
    else:
        return None
    # Kalshi's strike labels have fooled us before: the plain-English label must agree
    shown = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", m.get("yes_sub_title", ""))]
    return rng if shown == [x for x in rng if x is not None] else None


def day_values(series: dict, day, offset: int, kind: str) -> list[float]:
    """Each ensemble member's max (or min) over one climate day. `series` = {"time": [...UTC hours],
    "members": [[...]]} from forecasts.weather()."""
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - timedelta(hours=offset)
    want = {(start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(24)}
    idx = [i for i, t in enumerate(series["time"]) if t in want]
    if len(idx) < 24:
        return []
    out = []
    for vals in series["members"]:
        v = [vals[i] for i in idx if vals[i] is not None]
        if len(v) == 24:
            out.append(max(v) if kind == "max" else min(v))
    return out


def prob_in_range(values: list[float], lo, hi, sd: float) -> float:
    """Chance the reported (rounded) temperature lands in [lo, hi]: each member blurred by sd."""
    tot = 0.0
    for v in values:
        up = 1.0 if hi is None else phi((hi + 0.5 - v) / sd)
        down = 0.0 if lo is None else phi((lo - 0.5 - v) / sd)
        tot += up - down
    return tot / len(values)


def nws_center(periods: list, day, kind: str):
    """NWS official forecast for one climate day: the daytime high on `day`, or the overnight
    low of the night before it (the low usually comes around dawn)."""
    want = day if kind == "max" else day - timedelta(days=1)
    for p in periods:
        if p["day"] == (kind == "max") and p["start"][:10] == want.isoformat():
            return p["temp"]
    return None


def weather_picks(events: list, ensemble: dict, nws: dict, now: datetime) -> list[dict]:
    """Center = NWS forecast (bias-corrected, what the market watches); spread = how far the
    ~80 ensemble runs scatter around their own mean, plus a blur of sd degrees."""
    picks = []
    for e in events:
        kind = weather_kind(e)
        day = event_date(e["event_ticker"]) if kind else None
        if not day or not e.get("markets"):
            continue
        stn = station_of(e["markets"][0])
        if not stn or stn not in ensemble or stn not in nws:
            continue
        offset = STATIONS[stn][2]
        local_today = (now + timedelta(hours=offset)).date()
        ahead = (day - local_today).days
        if not 1 <= ahead <= WEATHER["max_days_ahead"]:
            continue  # today's high is partly observed already: forecasts alone would be stale
        day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - timedelta(hours=offset)
        if kind == "min" and day_start - now < timedelta(hours=WEATHER["min_hours_before_low_day"]):
            continue
        raw = day_values(ensemble[stn], day, offset, kind)
        center = nws_center(nws[stn], day, kind)
        if len(raw) < 20 or center is None:
            continue
        mean = sum(raw) / len(raw)
        vals = [center + v - mean for v in raw]
        sd = WEATHER["sd"] + WEATHER["sd_per_extra_day"] * (ahead - 1)
        for m in e["markets"]:
            rng = temp_range(m)
            if rng is None:
                continue
            p = prob_in_range(vals, *rng, sd)
            picks.append(_pick("weather", e, m, p, f"NWS forecast {center}°F, {len(raw)}-run spread ({stn})"))
    return picks


# ---------------------------------------------------------------- sports
LEAGUES = {"KXNFLGAME": "football/nfl", "KXNCAAFGAME": "football/college-football",
           "KXNHLGAME": "hockey/nhl", "KXNBAGAME": "basketball/nba", "KXMLBGAME": "baseball/mlb",
           "KXWNBAGAME": "basketball/wnba"}
SPORTS = {"min_minutes_before_start": 10}


def american_to_prob(odds) -> float | None:
    s = str(odds).strip().upper()
    if s in ("EVEN", "EV"):
        return 0.5
    try:
        o = float(s)
    except ValueError:
        return None
    if o <= -100:
        return -o / (-o + 100)
    if o >= 100:
        return 100 / (o + 100)
    return None


def norm(s: str) -> str:
    s = s.lower().replace("st.", "state").replace("&", "and").replace("é", "e")
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", s).split())


def team_matches(name: str, team: dict) -> bool:
    n = norm(name)
    if not n:
        return False
    names = {norm(team.get(k) or "") for k in ("location", "displayName", "shortDisplayName", "name", "abbreviation")}
    return n in names or (len(n) >= 4 and norm(team.get("displayName") or "").startswith(n))


def match_game(e: dict, games: list, now: datetime):
    """The ESPN game for a 2-team Kalshi event: same teams, same date (ET or PT), not started."""
    mk = e.get("markets", [])
    day = event_date(e["event_ticker"])
    if len(mk) != 2 or not day:
        return None
    a, b = mk[0].get("yes_sub_title", ""), mk[1].get("yes_sub_title", "")
    found = []
    for g in games:
        start = datetime.fromisoformat(g["start"].replace("Z", "+00:00"))
        if day not in (start.astimezone(ET).date(), start.astimezone(PT).date()):
            continue
        t0, t1 = g["teams"]
        if team_matches(a, t0) and team_matches(b, t1) and not (team_matches(a, t1) or team_matches(b, t0)):
            found.append((g, {mk[0]["ticker"]: t0, mk[1]["ticker"]: t1}))
        elif team_matches(a, t1) and team_matches(b, t0) and not (team_matches(a, t0) or team_matches(b, t1)):
            found.append((g, {mk[0]["ticker"]: t1, mk[1]["ticker"]: t0}))
    if len(found) != 1:
        return None  # none, or ambiguous: never guess
    g, teams = found[0]
    start = datetime.fromisoformat(g["start"].replace("Z", "+00:00"))
    if g["state"] != "pre" or start < now + timedelta(minutes=SPORTS["min_minutes_before_start"]):
        return None  # live odds would be stale
    return g, teams


def sports_picks(events: list, games_by_league: dict, now: datetime) -> list[dict]:
    picks = []
    for e in events:
        league = LEAGUES.get(e.get("series_ticker", ""))
        if not league or league not in games_by_league:
            continue
        hit = match_game(e, games_by_league[league], now)
        if not hit:
            continue
        g, teams = hit
        raw = {t: american_to_prob(team.get("ml")) for t, team in teams.items()}
        if any(v is None for v in raw.values()):
            continue
        total = sum(raw.values())
        for m in e["markets"]:
            p = raw[m["ticker"]] / total
            picks.append(_pick("sports", e, m, p, f"{g['book']} moneyline {teams[m['ticker']].get('ml')} "
                                                   f"({p * 100:.0f}% after removing the book's cut)"))
    return picks


# ---------------------------------------------------------------- economics
# Kalshi series -> (Cleveland Fed file, nowcast series name, nowcast error sd in % points).
# sd = the nowcast's own error vs the published number, last 57 months, measured 2026-10-09
# (it barely shrinks in the final 10 days before release).
ECON = {"KXCPI": ("month", "CPI Inflation", 0.16), "KXCPICORE": ("month", "Core CPI Inflation", 0.13),
        "KXCPIYOY": ("year", "CPI Inflation", 0.17), "KXCPICOREYOY": ("year", "Core CPI Inflation", 0.14)}


def nowcast_value(entries: list, period: str, series: str) -> float | None:
    """Latest nowcast for `period` ('2026-9'); None once the actual number is out."""
    for x in entries:
        if x.get("chart", {}).get("subcaption") != period:
            continue
        by = {s["seriesname"]: [d.get("value") for d in s.get("data", []) if d.get("value") not in ("", None)]
              for s in x.get("dataset", [])}
        if by.get("Actual " + series):
            return None
        vals = by.get(series) or []
        return float(vals[-1]) if vals else None
    return None


def econ_picks(events: list, nowcasts: dict) -> list[dict]:
    picks = []
    for e in events:
        cfg = ECON.get(e.get("series_ticker", ""))
        m = re.search(r"-(\d{2})([A-Z]{3})$", e["event_ticker"])
        if not cfg or not m or m.group(2) not in MONTHS:
            continue
        file, series, sd = cfg
        x = nowcast_value(nowcasts.get(file, []), f"20{m.group(1)}-{MONTHS[m.group(2)]}", series)
        if x is None:
            continue
        for mk in e.get("markets", []):
            if mk.get("strike_type") != "greater" or mk.get("floor_strike") is None:
                continue
            # "above 0.2%" as reported to one decimal = 0.3 or more = true value >= 0.25
            p = 1 - phi((mk["floor_strike"] + 0.05 - x) / sd)
            picks.append(_pick("econ", e, mk, p, f"Cleveland Fed nowcast {x:.2f}%"))
    return picks


# ---------------------------------------------------------------- longshot bias
# Research on Kalshi trades (Burgi, Deng & Whelan 2025) found contracts priced under 10c win far
# less often than their price says. Model: a YES longshot's real chance is `shrink` x its price.
LONGSHOT = {"min_yes": 0.03, "max_yes": 0.10, "shrink": 0.5, "max_spread": 0.03,
            "min_volume_24h": 50, "max_days": 30}
MODELLED = tuple(LEAGUES) + tuple(ECON) + ("KXHIGH", "KXLOWT")


def longshot_picks(events: list, now: datetime) -> list[dict]:
    picks = []
    for e in events:
        if e.get("series_ticker", "").startswith(MODELLED):
            continue  # those have their own model
        for m in e.get("markets", []):
            yb, ya = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
            if not (LONGSHOT["min_yes"] <= ya <= LONGSHOT["max_yes"]) or yb <= 0 or ya - yb > LONGSHOT["max_spread"]:
                continue
            if _f(m.get("volume_24h_fp")) < LONGSHOT["min_volume_24h"]:
                continue
            try:
                close = datetime.fromisoformat(m["close_time"].replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            if not now < close <= now + timedelta(days=LONGSHOT["max_days"]):
                continue
            mid = (ya + yb) / 2
            picks.append(_pick("longshot", e, m, mid * LONGSHOT["shrink"],
                               f"longshot at {mid * 100:.1f}c; history says ~{mid * LONGSHOT['shrink'] * 100:.1f}%"))
    return picks
