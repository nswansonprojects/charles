# Charles — a simple, tested investing bot for your own server

Charles invests an **Alpaca paper account** (simulated money) with one plan:

1. **80% in the whole market** (SPY, an S&P 500 index fund).
2. **4 momentum picks, 5% each**: the stocks from a list of 100 big US companies that rose the most
   over the past year (skipping the latest month) and are above their 200-day average. Re-picked on
   the first trading day of each month. Before a new pick is bought, an AI (Groq, free key) reads its
   recent headlines and can skip it for serious bad news.
3. **A safety switch**: when SPY ends a month below its 200-day average, everything moves to
   T-bills (SGOV) until a month ends back above it.
4. **Aggressive mode, the bot's own toggle**: when SPY is above its average and calm (daily swings
   under 20% a year), a monthly check moves the 80% core into SSO, a 2x S&P 500 fund. Any day the
   swings pass 25% or SPY closes below its average, the core goes straight back to SPY.

A mobile-friendly dashboard runs on port 8502, and Telegram messages (optional) tell you about every
trade. A separate paper-only Kalshi prediction-market bot lives in `kalshi/` (see its README); its
**Toggle Evil Charles** button makes it bet the opposite side of every bet it decides to place.

## Charles Crypto (practice money)

The dashboard's **Crypto** tab runs a $1,000 paper book of 60% bitcoin and 40% ether, each held only
while it's above its 200-day average (checked weekly; otherwise that part sits in cash). Prices are
Coinbase's public prices with a 0.6% fee per trade; it needs no exchange account and never buys anything
for real. A systemd timer (`charles-crypto.timer`, set up by the installer) steps it every hour.

`crypto_backtest.py` → `docs/crypto_backtest.json`, weekly from Dec 2016 to Oct 2026:

| $1,000, Dec 2016 to Oct 2026 | A year, on average | Worst drop |
|---|---|---|
| The crypto plan | +75% | −61% |
| Just hold bitcoin | +60% | −84% |
| *Since 2021:* the crypto plan | +9% | −41% |
| *Since 2021:* just hold bitcoin | +16% | −77% |

It cuts the big drops, and lately it has earned less than just holding bitcoin. Not financial advice.

Setup guide: **https://charles-bot.xyz/setup.html** (one-line installer for an Ubuntu server).

## A trading day (US Eastern)

| Time | What happens |
|------|--------------|
| 10:00 | **The daily check** (`rebalancer.py`): on the first trading day of a month it re-picks the stocks and looks at the safety switch; on other days it only fixes a holding that drifted more than 3 points from its target, or drops aggressive mode if the market turned choppy. Most days: no orders. |
| 14:00 | The same check again, as a retry |
| every 30 min (9:30–16:00) | Account value saved for the chart |
| 16:30 | Daily summary on Telegram |
| every hour | Heartbeat message |

Orders are dollar (notional) market orders, so fractional shares work on a small account. Sales go
first, then buys. A daily loss limit (`MAX_DAILY_LOSS` in `config.py`) pauses buys for the rest of
the day.

## The evidence (`backtest_core.py` → `docs/backtest.json`)

The backtest runs the live decision code (`rebalancer.decide`) day by day from July 2007: signals from
the previous close, fills at the next open plus 5 bp slippage, dividends and fund fees included.

| $1,000 from Jul 2007 to Oct 2026 | A year, on average | Worst drop |
|---|---|---|
| The plan (stock picks, aggressive mode on) | +18.9% | −39% |
| The plan with sector funds as picks | +11.7% | −35% |
| The plan without aggressive mode | +15.5% | −29% |
| SPY, buy and hold | +10.8% | −55% |

Read it with care: the stock list is today's (it already contains past winners), which flatters the
picks; the sector-fund version has no such head start, so expect something between the two. Past and
simulated results don't predict real ones. Not financial advice.

## Self-tuning (the numbers, never the stocks)

On the first Sunday of each month `evaluate.py` (systemd timer `charles-evaluate`, set up by the
installer) backtests one-step changes to the plan's numbers — core ±5 points, ±1 pick, trend line
±50 days, daily vs monthly switch check, the switch on/off, aggressive mode on/off and its calm limit —
on the stock list and on sector funds, in both halves of history. A change "passes" only if it earns at
least 0.5% a year more in all four tests without a worst drop more than 2 points deeper. The result is
written to `data/evaluation.json` and shown on the dashboard's Self-Tune tab; you decide whether to
apply it in `strategy.json`.

## Settings

| What | Where |
|------|-------|
| The plan (core %, picks, safety switch, aggressive mode, 2x/3x fund) | `strategy.json`, clamped to the limits in `strategy_settings.py`, over `allocator.DEFAULTS`. Read at every check: no restart needed. |
| Daily loss limit, start balance, AI model | `config.py` |
| API keys | `.env` (copy `.env.example`; never commit it). `charles-keys` updates them. |
| Kalshi / Coinbase keys | `charles-connect kalshi` or `charles-connect crypto`, over SSH (saved in `data/accounts/`, root-only). The bots don't use them yet. The dashboard's Connect Accounts page shows the steps. |

## Commands

| Command | Does |
|---------|------|
| `charles-start`, `charles-stop`, `charles-restart`, `charles-status`, `charles-logs` | Control and watch the bot |
| `charles-update` | `git pull`, packages, restart (re-run the installer for new services) |
| `charles-connect --status` | Which broker accounts are connected (no secrets shown) |
| `python rebalancer.py --dry-run [--veto]` | What the daily check would do right now, without placing anything |
| `python backtest_core.py` | Re-run the backtest (a minute or two; needs about 1 GB of free memory) |
| `python -m unittest discover -s tests -t tests` | Unit tests |

## Files

```
main.py               startup and schedule (the daily check, chart points, summaries, heartbeat)
allocator.py          the plan's rules: safety switch, aggressive mode, momentum ranking, targets (pure)
rebalancer.py         the daily check: prices → decide → sell, then buy → data/plan.json
news_veto.py          the AI's only job: veto a new pick on serious bad news (fails open)
backtest_core.py      the plan on daily history since 2007 (same decide() as live)
evaluate.py           monthly rule check → data/evaluation.json
alpaca_executor.py    the paper account: read it, place notional orders, trade log
risk_manager.py       the daily loss limit
strategy_settings.py  loads strategy.json within hard limits
alerts.py             Telegram messages
webdash.py, web/      mobile dashboard (port 8502; stocks, Kalshi and crypto tabs, Connect Accounts steps)
crypto_plan.py, crypto_paper.py, crypto_market.py, crypto_backtest.py   the crypto paper book
connections.py        charles-connect: broker keys over SSH
reports.py            weekly/monthly/yearly reports
state_io.py, timeofday.py   atomic state writes, ET market date
update_keys.py, server_confirm.py, credential_manager.py   API key handling
kalshi/               the Kalshi paper bot (its own README, env and services)
tests/                unit tests
data/  logs/  reports/   runtime state and output (not in git)
```

## Safety notes

- **Paper trading only** as shipped: the Alpaca client is hard-coded to `paper=True`. Real money
  needs live keys and a deliberate code change — and is entirely your responsibility.
- **Each stock is capped at 5% of the account**; leverage (the 2x fund) is used only in calm, rising
  markets and dropped on the first choppy day.
- **The dashboard has no login**: open port 8502 only if you're fine with anyone seeing it.
