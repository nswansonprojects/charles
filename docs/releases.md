# Making a release

Releases are git tags on the public repo. `updates.py` reads them, and `CHANGELOG.json` at the newest tag says what
each one changed. The owner decides when a release goes out; the check-up and dev session never make one.

1. Update the public repo (the usual snapshot).
2. Add an entry at the top of `releases` in `CHANGELOG.json`: the version, the date, one line per change in plain
   English, and `kind`.
   - `fix`: it doesn't change how Charles trades.
   - `rule`: it does, or might. **When in doubt, `rule`.**
3. Commit, then `git tag v1.4.2 && git push origin master v1.4.2`.

**Never move or delete a tag.** Copies already on it wouldn't follow: `git fetch` refuses to move a tag it has.
Fix a bad release by making a newer one.

What the dashboard does with it:
- A release with no `CHANGELOG.json` entry counts as a rule change.
- Whatever the changelog says, moving across a change to a trading file (`RULE_FILES` in updates.py) or to a value in
  `strategy.json` needs the confirm screen.
- The confirm screen lists the settings old → new, the trading files that change, and the backtest numbers from
  `docs/backtest.json` at both versions, so re-run the backtest before a release that changes a rule.
- Settings the user changed stay as they are. `data/` is never touched.
- The owner's server runs the private repo, so the card says "updates through git" and has no buttons.

To test the flow without touching the public repo, point `CHARLES_RELEASE_REPO` at a local bare repo.
