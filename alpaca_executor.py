"""
alpaca_executor.py — v4: Live Alpaca paper trading.
New: short selling, inverse ETF hedging, trailing stop registration,
     daily loss kill switch integration, drawdown-aware sizing.
"""

import logging
import json
import os
from datetime import datetime, timezone

import config
import state_io
import credential_manager as sec

log = logging.getLogger(__name__)
TRADE_LOG_FILE = os.path.join(os.path.dirname(__file__), "data", "trades.json")


def _client():
    from alpaca.trading.client import TradingClient
    # Paper only, on purpose. Real money needs live keys and a deliberate change here.
    return TradingClient(sec.alpaca_key(), sec.alpaca_secret(), paper=True)


def _is_option(p) -> bool:
    return str(getattr(getattr(p, "asset_class", None), "value", getattr(p, "asset_class", ""))) == "us_option"


def _stock_positions(c) -> list:
    """Stock positions only. Option legs belong to options_bot.py, which manages them
    as spreads; stock stops, take-profits and time exits must never touch them. Shares the
    ORB sleeve holds (orb.py, its own stop/target/flatten) are left out the same way."""
    orb_held = _orb_held()
    return [p for p in c.get_all_positions() if not _is_option(p) and p.symbol not in orb_held]


def _orb_held() -> set:
    """Symbols the ORB sleeve owns right now; always empty while ORB_ENABLED is off."""
    if not getattr(config, "ORB_ENABLED", False):
        return set()
    try:
        import orb
        return orb.held_symbols()
    except Exception as e:
        log.warning(f"  ORB state unreadable ({e})")
        return set()


def get_account_summary() -> dict:
    try:
        c         = _client()
        account   = c.get_account()
        positions = _stock_positions(c)
        trades    = _load_trades()
        closed    = [t for t in trades if t["action"] in ("SELL","SHORT_COVER") and "pnl" in t]
        wins      = [t for t in closed if t["pnl"] > 0]
        total_val = float(account.portfolio_value)
        start     = config.VIRTUAL_CASH

        pos_list = [{
            "symbol":         p.symbol,
            "shares":         float(p.qty),
            "side":           p.side.value if hasattr(p.side,"value") else str(p.side),
            "avg_price":      float(p.avg_entry_price),
            "current_price":  float(p.current_price),
            "market_value":   float(p.market_value),
            "unrealized_pnl": float(p.unrealized_pl),
            "unrealized_pct": float(p.unrealized_plpc) * 100,
        } for p in positions]

        return {
            "total_value":      round(total_val, 2),
            "cash":             round(float(account.cash), 2),
            "invested":         round(total_val - float(account.cash), 2),
            "total_return":     round(total_val - start, 2),
            "total_return_pct": round((total_val - start) / start * 100, 2),
            "open_positions":   len(positions),
            "total_trades":     len(trades),
            "win_rate":         round(len(wins)/len(closed)*100, 1) if closed else 0,
            "positions":        pos_list,
            "trades":           trades[-20:],
            "buying_power":     round(float(account.buying_power), 2),
        }
    except Exception as e:
        log.error(f"Account fetch failed: {e}")
        return {}


def execute_decision(decision: dict) -> dict | None:
    import market_filter as mf
    import risk_manager  as rm

    try:
        import alerts as _alerts
    except Exception:
        _alerts = None

    action = decision.get("action","HOLD")
    if action == "HOLD":
        return None

    # ── Kill switch ──
    if rm.is_trading_halted():
        log.warning(f"  🚨 {decision['symbol']}: trading halted (daily loss limit)")
        return None

    if decision.get("confidence", 0) < config.MIN_CONFIDENCE:
        log.info(f"  🚫 {decision['symbol']}: {action} confidence "
                 f"{decision.get('confidence', 0):.0%} below minimum {config.MIN_CONFIDENCE:.0%}")
        return None

    # ── One strategy per symbol: ORB owns it today ──
    if action in ("BUY","SHORT") and decision["symbol"].replace("/","") in _orb_held():
        log.info(f"  🚫 {decision['symbol']}: held by the ORB sleeve today (strategy=orb) — skipping")
        return None

    # ── Pre-trade filters (BUY and SHORT) ──
    if action in ("BUY","SHORT"):
        ok, reason = mf.should_trade(decision)
        if not ok:
            log.info(f"  🚫 {decision['symbol']}: {reason}")
            return None

    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums    import OrderSide, TimeInForce
    from alpaca.common.exceptions import APIError

    c      = _client()
    symbol = decision["symbol"].replace("/","")
    price  = decision.get("price") or 0

    try:
        account   = c.get_account()
        bp        = float(account.buying_power)
        port_val  = float(account.portfolio_value)

        # Drawdown-aware sizing
        dd_mult   = rm.drawdown_size_multiplier(port_val)   # vs peak equity

        # Regime sizing
        try:
            from regime import regime_multipliers, detect_regime
            reg, _  = detect_regime()
            r_mult  = regime_multipliers(reg).get("size_mult", 1.0)
        except Exception:
            r_mult  = 1.0

        record = None

        # ── BUY (long) ────────────────────────────────────────────────────────
        if action == "BUY":
            size_pct  = mf.position_size_pct(decision["confidence"]) * dd_mult * r_mult
            spend     = min(port_val * size_pct, bp * 0.95)
            # Alpaca minimums: $10 for crypto, $1 for stocks
            min_order = 1.0   # stocks only
            if spend < min_order:
                log.info(f"  💸 {symbol}: spend ${spend:.2f} below Alpaca minimum ${min_order}")
                return None

            positions = _stock_positions(c)
            if len(positions) >= config.MAX_OPEN_POSITIONS and \
               symbol not in [p.symbol for p in positions]:
                log.info(f"  🚫 {symbol}: max positions reached")
                return None

            order = c.submit_order(MarketOrderRequest(
                symbol=symbol, notional=round(spend,2),
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
            ))
            log.info(f"  ✅ BUY  {symbol} ${spend:.2f} ({size_pct:.1%}) | {order.id}")

            # Register trailing stop
            if price > 0:
                rm.register_trailing_stop(symbol, price)

            # Broker stop as soon as the buy fills (else the next check_stops does it)
            try:
                import time
                for _ in range(10):
                    if str(getattr(c.get_order_by_id(order.id).status, "value", "")) == "filled":
                        _sync_broker_stops(c, [c.get_open_position(symbol)])
                        break
                    time.sleep(0.5)
            except Exception as e:
                log.warning(f"  ⚠️  {symbol}: broker stop not placed yet ({e})")

            record = {
                "timestamp":  datetime.now(timezone.utc).isoformat(),
                "symbol":     symbol, "action": "BUY",
                "value":      round(spend,2), "price": price,
                "size_pct":   round(size_pct,4), "order_id": str(order.id),
                "confidence": decision["confidence"],
                "reasoning":  decision["reasoning"],
                "source":     decision.get("source","llm"),
            }
            mf.record_trade_time(symbol)
            _alerts.trade_executed(record)

        # ── SHORT ─────────────────────────────────────────────────────────────
        elif action == "SHORT":
            # Check we don't already have a long in this symbol
            positions = _stock_positions(c)
            held = {p.symbol: p for p in positions}
            if symbol in held and float(held[symbol].qty) > 0:
                log.info(f"  ⏭  {symbol}: have long position, skipping short")
                return None

            size_pct = mf.position_size_pct(decision["confidence"]) * dd_mult * r_mult * 0.8
            spend    = min(port_val * size_pct, bp * 0.90)
            if spend < 1.0:
                return None

            order = c.submit_order(MarketOrderRequest(
                symbol=symbol, notional=round(spend,2),
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            ))
            log.info(f"  ✅ SHORT {symbol} ${spend:.2f} | {order.id}")

            record = {
                "timestamp":  datetime.now(timezone.utc).isoformat(),
                "symbol":     symbol, "action": "SHORT",
                "value":      round(spend,2), "price": price,
                "order_id":   str(order.id),
                "confidence": decision["confidence"],
                "reasoning":  decision["reasoning"],
                "source":     decision.get("source","llm"),
            }
            mf.record_trade_time(symbol)
            _alerts.trade_executed(record)

        # ── SELL (close long) ─────────────────────────────────────────────────
        elif action == "SELL":
            positions = _stock_positions(c)
            held = {p.symbol: p for p in positions}
            if symbol not in held:
                log.info(f"  📭 {symbol}: no position")
                return None

            pos      = held[symbol]
            proceeds = float(pos.market_value)
            pnl      = float(pos.unrealized_pl)
            pnl_pct  = float(pos.unrealized_plpc) * 100

            if not _close(c, symbol):
                log.info(f"  {symbol}: sold by broker stop instead of {decision.get('source','llm')}")
            rm.remove_trailing_stop(symbol)
            rm.record_daily_pnl(pnl)
            log.info(f"  ✅ SELL {symbol} ${proceeds:.2f} P&L ${pnl:+.2f} ({pnl_pct:+.1f}%)")

            record = {
                "timestamp":  datetime.now(timezone.utc).isoformat(),
                "symbol":     symbol, "action": "SELL",
                "value":      round(proceeds,2), "price": price,
                "pnl":        round(pnl,2), "pnl_pct": round(pnl_pct,2),
                "confidence": decision["confidence"],
                "reasoning":  decision["reasoning"],
                "source":     decision.get("source","llm"),
            }
            mf.record_trade_time(symbol)
            _alerts.trade_executed(record)

        if record:
            trades = _load_trades()
            trades.append(record)
            _save_trades(trades)
        return record

    except APIError as e:
        log.error(f"  ❌ Alpaca API error {symbol}: {e}")
        try:
            import alerts; alerts.error_alert(f"API error {symbol}: {e}")
        except Exception:
            pass
        return None
    except Exception as e:
        log.error(f"  ❌ Order failed {symbol}: {e}")
        return None


# ── Broker-side stops ─────────────────────────────────────────────────────────
# Fractional positions only allow DAY stop orders at Alpaca (no GTC, no trailing
# type), so check_stops keeps one tagged stop order per position at the current
# trailing-stop price. They expire at the close and are re-placed next morning.

STOP_TAG = "charles-stop-"


def _open_stop_orders(c) -> dict:
    """symbol → list of our open stop orders at the broker."""
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums    import QueryOrderStatus
    out = {}
    for o in c.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500)):
        if (o.client_order_id or "").startswith(STOP_TAG):
            out.setdefault(o.symbol, []).append(o)
    return out


_FINAL = ("canceled", "filled", "expired", "rejected")


def _wait_final(c, order_id, timeout: float) -> str:
    """Poll an order until it reaches a final status or timeout. Returns the last status."""
    import time
    status = ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = str(getattr(c.get_order_by_id(order_id).status, "value", ""))
        if status in _FINAL:
            break
        time.sleep(0.5)
    return status


def _cancel_and_wait(c, order_id, timeout: float = 5.0) -> str:
    """Cancel an order and wait for a final status. Returns that status."""
    try:
        c.cancel_order_by_id(order_id)
    except Exception as e:
        log.warning(f"  ⚠️  Cancel {order_id} failed: {e}")
    return _wait_final(c, order_id, timeout)


def _shares_still_held(err: Exception) -> bool:
    """Alpaca's 'insufficient qty available' (40310000): an old order still holds the shares."""
    text = str(err)
    return "40310000" in text or "insufficient qty" in text


def _close(c, symbol: str) -> bool:
    """
    Close a position after cancelling its broker stop, whose hold on the
    shares would otherwise make Alpaca reject the sell. Returns False if the
    broker stop filled first; check_stops records that fill.
    """
    for o in _open_stop_orders(c).get(symbol, []):
        if _cancel_and_wait(c, o.id) == "filled":
            log.info(f"  🛑 {symbol}: broker stop filled before close")
            return False
    c.close_position(symbol)
    return True


def _submit_stop(c, symbol: str, qty: float, stop_price: float):
    import time
    from alpaca.trading.requests import StopOrderRequest
    from alpaca.trading.enums    import OrderSide, TimeInForce
    return c.submit_order(StopOrderRequest(
        symbol=symbol, qty=qty, side=OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
        stop_price=round(stop_price, 2),
        client_order_id=f"{STOP_TAG}{symbol}-{int(time.time())}",
    ))


def _sync_broker_stops(c, positions):
    """Make each long position have exactly one broker stop at its trailing stop."""
    import risk_manager as rm
    trailing = rm.get_trailing_stops()
    orders   = _open_stop_orders(c)

    for p in positions:
        symbol = p.symbol
        qty    = float(p.qty)
        if qty <= 0:
            continue
        ts     = trailing.get(symbol)
        target = ts["stop"] if ts else float(p.avg_entry_price) * (1 - config.STOP_LOSS_PCT)
        target = round(target, 2)
        if ts and ts.get("avg_entry") != float(p.avg_entry_price):
            rm.set_trailing_field(symbol, "avg_entry", float(p.avg_entry_price))

        existing = orders.get(symbol, [])
        keep = None
        for o in existing:
            if keep is None and abs(float(o.qty) - qty) < 1e-9 \
               and abs(float(o.stop_price) - target) < 0.005:
                keep = o
            elif _cancel_and_wait(c, o.id) == "filled":
                keep = "filled"
        if keep is not None:
            continue   # correct order already resting, or it just filled

        verb = "moved" if existing else "placed"
        try:
            try:
                _submit_stop(c, symbol, qty, target)
            except Exception as e:
                # At the open a cancel can take longer than _cancel_and_wait's 5s; the
                # old order still holds the shares, so the new stop is rejected. Wait
                # for the old orders to finish cancelling, then retry once.
                if not (existing and _shares_still_held(e)):
                    raise
                log.warning(f"  ⏳ {symbol}: old stop still cancelling — waiting to retry")
                for o in existing:
                    if _wait_final(c, o.id, timeout=15.0) == "filled":
                        log.info(f"  🛑 {symbol}: old broker stop filled while cancelling")
                        break
                else:
                    _submit_stop(c, symbol, qty, target)
                    verb += " (after retry)"
                    log.info(f"  🛡️  Broker stop {verb}: {symbol} {qty:g} sh @ ${target:.2f}")
                continue
            log.info(f"  🛡️  Broker stop {verb}: {symbol} {qty:g} sh @ ${target:.2f}")
        except Exception as e:
            log.error(f"  ❌ Broker stop failed {symbol}: {e} (in-process stop still active)")


def _record_broker_fills(c, positions, _alerts=None):
    """
    Book stop orders that filled at the broker since the last check. A fill is
    new while the symbol still has trailing-stop state but no position.
    """
    import risk_manager  as rm
    import market_filter as mf
    from datetime import timedelta
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums    import QueryOrderStatus

    trailing = rm.get_trailing_stops()
    held     = {p.symbol for p in positions}
    gone     = [s for s in trailing if s not in held]
    if not gone:
        return

    after  = datetime.now(timezone.utc) - timedelta(days=4)
    closed = c.get_orders(GetOrdersRequest(status=QueryOrderStatus.CLOSED,
                                           symbols=gone, after=after, limit=500))
    for symbol in gone:
        fills = [o for o in closed if o.symbol == symbol
                 and (o.client_order_id or "").startswith(STOP_TAG)
                 and str(getattr(o.status, "value", "")) == "filled"]
        if not fills:
            continue   # closed some other way; that path cleans up itself
        o      = max(fills, key=lambda x: x.filled_at)
        qty    = float(o.filled_qty)
        price  = float(o.filled_avg_price)
        entry  = trailing[symbol].get("avg_entry") or trailing[symbol]["entry"]
        pnl    = (price - entry) * qty
        pnl_pct = (price / entry - 1) * 100

        rm.remove_trailing_stop(symbol)
        rm.record_daily_pnl(pnl)
        mf.record_trade_time(symbol)
        trades = _load_trades()
        trades.append({
            "timestamp": o.filled_at.isoformat(),
            "symbol":    symbol, "action": "SELL",
            "value":     round(price * qty, 2), "price": round(price, 4),
            "pnl":       round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
            "reason":    "broker_stop", "source": "risk_management",
            "confidence":1.0, "reasoning": "Auto-closed: broker stop filled",
            "order_id":  str(o.id),
        })
        _save_trades(trades)
        log.info(f"  🛑 Broker stop filled: {symbol} {qty:g} sh @ ${price:.2f} "
                 f"P&L ${pnl:+.2f} ({pnl_pct:+.1f}%)")
        if _alerts:
            _alerts.stop_loss_hit(symbol, pnl_pct)


def check_stops():
    """
    Check all open positions:
      0. Book broker stops that filled since the last check
      1. Trailing stop triggered?
      2. Fixed stop-loss / take-profit?
      3. Keep a broker stop resting at each trailing stop
    """
    import risk_manager as rm
    try:
        import alerts as _alerts
    except Exception:
        _alerts = None

    import market_filter as mf

    try:
        c         = _client()
        positions = _stock_positions(c)

        # 0. Broker stops that filled since the last check
        try:
            _record_broker_fills(c, positions, _alerts)
        except Exception as e:
            log.error(f"  ❌ Broker fill check failed: {e}")

        # Snapshot each position's P&L path (peak, trough, give-back); never trades
        try:
            import position_tracker
            for d in position_tracker.update(positions):
                log.info(f"  📈 {d['symbol']} path closed: peak ${d['peak_pnl']:+.2f}, "
                         f"exit ${d['exit_pnl'] if d['exit_pnl'] is not None else 0:+.2f}, gave back ${d['gave_back'] or 0:.2f}")
        except Exception as e:
            log.warning(f"  Position path snapshot failed: {e}")

        if not positions:
            return

        current_prices = {p.symbol: float(p.current_price) for p in positions}

        # 1. Trailing stops
        stopped = rm.update_trailing_stops(current_prices)
        for symbol in stopped:
            try:
                reason = "trailing_stop" if _close(c, symbol) else "broker_stop"
                mf.record_trade_time(symbol)
                pos = next((p for p in positions if p.symbol == symbol), None)
                if pos:
                    pnl = float(pos.unrealized_pl)
                    rm.record_daily_pnl(pnl)
                    _log_close(symbol, pos, reason)
                    if _alerts:
                        _alerts.stop_loss_hit(symbol, float(pos.unrealized_plpc)*100)
                log.info(f"  🛑 Trailing stop closed: {symbol}")
            except Exception as e:
                log.error(f"  ❌ Trailing stop close failed {symbol}: {e}")

        # 2. Fixed stop-loss / take-profit
        for p in positions:
            if p.symbol in stopped:
                continue  # already closed by trailing stop above
            try:
                pct = float(p.unrealized_plpc) * 100
                if pct <= -(config.STOP_LOSS_PCT * 100):
                    log.info(f"  🛑 Fixed stop-loss: {p.symbol} at {pct:.1f}%")
                    reason = "stop_loss" if _close(c, p.symbol) else "broker_stop"
                    mf.record_trade_time(p.symbol)
                    rm.remove_trailing_stop(p.symbol)
                    rm.record_daily_pnl(float(p.unrealized_pl))
                    _log_close(p.symbol, p, reason)
                    if _alerts:
                        _alerts.stop_loss_hit(p.symbol, pct)

                elif pct >= (config.TAKE_PROFIT_PCT * 100):
                    log.info(f"  🎯 Take profit: {p.symbol} at {pct:.1f}%")
                    reason = "take_profit" if _close(c, p.symbol) else "broker_stop"
                    mf.record_trade_time(p.symbol)
                    rm.remove_trailing_stop(p.symbol)
                    rm.record_daily_pnl(float(p.unrealized_pl))
                    _log_close(p.symbol, p, reason)
                    if _alerts:
                        _alerts.take_profit_hit(p.symbol, pct)
            except Exception as e:
                log.error(f"  ❌ Fixed stop/take-profit failed {p.symbol}: {e}")

        # 3. Broker stops for whatever is still open
        try:
            _sync_broker_stops(c, _stock_positions(c))
        except Exception as e:
            log.error(f"  ❌ Broker stop sync failed: {e}")

    except Exception as e:
        log.error(f"Stop check failed: {e}")


def _log_close(symbol: str, p, reason: str):
    trades = _load_trades()
    trades.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol":    symbol, "action": "SELL",
        "value":     round(float(p.market_value),2),
        "price":     round(float(p.current_price),4),
        "pnl":       round(float(p.unrealized_pl),2),
        "pnl_pct":   round(float(p.unrealized_plpc)*100,2),
        "reason":    reason, "source": "risk_management",
        "confidence":1.0, "reasoning": f"Auto-closed: {reason.replace('_',' ')}",
    })
    _save_trades(trades)


def _load_trades() -> list:
    if os.path.exists(TRADE_LOG_FILE):
        with open(TRADE_LOG_FILE) as f:
            return json.load(f)
    return []

def _save_trades(trades):
    state_io.write_json(TRADE_LOG_FILE, trades, indent=2)
