"""Fresh installs: returns are measured from the account's value when Charles first started (a new Alpaca
paper account holds $100,000, not config.VIRTUAL_CASH's $1,000), and the default daily loss limit is a
share of the account, so it fits any account size."""
import json
import os
from types import SimpleNamespace

from base import BotTestCase, config

import alpaca_executor as ex
import equity_tracker as et
import risk_manager as rm


class StartBalance(BotTestCase):
    def test_without_a_start_file_the_config_value_is_used(self):
        self.assertEqual(et.start_cash(), 1000.0)

    def test_fresh_install_measures_from_the_first_start(self):
        self.assertEqual(et.ensure_start(100_000.0), 100_000.0)
        self.assertEqual(et.start_cash(), 100_000.0)
        self.assertEqual(et.ensure_start(103_000.0), 100_000.0)      # written once, never moved
        with open(et.START_FILE) as f:
            self.assertEqual(json.load(f)["from"], "account value at the first start")

    def test_an_installs_own_balance_wins(self):
        self.assertEqual(et.ensure_start(1_070.55, balance=1000.0), 1000.0)    # the owner's launch flag
        self.assertEqual(et.start_cash(), 1000.0)

    def test_no_account_value_writes_nothing(self):
        self.assertEqual(et.ensure_start(None), 1000.0)
        self.assertFalse(os.path.exists(et.START_FILE))

    def test_account_summary_return_uses_the_start(self):
        acct = SimpleNamespace(portfolio_value="100250.00", cash="20000.00", equity="100250.00", last_equity="100100.00")
        client = SimpleNamespace(get_account=lambda: acct, get_all_positions=lambda: [])
        self.patch(ex, "_client", lambda: client)
        et.ensure_start(100_000.0)
        s = ex.get_account_summary()
        self.assertEqual((s["total_return"], s["total_return_pct"]), (250.0, 0.25))   # was +9,925%

    def test_equity_curve_points_use_the_start(self):
        et.ensure_start(100_000.0)
        et.record_snapshot(101_000.0)
        self.assertEqual(et.load_curve()[-1]["pct"], 1.0)
        self.assertEqual(et.peak_value(), 101_000.0)


class LossLimitFitsTheAccount(BotTestCase):
    def setUp(self):
        super().setUp()
        config.MAX_DAILY_LOSS = None          # no dollar amount set: the % default applies

    def test_two_percent_of_a_100k_account(self):
        rm.check_day_change(-25.0, 100_000.0)                 # $20 used to pause buys on a $100k account
        self.assertFalse(rm.is_trading_halted())
        rm.check_day_change(-2_100.0, 100_000.0)              # 2% of yesterday's $102,100 is $2,042
        self.assertTrue(rm.is_trading_halted())
        name, args, _ = self.alerts.calls[-1]
        self.assertEqual((name, args[1]), ("daily_loss_halted", 2042.0))
        self.assertEqual(rm.get_daily_stats()["limit"], 2042.0)

    def test_an_owners_dollar_limit_still_wins(self):
        config.MAX_DAILY_LOSS = 20.0
        rm.check_day_change(-20.5, 100_000.0)
        self.assertTrue(rm.is_trading_halted())

    def test_unknown_account_value_never_halts(self):
        rm.check_day_change(-500.0)
        self.assertFalse(rm.is_trading_halted())
