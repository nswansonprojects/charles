# Charles

A simple, free investing bot for your own server, with all of its code public. It trades **paper money linked to real market data**
through a free Alpaca paper account, and you watch it from your phone. No subscription, and your keys and data stay on
a server you own. Live trading (stocks only, in your own Alpaca live account) stays off unless you turn it on yourself.

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

- **Home**: how each bot is doing (practice money unless you turned on live trading), Pause, and Get started on a new install.
- **Stocks**: holdings and a feed of what the bot did and the news about what it holds. **Plan**: sliders, a test on past prices, Undo.
- **Leaderboard**: settings people chose to share, each run forward by the leaderboard's server (nobody's own results are accepted). Copy takes two taps, and monthly challenges are opt-in.
- **Updates**: a version history with Restore. Anything that changes how it trades shows old → new and asks first. Software updates never install by themselves.
- **Self-Tune**: on unless you turn it off. Each month it tests small changes to the plan's numbers on past prices, and it may make one passing change a quarter by itself, on paper money only (while live trading is on, it only shows its results). Every change shows on the dashboard and can be undone.

**Add-ons**, off by default: `charles-addon on crypto` (a $1,000 paper book of bitcoin and ether, Coinbase's public prices) and
`charles-addon on kalshi` (a paper-only prediction-market bot; see [kalshi/README.md](kalshi/README.md)).

## Simulated results

`backtest_core.py` runs the same decision code as the live bot on daily history, with slippage, dividends and fund fees.

| $1,000, Jul 2007 to Oct 2026 (hypothetical) | A year, on average | Worst drop |
|---|---|---|
| The plan, with sector funds as its picks | +11.7% | −35% |
| SPY, buy and hold | +10.9% | −55% |

These are hypothetical results from a simulation, not real trading, and the rules were written knowing how these years
went. They have limits that real results don't; see section 5 of the [Risk Disclosure](RISK.md). Past and simulated
results don't predict future results. **Charles is software, not investment advice, and promises nothing.**

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

## Change it with Claude Code

Charles is plain Python on your own server, so you can change anything about it. The easiest way is
[Claude Code](https://www.anthropic.com/claude-code): describe the change in plain words, and it reads the code, makes the
change and runs the tests. It needs a Claude subscription or Anthropic API credit (on your own bill), and a server with 4 GB
of memory is comfortable.

```
curl -fsSL https://claude.ai/install.sh | bash
exec bash
cd ~/charles
claude
```

Then ask things like:

- *Why did Charles pick these stocks this month?*
- *Backtest the plan with 6 picks instead of 4.*
- *Add a card to the dashboard that shows my trades by month.*
- *The dashboard won't load. Find out why and fix it.*

The repo's `CLAUDE.md` tells Claude Code how Charles fits together and the rules to keep: practice money first, keys
stay in `.env`, run the tests, keep your changes on a git branch so `charles-update` still works. Read what it changed
before you keep it.

## Legal

Charles is free software under its [Terms of Service and License Agreement](LICENSE.md), which you accept when you install
it. Read the [Risk Disclosure](RISK.md) before trading real money, and the [Privacy Policy](PRIVACY.md) for what the website,
the leaderboard and the paid setup collect (very little). Charles is a tool: we aren't a broker, an investment adviser or a
fiduciary, we never hold your money or your keys, and nothing here is a recommendation to buy or sell anything.
Questions, security reports or legal notices: support@charles-bot.xyz.

## For developers

Python, with a standard-library web server and plain HTML in `web/`.

| File | What it is |
|---|---|
| `main.py`, `rebalancer.py`, `allocator.py` | The schedule, the daily check, and the plan's rules as pure functions |
| `backtest_core.py`, `evaluate.py` | The simulation, and a monthly check of one-step changes to the plan |
| `alpaca_executor.py`, `risk_manager.py`, `news_veto.py` | The paper account, the loss limit, the headline check |
| `webdash.py`, `auth.py`, `control.py`, `updates.py` | The dashboard, its login, its requests to the root job, updates |
| `stock_settings.py`, `crypto_settings.py` | Checking, applying and undoing a settings change (the sliders, Copy, self-tune) |
| `crypto_*.py`, `kalshi/` | The add-ons |
| `leaderboard/` | The board's server, its simulator and the client ([docs/leaderboard.md](docs/leaderboard.md)) |

Run the tests with `python -m unittest discover -s tests -t tests`. Releases are explained in [docs/releases.md](docs/releases.md).
