"""
Shared test setup: every state file is redirected to a temp folder, Telegram
and the network are stubbed, and a guard makes any attempt to open a real
data/, logs/ or .env file fail the test instead of touching it.
"""
import builtins
import os
import shutil
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config            # noqa: E402
import equity_tracker    # noqa: E402
import market_filter     # noqa: E402
import risk_manager      # noqa: E402

_PROTECTED = (os.path.join(ROOT, "data") + os.sep, os.path.join(ROOT, "logs") + os.sep,
              os.path.join(ROOT, ".env"))
_real_open, _real_replace, _real_os_open = builtins.open, os.replace, os.open


def _check(path):
    p = os.path.abspath(str(path))
    if p.startswith(_PROTECTED) or p == _PROTECTED[2]:
        raise AssertionError(f"test tried to touch a real bot file: {p}")


def _guarded_open(file, *a, **k):
    if isinstance(file, (str, bytes, os.PathLike)):
        _check(os.fsdecode(file))
    return _real_open(file, *a, **k)


def _guarded_os_open(path, *a, **k):
    _check(os.fsdecode(path))
    return _real_os_open(path, *a, **k)


def _guarded_replace(src, dst, *a, **k):
    _check(os.fsdecode(src)); _check(os.fsdecode(dst))
    return _real_replace(src, dst, *a, **k)


class FakeAlerts(types.ModuleType):
    """Stands in for alerts.py: records calls, never sends anything."""
    def __init__(self):
        super().__init__("alerts")
        self.calls = []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: self.calls.append((name, a, k))


_CONFIG_KEYS = ("MIN_CONFIDENCE", "MIN_CONFIDENCE_FLOOR", "MIN_POSITION_SIZE_PCT",
                "MAX_POSITION_SIZE_PCT", "MAX_OPEN_POSITIONS", "STOP_LOSS_PCT",
                "TAKE_PROFIT_PCT", "TRAILING_STOP_ATR_MULT", "TRADE_COOLDOWN_HOURS",
                "MAX_DAILY_LOSS", "EARNINGS_BLACKOUT_DAYS", "LLM_SELL_MIN_CONFIDENCE")


class BotTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="charles-test-")
        self._saved = []

        def patch(obj, name, value):
            self._saved.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)
        self.patch = patch

        # guard real files
        patch(builtins, "open", _guarded_open)
        patch(os, "open", _guarded_os_open)
        patch(os, "replace", _guarded_replace)
        # state files → temp
        patch(risk_manager, "DATA_DIR", self.tmp)
        patch(risk_manager, "TRAILING_FILE", os.path.join(self.tmp, "trailing_stops.json"))
        patch(risk_manager, "DAILY_LOSS_FILE", os.path.join(self.tmp, "daily_loss.json"))
        patch(market_filter, "COOLDOWN_FILE", os.path.join(self.tmp, "cooldowns.json"))
        patch(equity_tracker, "EQUITY_FILE", os.path.join(self.tmp, "equity_curve.json"))
        patch(equity_tracker, "PEAK_FILE", os.path.join(self.tmp, "peak_equity.json"))
        # no Telegram
        self.alerts = FakeAlerts()
        self._saved_alerts = sys.modules.get("alerts")
        sys.modules["alerts"] = self.alerts
        # known config values
        self._config = {k: getattr(config, k) for k in _CONFIG_KEYS if hasattr(config, k)}
        config.MIN_CONFIDENCE = 0.60
        config.MIN_CONFIDENCE_FLOOR = 0.60
        config.MIN_POSITION_SIZE_PCT = 0.10
        config.MAX_POSITION_SIZE_PCT = 0.15
        config.MAX_OPEN_POSITIONS = 4
        config.STOP_LOSS_PCT = 0.05
        config.TRAILING_STOP_ATR_MULT = 2.0
        config.PROFIT_LOCK_TRIGGER = 0.04
        config.PROFIT_LOCK_KEEP = 0.5
        config.TRADE_COOLDOWN_HOURS = 4
        config.MAX_DAILY_LOSS = 20.0
        config.EARNINGS_BLACKOUT_DAYS = 3

    def tearDown(self):
        for obj, name, value in reversed(self._saved):
            setattr(obj, name, value)
        if self._saved_alerts is not None:
            sys.modules["alerts"] = self._saved_alerts
        else:
            sys.modules.pop("alerts", None)
        for k, v in self._config.items():
            setattr(config, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)
