# Charles

A simple, open source investing bot for your own server. It trades **paper money linked to real market data**
through a free Alpaca paper account, and you watch it from your phone. No real money, no subscription, and your
keys and data stay on a server you own.

**Set it up:** https://charles-bot.xyz/setup.html (one command on a fresh Ubuntu server; $6 a month at DigitalOcean is enough)

## The default plan

Change any of it with the sliders on the dashboard, or copy someone's settings from the leaderboard.

1. **80% in the whole market** (SPY).
2. **4 momentum picks, 5% each**, chosen monthly from 100 big US companies. Before a new pick is bought, an AI (Groq, a
   free key, optional) reads its headlines and can skip it for serious bad news. That's the AI's only job in trading.
3. **A safety switch**: when SPY ends a month below its 200-day average, everything moves to T-bills (SGOV) until a month ends back above it.
4. **Aggressive mode**: while SPY is above its average and calm, the core sits in SSO, a 2x S&P 500 fund. Any choppy day sends it back to SPY.

Most days it places no orders. A daily loss limit pauses buys for the rest of the day.

## What's on the dashboard

- **Home**: what each bot has earned, Pause, and Get started on a new install.
- **Stocks**: holdings and a feed of what the bot did and the news about what it holds. **Plan**: sliders, a test on past prices, Undo.
- **Leaderboard**: settings people chose to share, each run forward by the leaderboard's server (nobody's own results are accepted). Copy takes two taps, and monthly challenges are opt-in.
- **Ask Charles** (optional): a chat on your own Anthropic key, with a monthly spending cap. It can read and propose settings. It can't place orders or see your keys.
- **Updates**: a version history with Restore. Anything that changes how it trades shows old → new and asks first. Nothing updates by itself.

**Add-ons**, off by default: `charles-addon on crypto` (a $1,000 paper book of bitcoin and ether, Coinbase's public prices) and
`charles-addon on kalshi` (a paper-only prediction-market bot; see [kalshi/README.md](kalshi/README.md)).

## Simulated results

`backtest_core.py` runs the same decision code as the live bot on daily history, with slippage, dividends and fund fees.

| $1,000, Jul 2007 to Oct 2026 | A year, on average | Worst drop |
|---|---|---|
| The plan with sector funds as picks | +11.7% | −35% |
| The plan with stock picks | +18.9% | −39% |
| SPY, buy and hold | +10.9% | −55% |

Read it with care. The stock list is today's, so it already contains past winners, which flatters the stock-pick line. Simulated
results don't predict real ones. **Charles is not financial advice and promises nothing.**

## Commands

| Command | Does |
|---|---|
| `charles-start` · `charles-stop` · `charles-restart` · `charles-status` · `charles-logs` | Control and watch the bot |
| `charles-login` | Print a one-time link to log in to the dashboard |
| `charles-keys` | Add or change your keys (they stay in `.env` on your server) |
| `charles-pause` · `charles-resume` | Hold all orders, or start again |
| `charles-update` · `charles-restore` | Install a new version, or go back to an earlier one |
| `charles-addon` | Turn crypto or Kalshi on or off |
| `charles-copy <id>` | Copy settings from the leaderboard |
| `charles-chat --cap <dollars>` | Set the chat's monthly cap |

## For developers

Python, with a standard-library web server and plain HTML in `web/`.

| File | What it is |
|---|---|
| `main.py`, `rebalancer.py`, `allocator.py` | The schedule, the daily check, and the plan's rules as pure functions |
| `backtest_core.py`, `evaluate.py` | The simulation, and a monthly check of one-step changes to the plan |
| `alpaca_executor.py`, `risk_manager.py`, `news_veto.py` | The paper account, the loss limit, the headline check |
| `webdash.py`, `auth.py`, `control.py`, `updates.py`, `chat.py` | The dashboard, its login, its requests to the root job, updates, the chat |
| `crypto_*.py`, `kalshi/` | The add-ons |
| `leaderboard/` | The board's server, its simulator and the client ([docs/leaderboard.md](docs/leaderboard.md)) |

Run the tests with `python -m unittest discover -s tests -t tests`. Releases are explained in [docs/releases.md](docs/releases.md).
