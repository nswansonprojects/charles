"""
alpaca_executor.py — the Alpaca paper account: read it, and place the rebalancer's orders.

Orders are notional (dollar) market orders, so fractional shares work on a small account. Every
order the bot places carries a client_order_id starting with ORDER_TAG. Option positions are left
out of the stock positions on purpose (the retired options sleeve never opened one, but a leg must
never be sold as a stock).
"""

import json
import logging
import os
import time
from datetime import datetime, timezone

import config
import state_io
import credential_manager as sec

log = logging.getLogger(__name__)
TRADE_LOG_FILE = os.path.join(os.path.dirname(__file__), "data", "trades.json")
ORDER_TAG = "charles-reb-"
STOP_TAG = "charles-stop-"            # broker stops of the old rules; cancelled before a sale
_FINAL = ("canceled", "filled", "expired", "rejected")


def _client():
    from alpaca.trading.client import TradingClient
    # Paper only, on purpose. Real money needs live keys and a deliberate change here.
    return TradingClient(sec.alpaca_key(), sec.alpaca_secret(), paper=True)


def _is_option(p) -> bool:
    return str(getattr(getattr(p, "asset_class", None), "value", getattr(p, "asset_class", ""))) == "us_option"


def _stock_positions(c) -> list:
    return [p for p in c.get_all_positions() if not _is_option(p)]


def market_open(c=None) -> bool:
    try:
        return bool((c or _client()).get_clock().is_open)
    except Exception as e:
        log.warning(f"Market clock check failed: {e}")
        return False


def get_account_summary() -> dict:
    try:
        c = _client()
        account = c.get_account()
        positions = _stock_positions(c)
        total_val = float(account.portfolio_value)
        start = config.VIRTUAL_CASH
        pos_list = [{
            "symbol":         p.symbol,
            "shares":         float(p.qty),
            "avg_price":      float(p.avg_entry_price),
            "current_price":  float(p.current_price),
            "market_value":   float(p.market_value),
            "unrealized_pnl": float(p.unrealized_pl),
            "unrealized_pct": float(p.unrealized_plpc) * 100,
            "day_change":     round(float(p.market_value) - float(p.lastday_price) * float(p.qty), 2)
                              if getattr(p, "lastday_price", None) else None,
        } for p in positions]
        return {
            "total_value":      round(total_val, 2),
            "cash":             round(float(account.cash), 2),
            "invested":         round(total_val - float(account.cash), 2),
            "total_return":     round(total_val - start, 2),
            "total_return_pct": round((total_val - start) / start * 100, 2),
            "day_change":       round(float(account.equity) - float(account.last_equity), 2),
            "open_positions":   len(positions),
            "positions":        pos_list,
        }
    except Exception as e:
        log.error(f"Account fetch failed: {e}")
        return {}


# ── Orders ────────────────────────────────────────────────────────────────────

def _wait_final(c, order_id, timeout: float) -> object | None:
    """Poll an order until it reaches a final status or the timeout. Returns the last order seen."""
    o, deadline = None, time.time() + timeout
    while time.time() < deadline:
        o = c.get_order_by_id(order_id)
        if str(getattr(o.status, "value", o.status)) in _FINAL:
            break
        time.sleep(0.5)
    return o


def _cancel_old_stops(c, symbol: str):
    """The old rules kept a DAY stop order per position; it holds the shares, so a sale would be
    rejected while it's open. They expire at each close and nothing re-places them now."""
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus
    for o in c.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol], limit=50)):
        if (o.client_order_id or "").startswith(STOP_TAG):
            try:
                c.cancel_order_by_id(o.id)
            except Exception as e:
                log.warning(f"  ⚠️  Cancel old stop {symbol} failed: {e}")
            _wait_final(c, o.id, 5)


def _tag(symbol: str, side: str) -> str:
    return f"{ORDER_TAG}{side}-{symbol}-{int(time.time() * 1000) % 10**10}"


def sell(c, symbol: str, amount: float | None) -> dict | None:
    """Sell `amount` dollars of `symbol`, or all of it (amount None). Waits up to 30 s for the fill.
    Returns {"order_id", "status", "value", "price", "qty", "pnl", "pnl_pct"} or None."""
    from alpaca.trading.requests import MarketOrderRequest, ClosePositionRequest
    from alpaca.trading.enums import OrderSide, TimeInForce
    try:
        pos = c.get_open_position(symbol)
    except Exception:
        log.info(f"  📭 {symbol}: nothing to sell")
        return None
    qty_held, mv = float(pos.qty), float(pos.market_value)
    _cancel_old_stops(c, symbol)
    if amount is None or amount >= mv * 0.995:
        order = c.close_position(symbol)
        frac = 1.0
    else:
        order = c.submit_order(MarketOrderRequest(symbol=symbol, notional=round(amount, 2), side=OrderSide.SELL,
                                                  time_in_force=TimeInForce.DAY,
                                                  client_order_id=_tag(symbol, "sell")))
        frac = amount / mv if mv else 0
    o = _wait_final(c, order.id, 30) or order
    status = str(getattr(o.status, "value", o.status))
    price = float(o.filled_avg_price) if getattr(o, "filled_avg_price", None) else float(pos.current_price)
    qty = float(o.filled_qty) if getattr(o, "filled_qty", None) else qty_held * frac
    entry = float(pos.avg_entry_price)
    return {"order_id": str(order.id), "status": status, "value": round(price * qty, 2), "price": round(price, 4),
            "qty": qty, "pnl": round((price - entry) * qty, 2), "pnl_pct": round((price / entry - 1) * 100, 2)}


def buy(c, symbol: str, amount: float) -> dict | None:
    """Buy `amount` dollars of `symbol` (notional market order). Waits up to 30 s for the fill."""
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce
    order = c.submit_order(MarketOrderRequest(symbol=symbol, notional=round(amount, 2), side=OrderSide.BUY,
                                              time_in_force=TimeInForce.DAY, client_order_id=_tag(symbol, "buy")))
    o = _wait_final(c, order.id, 30) or order
    status = str(getattr(o.status, "value", o.status))
    price = float(o.filled_avg_price) if getattr(o, "filled_avg_price", None) else None
    return {"order_id": str(order.id), "status": status, "value": round(amount, 2), "price": price,
            "qty": float(o.filled_qty) if getattr(o, "filled_qty", None) else None}


# ── Trade log (data/trades.json; the dashboard's Activity tab) ────────────────

def _load_trades() -> list:
    if os.path.exists(TRADE_LOG_FILE):
        with open(TRADE_LOG_FILE) as f:
            return json.load(f)
    return []


def _save_trades(trades):
    state_io.write_json(TRADE_LOG_FILE, trades, indent=2)


def log_trade(record: dict):
    trades = _load_trades()
    trades.append({"timestamp": datetime.now(timezone.utc).isoformat(), **record})
    _save_trades(trades)
