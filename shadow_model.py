"""
shadow_model.py — a second AI model, scored on the same decisions as the live one (roadmap task 7).

After each trading cycle the prompts the live AI just answered (brain.take_last_prompts) go to a
second Groq model; its BUY/SELL/HOLD is logged to data/decisions_model_b.jsonl in the same format
as data/decisions.jsonl, and decision_scorer grades both side by side ("model B · ..." groups). A
newer model goes live only if it picks better on our own data. Never trades and never blocks the
live cycle: it runs in its own thread, one call per symbol per cycle, stops on HTTP 429 and skips
the cycle if the previous run is still going.
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)

MODEL = "openai/gpt-oss-120b"
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "decisions_model_b.jsonl")
CALL_DELAY_SEC = 8.0        # pacing between calls (Groq free tier tokens/minute)
_busy = threading.Lock()


class RateLimited(Exception):
    pass


def _ask(prompt: str) -> str:
    import requests
    import brain
    key = brain._get_groq_key()
    if not key:
        raise RuntimeError("no Groq key")
    r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                      json={"model": MODEL, "messages": [{"role": "user", "content": prompt}],
                            "temperature": 0.2, "max_tokens": 1500},
                      timeout=30)
    if r.status_code == 429:
        raise RateLimited()
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def parse(text: str):
    """(ACTION, confidence) from the model's JSON answer, or None."""
    try:
        s, e = text.find("{"), text.rfind("}") + 1
        res = json.loads(text[s:e])
        action = str(res.get("action", "")).upper()
        if action not in ("BUY", "SELL", "HOLD"):
            return None
        return action, float(res.get("confidence", 0.5))
    except (ValueError, TypeError, AttributeError):
        return None


def run(prompts: dict, decisions: list, regime: str, ask=_ask, sleep=time.sleep,
        path: str | None = None) -> int:
    """Ask the second model about each prompt and log its answers. Returns rows written."""
    live = {d.get("symbol"): d for d in decisions}
    written = 0
    for i, (sym, p) in enumerate(prompts.items()):
        if i:
            sleep(CALL_DELAY_SEC)
        try:
            parsed = parse(ask(p["prompt"]))
        except RateLimited:
            log.info("Second model: rate limited (429), rest of this cycle skipped")
            break
        except Exception as e:
            log.info(f"Second model: {sym} skipped ({type(e).__name__})")
            continue
        if not parsed:
            continue
        row = {"ts": datetime.now(timezone.utc).isoformat(), "symbol": sym,
               "price": p.get("price") or live.get(sym, {}).get("price"), "held": p.get("held", False),
               "regime": regime, "rule_signal": p.get("rule_signal"), "ai_action": parsed[0],
               "ai_conf": parsed[1], "source": f"llm:{MODEL}",
               "live_action": live.get(sym, {}).get("action")}
        with open(path or LOG_FILE, "a") as f:
            f.write(json.dumps(row) + "\n")
        written += 1
    return written


def start(decisions: list, regime: str) -> bool:
    """Called after each live cycle: run() in a background thread unless one is still going."""
    import brain
    prompts = brain.take_last_prompts()
    if not prompts or not _busy.acquire(blocking=False):
        return False

    def go():
        try:
            n = run(prompts, decisions, regime)
            log.info(f"  🧪 Second model ({MODEL}): {n}/{len(prompts)} decisions logged")
        except Exception as e:
            log.warning(f"Second model run failed: {e}")
        finally:
            _busy.release()
    threading.Thread(target=go, name="shadow-model", daemon=True).start()
    return True
