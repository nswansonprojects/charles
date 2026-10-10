# ============================================================
#  TRADING BOT CONFIGURATION  —  v8
#  Stocks only | Cash account optimized | $1,000 account
# ============================================================
import os

# --- Alpaca Paper Trading ← YOUR KEYS GO IN .env FILE ------
# Leave these empty — keys are loaded from .env by credential_manager
ALPACA_API_KEY    = ""
ALPACA_SECRET_KEY = ""
# Paper trading is fixed in alpaca_executor._client() (paper=True); there is no switch here.

# --- Telegram Alerts ← YOUR KEYS GO IN .env FILE -----------
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID   = ""

# --- AI / LLM Settings ---------------------------------------
# Groq is recommended for server deployment (free, fast, no GPU needed)
# Get a free API key at console.groq.com
# If GROQ_API_KEY is set, Groq is used. Otherwise the rule-based signal is used.
GROQ_API_KEY  = ""          # set in .env as GROQ_API_KEY
GROQ_MODEL    = "openai/gpt-oss-20b"   # replaces deprecated llama-3.1-8b-instant

# Ollama fallback removed 2026-10-01: llama3.1 can't load in this server's 3 GB RAM.

# Which LLM to use: "groq", "rules", or "auto" (groq if key set, else rules).
# main.check_llm overwrites this at startup.
LLM_PROVIDER = "auto"

# --- Stock Watchlist (screener auto-updates daily) ----------
# Crypto removed — stocks only for cleaner signals and zero fees
STOCK_WATCHLIST = ["MRVL","SMCI","ORCL","AMAT","ARM","ASML","MU","KLAC"]

# --- Portfolio settings --------------------------------------
VIRTUAL_CASH          = 1_000.0   # matches $1,000 Alpaca account

# --- Position sizing -----------------------------------------
# Cash account: size conservatively to always have dry powder
MIN_POSITION_SIZE_PCT = 0.10      # 10% = $100/trade minimum

# --- Stop loss / Take profit ---------------------------------

# --- Trailing stops ------------------------------------------
TRAILING_STOP_ATR_MULT = 2.0      # wide enough for noise, tight for reversals
# Profit lock (owner, 2026-10-09): once a position's best gain reaches the trigger, its stop is
# at least entry × (1 + keep × best gain). 0 trigger = off.
PROFIT_LOCK_TRIGGER    = 0.04
PROFIT_LOCK_KEEP       = 0.5

# --- Daily max loss kill switch ------------------------------

# --- Strategy thresholds ------------------------------------
RSI_OVERSOLD          = 35
RSI_OVERBOUGHT        = 65

# --- Volume confirmation ------------------------------------
VOLUME_CONFIRM_RATIO  = 1.2       # volume must be 1.2x 20-day avg

# --- Cooldown / duplicate prevention ------------------------

# --- Earnings blackout --------------------------------------
EARNINGS_BLACKOUT_DAYS = 3        # skip stocks within 3 days of earnings

# --- Market trend filter ------------------------------------
SPY_TREND_PERIOD      = 20        # 20-day MA for bull/bear detection

# --- Backtesting --------------------------------------------

# --- Reporting ----------------------------------------------
STREAMLIT_PORT        = 8501
LOG_LEVEL             = "INFO"

# --- News sources -------------------------------------------
RSS_FEEDS = [
    # Financial news
    # Reuters ended public RSS in 2020; bare Yahoo headline feed returns 400 (2026-10-01).
    # Nasdaq's feed was tried and dropped: it hangs unless the User-Agent looks like a browser,
    # while Yahoo returns 429 to browser User-Agents.
    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147",  # CNBC business
    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910",  # CNBC technology
    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",  # CNBC finance
    "https://feeds.marketwatch.com/marketwatch/topstories",
    "https://www.investing.com/rss/news.rss",
    "https://seekingalpha.com/market_currents.xml",
    # Stock specific
    "https://finance.yahoo.com/rss/2.0/headline?s=AAPL,MSFT,NVDA,TSLA,GOOGL",
]


# ============================================================
#  RUNTIME SETTINGS
#  No backtested return exists for the live strategy: entries are
#  the AI's call. backtest_live.py (rule signal in place of the AI)
#  made 1 trade from 2023-11 to 2026-09. The only evidence is the
#  live record: +$17.77 on 88 closed trades, 2026-06-05..09-30.
# ============================================================
MAX_OPEN_POSITIONS      = 4
MAX_POSITION_SIZE_PCT   = 0.15
STOP_LOSS_PCT           = 0.05
TAKE_PROFIT_PCT         = 0.15
MIN_CONFIDENCE          = 0.60
MIN_CONFIDENCE_FLOOR    = 0.60    # auto-tuner may raise MIN_CONFIDENCE but never go below this
# Rule-signal fallbacks (Groq down / token budget out) are stamped 0.55, below this
# bar, so only the AI opens trades. Deliberate (owner, 2026-10-01): the rule signal has
# no track record (backtest_live: 1 trade in ~3 years). Stops and exits still work.
LLM_SELL_MIN_CONFIDENCE = 0.75    # LLM may only exit a held position at/above this
STOCK_SCAN_INTERVAL_MIN = 30
TRADE_COOLDOWN_HOURS    = 4
MAX_DAILY_LOSS          = 20.0    # 2% of $1k; reachable (4 × $150 × 5% = $30 worst case)
GROQ_TOKEN_BUDGET       = 7200    # matches full 8-symbol batch, paced
MAX_SYMBOLS_PER_CYCLE   = 8       # screener's top-N watchlist; Groq budget assumes 8
GROQ_CALL_DELAY_SEC     = 10.0


# ============================================================
#  OPENING RANGE BREAKOUT sleeve (orb.py) — intraday, paper only
#  Off unless ORB_ENABLED=true in the environment or .env (on this server:
#  systemd drop-in charles.service.d/orb.conf). With it off, nothing ORB runs.
#  Evidence and how the values were picked: reports/orb/ (backtest_orb.py).
# ============================================================
def _env_flag(name: str) -> bool:
    """A true/false switch from the process environment, else from .env (read here because
    config is imported before main.py loads .env). Missing = False."""
    value = os.environ.get(name)
    if value is None:
        try:
            from dotenv import dotenv_values
            value = dotenv_values(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")).get(name)
        except Exception:
            value = None
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


ORB_ENABLED            = _env_flag("ORB_ENABLED")
ORB_SYMBOLS            = ["QQQ"]  # liquid ETFs; Alpaca has no futures (the MNQ idea → QQQ)
ORB_RANGE_MINUTES      = 5        # opening range = first N minutes after 09:30 ET
ORB_ENTRY_CUTOFF_MIN   = 60       # no new entries later than this after the open
ORB_MAX_TRADES_PER_DAY = 1
ORB_ALLOW_SHORT        = False    # cash account: Alpaca refuses shorts (backtest-only switch)
ORB_MIN_RANGE_ATR      = 0.0      # skip if range < this × daily ATR(14) (0 = no minimum)
ORB_MAX_RANGE_ATR      = 0.0      # skip if range > this × daily ATR(14) (0 = no maximum)
ORB_STOP_MODE          = "range"  # "range" = other side of the range; "fixed" = ORB_FIXED_STOP_PCT
ORB_FIXED_STOP_PCT     = 0.005
ORB_REWARD_RISK        = 1.5      # target = entry + this × risk
ORB_TRAIL_PCT          = 0.0      # trailing stop distance from the best close (0 = off)
ORB_FLATTEN_ET         = "15:55"  # flat by then (5 min before the close on half days)
ORB_MAX_RISK_PER_TRADE = 5.0      # $ lost at the stop; size also capped at MAX_POSITION_SIZE_PCT
ORB_DAILY_LOSS_LIMIT   = 10.0     # ORB stops for the day after losing this much
ORB_USE_REGIME_FILTER  = False    # only trade when regime.py allows longs
ORB_USE_LLM_CONFIRM    = False    # run the signal through the LLM + MIN_CONFIDENCE
ORB_SLIPPAGE_BPS       = 2.0      # backtest costs per side (Alpaca charges no commission)
ORB_COMMISSION_PER_SHARE = 0.0
