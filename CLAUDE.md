# Charles: notes for Claude Code

You're on the user's own server, in their copy of Charles: an open source investing bot that trades a free Alpaca
**paper** (practice) account by default. The user asks for changes in plain words; they may not be a programmer, so
explain what you changed and why in plain words too, and ask before anything risky.

## How it fits together

- `main.py` runs the schedule (service `charles`). Once a weekday, `rebalancer.py` checks the plan and places orders;
  `allocator.py` holds the plan's rules as pure functions.
- The plan's settings are in `strategy.json` (read through `strategy_settings.py`, within its LIMITS); the dashboard's
  sliders, Copy and self-tune change them through `stock_settings.py`, and `crypto_settings.py` does the same for crypto.
- `backtest_core.py` runs the same decision code on history; `evaluate.py` checks one-step changes each month.
- `news_veto.py` is the only AI in trading: before a new pick is bought, it can skip it for serious bad news (Groq key, optional).
- The dashboard is `webdash.py` (service `charles-web`, read-only) with pages in `web/`. Changes from the dashboard are
  requests that `control.py` and `updates.py` (service `charles-updater`) carry out as root.
- Add-ons: `crypto_*.py` (a paper crypto book) and `kalshi/` (a paper prediction-market bot). Records live in `data/`,
  logs in `logs/`.

## Commands

- Python: `~/miniconda3/envs/charles/bin/python`
- Tests (run after every change; all must pass): `cd ~/charles/tests && ~/miniconda3/envs/charles/bin/python -m unittest discover -s . -t .`
- See what the plan would do today without placing an order: `python rebalancer.py --dry-run`
- Backtest after changing a rule: `python backtest_core.py`
- `charles-restart` applies code changes to the bot; `systemctl restart charles-web` to the dashboard. `charles-logs`
  shows the log. Check the log after a restart.

## Rules

- **Practice money first.** Never turn on live trading (`charles-live`), never edit `data/trading_mode.json`, and never
  place, cancel or close orders yourself. If the user wants a change to how Charles trades, make it, backtest it, show
  them the result and let it run on the paper account before they think about real money.
- **Keys stay secret.** Never read, print or edit `.env`. The user adds or changes keys with `charles-keys`, in their own
  terminal; never ask them to paste a key into this conversation.
- **Don't delete anything in `data/` or `logs/`**: they're the bot's history.
- **Keep the user's changes on a git branch** (`git checkout -b my-changes`, then commit). `charles-update` refuses to run
  while code has uncommitted changes, and an update moves the code to the new release: afterwards, offer to bring their
  branch back on top of it (`git rebase` or `git merge`), and run the tests again.
- Settings changes go through `stock_settings.py` / `crypto_settings.py`, so they're checked and can be undone on the
  dashboard. Keep the daily loss limit (`MAX_DAILY_LOSS`) as the user set it unless they ask.
- Charles is software, not investment advice. Don't promise returns, and describe backtests as hypothetical.
