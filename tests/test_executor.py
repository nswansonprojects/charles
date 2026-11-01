"""alpaca_executor: what gets logged for an order that hasn't filled within the 30 s wait."""
from types import SimpleNamespace

from base import BotTestCase

import alpaca_executor as ex


class UnfilledOrders(BotTestCase):
    def setUp(self):
        super().setUp()
        pending = SimpleNamespace(id="o1", status="accepted", filled_qty="0", filled_avg_price=None)
        self.patch(ex, "_wait_final", lambda c, order_id, timeout: pending)     # no 30 s wait in a test
        self.patch(ex, "_cancel_old_stops", lambda c, symbol: None)
        pos = SimpleNamespace(qty="2", market_value="100.00", current_price="50.00", avg_entry_price="40.00")
        self.client = SimpleNamespace(get_open_position=lambda sym: pos, close_position=lambda sym: pending,
                                      submit_order=lambda req: pending)

    def test_unfilled_sale_is_logged_at_the_expected_size(self):
        # Alpaca reports filled_qty "0" (a non-empty string) for an order that hasn't filled yet
        r = ex.sell(self.client, "XOM", None)
        self.assertEqual((r["status"], r["qty"], r["value"], r["pnl"]), ("accepted", 2.0, 100.0, 20.0))

    def test_unfilled_buy_has_no_quantity_yet(self):
        r = ex.buy(self.client, "SPY", 50.0)
        self.assertEqual((r["status"], r["qty"], r["value"]), ("accepted", None, 50.0))
