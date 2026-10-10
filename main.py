"""
main.py — Charles trading bot entry point.
Runs under systemd (service `charles`) in the conda env `tradingbot`.
"""

import json, logging, os, subprocess, sys, time, threading
import fcntl as _fcntl   # file locking to prevent duplicate instances
from datetime import datetime, timezone

# ── Single instance lock ──────────────────────────────────────────────────────
_LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", ".bot.lock")
os.makedirs(os.path.dirname(_LOCK_FILE), exist_ok=True)

try:
    _lock_fh = open(_LOCK_FILE, "w")
    _fcntl.flock(_lock_fh, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
    _lock_fh.write(str(os.getpid()))
    _lock_fh.flush()
except (IOError, OSError):
    print("⚠️  Another instance of Charles is already running.")
    print("   Run 'charles-stop' first, or check: ps aux | grep main.py")
    sys.exit(1)

from apscheduler.schedulers.background import BackgroundScheduler
import requests

import config
import credential_manager as sec
import collector
import brain
import screener
import alpaca_executor as alpaca
import alerts
import reports as rpt
import regime          as reg_mod
import risk_manager    as rm
import equity_tracker
import timeofday
import strategy_settings
import macro_calendar

# ── Logging ───────────────────────────────────────────────────────────────────
os.makedirs(os.path.join(os.path.dirname(__file__), "logs"), exist_ok=True)
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# Shared snapshot
_latest_snapshot: dict = {}
_snap_lock = threading.Lock()


# ── Startup checks ────────────────────────────────────────────────────────────
def check_llm() -> bool:
    """Check Groq connection. Falls back to rule-based signals if unavailable."""
    import credential_manager as cm
    groq_key = cm.groq_api_key()

    if not groq_key or groq_key.startswith(("YOUR_", "REPLACE_")):
        log.warning("⚠️  Groq API key not configured")
        log.warning("    → Get a free key at console.groq.com")
        log.warning("    → Add GROQ_API_KEY to your .env file")
        log.warning("    → Running with rule-based signals only")
        config.LLM_PROVIDER = "rules"
        return False

    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {groq_key}",
                     "Content-Type": "application/json"},
            json={"model": config.GROQ_MODEL,
                  "messages": [{"role": "user", "content": "Say OK"}],
                  "max_tokens": 5},
            timeout=10,
        )
        if r.status_code == 200:
            log.info(f"✅ Groq connected — model: {config.GROQ_MODEL}")
            config.LLM_PROVIDER = "groq"
            return True
        else:
            log.warning(f"⚠️  Groq key invalid ({r.status_code}) — rule-based fallback")
            config.LLM_PROVIDER = "rules"
            return False
    except Exception as e:
        log.warning(f"⚠️  Groq unreachable ({e}) — rule-based fallback")
        config.LLM_PROVIDER = "rules"
        return False

def check_alpaca() -> bool:
    if not sec.validate()["alpaca"]:
        log.error("❌ Alpaca keys not set in config.py")
        return False
    try:
        s = alpaca.get_account_summary()
        if s:
            log.info(f"✅ Alpaca — value: ${s['total_value']:,.2f}  "
                     f"cash: ${s['cash']:,.2f}  power: ${s['buying_power']:,.2f}")
            return True
    except Exception as e:
        log.error(f"❌ Alpaca failed: {e}")
    return False


# ── Cycle helpers ─────────────────────────────────────────────────────────────
def _refresh_news():
    global _latest_snapshot
    log.info("📰 Refreshing news...")
    try:
        syms = config.STOCK_WATCHLIST
        news = collector.fetch_news(syms)
        with _snap_lock:
            if _latest_snapshot:
                _latest_snapshot["news"] = news
        log.info(f"  ✅ {len(news)} articles refreshed")
    except Exception as e:
        log.warning(f"News refresh error: {e}")


def _detect_and_log_regime() -> str:
    try:
        regime, detail = reg_mod.detect_regime()
        mults = reg_mod.regime_multipliers(regime)
        log.info(f"  🌡️  Regime: {regime} — {mults['description']}")
        return regime
    except Exception as e:
        log.warning(f"Regime detection failed: {e}")
        return "TRENDING_BULL"


def _execute_decisions(decisions: list):
    placed, buys = 0, []
    for d in decisions:
        log.info(f"  {d['symbol']:10s} → {d['action']:5s} | "
                 f"conf={d.get('confidence',0):.0%} | {d.get('reasoning','')[:65]}")
        trade = alpaca.execute_decision(d)
        if trade:
            placed += 1
            if trade.get("action") == "BUY":
                buys.append(trade)
    if buys:
        _open_option_spreads(buys)
    return placed


def _open_option_spreads(buys: list):
    """Options sleeve (options_bot.py): a call spread on each stock Charles just bought."""
    try:
        import options_bot
        options_bot.on_stock_buys(buys)
    except Exception as e:
        log.warning(f"Options sleeve failed: {e}")


def _manage_options():
    try:
        import options_bot
        options_bot.manage()
    except Exception as e:
        log.warning(f"Options check failed: {e}")


def _orb_tick():
    """Opening Range Breakout sleeve: one minute of orb.py (scheduled only when ORB_ENABLED)."""
    try:
        import orb
        orb.tick()
    except Exception as e:
        log.error(f"[orb] tick failed: {e}")


def _update_shadow(decisions: list, snapshot: dict, regime: str):
    """Feed this cycle to the shadow portfolios (shadow.py). Never affects live trading."""
    try:
        import shadow
        shadow.on_cycle(decisions, snapshot.get("market", {}), regime)
    except Exception as e:
        log.warning(f"Shadow portfolios update failed: {e}")
    try:
        import shadow_daily                      # daily-data strategy books (once a day, 15:30 ET)
        shadow_daily.maybe_run()
    except Exception as e:
        log.warning(f"Daily shadow books update failed: {e}")


def _record_equity():
    try:
        s = alpaca.get_account_summary()
        if s:
            equity_tracker.record_snapshot(s["total_value"])
    except Exception:
        pass


def _get_position_context() -> dict:
    """
    Build position context dict for LLM — tells it what we own and at what P&L.
    {symbol: {held, avg_price, unrealized_pnl, unrealized_pct, market_value, hold_days}}
    """
    try:
        summary = alpaca.get_account_summary()
        positions = {}
        for p in summary.get("positions", []):
            sym = p["symbol"]
            # Try to find hold duration from trade log
            hold_days = "?"
            hold_minutes = None
            try:
                import json, os
                tf = os.path.join(os.path.dirname(__file__), "data", "trades.json")
                if os.path.exists(tf):
                    trades = json.load(open(tf))
                    buys = [t for t in trades
                            if t.get("symbol") == sym and t.get("action") == "BUY"]
                    if buys:
                        from datetime import datetime, timezone
                        last_buy = datetime.fromisoformat(buys[-1]["timestamp"])
                        hold_days = (datetime.now(timezone.utc) - last_buy).days
                        hold_minutes = (datetime.now(timezone.utc) - last_buy).total_seconds() / 60
            except Exception:
                pass

            # Get trailing stop
            ts_val = 0
            try:
                import risk_manager as rm
                ts = rm.get_trailing_stops().get(sym, {})
                ts_val = ts.get("stop", 0)
            except Exception:
                pass

            positions[sym] = {
                "held":           True,
                "avg_price":      p.get("avg_price", 0),
                "unrealized_pnl": p.get("unrealized_pnl", 0),
                "unrealized_pct": p.get("unrealized_pct", 0),
                "market_value":   p.get("market_value", 0),
                "trailing_stop":  ts_val,
                "hold_days":      hold_days,
                "hold_minutes":   hold_minutes,
            }
        return positions
    except Exception as e:
        log.debug(f"Position context fetch failed: {e}")
        return {}


def _run_stock_cycle():
    global _latest_snapshot
    if rm.is_trading_halted():
        log.warning("🚨 Daily loss limit active — stock cycle skipped")
        return
    # Skip new order placement if the market isn't genuinely open yet —
    # the cron hour range can fire a few minutes before Alpaca's real open,
    # and orders submitted then just sit unfilled until open anyway.
    try:
        clock = alpaca._client().get_clock()
        if not clock.is_open:
            log.info(f"  🕐 Market not open yet (next open: {clock.next_open}) — skipping stock cycle")
            return
    except Exception as e:
        log.debug(f"Market clock check failed: {e} — proceeding anyway")
    # Time-of-day and macro checks
    sess = timeofday.session_info()
    log.info(f"📈 Stock cycle... [{sess.get('phase','?')} | {sess.get('time_et','?')}]")
    in_macro, macro_event = macro_calendar.in_macro_blackout()
    if in_macro:
        log.warning(f"  ⚠️  Macro blackout active: {macro_event}")
    try:
    
        snap = collector.collect_all()
        with _snap_lock:
            _latest_snapshot.update(snap)
            merged = dict(_latest_snapshot)

        regime      = _detect_and_log_regime()
        pos_context = _get_position_context()
        decisions   = brain.analyse_snapshot(merged, regime=regime,
                                              open_positions=pos_context)
        placed      = _execute_decisions([d for d in decisions
                                          if d.get("type") in ("stock","hedge")])
        _update_shadow(decisions, merged, regime)
        _record_equity()
        log.info(f"  ✅ Stock cycle done — {placed} trades placed")
    except Exception as e:
        log.error(f"Stock cycle error: {e}")
        alerts.error_alert(f"Stock cycle error: {e}")



def _morning_routine():
    global _latest_snapshot
    log.info("🌅 Morning routine...")

    # Run screener
    screener.run_screener()
    stocks, _ = screener.get_current_watchlist()
    config.STOCK_WATCHLIST = stocks

    # Full data collect
    snap = collector.collect_all()
    with _snap_lock:
        _latest_snapshot = snap

    # Regime + market trend
    regime, detail = reg_mod.detect_regime()
    mults = reg_mod.regime_multipliers(regime)
    log.info(f"  🌡️  Regime: {regime} — {mults['description']}")
    alerts.market_trend(regime, detail.get("momentum_10d", 0),
                        allow_longs=mults.get("allow_longs", True),
                        description=mults.get("description", ""))

    # Auto-tune confidence
    tuned = screener.get_tuned_confidence()
    if tuned != config.MIN_CONFIDENCE:
        config.MIN_CONFIDENCE = tuned
        log.info(f"  🎯 Confidence threshold → {tuned:.0%}")

    # Earnings exit — sell positions with upcoming earnings
    try:
        import market_filter as _mf
        pos_ctx = _get_position_context()
        to_exit = _mf.get_positions_near_earnings(pos_ctx)
        for sym in to_exit:
            log.warning(f"  📅 Exiting {sym} before earnings")
            alpaca.execute_decision({
                "symbol": sym, "type": "stock", "action": "SELL",
                "price": None, "confidence": 0.95,
                "reasoning": "Earnings blackout exit — avoiding overnight earnings risk",
                "source": "earnings_exit",
            })
    except Exception as _e:
        log.warning(f"Earnings exit check failed: {_e}")

    # Time-based exit — cut flat trades older than 7 days
    _check_time_based_exits()

    # Performance feedback
    screener.update_performance_feedback()

    # Daily stats
    daily = rm.get_daily_stats()
    log.info(f"  💰 Daily P&L so far: ${daily.get('loss',0):+,.2f}")


def _send_heartbeat():
    """Hourly status ping to Telegram."""
    try:
        # Alpaca portfolio
        summary     = alpaca.get_account_summary()
        total_value = summary.get("total_value", config.VIRTUAL_CASH)
        open_pos    = summary.get("open_positions", 0)

        # Daily P&L — compare to start-of-day value from equity curve
        daily_pnl     = 0.0
        daily_pnl_pct = 0.0
        try:
            curve = equity_tracker.load_curve()
            if curve:
                # Find first snapshot from today (ET trading day; curve ts is UTC)
                today_str = timeofday.market_date()
                today_pts = [p for p in curve if timeofday.market_date(p["ts"]) == today_str]
                if today_pts:
                    start_today = today_pts[0]["value"]
                    daily_pnl     = total_value - start_today
                    daily_pnl_pct = daily_pnl / start_today * 100
                else:
                    # No snapshot yet today — record one now and show $0
                    equity_tracker.record_snapshot(total_value)
                    daily_pnl     = 0.0
                    daily_pnl_pct = 0.0
        except Exception:
            pass

        # Sanity check — if P&L is more than 50% of account something is wrong
        if abs(daily_pnl) > config.VIRTUAL_CASH * 0.5:
            daily_pnl     = 0.0
            daily_pnl_pct = 0.0

        # Trades placed today
        trades_today = 0
        last_trade   = "None today"
        try:
            tf = os.path.join(os.path.dirname(__file__), "data", "trades.json")
            if os.path.exists(tf):
                with open(tf) as f2:
                    trades = json.load(f2)
                today_str    = timeofday.market_date()
                today_trades = [t for t in trades
                                if timeofday.market_date(t.get("timestamp", "")) == today_str]
                trades_today = len(today_trades)
                if today_trades:
                    lt = today_trades[-1]
                    pnl_str = f" P&L ${lt['pnl']:+,.0f}" if "pnl" in lt else ""
                    last_trade = f"{lt['action']} {lt['symbol']}{pnl_str}"
        except Exception:
            pass

        # Regime
        regime = "TRENDING_BULL"
        try:
            regime, _ = reg_mod.detect_regime()
        except Exception:
            pass

        # Next macro event
        next_macro = None
        try:
            next_macro = macro_calendar.next_event()
        except Exception:
            pass

        # FinBERT status
        finbert_on = False
        try:
            import finbert_sentiment as fbs
            finbert_on = fbs.is_finbert_active()
        except Exception:
            pass

        # Verify credentials before calling heartbeat
        import credential_manager as _cm
        tg_token = _cm.telegram_token()
        tg_chat  = _cm.telegram_chat_id()
        if not tg_token or not tg_chat:
            log.warning("💓 Heartbeat skipped — Telegram not configured (no token/chat_id)")
        elif tg_token.startswith(("YOUR_","REPLACE_","your_")):
            log.warning("💓 Heartbeat skipped — Telegram token is placeholder")
        else:
            log.info(f"💓 Sending heartbeat to Telegram (token: {tg_token[:10]}... chat: {tg_chat})")

        alerts.heartbeat({
            "active":         True,
            "halted":         rm.is_trading_halted(),
            "daily_pnl":      round(daily_pnl, 2),
            "daily_pnl_pct":  round(daily_pnl_pct, 2),
            "total_value":    round(total_value, 2),
            "open_positions": open_pos,
            "regime":         regime,
            "trades_today":   trades_today,
            "next_macro":     next_macro,
            "last_trade":     last_trade,
            "finbert_on":     finbert_on,
        })

    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")


def _score_decisions():
    """Grade logged decisions by what the stocks did next (data/decision_scorecard.json)."""
    try:
        import decision_scorer
        decision_scorer.run()
    except Exception as e:
        log.warning(f"Decision scoring failed: {e}")


def _daily_summary():
    s = alpaca.get_account_summary()
    if s:
        extra = None
        if config.ORB_ENABLED:
            try:
                import orb
                extra = ["📐 " + orb.summary_line(orb.load_state(), orb.trades())]
            except Exception as e:
                log.warning(f"ORB summary line failed: {e}")
        alerts.daily_summary(s, extra)
        log.info(f"📊 Daily summary — return: {s['total_return_pct']:+.2f}%")


# ── Scheduler ─────────────────────────────────────────────────────────────────
def _weekly_report():
    try:
        stats = rpt.generate_report("weekly")
        rpt.send_report_telegram(stats)
    except Exception as e:
        log.warning(f"Weekly report failed: {e}")

def _monthly_report():
    try:
        stats = rpt.generate_report("monthly", previous=True)
        rpt.send_report_telegram(stats)
    except Exception as e:
        log.warning(f"Monthly report failed: {e}")

def _yearly_report():
    try:
        stats = rpt.generate_report("yearly", previous=True)
        rpt.send_report_telegram(stats)
    except Exception as e:
        log.warning(f"Yearly report failed: {e}")



def _check_time_based_exits():
    """
    Exit positions that have been held 7+ days with < 2% gain.
    Dead capital — better deployed elsewhere.
    Called from morning routine and mid-day refresh.
    """
    try:
        summary   = alpaca.get_account_summary()
        positions = summary.get("positions", [])
        if not positions:
            return

        import json as _json
        tf = os.path.join(os.path.dirname(__file__), "data", "trades.json")
        trades = []
        if os.path.exists(tf):
            with open(tf) as f:
                trades = _json.load(f)

        for p in positions:
            sym         = p["symbol"]
            unreal_pct  = p.get("unrealized_pct", 0)

            # Find hold duration
            hold_days = 0
            buys = [t for t in trades
                    if t.get("symbol") == sym and t.get("action") == "BUY"]
            if buys:
                last_buy = datetime.fromisoformat(buys[-1]["timestamp"])
                hold_days = (datetime.now(timezone.utc) - last_buy).days

            # Exit if held 7+ days and not up meaningfully
            if hold_days >= 7 and unreal_pct < 2.0:
                log.info(f"  ⏰ {sym}: held {hold_days}d, only {unreal_pct:+.1f}% — time-based exit")
                alpaca.execute_decision({
                    "symbol":     sym,
                    "type":       "stock",
                    "action":     "SELL",
                    "price":      None,
                    "confidence": 0.90,
                    "reasoning":  f"Time-based exit: {hold_days}d held, {unreal_pct:+.1f}% gain — redeploying capital",
                    "source":     "time_exit",
                })
    except Exception as e:
        log.warning(f"Time-based exit check failed: {e}")

def _midday_screener_refresh():
    """
    1 PM ET mid-day screener refresh.
    Re-scores the stock universe to catch momentum shifts that emerged
    during the morning session. Updates config.STOCK_WATCHLIST so the
    next _run_stock_cycle() acts on fresh candidates.
    """
    global _latest_snapshot
    log.info("🔄 Mid-day screener refresh...")
    try:
        screener.run_screener()
        stocks, _ = screener.get_current_watchlist()
        if stocks:
            config.STOCK_WATCHLIST = stocks
            log.info(f"  📈 Updated watchlist: {stocks}")
            # Immediately collect fresh data and run decisions on new list
            snap = collector.collect_all()
            with _snap_lock:
                _latest_snapshot.update(snap)
                merged = dict(_latest_snapshot)
            regime      = _detect_and_log_regime()
            pos_context = _get_position_context()
            decisions   = brain.analyse_snapshot(merged, regime=regime,
                                                  open_positions=pos_context)
            placed = _execute_decisions([d for d in decisions
                                         if d.get("type") in ("stock", "hedge")])
            _update_shadow(decisions, merged, regime)
            log.info(f"  ✅ Mid-day cycle done — {placed} trades placed")
        else:
            log.warning("  ⚠️  Mid-day screener returned no stocks — keeping current watchlist")
    except Exception as e:
        log.error(f"Mid-day screener refresh error: {e}")

def start_scheduler():
    sched = BackgroundScheduler(timezone="America/New_York")

    # Morning routine — 8:45 AM ET weekdays
    sched.add_job(_morning_routine, "cron",
                  hour=8, minute=45, day_of_week="mon-fri", id="morning")

    # Stock scans — every 30 min during market hours
    sched.add_job(_run_stock_cycle, "cron",
                  hour="9-15", minute=f"*/{config.STOCK_SCAN_INTERVAL_MIN}",
                  day_of_week="mon-fri", id="stocks")

    # News refresh — top of every hour
    sched.add_job(_refresh_news, "cron", minute=0, id="news")

    # Mid-day screener refresh — 1:00 PM ET weekdays
    sched.add_job(_midday_screener_refresh, "cron",
                  hour=13, minute=0, day_of_week="mon-fri", id="midday_screener")

    # Stop checks — every 15 min during market hours
    sched.add_job(alpaca.check_stops, "cron",
                  hour="9-16", minute="*/15",
                  day_of_week="mon-fri", id="stops")

    # Options sleeve exits — every 15 min in market hours, offset from the stop checks
    sched.add_job(_manage_options, "cron",
                  hour="9-15", minute="7-59/15",
                  day_of_week="mon-fri", id="options")

    # Opening Range Breakout sleeve (orb.py) — every minute in market hours, only when enabled
    if config.ORB_ENABLED:
        sched.add_job(_orb_tick, "cron",
                      hour="9-15", minute="*", second=10,
                      day_of_week="mon-fri", id="orb", max_instances=1, coalesce=True)

    # Daily summary — 4:30 PM ET
    sched.add_job(_daily_summary, "cron",
                  hour=16, minute=30, day_of_week="mon-fri", id="summary")

    # Decision scorecard — 5:05 PM ET, after the close
    sched.add_job(_score_decisions, "cron",
                  hour=17, minute=5, day_of_week="mon-fri", id="decision_scores")

    # Heartbeat — every hour on the hour, 24/7
    sched.add_job(_send_heartbeat, "cron",
                  minute=0, id="heartbeat")
    # Weekly report — Sunday 6 PM ET
    sched.add_job(_weekly_report, "cron",
                  day_of_week="sun", hour=18, minute=0, id="weekly_report")
    # Monthly report — 1st of month 7 PM ET
    sched.add_job(_monthly_report, "cron",
                  day=1, hour=19, minute=0, id="monthly_report")
    # Yearly report — Jan 1st 8 PM ET
    sched.add_job(_yearly_report, "cron",
                  month=1, day=1, hour=20, minute=0, id="yearly_report")
    sched.start()
    log.info("⏰ Scheduler active:")
    log.info(f"   📈 Stocks:   every {config.STOCK_SCAN_INTERVAL_MIN} min  09:00–16:00 ET")
    log.info(f"   🔄 Mid-day:  screener refresh 01:00 PM ET")
    log.info(f"   📰 News:     top of every hour")
    log.info(f"   🛡️  Stops:    every 15 min (trailing + fixed)")
    log.info(f"   🧩 Options:  spread exits every 15 min (:07/:22/:37/:52)")
    if config.ORB_ENABLED:
        log.info(f"   📐 ORB:      every minute 09:30–15:55 ET ({', '.join(config.ORB_SYMBOLS)}, strategy=orb)")
    log.info(f"   🌅 Screener: 08:45 AM ET")
    log.info(f"   📊 Summary:  04:30 PM ET")
    log.info(f"   💓 Heartbeat: every hour on the hour")
    log.info(f"   📊 Weekly:   Sunday 6 PM ET")
    log.info(f"   📅 Monthly:  1st of month 7 PM ET")
    log.info(f"   🗓️  Yearly:   Jan 1st 8 PM ET")
    return sched


# ── Dashboard ─────────────────────────────────────────────────────────────────
def launch_dashboard():
    # The dashboard is its own service (charles-web, webdash.py); the Streamlit one was
    # retired 2026-10-08. This line marks the end of startup for ops/restart-and-verify.
    log.info("🌐 Dashboard → http://localhost:8502 (service charles-web)")


# ── Main ──────────────────────────────────────────────────────────────────────
# ── Launch flag paths ────────────────────────────────────────────────────────
# Optional. Written by the old setup wizard; when present it overrides sizing,
# daily-loss and credential settings (see _apply_wizard_settings).
_LAUNCH_FLAG = os.path.join(os.path.dirname(__file__), "data", "launch_authorized.json")


def _apply_wizard_settings():
    """Apply settings from the launch flag to config at runtime."""
    if not os.path.exists(_LAUNCH_FLAG):
        return
    try:
        with open(_LAUNCH_FLAG) as f:
            data = json.load(f)

        # Apply to config at runtime
        if data.get("balance"):          config.VIRTUAL_CASH          = data["balance"]
        if data.get("max_positions"):    config.MAX_OPEN_POSITIONS    = data["max_positions"]
        if data.get("max_pct"):          config.MAX_POSITION_SIZE_PCT = data["max_pct"]
        if data.get("min_pct"):          config.MIN_POSITION_SIZE_PCT = data["min_pct"]
        if data.get("daily_loss"):       config.MAX_DAILY_LOSS        = data["daily_loss"]
        # Load .env first so it takes priority over empty wizard values
        try:
            from dotenv import load_dotenv as _lde
            _lde(os.path.join(os.path.dirname(__file__), ".env"), override=True)
            import logging as _lg
            _lg.getLogger(__name__).info("✅ .env reloaded into environment")
        except Exception as _e:
            pass
        # Only set from wizard if value is non-empty AND not already set from .env
        if data.get("groq_key"):
            os.environ.setdefault("GROQ_API_KEY", data["groq_key"])
        if data.get("alpaca_key"):       os.environ["ALPACA_API_KEY"]    = data["alpaca_key"]
        if data.get("alpaca_secret"):    os.environ["ALPACA_SECRET_KEY"] = data["alpaca_secret"]
        if data.get("tg_token"):         os.environ["TELEGRAM_BOT_TOKEN"] = data["tg_token"]
        if data.get("tg_chat_id"):       os.environ["TELEGRAM_CHAT_ID"]   = data["tg_chat_id"]
        if data.get("dashboard_pwd"):    os.environ["DASHBOARD_PASSWORD"]  = data["dashboard_pwd"]

        log.info("✅ Wizard settings applied to runtime config")
    except Exception as e:
        log.warning(f"Could not apply wizard settings: {e}")


if __name__ == "__main__":
    if os.path.exists(_LAUNCH_FLAG):
        _apply_wizard_settings()
    else:
        log.info("No data/launch_authorized.json — using keys from .env and sizing from config.py")
    strategy_settings.apply()   # strategy.json overrides config + launch sizing

    print("\n" + "=" * 60)
    print("  🤖  Charles AI Trading Bot  v1.0")
    print("=" * 60 + "\n")

    check_llm()
    if not check_alpaca():
        print("\n⛔ Alpaca connection failed. Re-run main.py and check your keys.\n")
        sys.exit(1)

    # Test Telegram before claiming it works
    tg_working = alerts.test_telegram()
    if tg_working:
        alerts.bot_started()
        log.info("✅ Telegram alerts active")
    else:
        log.warning("⚠️  Telegram not working — check TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID "
                    "(.env, overridden by tg_token / tg_chat_id in data/launch_authorized.json)")

    time.sleep(2)
    _send_heartbeat()

    # First run
    _morning_routine()

    sched = start_scheduler()
    time.sleep(2)
    launch_dashboard()

    print(f"\n✅ Bot running — v1.0")
    print("📊 Dashboard:   http://localhost:8502")
    llm_mode = config.LLM_PROVIDER if hasattr(config, "LLM_PROVIDER") else "rules"
    print(f"🧠 AI Engine:   {llm_mode.upper()}")
    print(f"📱 Telegram:    {'configured ✅' if os.environ.get('TELEGRAM_BOT_TOKEN') else 'not set (optional)'}")
    print(f"🌡️  Regime:      auto-detected every 30 min")
    print(f"🛑 Trailing:    ATR×{config.TRAILING_STOP_ATR_MULT} stops on all longs")
    print(f"🚨 Kill switch: halts at ${config.MAX_DAILY_LOSS:,.0f} daily loss")
    print("   Press Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("🛑 Bot stopped.")
        sched.shutdown()
