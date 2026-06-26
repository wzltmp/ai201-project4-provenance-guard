"""Confidence fusion — combine the two signals into one calibrated score + verdict.

Implements planning.md §2 verbatim:

    p_ai       = 0.6*p_llm + 0.4*p_stylo        # LLM weighted higher, can't dominate
    raw        = 2*|p_ai - 0.5|                  # certainty of direction: 0 at coin-flip, 1 at extremes
    disagree   = |p_llm - p_stylo|               # how much the two signals conflict
    short_pen  = 1.0 if word_count >= 60 else scaled toward 0.5 by length
    confidence = clamp(raw * (1 - 0.5*disagree) * short_pen, 0, 1)

``confidence`` is an honest *certainty of direction*, NOT the probability of AI. The verdict uses a
single symmetric bar (``VERDICT_BAR``); ``p_ai`` only picks which side. Below the bar → ``uncertain``
(the system declines to make a confident public claim — see planning.md §2).
"""

from __future__ import annotations

from typing import Any

# --- tunable constants (calibrated via scripts/try_scoring.py) -----------------------------
WEIGHT_LLM = 0.6
WEIGHT_STYLO = 0.4
DISAGREE_STRENGTH = 0.5   # full disagreement multiplies confidence by (1 - 0.5) = 0.5
SHORT_TEXT_MIN = 60       # at/above this word count, no short-text penalty
SHORT_TEXT_FLOOR = 0.5    # penalty multiplier as length → 0
VERDICT_BAR = 0.70        # confidence needed to make a definitive (ai|human) claim


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _short_text_penalty(word_count: int) -> float:
    """1.0 for texts >= SHORT_TEXT_MIN words; scales linearly down to SHORT_TEXT_FLOOR at 0 words."""
    if word_count >= SHORT_TEXT_MIN:
        return 1.0
    return SHORT_TEXT_FLOOR + (1.0 - SHORT_TEXT_FLOOR) * (word_count / SHORT_TEXT_MIN)


def fuse(p_llm: float, p_stylo: float, word_count: int) -> dict[str, Any]:
    """Fuse the two per-signal probabilities into ``{p_ai, confidence, verdict}``.

    ``verdict`` ∈ {``ai``, ``human``, ``uncertain``}.
    """
    p_ai = WEIGHT_LLM * p_llm + WEIGHT_STYLO * p_stylo

    raw = 2.0 * abs(p_ai - 0.5)
    disagree = abs(p_llm - p_stylo)
    short_pen = _short_text_penalty(word_count)

    confidence = _clamp(raw * (1.0 - DISAGREE_STRENGTH * disagree) * short_pen)

    if confidence < VERDICT_BAR:
        verdict = "uncertain"
    elif p_ai >= 0.5:
        verdict = "ai"
    else:
        verdict = "human"

    return {
        "p_ai": round(p_ai, 3),
        "confidence": round(confidence, 3),
        "verdict": verdict,
    }
