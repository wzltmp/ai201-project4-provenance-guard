"""Signal 3 — Readability heuristics (pure standard library, no external packages).

Measures *structural predictability and personal voice patterns* — orthogonal to Signal 1
(holistic LLM semantics) and Signal 2 (sentence-level stylometric variance):

  - AI prose avoids first- and second-person pronouns (impersonal, generic register).
  - AI prose ends nearly all sentences with periods (monotone punctuation variety).
  - AI prose repeats bigram sequences more than human writing on longer texts.

Each feature normalizes to a 0–1 AI-ness sub-score; a weighted sum gives ``p_ai``.
Calibrated against the mini-corpus in ``scripts/try_scoring.py`` (Stretch Feature A).
"""

from __future__ import annotations

import re
from typing import Any

# --- sub-score weights (sum to 1.0) ---------------------------------------------------------
_W_PRONOUN = 0.40      # personal pronoun absence — most reliable differentiator
_W_PUNCT = 0.35        # punctuation monotony (all-period endings → AI)
_W_BIGRAM = 0.25       # bigram repetition rate (meaningful on longer texts)

# --- normalization thresholds ----------------------------------------------------------------
# At or above this pronoun rate the text is confidently human-voiced.
_PRONOUN_HUMAN_RATE = 0.05   # 5 pronouns per 100 words
# At or below this rate it looks impersonal / AI-like.
_PRONOUN_AI_RATE = 0.005     # 0.5 pronouns per 100 words

# Sentence-ending punctuation splitter (mirrors stylometry._SENTENCE_RE).
_SENTENCE_RE = re.compile(r"[.!?]+(?:\s+|$)")
_WORD_RE = re.compile(r"[A-Za-z']+")

# First- and second-person personal pronouns (case-insensitive whole-word match).
_PRONOUN_RE = re.compile(
    r"\b(i|me|my|mine|myself|we|us|our|ours|ourselves|you|your|yours|yourself|yourselves)\b",
    re.IGNORECASE,
)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _sentences(text: str) -> list[str]:
    parts = [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def compute_features(text: str) -> dict[str, Any]:
    """Compute raw readability features. Exposed for tests / audit logs."""
    words = _WORD_RE.findall(text)
    word_count = len(words)
    sentences = _sentences(text)

    # Bigram repetition: 1 - unique_bigrams/total_bigrams. Meaningful for longer text.
    bigrams = list(zip(words, words[1:])) if len(words) >= 2 else []
    bigram_repeat_rate = (
        round(1.0 - len(set(bigrams)) / len(bigrams), 3) if bigrams else 0.0
    )

    # Fraction of sentence-terminal punctuation groups that are ? or ! (not just periods).
    # Use findall on the original text so we capture the punctuation BEFORE the split strips it.
    terminal_puncts = re.findall(r"[.!?]+", text)
    punct_variety = (
        round(sum(1 for p in terminal_puncts if "?" in p or "!" in p) / len(terminal_puncts), 3)
        if terminal_puncts
        else 0.0
    )

    # First/second-person pronoun density (pronouns per word).
    pronoun_count = len(_PRONOUN_RE.findall(text))
    pronoun_density = round(pronoun_count / word_count, 4) if word_count else 0.0

    return {
        "bigram_repeat_rate": bigram_repeat_rate,
        "punct_variety": punct_variety,
        "pronoun_density": pronoun_density,
        "word_count": word_count,
    }


def score_readability(text: str) -> dict[str, Any]:
    """Score ``text`` with readability heuristics.

    Returns ``{"signal": "readability", "p_ai": float, "features": {...}}``.
    Never raises; degrades to ``p_ai=0.5`` on any error.
    """
    try:
        f = compute_features(text)

        # Personal pronoun absence: low pronoun rate → AI-like (impersonal register).
        # Interpolate: _PRONOUN_HUMAN_RATE → 0 AI-ness; _PRONOUN_AI_RATE → 1 AI-ness.
        sub_pronoun = _clamp(
            1.0 - (f["pronoun_density"] - _PRONOUN_AI_RATE)
            / (_PRONOUN_HUMAN_RATE - _PRONOUN_AI_RATE)
        )

        # Punctuation monotony: all periods → 1.0; varied endings → lower.
        sub_punct = _clamp(1.0 - f["punct_variety"])

        # Bigram repetition.
        sub_bigram = _clamp(f["bigram_repeat_rate"])

        p_ai = (
            _W_PRONOUN * sub_pronoun
            + _W_PUNCT * sub_punct
            + _W_BIGRAM * sub_bigram
        )

        return {"signal": "readability", "p_ai": round(_clamp(p_ai), 3), "features": f}

    except Exception:  # noqa: BLE001
        return {
            "signal": "readability",
            "p_ai": 0.5,
            "features": {
                "bigram_repeat_rate": 0.0,
                "punct_variety": 0.0,
                "pronoun_density": 0.0,
                "word_count": 0,
            },
        }
