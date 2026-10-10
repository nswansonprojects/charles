"""Broker stop sync: moving a stop when the old order's cancel is slow (2026-10-01 HPE)."""
import time
import types

from base import BotTestCase

import alpaca_executor as ax
import risk_manager as rm
import state_io

HELD_ERR = ('{"available":"0","code":40310000,"existing_qty":"1.2","held_for_orders":"1.2",'
            '"message":"insufficient qty available for order (requested: 1.2, available: 0)"}')


class FakeAlpaca:
    """Just enough of TradingClient for _sync_broker_stops."""
    def __init__(self, cancel_polls=0, submit_errors=(), final_status="canceled"):
        self.orders = {}                      # id -> order
        self.submitted = []
        self.cancel_polls = cancel_polls      # status polls before a cancel becomes final
        self.submit_errors = list(submit_errors)
        self.final_status = final_status

    def add_stop(self, oid, symbol, qty, stop):
        self.orders[oid] = types.SimpleNamespace(
            id=oid, symbol=symbol, qty=str(qty), stop_price=str(stop),
            client_order_id=f"{ax.STOP_TAG}{symbol}-1", polls=None,
            status=types.SimpleNamespace(value="new"))

    def get_orders(self, req):
        return [o for o in self.orders.values() if o.status.value in ("new", "pending_cancel")]

    def cancel_order_by_id(self, oid):
        o = self.orders[oid]
        o.status.value, o.polls = "pending_cancel", self.cancel_polls

    def get_order_by_id(self, oid):
        o = self.orders[oid]
        if o.status.value == "pending_cancel":
            if o.polls <= 0:
                o.status.value = self.final_status
            o.polls -= 1
        return o

    def submit_order(self, req):
        if self.submit_errors:
            raise Exception(self.submit_errors.pop(0))
        self.submitted.append((req.symbol, float(req.qty), float(req.stop_price)))
        return types.SimpleNamespace(id="new-order")


def position(symbol="HPE", qty=1.2, avg=60.0):
    return types.SimpleNamespace(symbol=symbol, qty=str(qty), avg_entry_price=str(avg))


class SyncBrokerStops(BotTestCase):
    def setUp(self):
        super().setUp()
        self.patch(time, "sleep", lambda s: None)
        state_io.write_json(rm.TRAILING_FILE, {"HPE": {"entry": 60.0, "highest": 65.58,
                                                       "stop": 62.32, "atr": 1.0, "dist": 3.26}})

    def test_normal_move_first_try(self):
        c = FakeAlpaca()
        c.add_stop("old", "HPE", 1.2, 61.00)
        ax._sync_broker_stops(c, [position()])
        self.assertEqual(c.submitted, [("HPE", 1.2, 62.32)])
        self.assertEqual(c.orders["old"].status.value, "canceled")

    def test_slow_cancel_at_open_is_retried(self):
        # this morning: cancel not final within 5s, first submit rejected as shares held
        c = FakeAlpaca(cancel_polls=30, submit_errors=[HELD_ERR])
        c.add_stop("old", "HPE", 1.2, 61.00)
        ax._sync_broker_stops(c, [position()])
        self.assertEqual(c.submitted, [("HPE", 1.2, 62.32)])

    def test_other_errors_not_retried(self):
        c = FakeAlpaca(submit_errors=["some other API error"])
        c.add_stop("old", "HPE", 1.2, 61.00)
        ax._sync_broker_stops(c, [position()])          # logged, not raised
        self.assertEqual(c.submitted, [])

    def test_no_retry_when_placing_first_stop(self):
        c = FakeAlpaca(submit_errors=[HELD_ERR])
        ax._sync_broker_stops(c, [position()])
        self.assertEqual(c.submitted, [])

    def test_old_stop_filled_while_cancelling_gets_no_new_order(self):
        c = FakeAlpaca(cancel_polls=30, submit_errors=[HELD_ERR], final_status="filled")
        c.add_stop("old", "HPE", 1.2, 61.00)
        ax._sync_broker_stops(c, [position()])
        self.assertEqual(c.submitted, [])

    def test_correct_stop_is_left_alone(self):
        c = FakeAlpaca()
        c.add_stop("ok", "HPE", 1.2, 62.32)
        ax._sync_broker_stops(c, [position()])
        self.assertEqual(c.submitted, [])
        self.assertEqual(c.orders["ok"].status.value, "new")
