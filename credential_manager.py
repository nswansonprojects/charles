"""
secrets.py — Secure credential management.

Loads sensitive keys from environment variables first,
falling back to config.py values. This way config.py
can have empty strings and real keys live in .env only.

Setup (one time):
  1. Create a file called .env in your tradingbot folder
  2. Add your keys:
       ALPACA_API_KEY=your_key_here
       ALPACA_SECRET_KEY=your_secret_here
       TELEGRAM_BOT_TOKEN=your_token_here
       TELEGRAM_CHAT_ID=your_chat_id_here
       DASHBOARD_PASSWORD=choose_a_password
  3. The bot reads from .env automatically on startup
  4. Never share or commit the .env file

The .env file is listed in .gitignore automatically.
"""

import os
import logging

log = logging.getLogger(__name__)

# Load .env file if it exists (python-dotenv)
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(_env_path):
        try:
            load_dotenv(_env_path, encoding="utf-8")
            log.info("✅ Loaded credentials from .env file")
        except Exception:
            # .env has encoding issues — read it manually
            try:
                vals = {}
                with open(_env_path, encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if "=" in line and not line.startswith("#"):
                            k, v = line.split("=", 1)
                            os.environ[k.strip()] = v.strip()
                log.info("✅ Loaded credentials from .env file (manual parse)")
            except Exception as e:
                log.warning(f"⚠️  Could not load .env file: {e} — using config.py values")
    else:
        log.info("ℹ️  No .env file found — using config.py values")
except ImportError:
    pass


def get(key: str, fallback: str = "") -> str:
    """
    Get a credential. Checks environment variables first,
    then falls back to config.py.
    """
    import config
    return os.environ.get(key) or getattr(config, key, fallback) or fallback


def alpaca_key() -> str:
    return get("ALPACA_API_KEY")

def alpaca_secret() -> str:
    return get("ALPACA_SECRET_KEY")

def telegram_token() -> str:
    return get("TELEGRAM_BOT_TOKEN")

def telegram_chat_id() -> str:
    return get("TELEGRAM_CHAT_ID")

def groq_api_key() -> str:
    """Get Groq API key from env or config."""
    return get("GROQ_API_KEY", "")


def dashboard_password() -> str:
    """Password for the Streamlit dashboard. Empty = no password required."""
    return get("DASHBOARD_PASSWORD", "")


def validate() -> dict[str, bool]:
    """Check which credentials are configured."""
    return {
        "alpaca":   bool(alpaca_key() and alpaca_key() != "YOUR_ALPACA_API_KEY_HERE"),
        "telegram": bool(telegram_token()),
        "password": bool(dashboard_password()),
    }


def create_env_template():
    """
    Write a .env.template file showing the format.
    Users copy this to .env and fill in their values.
    """
    template = """# ============================================================
#  TRADING BOT SECRETS  —  copy this file to .env
#  and fill in your actual values.
#  NEVER share or commit the .env file.
# ============================================================

# Alpaca Paper Trading (get from alpaca.markets)
ALPACA_API_KEY=your_alpaca_key_here
ALPACA_SECRET_KEY=your_alpaca_secret_here

# Telegram Bot (get from @BotFather)
TELEGRAM_BOT_TOKEN=your_telegram_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_id_here

# Dashboard password (choose any password you want)
# Leave empty to disable password protection
DASHBOARD_PASSWORD=
"""
    path = os.path.join(os.path.dirname(__file__), ".env.template")
    with open(path, "w") as f:
        f.write(template)
    log.info(f"📄 .env.template created at {path}")

    # Also create .gitignore to protect .env
    gi_path = os.path.join(os.path.dirname(__file__), ".gitignore")
    if not os.path.exists(gi_path):
        with open(gi_path, "w") as f:
            f.write(".env\n*.pyc\n__pycache__/\ndata/\nlogs/\n")
        log.info("🔒 .gitignore created to protect .env file")
