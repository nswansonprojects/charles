"""
brain.py — AI decision engine v5.
New in v5:
  - Multi-timeframe (hourly + daily) signal weighting
  - LLM position context (bot knows what it owns + P&L)
  - Time-of-day awareness in prompts
  - Macro event blackout awareness
  - FinBERT sentiment scores
  - Position re-evaluation for existing holdings
"""

import json
import logging
import os
import requests
from datetime import datetime, timezone

import config
log = logging.getLogger(__name__)

DECISIONS_FILE = os.path.join(os.path.dirname(__file__), "data", "decisions.jsonl")


# Inputs saved with every decision so a model can later learn which ones predict returns
# (roadmap: learning model). Scalars only; price_history and the like are left out.
FEATURE_KEYS = ("change_pct", "intraday_change_pct", "vol_ratio", "market_cap", "rsi_7", "rsi_21",
                "macd", "macd_signal", "bb_pos", "atr_pct", "ma50_dist_pct", "above_ma50",
                "hourly_macd", "hourly_macd_signal", "hourly_bb_pos", "hourly_trend")


def _log_decision(symbol: str, d: dict, held: bool, regime: str, raw: dict, final: dict,
                  sentiment: float | None = None):
    """One line per decision, so the AI can later be scored against the rule signal."""
    try:
        rule_signal, rule_score = technical_signal(d)
        row = {
            "ts":          datetime.now(timezone.utc).isoformat(),
            "symbol":      symbol,
            "price":       d.get("price"),
            "held":        held,
            "regime":      regime,
            "rule_signal": rule_signal,
            "rule_score":  rule_score,
            "ai_action":   raw.get("action"),
            "ai_conf":     raw.get("confidence"),
            "source":      raw.get("source"),
            "final":       final.get("action"),
            "rsi":         d.get("rsi"),
            "hourly_rsi":  d.get("hourly_rsi"),
            "mtf":         d.get("mtf_agreement"),
            "sentiment":   sentiment,
            "features":    {k: d.get(k) for k in FEATURE_KEYS if d.get(k) is not None},
        }
        with open(DECISIONS_FILE, "a") as f:
            f.write(json.dumps(row) + "\n")
    except Exception as e:
        log.debug(f"Decision log failed {symbol}: {e}")


# ── Rule-based signal ─────────────────────────────────────────────────────────

def technical_signal(d: dict) -> tuple[str, float]:
    """
    Multi-timeframe signal using daily + hourly indicators.
    Returns (signal, score) where score is -1.0 to +1.0.
    """
    # Daily indicators
    rsi_d    = d.get("rsi", 50)
    macd_d   = d.get("macd", 0)
    sig_d    = d.get("macd_signal", 0)
    bb_pos_d = d.get("bb_pos", 0.5)
    vol_ok      = d.get("vol_confirm", True)
    crossed_ma50 = d.get("crossed_ma50", False)
    above_ma50   = d.get("above_ma50", False)
    ma50_dist    = d.get("ma50_dist_pct", 0.0)
    mtf_buy  = d.get("mtf_buy", False)
    mtf_sell = d.get("mtf_sell", False)

    # Hourly indicators
    rsi_h    = d.get("hourly_rsi", rsi_d)
    macd_h   = d.get("hourly_macd", macd_d)
    sig_h    = d.get("hourly_macd_signal", sig_d)
    bb_pos_h = d.get("hourly_bb_pos", bb_pos_d)
    mtf_agr  = d.get("mtf_agreement", "mixed")

    score = 0.0

    # ── Daily signals (60% weight) ──
    if rsi_d < config.RSI_OVERSOLD:
        score += 0.25
    elif rsi_d > config.RSI_OVERBOUGHT:
        score -= 0.25

    if macd_d > sig_d:
        score += 0.15
    else:
        score -= 0.15

    if bb_pos_d < 0.15:
        score += 0.10
    elif bb_pos_d > 0.85:
        score -= 0.10

    # ── Hourly signals (40% weight) ──
    if rsi_h < config.RSI_OVERSOLD:
        score += 0.18
    elif rsi_h > config.RSI_OVERBOUGHT:
        score -= 0.18

    if macd_h > sig_h:
        score += 0.12
    else:
        score -= 0.12

    if bb_pos_h < 0.20:
        score += 0.08
    elif bb_pos_h > 0.80:
        score -= 0.08

    # ── Multi-TF agreement bonus ──
    if mtf_agr == "bullish":
        score += 0.12
    elif mtf_agr == "bearish":
        score -= 0.12

    # ── Volume gate ──
    if not vol_ok:
        score *= 0.6

    # ── 50MA breakout bonus ──
    # Crossed up: strong momentum entry signal
    if crossed_ma50 and 45 <= rsi_d <= 72 and macd_d > sig_d:
        score += 0.20
    # Already above MA50 and close to it (continuation): mild boost
    elif above_ma50 and 0 < ma50_dist <= 5.0 and 45 <= rsi_d <= 68:
        score += 0.10
    # Below MA50: mild penalty — not in uptrend
    elif not above_ma50 and rsi_d > config.RSI_OVERSOLD:
        score -= 0.08

    # ── MTF legacy bonus ──
    if mtf_buy:
        score += 0.05
    elif mtf_sell:
        score -= 0.05

    score = round(max(-1.0, min(1.0, score)), 3)

    if score >= 0.40:
        return "BUY", score
    elif score <= -0.35:
        return "SELL", score
    return "HOLD", score


def sentiment_for_symbol(symbol: str, news: list) -> float:
    base = symbol.split("/")[0]
    rel  = [a["sentiment"] for a in news if base in a.get("mentioned", [])]
    return round(sum(rel) / len(rel), 3) if rel else 0.0


# ── Groq LLM ──────────────────────────────────────────────────────────────────

def _get_groq_key() -> str:
    """Get Groq API key from credential_manager or config."""
    try:
        import credential_manager as cm
        key = cm.get("GROQ_API_KEY")
        if key and not key.startswith(("YOUR_", "REPLACE_")):
            return key
    except Exception:
        pass
    return getattr(config, "GROQ_API_KEY", "")


def _ask_groq(prompt: str) -> str:
    """Call Groq API — free tier, very fast inference."""
    key = _get_groq_key()
    if not key:
        return ""
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model":       config.GROQ_MODEL,
                "messages":    [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens":  1500,   # gpt-oss-20b spends tokens on internal reasoning before the answer
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        log.warning(f"Groq error: {e} — rule-based fallback")
        return ""


def _ask_llm(prompt: str) -> tuple[str, str]:
    """
    Unified LLM call. Returns (response_text, provider_used).
    Uses Groq if key is set. An empty response means the caller uses the rule fallback.
    (The Ollama fallback was removed 2026-10-01: llama3.1 can't load in this server's 3 GB RAM.)
    """
    provider = getattr(config, "LLM_PROVIDER", "auto")

    if provider == "groq" or (provider == "auto" and _get_groq_key()):
        response = _ask_groq(prompt)
        if response:
            log.debug("🚀 Groq responded")
            return response, "groq"
        log.warning("Groq failed — rule-based fallback")

    return "", "none"


def llm_decision(
    symbol: str,
    d: dict,
    sentiment: float,
    headlines: list[str],
    regime: str = "TRENDING_BULL",
    allow_short: bool = False,
    position_context: dict | None = None,
    session_info: dict | None = None,
    macro_event: str | None = None,
    force_rules_only: bool = False,
) -> dict:
    """
    Full AI decision with position context, time-of-day, and macro awareness.
    If force_rules_only is True, skips Groq entirely and returns the rule-based
    signal — used when the per-cycle Groq token budget has been exhausted.
    """
    rule_signal, rule_score = technical_signal(d)

    if force_rules_only:
        action = rule_signal
        if action == "SHORT":
            action = "SELL"
        return {
            "action":     action,
            "confidence": 0.55,   # below MIN_CONFIDENCE on purpose: rules never trade (#21)
            "reasoning":  f"Token budget exhausted this cycle — rule fallback: "
                          f"score={rule_score:+.2f}",
            "source":     "rules",
        }
    headlines_text = "\n".join(f"- {h}" for h in headlines[:5]) or "None"

    # ── Position context block ──
    if position_context and position_context.get("held"):
        pos = position_context
        pnl_sign = "+" if pos.get("unrealized_pnl", 0) >= 0 else ""
        pos_block = f"""
## Current Position (YOU ALREADY OWN THIS)
- Entry price:      ${pos.get('avg_price', 0):,.4f}
- Current P&L:      {pnl_sign}${pos.get('unrealized_pnl', 0):,.2f} ({pnl_sign}{pos.get('unrealized_pct', 0):.1f}%)
- Position value:   ${pos.get('market_value', 0):,.2f}
- Trailing stop at: ${pos.get('trailing_stop', 0):,.4f}
- Hold duration:    {pos.get('hold_days', '?')} days
- Re-evaluate:      Default is HOLD — stops and take-profit manage the exit.
  SELL only if the daily trend has clearly turned bearish. Mixed hourly
  signals, low volume, or "would not buy again today" are NOT reasons to SELL."""
    else:
        pos_block = "\n## Current Position\n- Not held. Fresh entry decision."

    # ── Session context ──
    session_block = ""
    if session_info:
        phase = session_info.get("phase", "unknown")
        mins  = session_info.get("mins_left", 0)
        opt   = session_info.get("optimal_window", False)
        session_block = f"\n## Session Context\n- Phase: {phase} ({session_info.get('time_et','?')})"
        if mins > 0:
            session_block += f"  |  {mins} min to close"
        session_block += f"\n- Optimal window: {'✅ Yes' if opt else '❌ No (signals less reliable)'}"

    # ── Macro event warning ──
    macro_block = ""
    if macro_event:
        macro_block = f"\n## ⚠️ Macro Event\n- {macro_event}\n- Consider HOLD to avoid whipsaw around this event."

    actions = '"BUY" | "SELL" | "HOLD"  (no shorting — cash account)'

    prompt = f"""You are a disciplined quantitative portfolio manager.
Regime: {regime} | Sentiment model: FinBERT
{pos_block}{session_block}{macro_block}

## Multi-Timeframe Signals for {symbol}
### Daily (trend, 60% weight)
- Price:         ${d.get('price','N/A')}  ({d.get('change_pct',0):+.2f}% today)
- RSI daily:     {d.get('rsi',50):.1f}  |  RSI 7d: {d.get('rsi_7',50):.1f}
- MACD:          {d.get('macd',0):.4f}  Signal: {d.get('macd_signal',0):.4f}
- BB position:   {d.get('bb_pos',0.5):.2f} (0=lower band, 1=upper band)
- ATR:           {d.get('atr_pct',0):.2f}% of price

### Hourly (momentum, 40% weight)
- RSI hourly:    {d.get('hourly_rsi',50):.1f}
- MACD hourly:   {d.get('hourly_macd',0):.4f}  Signal: {d.get('hourly_macd_signal',0):.4f}
- BB hourly:     {d.get('hourly_bb_pos',0.5):.2f}
- Intraday move: {d.get('intraday_change_pct',0):+.2f}%
- MTF agreement: {d.get('mtf_agreement','mixed').upper()}

### Confirmation
- Volume:        {d.get('vol_ratio',1.0):.1f}x avg  confirmed={d.get('vol_confirm',True)}
- Rule signal:   {rule_signal} (score={rule_score:+.2f})

## Sentiment & Macro
- FinBERT news:  {sentiment:+.3f} (-1 bearish → +1 bullish)

## Top Headlines
{headlines_text}

## Instructions
Weigh ALL timeframes. Daily sets the trend, hourly confirms timing.
Only describe RSI as "overbought" if it is above {config.RSI_OVERBOUGHT}, or "oversold" if below {config.RSI_OVERSOLD}.
Otherwise call it neutral or moderate. Do not use "overbought"/"oversold" language based on BB position, MACD, or general impression — those terms are reserved for RSI crossing its actual threshold.
{"SHORT = profit from price decline (bear regime only)." if allow_short else ""}
Respond ONLY with valid JSON, no extra text:
{{
  "action":     {actions},
  "confidence": 0.0 to 1.0,
  "reasoning":  "one concise sentence covering both timeframes"
}}"""

    raw, provider = _ask_llm(prompt)

    try:
        s = raw.find("{"); e = raw.rfind("}") + 1
        if s != -1 and e > s:
            res    = json.loads(raw[s:e])
            action = res.get("action", "HOLD").upper()
            return {
                "action":     action,
                "confidence": float(res.get("confidence", 0.5)),
                "reasoning":  res.get("reasoning", ""),
                "source":     f"llm:{provider}",
            }
    except Exception as ex:
        log.warning(f"LLM parse failed {symbol}: {ex}")

    action = rule_signal
    if action == "SHORT":
        action = "SELL"
    return {
        "action":     action,
        "confidence": 0.55,   # below MIN_CONFIDENCE on purpose: rules never trade (#21)
        "reasoning":  f"Rule fallback: score={rule_score:+.2f} | MTF={d.get('mtf_agreement','?')} | hourly_rsi={d.get('hourly_rsi',50):.1f}",
        "source":     "rules",
    }


# ── Full snapshot analysis ────────────────────────────────────────────────────

def analyse_snapshot(
    snapshot: dict,
    regime: str = "TRENDING_BULL",
    open_positions: dict | None = None,
) -> list[dict]:
    """
    Analyse all symbols. Pass open_positions dict for LLM context.
    open_positions: {symbol: {avg_price, unrealized_pnl, unrealized_pct,
                               market_value, trailing_stop, hold_days}}
    """
    try:
        from regime import regime_multipliers
        mults = regime_multipliers(regime)
    except Exception:
        mults = {"allow_shorts": False, "allow_hedges": False}

    allow_short = mults.get("allow_shorts", False)

    # Get session and macro context once for all symbols
    try:
        import timeofday as tod
        sess = tod.session_info()
    except Exception:
        sess = {}

    try:
        import macro_calendar as mc
        in_blackout, macro_event = mc.in_macro_blackout()
    except Exception:
        in_blackout, macro_event = False, None

    if in_blackout:
        log.warning(f"  📅 Macro blackout: {macro_event} — suppressing new entries")

    decisions  = []
    news       = snapshot.get("news", [])
    market     = snapshot.get("market", {})
    positions  = open_positions or {}

    # ── Per-cycle Groq token budget enforcement ─────────────────────────────
    EST_TOKENS_PER_CALL = 900  # measured ~643 real tokens on gpt-oss-20b, +margin
    token_budget = getattr(config, "GROQ_TOKEN_BUDGET", 6000)
    tokens_used_this_cycle = 0
    budget_exhausted_logged = False

    for symbol, d in market.items():
        force_rules_only = False
        if tokens_used_this_cycle + EST_TOKENS_PER_CALL > token_budget:
            if not budget_exhausted_logged:
                log.warning(f"  ⚠️  Groq token budget ({token_budget}) reached this cycle — "
                            f"remaining symbols use rule-based fallback")
                budget_exhausted_logged = True
            force_rules_only = True
        else:
            tokens_used_this_cycle += EST_TOKENS_PER_CALL
        # Pace Groq calls so they never burst past the TPM ceiling
        if not force_rules_only:
            import time as _time
            _time.sleep(getattr(config, "GROQ_CALL_DELAY_SEC", 8.6))
        log.info(f"🧠 Analysing {symbol}...")

        sentiment = sentiment_for_symbol(symbol, news)
        headlines = [a["title"] for a in news
                     if symbol.split("/")[0] in a.get("mentioned", [])]

        # Build position context for LLM
        pos_ctx = positions.get(symbol) or positions.get(symbol.replace("/", ""))
        if pos_ctx:
            pos_ctx = dict(pos_ctx, held=True)
        else:
            pos_ctx = {"held": False}

        # Suppress new buys during macro blackout (still re-evaluate existing)
        effective_macro = macro_event if (in_blackout and not pos_ctx["held"]) else None

        decision = llm_decision(
            symbol, d, sentiment, headlines,
            regime=regime,
            allow_short=allow_short,
            position_context=pos_ctx,
            session_info=sess,
            macro_event=effective_macro,
            force_rules_only=force_rules_only,
        )
        raw_decision = dict(decision)   # before the sell gate / time filter below

        # Don't let Groq flip-flop out of a position: an LLM SELL only goes
        # through if it's genuinely high-confidence, however long we've held.
        # Rule-based SELLs and the trailing/fixed stops are separate and
        # unaffected by this — they can still close the position mechanically.
        sell_min_conf = getattr(config, "LLM_SELL_MIN_CONFIDENCE", 0.75)
        if (pos_ctx["held"] and decision["action"] == "SELL"
                and decision.get("source", "").startswith("llm")
                and decision.get("confidence", 0) < sell_min_conf):
            log.info(f"  ⏳ {symbol}: LLM SELL confidence "
                     f"{decision.get('confidence', 0):.0%} below "
                     f"{sell_min_conf:.0%} — HOLD instead of SELL")
            decision["action"]    = "HOLD"
            decision["reasoning"] = (f"LLM SELL confidence {decision.get('confidence', 0):.0%} "
                                      f"below {sell_min_conf:.0%} exit threshold. "
                                      f"Original: {decision.get('reasoning', '')}")

        # Override to HOLD during avoid windows for new entries
        if (sess.get("avoid_trading") and decision["action"] == "BUY"
                and not pos_ctx["held"]):
            log.info(f"  ⏰ {symbol}: time filter ({sess.get('avoid_reason','')}) — HOLD")
            decision["action"]    = "HOLD"
            decision["reasoning"] = f"Time filter: {sess.get('avoid_reason','opening/closing noise')}"

        _log_decision(symbol, d, pos_ctx["held"], regime, raw_decision, decision, sentiment)

        decisions.append({
            "symbol":       symbol,
            "type":         d.get("type", "stock"),
            "price":        d.get("price"),
            "change_pct":   d.get("change_pct"),
            "rsi":          d.get("rsi"),
            "hourly_rsi":   d.get("hourly_rsi"),
            "mtf_agreement":d.get("mtf_agreement", "mixed"),
            "bb_pos":       d.get("bb_pos"),
            "vol_confirm":  d.get("vol_confirm", True),
            "sentiment":    sentiment,
            "action":       decision["action"],
            "confidence":   decision["confidence"],
            "reasoning":    decision["reasoning"],
            "source":       decision.get("source", "llm"),
            "regime":       regime,
            "session_phase":sess.get("phase", "unknown"),
        })

    return decisions
