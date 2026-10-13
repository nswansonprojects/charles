# Kalshi paper bot (PAPER ONLY)

A separate bot that paper-bets on [Kalshi](https://kalshi.com) prediction markets with a pretend
$1,000. It only reads Kalshi's public market data — there is no API key and no code that can place
a real order. It runs alongside Charles and shows up as the **Kalshi** tab on Charles's dashboard.

## What it does

Every 15 minutes it downloads Kalshi's open markets, then:

**Betting book (main).** Four strategies (`strategies.py`) each estimate the chance a market
resolves YES from an outside source. When that beats the price by more than fees, it takes a paper bet.

| Strategy | Probability comes from |
|---|---|
| weather | NWS point forecast + Open-Meteo GFS/ECMWF ensemble spread, days 1–3 ahead |
| sports | DraftKings moneylines via ESPN, bookmaker's cut removed |
| econ | Cleveland Fed CPI nowcast |
| longshot | cheap YES contracts win less often than priced, so it bets NO |

Weather and econ forecasts are blended with the market price (`trust_model`) until they prove they
beat it. Sizing is quarter-Kelly with caps per bet, per event and per strategy. Bets settle at
Kalshi's result or are sold early once the market catches up. All settings: `betting.RULES`,
with overrides in `tuning.json`.

**Arbitrage scanner (side).** Looks for linked markets whose prices contradict each other by
more than fees (e.g. "above 200" priced higher than "above 100") and paper-trades the
guaranteed sets (`network.py`, `evaluate.py`, `maker.py`). These are rare.

## Run it

Python 3.11+ in its own virtual environment (not Charles's):

    cd kalshi
    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
    .venv/bin/python -m pytest          # tests
    .venv/bin/python run.py             # one scan: settle, scan, paper bets
    .venv/bin/python run.py --report    # the paper books

Run a scan every 15 minutes, e.g. with cron:

    */15 * * * * cd /path/to/charles/kalshi && .venv/bin/python run.py >> scan.log 2>&1

Dashboard (read-only, port 8503): `.venv/bin/python webdash.py`. Charles's dashboard passes it
through at `/kalshi/` and shows the Kalshi tab once it answers (set `KALSHI_URL` if it runs elsewhere).
The Self-Tuning tab stays empty here: it shows reports from a private daily review that isn't part
of this repo.

All state lives in `data/` (created on first run): `bets.json` (betting book), `ledger.json`
(arbitrage book), `scans.jsonl`, equity history and a cache of outside data.
