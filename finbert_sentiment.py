"""
finbert_sentiment.py — Finance-specific sentiment analysis.
Uses FinBERT (ProsusAI/finbert) which understands financial language
far better than VADER. Runs 100% locally via HuggingFace transformers.
Falls back to VADER if transformers not available.
All free, no API calls.
"""

import logging
import os

log = logging.getLogger(__name__)

# Once the model is in the local cache, keep Hugging Face offline: otherwise every load
# checks the Hub (logging "unauthenticated requests to the HF Hub ... set a HF_TOKEN").
# Must be set before transformers/huggingface_hub are imported; this module is the only
# importer. With no cache yet, stay online so the first run can download the model.
_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "finbert_cache")
if os.path.isdir(os.path.join(_CACHE_DIR, "models--ProsusAI--finbert", "snapshots")):
    os.environ.setdefault("HF_HUB_OFFLINE", "1")

# Cache the model so we only load it once
_pipeline = None
_use_finbert = None


def _load_model():
    global _pipeline, _use_finbert
    if _use_finbert is not None:
        return _use_finbert

    try:
        from transformers import pipeline as hf_pipeline
        import torch

        log.info("🧠 Loading FinBERT sentiment model (first run may take a minute)...")
        cache_dir = _CACHE_DIR
        os.makedirs(cache_dir, exist_ok=True)

        _pipeline = hf_pipeline(
            "text-classification",
            model="ProsusAI/finbert",
            tokenizer="ProsusAI/finbert",
            model_kwargs={"cache_dir": cache_dir},
            device=-1,      # CPU — no GPU needed
            top_k=None,     # return all three labels
        )
        log.info("✅ FinBERT loaded successfully")
        _use_finbert = True
        return True

    except ImportError:
        log.info("ℹ️  transformers not installed — using VADER sentiment (still good)")
        _use_finbert = False
        return False
    except Exception as e:
        log.warning(f"FinBERT load failed ({e}) — falling back to VADER")
        _use_finbert = False
        return False


def score(text: str) -> float:
    """
    Returns sentiment score from -1.0 (bearish) to +1.0 (bullish).
    Uses FinBERT if available, otherwise VADER.
    Truncates text to 512 chars (FinBERT token limit).
    """
    text = text[:512].strip()
    if not text:
        return 0.0

    if _load_model() and _pipeline is not None:
        return _score_finbert(text)
    else:
        return _score_vader(text)


def _score_finbert(text: str) -> float:
    """FinBERT returns positive/negative/neutral with probabilities."""
    try:
        results = _pipeline(text)[0]
        scores = {r["label"].lower(): r["score"] for r in results}
        # Convert to -1 to +1 scale
        positive = scores.get("positive", 0)
        negative = scores.get("negative", 0)
        return round(positive - negative, 4)
    except Exception as e:
        log.debug(f"FinBERT score failed: {e}")
        return _score_vader(text)


def _score_vader(text: str) -> float:
    """VADER fallback — general purpose but less finance-aware."""
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        vader = SentimentIntensityAnalyzer()
        return round(vader.polarity_scores(text)["compound"], 4)
    except Exception:
        return 0.0


def score_batch(texts: list[str]) -> list[float]:
    """Score multiple texts efficiently."""
    return [score(t) for t in texts]


def is_finbert_active() -> bool:
    """Returns True if FinBERT is loaded and running."""
    return _load_model() and _pipeline is not None
