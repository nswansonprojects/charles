# ============================================================
#  CHARLES CONFIGURATION
#  The plan's settings (core %, picks, safety switch) live in strategy.json,
#  read by strategy_settings.py over allocator.DEFAULTS. Keys live in .env.
# ============================================================

# --- Alpaca paper trading ← YOUR KEYS GO IN .env FILE ------
# Leave these empty — keys are loaded from .env by credential_manager.
ALPACA_API_KEY    = ""
ALPACA_SECRET_KEY = ""
# Paper unless the user turned live trading on over SSH (live.py, charles-live): alpaca_executor._client() decides.

# --- AI (the news veto only) ---------------------------------
# Free key at console.groq.com, set as GROQ_API_KEY in .env. Without it Charles runs the plan
# with no news veto.
GROQ_API_KEY = ""
GROQ_MODEL   = "openai/gpt-oss-20b"

# --- Account ----------------------------------------------------
VIRTUAL_CASH   = 1_000.0   # fallback starting balance: returns are measured from data/start.json, written at the
                           # bot's first start from the account's value (equity_tracker.ensure_start)
MAX_DAILY_LOSS = None      # $: no buys for the rest of the day after this loss. None = MAX_DAILY_LOSS_PCT below.
                           # data/launch_authorized.json "daily_loss" overrides it (the owner's $20)
MAX_DAILY_LOSS_PCT = 2.0   # % of the account at yesterday's close, used when MAX_DAILY_LOSS is None, so the
                           # limit fits any account size ($20 was 0.02% of a new $100,000 paper account)

LOG_LEVEL = "INFO"
