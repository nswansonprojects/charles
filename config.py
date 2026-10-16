# ============================================================
#  CHARLES CONFIGURATION
#  The plan's settings (core %, picks, safety switch) live in strategy.json,
#  read by strategy_settings.py over allocator.DEFAULTS. Keys live in .env.
# ============================================================

# --- Alpaca paper trading ← YOUR KEYS GO IN .env FILE ------
# Leave these empty — keys are loaded from .env by credential_manager.
ALPACA_API_KEY    = ""
ALPACA_SECRET_KEY = ""
# Paper trading is fixed in alpaca_executor._client() (paper=True); there is no switch here.

# --- Telegram alerts ← YOUR KEYS GO IN .env FILE -----------
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID   = ""

# --- AI (the news veto only) ---------------------------------
# Free key at console.groq.com, set as GROQ_API_KEY in .env. Without it Charles runs the plan
# with no news veto.
GROQ_API_KEY = ""
GROQ_MODEL   = "openai/gpt-oss-20b"

# --- Account ----------------------------------------------------
VIRTUAL_CASH   = 1_000.0   # the starting balance returns are measured from
MAX_DAILY_LOSS = 20.0      # no buys for the rest of the day after this loss (owner's setting;
                           # data/launch_authorized.json "daily_loss" overrides it)

LOG_LEVEL = "INFO"
