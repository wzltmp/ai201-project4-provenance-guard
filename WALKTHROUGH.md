# Provenance Guard — Demo Script (word-for-word)

**Setup before recording:**
- Terminal A: `python app.py` — leave running, keep visible
- Terminal B: ready to type
- This file open beside you to read from

---

## Intro (~20s)

> "This is Provenance Guard — a backend API for creative platforms that need to label
> content as AI-generated or human-written. The core design decision I made early on:
> uncertainty is a valid answer. When the evidence is mixed, the system says *uncertain*
> rather than forcing a guess. Let me show you end to end."

---

## Start (~5s)

**TYPE into Terminal B:**
```
bash scripts/demo.sh
```

*Health check appears.*

> "Server's running. The pipeline runs three independent signals on every submission —
> an LLM judge, stylometric heuristics, and a readability signal — and fuses their scores
> into a single confidence value."

---

## First submission — AI text (~50s)

*Step 2 output appears. Wait for it, then speak.*

**Point at the `signals` block:**

> "This is AI marketing copy — 'leverage,' 'holistic,' 'transformative,' no first-person
> voice, every sentence the same length. All three signals agree: the LLM flagged generic
> structure, stylometry caught uniform sentence lengths and AI vocabulary, readability
> found zero personal pronouns. That agreement is what matters — the confidence formula
> is driven by signal agreement, not just signal strength."

**Point at `confidence` and `attribution`:**

> "Confidence clears the 0.70 bar, so the verdict is *ai*."

**Point at `label.text`:**

> "The label text is plain English — it calls itself an estimate, not a fact, and
> explicitly offers an appeal. That's intentional. Even at high confidence, creators
> have recourse."

---

## Second submission — borderline human (~55s)

*Step 3 output appears. Wait for it.*

**Point at LLM and stylometric scores side by side:**

> "Formal economics paragraph — polished, no AI vocabulary, natural sentence variation.
> The LLM flagged the formal structure as AI-like, around 0.80. Stylometry looked at
> sentence variance and found no AI fingerprints, around 0.35. That's a 0.45 gap between
> two signals."

**Point at `confidence`:**

> "The disagreement penalty in the fusion formula collapses confidence to around 22% —
> well below 0.70. Verdict: *uncertain*."

> "This is the design working correctly. The system's response to its own internal
> conflict is to soften the claim, not harden a guess. This case — formal human writing
> the LLM over-flags — is the hardest false positive, and the system handles it by
> refusing to accuse."

---

## Appeal (~25s)

*Step 4 output appears.*

> "The creator contests it. They send their content ID and written reasoning. Status
> flips to under_review. The original decision is never overwritten — a human reviewer
> sees the system's verdict, both signal breakdowns, and the creator's case side by side
> in the appeals queue."

---

## Audit log (~20s)

*Steps 5a and 5b appear.*

> "Every decision lands in a structured SQLite audit log — attribution, confidence,
> timestamp, all three signal scores. That second entry has the appeal attached right
> beside the original classification. Nothing is overwritten, the full history is there."

---

## Analytics (~20s)

*Step 6 appears.*

> "Finally, an analytics endpoint. The signal disagreement rate is the most diagnostic
> number — it tells you *why* most results land in uncertain. It's not that the signals
> are weak, it's that they're genuinely pulling in opposite directions on ambiguous
> content. More actionable than just looking at verdict counts."

---

## Close (~10s)

> "Full design reasoning — why these signals, what they miss, how the confidence scores
> were validated — is in the README. Source is on GitHub."

---

**Total: ~3 minutes**
