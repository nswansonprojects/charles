# Leaderboard

Users can share a bot's settings and copy other people's. Ranked by % return over this week, this month
or all time, with worst drop and the benchmark (SPY / bitcoin) beside it.

## Trust model
- **Nobody's own results are accepted.** A submission is only a config. `leaderboard/sim.py` runs every
  config forward from the day after it was submitted, with the bots' own rules (`backtest_core.simulate`,
  `crypto_backtest.simulate`) on public prices, from $10,000 of simulated money. So results can't be faked,
  back-dated or cherry-picked, and different account sizes don't matter.
- Ranked after 20 days of results ("warming up" before). Resubmitting or withdrawing retires an entry and
  its record stays visible, so a bad run can't be hidden. An identical config can be on the board only once.
- **Uploaders:** each install signs with its own Ed25519 key (`data/leaderboard/identity.pem`, 600, made on
  first use). No accounts and no email. Anonymous by default ("Anonymous · <6 hex>"); a name is optional,
  3–20 chars, filtered, and first-come per key. Only settings + name are sent: no balances, keys, holdings, trades.
- **Downloaders:** `charles-copy <id>` accepts a config only if it is signed by the server key pinned in
  `leaderboard/server_pubkey.txt`, matches the id and hash it asked for, and passes the strict schema
  (`common.validate_settings`: known keys, exact types, in range; rejected, never clamped). It shows the
  diff, asks, backs up the old file, writes strategy.json / crypto.json. No restart and no code runs.
  The site only ever shows `charles-copy lb-xxxxxx`, never anything user-written.
- **Server:** signed requests only (±5 min clock window), 4 KB body cap, per-address rate limits
  (5 submits/hour), max 2,000 active entries per bot so the nightly run stays small.

## Where it runs
The real board answers at charles-bot.xyz/api/: a loopback service behind nginx. Its private key is kept off the code. Installs pin its public half in
`server_pubkey.txt` and refuse any config it didn't sign. Kalshi needs its own simulator before it can have a board.

## Monthly challenges (leaderboard/challenges.py)

One theme a month, with rules the server enforces. Entries close when the month starts, so everyone runs the same days. The nightly
sim settles them. Rank = the share of 1,000 random portfolios, built under the same rules and seeded by the challenge's id, that the
entry beat; the entry's long backtest shows next to it. One entry per key, at most 3 per address (a salted hash, forgotten when the
month starts), new keys are marked, no prizes. `/api/challenges/<id>/results.csv` is the open data: no names, no keys.
Not built yet: a one-sector theme (the config schema has no sector field) and an open-code division (it needs a sandbox).
Made-up preview entries: `python -m leaderboard.challenges --seed-demo 2026-10`, `--clear-demo` removes them.
