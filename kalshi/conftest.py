"""Every test gets its own scratch copies of the files the bot writes: a test must never touch data/
(2026-10-09 a sandbox run wrote to the real book; 2026-10-15 the first gates tests wrote levels.json)."""
import pytest

import betting
import gates
import predictions


@pytest.fixture(autouse=True)
def _scratch_data(tmp_path, monkeypatch):
    for mod, name, f in ((betting, "LEDGER", "bets.json"), (betting, "EQUITY", "bets_equity.jsonl"),
                         (betting, "LATEST", "bets_latest.json"), (predictions, "PRED", "predictions.json"),
                         (predictions, "GRADED", "predictions_graded.jsonl"), (gates, "STATE", "levels.json")):
        monkeypatch.setattr(mod, name, str(tmp_path / f))
