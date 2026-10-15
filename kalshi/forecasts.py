"""Outside data for the betting strategies (public, no API keys), cached under data/cache/.

- weather():  Open-Meteo ensemble forecasts (GFS + ECMWF, ~80 runs) per NWS station, hourly.
- models():   Open-Meteo deterministic runs (ECMWF, GFS, ICON, GEM, UKMO) per station, hourly.
- nws():      NWS official point forecasts per station (the weather picks' center).
- sports():   ESPN scoreboards with DraftKings moneylines, today + next 3 days (ET).
- nowcasts(): Cleveland Fed inflation nowcasts (month-over-month and year-over-year).

If a source fails, the last cached copy is used (stale data is logged); with none, it raises.
"""
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import requests

import strategies

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "cache")
UA = {"User-Agent": "Mozilla/5.0 (kalshi-bot paper research)"}
log = logging.getLogger("kalshi-net")


def _cached(name: str, ttl_min: float, fetch):
    path = os.path.join(CACHE, name + ".json")
    try:
        if time.time() - os.path.getmtime(path) < ttl_min * 60:
            with open(path) as f:
                return json.load(f)
    except (OSError, ValueError):
        pass
    try:
        data = fetch()
    except Exception as e:
        if not os.path.exists(path):
            raise
        log.warning(f"{name}: {e}; using cached copy")
        with open(path) as f:
            return json.load(f)
    os.makedirs(CACHE, exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(data, f)
    os.replace(path + ".tmp", path)
    return data


def _weather() -> dict:
    codes = list(strategies.STATIONS)
    r = requests.get("https://ensemble-api.open-meteo.com/v1/ensemble", timeout=60, params={
        "latitude": ",".join(str(strategies.STATIONS[c][0]) for c in codes),
        "longitude": ",".join(str(strategies.STATIONS[c][1]) for c in codes),
        "hourly": "temperature_2m", "models": "gfs025,ecmwf_ifs025", "temperature_unit": "fahrenheit",
        "timezone": "GMT", "forecast_days": 5})
    r.raise_for_status()
    body = r.json()
    body = body if isinstance(body, list) else [body]
    out = {}
    for code, loc in zip(codes, body):
        h = loc["hourly"]
        out[code] = {"time": h["time"], "members": [v for k, v in h.items() if k.startswith("temperature_2m")]}
    return out


def weather() -> dict:
    return _cached("weather", 60, _weather)


MODELS = "ecmwf_ifs025,gfs_seamless,icon_seamless,gem_seamless,ukmo_seamless"


def _models() -> dict:
    codes = list(strategies.STATIONS)
    r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=60, params={
        "latitude": ",".join(str(strategies.STATIONS[c][0]) for c in codes),
        "longitude": ",".join(str(strategies.STATIONS[c][1]) for c in codes),
        "hourly": "temperature_2m", "models": MODELS, "temperature_unit": "fahrenheit",
        "timezone": "GMT", "forecast_days": 5})
    r.raise_for_status()
    body = r.json()
    body = body if isinstance(body, list) else [body]
    return {code: {"time": loc["hourly"]["time"],
                   "members": [v for k, v in loc["hourly"].items() if k.startswith("temperature_2m")]}
            for code, loc in zip(codes, body)}


def models() -> dict:
    return _cached("models", 60, _models)


NWS_UA = {"User-Agent": "kalshi-bot paper research (charles-bot.xyz)", "Accept": "application/geo+json"}


def _nws_points() -> dict:
    """Forecast URL per station (fixed grid; cached for a month)."""
    out = {}
    for code, (lat, lon, _) in strategies.STATIONS.items():
        r = requests.get(f"https://api.weather.gov/points/{lat},{lon}", headers=NWS_UA, timeout=30)
        r.raise_for_status()
        out[code] = r.json()["properties"]["forecast"]
    return out


def _nws() -> dict:
    urls = _cached("nws_points", 60 * 24 * 30, _nws_points)
    out = {}
    for code, url in urls.items():
        try:
            r = requests.get(url, headers=NWS_UA, timeout=30)
            r.raise_for_status()
            out[code] = [{"start": p["startTime"], "day": p["isDaytime"], "temp": p["temperature"]}
                         for p in r.json()["properties"]["periods"]]
        except Exception as e:
            log.warning(f"nws {code}: {e}")
    if not out:
        raise RuntimeError("no NWS forecasts")
    return out


def nws() -> dict:
    """NWS official 7-day forecast periods per station (local start time, daytime?, temp F)."""
    return _cached("nws", 60, _nws)


def _odds(comp: dict):
    """(book name, {'home': '-150', 'away': '+130'}) from an ESPN competition, or None."""
    for o in comp.get("odds") or []:
        ml = o.get("moneyline") or {}
        lines = {}
        for side in ("home", "away"):
            s = ml.get(side) or {}
            lines[side] = ((s.get("close") or {}).get("odds") or (s.get("open") or {}).get("odds"))
        if all(lines.values()):
            return (o.get("provider") or {}).get("name", "sportsbook"), lines
    return None


def _scoreboard(league: str, day: str, groups: str | None) -> list:
    params = {"dates": day, "limit": 500}
    if groups:
        params["groups"] = groups
    r = requests.get(f"https://site.api.espn.com/apis/site/v2/sports/{league}/scoreboard", params=params,
                     timeout=30)
    r.raise_for_status()
    games = []
    for ev in r.json().get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        odds = _odds(comp)
        teams = comp.get("competitors") or []
        if not odds or len(teams) != 2:
            continue
        book, lines = odds
        games.append({"id": ev.get("id"), "start": ev.get("date", ""), "name": ev.get("name", ""),
                      "state": ((ev.get("status") or {}).get("type") or {}).get("state", ""), "book": book,
                      "season_type": (ev.get("season") or {}).get("type"),  # ESPN: 1 preseason, 2 regular, 3 post
                      "teams": [{**{k: t.get("team", {}).get(k) for k in
                                    ("location", "displayName", "shortDisplayName", "name", "abbreviation")},
                                 "home": t.get("homeAway") == "home", "ml": lines[t.get("homeAway")]}
                                for t in teams]})
    return games


def _sports() -> dict:
    now = datetime.now(timezone.utc).astimezone(strategies.ET)
    days = [(now + timedelta(days=i)).strftime("%Y%m%d") for i in range(4)]
    out = {}
    for league in sorted(set(strategies.LEAGUES.values())):
        groups = {"football/college-football": ("80", "81"),  # FBS + FCS
                  "basketball/mens-college-basketball": ("50",),  # all of Division I, not just the top 25
                  "basketball/womens-college-basketball": ("50",)}.get(league, (None,))
        games = {}
        for day in days:
            for g in groups:
                try:
                    for x in _scoreboard(league, day, g):
                        games[x["id"]] = x
                except Exception as e:
                    log.warning(f"espn {league} {day}: {e}")
        out[league] = list(games.values())
    return out


def sports() -> dict:
    return _cached("sports", 15, _sports)


def _nowcasts() -> dict:
    base = "https://www.clevelandfed.org/-/media/files/webcharts/inflationnowcasting/"
    out = {}
    for k in ("month", "year"):
        r = requests.get(base + f"nowcast_{k}.json", headers=UA, timeout=30)
        r.raise_for_status()
        out[k] = r.json()[-6:]
    return out


def nowcasts() -> dict:
    return _cached("nowcasts", 180, _nowcasts)
