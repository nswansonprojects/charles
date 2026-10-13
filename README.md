# Charles — self-hosted AI paper-trading bot

Charles trades US stocks on an **Alpaca paper account**. No real money is involved:
the Alpaca client is hard-coded to `paper=True`. An AI model (Groq, `openai/gpt-oss-20b`)
makes the buy/sell calls. Rule-based safety checks, position sizing and stop-losses around
it decide whether and how much to trade. It runs 24/7 as a systemd service. A mobile-friendly
dashboard (`webdash.py`) runs on port 8502, and Telegram alerts are optional.

Website and step-by-step guide: [charles-bot.xyz](https://charles-bot.xyz)

## Install (Ubuntu server)

```
curl -fsSL https://charles-bot.xyz/install.sh -o install.sh && bash install.sh
```

The installer clones this repo to `/root/charles`, creates a Python 3.11 conda env, installs
the requirements and sets up two services: `charles` (the bot) and `charles-web` (the dashboard).
Then:

1. Add your keys: `charles-keys`. You need [Alpaca](https://alpaca.markets) paper keys and a
   free [Groq](https://console.groq.com) key. A [Telegram bot](https://core.telegram.org/bots)
   token and chat ID are optional.
2. Start the bot: `charles-start`
3. Open the dashboard: `http://<server-ip>:8502`

The first start can take a few minutes while it loads the sentiment model.

### Manual install

```
git clone https://github.com/nswansonprojects/charles.git && cd charles
pip install -r requirements.txt          # Python 3.11
cp .env.example .env                     # then fill in your keys
python main.py                           # the bot
python webdash.py                        # the dashboard, in a second terminal
```

## A trading day (times are US Eastern)

| Time | What happens |
|------|--------------|
| 8:45 | Morning routine: screener picks the day's watchlist, market regime check, earnings and time-based exits |
| 9:00–15:30, every 30 min | Trading cycle: prices + news + sentiment → AI decision per stock → pre-trade checks → order |
| 9:00–16:45, every 15 min | Stop checks: trailing stops, fixed stops, broker stop orders kept in sync |
| 13:00 | Mid-day screener refresh |
| 16:30 | Daily summary on Telegram |
| every hour | News refresh and heartbeat message |

## How a trade is decided

1. **AI decision** (`brain.py`): BUY / SELL / HOLD with a confidence score. When Groq is
   unavailable, a rule signal is used, but it is stamped 55% confidence, so it never trades
   (on purpose: the rule signal has no track record).
2. **Pre-trade checks** (`alpaca_executor.execute_decision`, `market_filter.should_trade`).
   A buy needs all of:
   - confidence of at least 60%, plus the regime's boost (ranging +5, volatile +15);
     no new buys at all in a bear regime;
   - the daily-loss kill switch not tripped;
   - no earnings within 3 days, no 4-hour cooldown on the symbol;
   - SPY not in a downtrend; no macro-event blackout; not in the first 30 minutes or the
     last 15 minutes of the session.

   Sells and stops are never blocked by these checks.
3. **Sizing**: 10–15% of the account, scaled by confidence, then reduced in weak regimes
   and during drawdowns from the peak. At most 4 positions.
4. **Exits**: every position gets a trailing stop (2× ATR, never wider than the 5% stop)
   and a stop order at the broker. There's a take-profit at +15%, a profit lock that sells
   half at +4%, and a time exit for flat trades after 7 days. The AI may sell a holding only
   at 75%+ confidence.

## Extras

- **Options sleeve** (`options_bot.py`): when Charles buys a stock, it also buys one call
  debit spread on it, 2–6 weeks out (at most $60 per trade, 20% of the account, 3 open).
  Take profit at half the maximum, cut at half the debit lost, closed 5 days before expiry.
  Needs options enabled on the Alpaca paper account; without that it just logs a warning.
- **Opening range breakout** (`orb.py`, off unless `ORB_ENABLED=true` in `.env`): one intraday
  QQQ trade a day on a breakout of the first 5 minutes, stop at the range low, flat by 15:55.
  The backtest (`backtest_orb.py`) found **no edge** at the defaults; it is an experiment.
  The dashboard has an on/off button for it.
- **Shadow books** (`shadow.py`, `shadow_daily.py`): simulated $1,000 portfolios that test
  rule changes and other strategies on the same live data, without placing orders. The
  dashboard compares them.
- **Kalshi paper bot** (`kalshi/`, separate program): paper-bets a pretend $1,000 on Kalshi
  prediction markets (weather, sports, inflation, longshots) using public data only — no Kalshi
  account or key. Runs on its own every 15 minutes; when its dashboard is running, Charles's
  dashboard gets a **Kalshi** tab. Setup: [kalshi/README.md](kalshi/README.md).

## Settings — where things live

| What | Where |
|------|-------|
| Strategy and sizing (position size, stops, confidence bars, cooldown…) | `strategy.json`, clamped to the limits in `strategy_settings.py` |
| Everything else (news feeds, schedule, Groq budget, daily-loss limit) | `config.py` |
| API keys | `.env` (never commit it). Update with `charles-keys`. |

Restart after a change: `charles-restart`.

## Commands (installed by install.sh)

| Command | Does |
|---------|------|
| `charles-start`, `charles-stop`, `charles-restart` | Control the bot |
| `charles-status` / `charles-logs` | Service status / follow the log |
| `charles-keys` | Update API keys (then restarts) |
| `charles-update` | Pull the latest version from this repo and restart |

## Files

```
main.py               startup, scheduler, cycles, heartbeat
brain.py              AI prompt + decision, rule fallback, decision log
collector.py          prices, indicators, news (RSS feeds) + FinBERT sentiment
screener.py           daily watchlist from the stock universe
market_filter.py      pre-trade checks, earnings, cooldowns, confidence-based sizing
risk_manager.py       ATR, trailing stops, daily-loss kill switch, drawdown sizing
alpaca_executor.py    orders, broker stop orders, stop checks, trade log
regime.py             market regime (trending bull/bear, ranging, volatile)
options_bot.py        call-spread sleeve
orb.py                opening range breakout sleeve (off unless ORB_ENABLED)
shadow.py, shadow_daily.py   simulated test portfolios
strategy_settings.py  loads strategy.json within hard limits
state_io.py           crash-safe (atomic) JSON state writes
alerts.py             Telegram messages
webdash.py, web/      dashboard (port 8502; set WEBDASH_PORT to change)
backtest_live.py, backtest_orb.py   backtests
update_keys.py, server_confirm.py, credential_manager.py   API key handling
tests/                unit tests: cd tests && python -m unittest discover -s . -t .
kalshi/               Kalshi paper bot (separate; see kalshi/README.md)
data/  logs/  reports/   runtime state and output (not in git)
```

## Notes

- **Paper trading only.** Nothing here places real-money orders.
- **The dashboard has no login.** Anyone who can reach port 8502 can see it and use the ORB
  on/off button; it can't place orders. Firewall the port if you want it private.
- Upgrading from the old version (Streamlit dashboard on 8501, setup wizard): your keys and
  `data/` are kept. Re-run the installer to set up the new `charles-web` service.
- Not financial advice. Use at your own risk.
