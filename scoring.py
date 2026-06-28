"""Confidence fusion — combine signals into one calibrated score + verdict.

Two fusion paths (planning.md §2 + Stretch Feature A):

  3-signal prose path (p_read is not None):
    p_ai       = 0.50*p_llm + 0.30*p_stylo + 0.20*p_read
    disagree   = mean of the 3 pairwise |pi − pj|

  2-signal fallback (p_read is None, used for metadata content):
    p_ai       = 0.60*p_llm + 0.40*p_stylo
    disagree   = |p_llm − p_stylo|

  Shared:
    raw        = 2*|p_ai - 0.5|                     certainty of direction
    short_pen  = 1.0 if word_count ≥ 60, else scaled toward 0.5
    confidence = clamp(raw * (1 − 0.5*disagree) * short_pen, 0, 1)

``confidence`` is certainty of the direction claim, NOT probability of AI. The verdict uses a
single symmetric bar; ``p_ai`` only picks the side. Below the bar → ``uncertain``.
"""

from __future__ import annotations

from typing import Any

# Shared constants.
DISAGREE_STRENGTH = 0.5   # full disagreement multiplies confidence by 0.5
SHORT_TEXT_MIN = 60       # at/above this word count, no short-text penalty
SHORT_TEXT_FLOOR = 0.5    # penalty multiplier at 0 words
VERDICT_BAR = 0.70        # confidence threshold for a definitive verdict


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _short_text_penalty(word_count: int) -> float:
    """1.0 for texts >= SHORT_TEXT_MIN words; scales linearly down to SHORT_TEXT_FLOOR at 0."""
    if word_count >= SHORT_TEXT_MIN:
        return 1.0
    return SHORT_TEXT_FLOOR + (1.0 - SHORT_TEXT_FLOOR) * (word_count / SHORT_TEXT_MIN)


def fuse(
    p_llm: float,
    p_stylo: float,
    word_count: int,
    p_read: float | None = None,
) -> dict[str, Any]:
    """Fuse per-signal probabilities into ``{p_ai, confidence, verdict}``.

    Pass ``p_read`` (Signal 3) for the full 3-signal prose path.
    Omit (or pass None) for the 2-signal fallback (metadata content).
    ``verdict`` ∈ {``ai``, ``human``, ``uncertain``}.
    """
    if p_read is not None:
        # 3-signal path: LLM 0.50, Stylometry 0.30, Readability 0.20
        p_ai = 0.50 * p_llm + 0.30 * p_stylo + 0.20 * p_read
        disagree = (abs(p_llm - p_stylo) + abs(p_llm - p_read) + abs(p_stylo - p_read)) / 3.0
    else:
        # 2-signal fallback: LLM 0.60, other signal 0.40
        p_ai = 0.60 * p_llm + 0.40 * p_stylo
        disagree = abs(p_llm - p_stylo)

    raw = 2.0 * abs(p_ai - 0.5)
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
