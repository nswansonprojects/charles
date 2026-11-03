"""
main.py — Charles trading bot entry point.
Runs under systemd (service `charles`) in the conda env `tradingbot`.

One job does the trading: rebalancer.run() at 10:00 ET on weekdays (and 14:00 as a retry, which
normally finds nothing to do). The rest is reporting: equity snapshots for the chart, the daily
summary, the hourly log line and the weekly/monthly/yearly reports, all shown on the dashboard.
"""

import json, logging, os, sys, time
import fcntl as _fcntl   # file locking to prevent duplicate instances

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
import state_io
import alpaca_executor as alpaca
import alerts
import reports as rpt
import risk_manager as rm
import equity_tracker
import rebalancer
import timeofday
import strategy_settings

# ── Logging ───────────────────────────────────────────────────────────────────
os.makedirs(os.path.join(os.path.dirname(__file__), "logs"), exist_ok=True)
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


# ── Startup checks ────────────────────────────────────────────────────────────
def check_llm() -> bool:
    """Groq powers only the news veto; without it Charles still runs the plan (no vetoes)."""
    groq_key = sec.groq_api_key()
    if not groq_key or groq_key.startswith(("YOUR_", "REPLACE_")):
        log.warning("⚠️  Groq API key not configured — the news veto is off (get a free key at console.groq.com)")
        return False
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
            json={"model": config.GROQ_MODEL, "messages": [{"role": "user", "content": "Say OK"}],
                  "max_tokens": 5},
            timeout=10,
        )
        if r.status_code == 200:
            log.info(f"✅ Groq connected — model: {config.GROQ_MODEL} (news veto)")
            return True
        log.warning(f"⚠️  Groq key invalid ({r.status_code}) — the news veto is off")
    except Exception as e:
        log.warning(f"⚠️  Groq unreachable ({e}) — the news veto is off")
    return False


def check_alpaca() -> bool:
    import live
    if live.is_live():
        if not all(live.keys()):
            log.error("❌ Live trading is on, but the live Alpaca keys are missing (charles-live on)")
            return False
        log.info("🟢 LIVE trading: the user's own Alpaca live account (charles-live)")
    elif not sec.validate()["alpaca"]:
        log.error("❌ Alpaca keys not set")
        return False
    s = alpaca.get_account_summary()
    if s:
        log.info(f"✅ Alpaca — value: ${s['total_value']:,.2f}  cash: ${s['cash']:,.2f}")
        return True
    log.error("❌ Alpaca failed")
    return False


# ── Jobs ──────────────────────────────────────────────────────────────────────
def _rebalance():
    log.info(f"🧭 Rebalance check [{timeofday.now_et():%H:%M} ET]")
    try:
        res = rebalancer.run()
        st = res.get("state")
        if st:
            log.info(f"  {st['summary']}")
            log.info(f"  ✅ Rebalance done — {len(res.get('done', []))} orders")
    except Exception as e:
        log.error(f"Rebalance error: {e}")
        alerts.error_alert(f"Rebalance error: {e}")
    _record_equity()


def _record_equity():
    try:
        s = alpaca.get_account_summary()
        if s:
            equity_tracker.record_snapshot(s["total_value"])
    except Exception:
        pass


def _record_equity_in_hours():
    """Chart points every 30 min in market hours (the rebalance runs only once a day)."""
    if timeofday.is_market_open():
        _record_equity()


def _heartbeat():
    """An hourly line in bot.log: the daily check-up takes a log silent for 90 minutes to mean the bot is stuck."""
    try:
        summary = alpaca.get_account_summary()
        log.info(f"💓 Running: value ${summary.get('total_value', config.VIRTUAL_CASH):,.2f}, "
                 f"{summary.get('open_positions', 0)} holdings"
                 f"{', buys paused today' if rm.is_trading_halted() else ''}")
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")


def _daily_summary():
    s = alpaca.get_account_summary()
    if s:
        alerts.daily_summary(s, rebalancer.load_state())
        log.info(f"📊 Daily summary — return: {s['total_return_pct']:+.2f}%")


def _weekly_report():
    try:
        rpt.post_report(rpt.generate_report("weekly"))
    except Exception as e:
        log.warning(f"Weekly report failed: {e}")


def _monthly_report():
    try:
        rpt.post_report(rpt.generate_report("monthly", previous=True))
    except Exception as e:
        log.warning(f"Monthly report failed: {e}")


def _yearly_report():
    try:
        rpt.post_report(rpt.generate_report("yearly", previous=True))
    except Exception as e:
        log.warning(f"Yearly report failed: {e}")


def start_scheduler():
    sched = BackgroundScheduler(timezone="America/New_York")
    # The trading: once a day after the open has settled; 14:00 retries anything that failed
    sched.add_job(_rebalance, "cron", hour=10, minute=0, day_of_week="mon-fri", id="rebalance")
    sched.add_job(_rebalance, "cron", hour=14, minute=0, day_of_week="mon-fri", id="rebalance_retry")
    sched.add_job(_record_equity_in_hours, "cron", hour="9-15", minute="*/30", day_of_week="mon-fri", id="equity")
    sched.add_job(_daily_summary, "cron", hour=16, minute=30, day_of_week="mon-fri", id="summary")
    sched.add_job(_heartbeat, "cron", minute=0, id="heartbeat")
    sched.add_job(_weekly_report, "cron", day_of_week="sun", hour=18, minute=0, id="weekly_report")
    sched.add_job(_monthly_report, "cron", day=1, hour=19, minute=0, id="monthly_report")
    sched.add_job(_yearly_report, "cron", month=1, day=1, hour=20, minute=0, id="yearly_report")
    sched.start()
    log.info("⏰ Scheduler active:")
    log.info("   🧭 Rebalance: 10:00 AM ET weekdays (retry 2:00 PM)")
    log.info("   📈 Chart:     account value every 30 min in market hours")
    log.info("   📊 Summary:   04:30 PM ET")
    log.info("   💓 Heartbeat: every hour on the hour")
    log.info("   📊 Reports:   weekly Sun 6 PM, monthly 1st 7 PM, yearly Jan 1 8 PM ET")
    return sched


# ── Launch flag ───────────────────────────────────────────────────────────────
# Written by the old desktop setup wizard; still the live source of the daily-loss limit and
# the Alpaca credentials (see _apply_wizard_settings).
_LAUNCH_FLAG = os.path.join(os.path.dirname(__file__), "data", "launch_authorized.json")


def _apply_wizard_settings():
    """Apply settings from the launch flag to config at runtime. Returns the flag's starting balance
    (None without one)."""
    if not os.path.exists(_LAUNCH_FLAG):
        return None
    try:
        with open(_LAUNCH_FLAG) as f:
            data = json.load(f)
        if data.get("balance"):          config.VIRTUAL_CASH   = data["balance"]
        if data.get("daily_loss"):       config.MAX_DAILY_LOSS = data["daily_loss"]
        # Load .env first so it takes priority over empty wizard values
        try:
            from dotenv import load_dotenv as _lde
            _lde(os.path.join(os.path.dirname(__file__), ".env"), override=True)
            log.info("✅ .env reloaded into environment")
        except Exception:
            pass
        if data.get("groq_key"):
            os.environ.setdefault("GROQ_API_KEY", data["groq_key"])
        if data.get("alpaca_key"):       os.environ["ALPACA_API_KEY"]     = data["alpaca_key"]
        if data.get("alpaca_secret"):    os.environ["ALPACA_SECRET_KEY"]  = data["alpaca_secret"]
        log.info("✅ Wizard settings applied to runtime config")
        return data.get("balance")
    except Exception as e:
        log.warning(f"Could not apply wizard settings: {e}")
        return None


def _catch_up():
    """After a restart in market hours past 10:00 ET, run today's check now instead of waiting."""
    now = timeofday.now_et()
    if timeofday.is_market_open() and now.hour >= 10:
        plan = rebalancer.load_state()
        if timeofday.market_date(plan.get("updated")) != timeofday.market_date():
            _rebalance()


if __name__ == "__main__":
    if not os.path.exists(_LAUNCH_FLAG):
        if state_io.private_copy(os.path.dirname(os.path.abspath(__file__))):
            log.error(f"⛔ {_LAUNCH_FLAG} is missing — it holds the daily-loss and credential "
                      f"settings. Restore it from a backup; not starting.")
            sys.exit(1)
        log.info("No data/launch_authorized.json: using the keys in .env and the default settings")
    balance = _apply_wizard_settings()
    strategy_settings.apply()

    print("\n" + "=" * 60)
    print("  🐶  Charles — core + momentum, with a safety switch")
    print("=" * 60 + "\n")

    check_llm()
    if not check_alpaca():
        print("\n⛔ Alpaca connection failed. Re-run main.py and check your keys.\n")
        sys.exit(1)

    # What returns are measured from, fixed at the first start (data/start.json): the launch flag's
    # balance on older installs, else the account's value now (a new Alpaca paper account holds $100,000).
    import live          # a live account starts from its own value, never the paper install's launch balance
    start = equity_tracker.ensure_start((alpaca.get_account_summary() or {}).get("total_value"), None if live.is_live() else balance)
    log.info(f"📏 Returns are measured from ${start:,.2f}")

    alerts.bot_started()

    _record_equity()
    sched = start_scheduler()
    _catch_up()
    # The dashboard is its own service (charles-web, webdash.py). This line marks the end of
    # startup for ops/restart-and-verify.
    log.info("🌐 Dashboard → http://localhost:8502 (service charles-web)")
    lim = f"${config.MAX_DAILY_LOSS:,.0f}" if config.MAX_DAILY_LOSS else f"{config.MAX_DAILY_LOSS_PCT:g}% of the account"
    print(f"🚨 Daily loss limit: no buys after a {lim} loss in a day")
    print("   Press Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("🛑 Bot stopped.")
        sched.shutdown()
