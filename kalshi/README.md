# Kalshi paper bot (PAPER ONLY)

A separate bot that paper-bets on [Kalshi](https://kalshi.com) prediction markets with a pretend
$1,000. It only reads Kalshi's public market data — there is no API key and no code that can place
a real order. It runs alongside Charles and shows up as the **Kalshi** tab on Charles's dashboard.

## What it does

Every 15 minutes it downloads Kalshi's open markets, then:

**Betting book (main).** Four strategies (`strategies.py`) each estimate the chance a market
resolves YES from an outside source. When that beats the price by more than fees, it may take a paper bet.

| Strategy | Probability comes from |
|---|---|
| weather | NWS point forecast blended with 5 Open-Meteo models (ECMWF, GFS, ICON, GEM, UKMO) + GFS/ECMWF ensemble spread, days 1–3 ahead |
| sports | DraftKings moneylines via ESPN, bookmaker's cut removed (NFL, NBA, NHL, MLB, WNBA, college football and basketball) |
| econ | Cleveland Fed CPI nowcast |
| longshot | cheap YES contracts win less often than priced, so it bets NO |

**A strategy has to earn its money.** Every market each strategy prices is logged and graded
against Kalshi's own price when it settles, along with the bets it *would* have made
(`predictions.py`). Strategies move through levels automatically (`gates.py`):

- **predict** (where every strategy starts): no money, predictions only
- **small** ($5 bets): once its odds beat the market's (Brier score) over 50+ settled markets and its would-be bets made money after fees
- **normal**: after 30 small bets in profit

A strategy whose odds fall behind the market, or whose bets lose too much, drops back to predict.

Forecasts are blended with the market price (`trust_model`). Sizing is quarter-Kelly with caps per bet,
per event and per strategy, and a win must pay at least the stake. Bets settle at Kalshi's result or
are sold early (in part, if the bids are thin) once the market catches up. All settings:
`betting.RULES`, with overrides in `tuning.json`.

**Arbitrage scanner (side).** Looks for linked markets whose prices contradict each other by
more than fees (e.g. "above 200" priced higher than "above 100") and paper-trades the
guaranteed sets (`network.py`, `evaluate.py`, `maker.py`). These are rare.

## Run it

**With the installer** (https://charles-bot.xyz/install.sh): nothing to do — it sets up this bot's
own Python env, a 15-minute timer (`kalshi-net.timer`) and its dashboard (`kalshi-web`), and
`charles-update` keeps it current. Log: `kalshi-logs`.

**By hand:** Python 3.11+ in its own virtual environment (not Charles's):

    cd kalshi
    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
    .venv/bin/python -m pytest          # tests
    .venv/bin/python run.py             # one scan: settle, scan, paper bets
    .venv/bin/python run.py --report    # the paper books

Run a scan every 15 minutes, e.g. with cron:

    */15 * * * * cd /path/to/charles/kalshi && .venv/bin/python run.py >> scan.log 2>&1

Dashboard (read-only, port 8503): `.venv/bin/python webdash.py`. Charles's dashboard passes it
through at `/kalshi/` and shows the Kalshi tab once it answers (set `KALSHI_URL` if it runs elsewhere).
The Self-Tune tab only shows its progress bars here: it shows reports from a private daily review that isn't part
of this repo.

All state lives in `data/` (created on first run): `bets.json` (betting book), `ledger.json`
(arbitrage book), `scans.jsonl`, equity history and a cache of outside data.
