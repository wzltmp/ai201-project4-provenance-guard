"""Signal 2 — Stylometric heuristics (pure standard library, no external packages).

Measures *mechanical surface statistics* of the text — the opposite kind of evidence from the
LLM judge (Signal 1), which reads holistic "feel." Their blind spots don't overlap, so when they
disagree the fusion step (``scoring.fuse``) backs off toward *uncertain*.

Each feature is turned into a 0–1 "AI-ness" sub-score, then a weighted sum (clamped) gives
``p_ai``. The weights/cutoffs below are starting points tuned against the calibration corpus in
``scripts/try_scoring.py`` (see planning.md §1).

Features (planning.md §1):
- burstiness          stdev/mean of sentence lengths (coeff. of variation). AI → LOW (uniform).
- lexical_diversity   unique/total words (type–token ratio).               AI → LOWER (repetitive).
- transition_density  rate of "Moreover/Furthermore/However/…" openers.    AI → HIGH.
- ai_vocab_hits       count of LLM-favored words ("delve, tapestry, …").   AI → MORE.
"""

from __future__ import annotations

import re
import statistics
from typing import Any

# --- sub-score weights (sum to 1.0) ---------------------------------------------------------
# Burstiness is the most robust signal, so it carries the most weight; lexical diversity is
# length-sensitive and noisy, so it carries the least.
_W_BURST = 0.35
_W_TRANSITION = 0.25
_W_VOCAB = 0.25
_W_LEXICAL = 0.15

# --- normalization cutoffs ------------------------------------------------------------------
# Below this coefficient of variation, sentence lengths look machine-uniform (max AI-ness).
_BURST_AI = 0.25
# Above this, sentence lengths are human-bursty (min AI-ness).
_BURST_HUMAN = 0.75
# Transition-opener rate that reads as fully "AI scaffolding".
_TRANSITION_FULL = 0.30
# ai_vocab_hits that saturate the sub-score.
_VOCAB_FULL = 3.0
# Type–token ratio: at/under _LEX_AI looks repetitive (AI); at/over _LEX_HUMAN looks varied.
_LEX_AI = 0.40
_LEX_HUMAN = 0.70

# Short-text floor: under this many words, neither stat is stable (drives the §2 short-text penalty).
SHORT_TEXT_MIN = 60

# Phrases that, when they *open* a sentence, signal AI-style connective scaffolding.
_TRANSITIONS = (
    "moreover", "furthermore", "however", "in conclusion", "additionally", "consequently",
    "nevertheless", "nonetheless", "therefore", "thus", "in addition", "on the other hand",
    "it is important to note", "it is worth noting", "in summary", "overall", "ultimately",
    "as a result", "in essence", "notably",
)

# Vocabulary disproportionately favored by current LLMs.
_AI_VOCAB = (
    "delve", "tapestry", "testament", "boundless", "realm", "underscore", "underscores",
    "multifaceted", "intricate", "nuanced", "pivotal", "paradigm", "leverage", "leveraging",
    "seamless", "holistic", "robust", "myriad", "landscape", "navigate", "navigating",
    "foster", "fostering", "transformative", "unlock", "unlocking", "elevate", "empower",
    "empowering", "synergy", "synergies", "vibrant", "embark", "cutting-edge", "ever-evolving",
)

_WORD_RE = re.compile(r"[A-Za-z']+")
# Split on sentence-ending punctuation followed by whitespace.
_SENTENCE_RE = re.compile(r"[.!?]+(?:\s+|$)")


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _linear(value: float, ai_at: float, human_at: float) -> float:
    """Map ``value`` to a 0–1 AI-ness score, =1 at ``ai_at`` and =0 at ``human_at``.

    Works whether the AI end is the low side (``ai_at < human_at``) or the high side.
    """
    if ai_at == human_at:
        return 0.0
    return _clamp((human_at - value) / (human_at - ai_at))


def _sentences(text: str) -> list[str]:
    parts = [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def compute_features(text: str) -> dict[str, Any]:
    """Compute the raw stylometric features (no scoring). Exposed for the audit log / tests."""
    words = _WORD_RE.findall(text.lower())
    word_count = len(words)
    sentences = _sentences(text)
    sentence_lengths = [len(_WORD_RE.findall(s)) for s in sentences] or [word_count]

    mean_len = statistics.fmean(sentence_lengths) if sentence_lengths else 0.0
    # Coefficient of variation of sentence length; 0 when <2 sentences or mean 0.
    if len(sentence_lengths) >= 2 and mean_len > 0:
        burstiness = statistics.pstdev(sentence_lengths) / mean_len
    else:
        burstiness = 0.0

    lexical_diversity = (len(set(words)) / word_count) if word_count else 0.0

    transition_hits = sum(
        1 for s in sentences if any(s.lower().lstrip().startswith(t) for t in _TRANSITIONS)
    )
    transition_density = (transition_hits / len(sentences)) if sentences else 0.0

    ai_vocab_hits = sum(1 for w in words if w in _AI_VOCAB)

    return {
        "burstiness": round(burstiness, 3),
        "lexical_diversity": round(lexical_diversity, 3),
        "transition_density": round(transition_density, 3),
        "ai_vocab_hits": ai_vocab_hits,
        "mean_sentence_len": round(mean_len, 1),
        "word_count": word_count,
    }


def score_stylometry(text: str) -> dict[str, Any]:
    """Score ``text`` with stylometric heuristics.

    Returns ``{"signal": "stylometric", "p_ai": float, "features": {...}}``.
    """
    f = compute_features(text)

    sub_burst = _linear(f["burstiness"], ai_at=_BURST_AI, human_at=_BURST_HUMAN)
    sub_transition = _clamp(f["transition_density"] / _TRANSITION_FULL)
    sub_vocab = _clamp(f["ai_vocab_hits"] / _VOCAB_FULL)
    sub_lexical = _linear(f["lexical_diversity"], ai_at=_LEX_AI, human_at=_LEX_HUMAN)

    p_ai = (
        _W_BURST * sub_burst
        + _W_TRANSITION * sub_transition
        + _W_VOCAB * sub_vocab
        + _W_LEXICAL * sub_lexical
    )

    return {"signal": "stylometric", "p_ai": round(_clamp(p_ai), 3), "features": f}
