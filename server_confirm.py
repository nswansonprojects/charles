"""
server_confirm.py — Terminal key confirmation for server startup.
Shows current API keys from .env and asks user to confirm before launching.
Used by update_keys.py (the `charles-keys` command).
"""

import os
import sys
import time

ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def _load_env() -> dict:
    """Load current .env contents."""
    vals = {}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    vals[k.strip()] = v.strip()
    return vals


def _mask(val: str, show: int = 6) -> str:
    """Show first N chars then mask the rest."""
    if not val or val.startswith("YOUR_") or val.startswith("REPLACE_"):
        return "❌ NOT SET"
    if len(val) <= show:
        return val
    return val[:show] + "..." + val[-4:]


def _save_env(vals: dict):
    """Write updated values back to .env."""
    lines = ["# Trading Bot Credentials\n"]
    for k, v in vals.items():
        lines.append(f"{k}={v}\n")
    with open(ENV_FILE, "w") as f:
        f.writelines(lines)


def _get_input(prompt: str, current: str = "") -> str:
    """Get input, return current value if user just presses Enter."""
    if current:
        val = input(f"{prompt} (current: {_mask(current)}, Enter to keep): ").strip()
    else:
        val = input(f"{prompt}: ").strip()
    return val if val else current


def run_confirmation() -> bool:
    """
    Show current API keys and ask for confirmation.
    Returns True if user confirms and bot should start.
    Returns False if user cancels.
    """
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    RED    = "\033[91m"
    CYAN   = "\033[96m"
    BOLD   = "\033[1m"
    RESET  = "\033[0m"

    print(f"\n{BOLD}{CYAN}{'='*55}{RESET}")
    print(f"{BOLD}{CYAN}  🤖  Charles Trading Bot — Key Confirmation{RESET}")
    print(f"{BOLD}{CYAN}{'='*55}{RESET}\n")

    vals = _load_env()

    if not vals:
        print(f"{RED}⚠️  No .env file found at:{RESET}")
        print(f"   {ENV_FILE}\n")
        print("Create one with: nano ~/charles/.env")
        return False

    # Display current keys
    keys_to_show = [
        ("ALPACA_API_KEY",      "Alpaca API Key",    "required"),
        ("ALPACA_SECRET_KEY",   "Alpaca Secret",     "required"),
        ("TELEGRAM_BOT_TOKEN",  "Telegram Token",    "optional"),
        ("TELEGRAM_CHAT_ID",    "Telegram Chat ID",  "optional"),
        ("GROQ_API_KEY",        "Groq API Key",      "optional"),
        ("DASHBOARD_PASSWORD",  "Dashboard Password","optional"),
    ]

    print(f"{BOLD}Current API Keys:{RESET}")
    print(f"{'─'*55}")

    all_required_set = True
    for key, label, req in keys_to_show:
        val     = vals.get(key, "")
        masked  = _mask(val)
        is_set  = val and not val.startswith("YOUR_") and not val.startswith("REPLACE_")

        if req == "required":
            status = f"{GREEN}✅{RESET}" if is_set else f"{RED}❌{RESET}"
            if not is_set:
                all_required_set = False
        else:
            status = f"{GREEN}✅{RESET}" if is_set else f"{YELLOW}⚪{RESET}"

        req_label = f"{CYAN}[required]{RESET}" if req == "required" else f"[optional]"
        print(f"  {status} {label:<22} {masked:<30} {req_label}")

    print(f"{'─'*55}")
    print(f"\n{BOLD}File location:{RESET} {ENV_FILE}\n")

    if not all_required_set:
        print(f"{RED}⚠️  Required keys are missing. You must set them before starting.{RESET}\n")

    # Ask what to do
    print("Options:")
    print(f"  {GREEN}[1]{RESET} Save keys and restart the bot")
    print(f"  {YELLOW}[2]{RESET} Update a key")
    print(f"  {YELLOW}[3]{RESET} View raw .env file")
    print(f"  {RED}[4]{RESET} Cancel\n")

    while True:
        choice = input("Choose [1/2/3/4]: ").strip()

        if choice == "1":
            if not all_required_set:
                print(f"\n{RED}Cannot continue — required keys are missing. Choose [2] to update them.{RESET}\n")
                continue
            print(f"\n{GREEN}✅ Keys confirmed{RESET}\n")
            return True

        elif choice == "2":
            print(f"\n{BOLD}Which key would you like to update?{RESET}")
            for i, (key, label, req) in enumerate(keys_to_show, 1):
                print(f"  [{i}] {label}")
            print(f"  [0] Back\n")

            try:
                idx = int(input("Choose: ").strip())
                if idx == 0:
                    continue
                if 1 <= idx <= len(keys_to_show):
                    key, label, _ = keys_to_show[idx - 1]
                    current = vals.get(key, "")
                    print(f"\nCurrent {label}: {_mask(current)}")
                    new_val = input(f"New value (paste here, Enter to keep current): ").strip()
                    if new_val:
                        vals[key] = new_val
                        _save_env(vals)
                        print(f"{GREEN}✅ {label} updated{RESET}\n")
                        # Reload display
                        return run_confirmation()
            except (ValueError, IndexError):
                pass

        elif choice == "3":
            print(f"\n{BOLD}Raw .env file contents:{RESET}")
            print(f"{'─'*55}")
            with open(ENV_FILE) as f:
                for line in f:
                    line = line.rstrip()
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        # Mask values for security
                        print(f"{k}={_mask(v.strip())}")
                    else:
                        print(line)
            print(f"{'─'*55}\n")

        elif choice == "4":
            print(f"\n{YELLOW}Cancelled — nothing changed, the bot keeps running.{RESET}\n")
            return False

        else:
            print("Please enter 1, 2, 3, or 4")


if __name__ == "__main__":
    print("Run update_keys.py (or `charles-keys`) instead — it also applies the keys and restarts the bot.")
