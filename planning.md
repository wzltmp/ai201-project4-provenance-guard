# Provenance Guard — Planning

This document captures the **architecture and design decisions** made *before* writing code
(Milestone 1). It is the source of truth that all later code implements. The verbatim
transparency-label text and the final rate-limit numbers also appear in the `README`; this
file is where the *reasoning* lives.

---

## Architecture

### Narrative — the path a single piece of text takes

A creator (or the platform on their behalf) sends text to **`POST /submit`**. Here is every
component that text touches, in order, and what each one does:

1. **Flask API layer** — receives the HTTP request, validates the body (non-empty text,
   sane length, allowed `content_type`), and enforces **rate limiting** via Flask-Limiter
   *before* any expensive work happens. If valid, it assigns a `content_id` and hands the
   raw text to the detection orchestrator.

2. **Detection Orchestrator** — runs the **two independent signals** and collects their
   results. It does not decide anything itself; it just gathers evidence.
   - **Signal 1 — LLM semantic judge (Groq `llama-3.3-70b-versatile`).** Sends the text to
     the model with a structured prompt and parses back `p_llm` ∈ [0,1] (probability the
     text is AI-generated) plus a short rationale. This is the *holistic / meaning-aware*
     signal.
   - **Signal 2 — Stylometric analyzer (pure Python, no external libraries).** Computes
     measurable surface statistics of the text (sentence-length variance, lexical diversity,
     transition-phrase density, AI-favored vocabulary, punctuation patterns) and maps them to
     `p_stylo` ∈ [0,1]. This is the *mechanical / statistical* signal.

3. **Confidence Scorer (fusion)** — blends `p_llm` and `p_stylo` into a single `p_ai`,
   then derives a **calibrated `confidence`** that is *reduced* when the two signals disagree
   or when the text is too short to judge. It outputs a `verdict` (`ai` / `human` /
   `uncertain`) and the `confidence` number. This is the heart of the "genuine uncertainty"
   requirement: disagreement and thin input both push results toward *uncertain* instead of a
   false-confident accusation.

4. **Label Renderer** — maps `(verdict, confidence)` to exactly **one of three transparency
   label variants** (high-confidence AI / high-confidence human / uncertain) and fills in the
   confidence percentage. This is the plain-language string an end reader actually sees.

5. **Audit Store (SQLite)** — every decision is written as a structured row: `content_id`,
   timestamp, the verdict, `p_ai`, `confidence`, **both signal scores and their feature
   detail**, the chosen label variant, and a `status` (`classified` → later `under_review`
   if appealed). This row is what `GET /log` returns and what an appeal later links back to.

6. **Response** — the API returns the structured JSON (`content_id`, `attribution`, `p_ai`,
   `confidence`, per-signal breakdown, `label.text`, `status`).

If the creator disputes the result they call **`POST /appeal`**. The **Appeals Handler** looks
up the original decision, sets its `status` to `"under_review"`, stores the creator's written
reasoning, and writes an **appeal row to the Audit Store linked to the original decision**
(no automated re-classification — a human reviews). It returns `{ appeal_id, status:
"under_review" }`.

### Diagram

**Flow 1 — Submission** (`POST /submit`). Arrow labels = what passes between components.

```
 Client
   │  POST /submit  { content, content_type, author_id? }
   ▼
┌──────────────────────────────────────────────────────────────┐
│ Flask API layer   — validate + Flask-Limiter (rate limit)     │
└──────────────────────────────────────────────────────────────┘
   │  raw text  (+ new content_id)
   ▼
┌──────────────────────────────────────────────────────────────┐
│ Detection Orchestrator                                         │
│    ├─► Signal 1: LLM judge (Groq llama-3.3-70b-versatile)      │
│    │        ── text ──►        ◄── p_llm ∈[0,1] + rationale    │
│    └─► Signal 2: Stylometric analyzer (pure Python)            │
│             ── text ──►        ◄── p_stylo ∈[0,1] + features   │
└──────────────────────────────────────────────────────────────┘
   │  p_llm, p_stylo, features, rationale
   ▼
┌──────────────────────────────────────────────────────────────┐
│ Confidence Scorer (fusion)                                     │
│    blend(p_llm,p_stylo) → p_ai ;  apply disagreement +         │
│    short-text penalties → confidence                           │
└──────────────────────────────────────────────────────────────┘
   │  verdict ∈ {ai, human, uncertain},  p_ai,  confidence
   ▼
┌──────────────────────────────────────────────────────────────┐
│ Label Renderer  →  1 of 3 variant strings (with confidence %) │
└──────────────────────────────────────────────────────────────┘
   │  full decision record (verdict, scores, signals, label text)
   ├───────────────────────────────►  Audit Store (SQLite)  [INSERT decision row]
   ▼
 Response  { content_id, attribution, p_ai, confidence, signals{}, label{text}, status }
   ▼
 Client
```

**Flow 2 — Appeal** (`POST /appeal`).

```
 Client
   │  POST /appeal  { content_id, reason }
   ▼
┌───────────────────────────────────────────────┐
│ Flask API layer  — validate (content_id, reason)│
└───────────────────────────────────────────────┘
   │  content_id, reason
   ▼
┌───────────────────────────────────────────────┐
│ Appeals Handler                                │
│   - look up original decision by content_id    │
│   - status: classified → "under_review"        │
│   - attach creator reasoning                   │
└───────────────────────────────────────────────┘
   │  appeal record  (FK → original decision)
   ├──────────────►  Audit Store (SQLite)  [INSERT appeal row + UPDATE status]
   ▼
 Response  { content_id, appeal_id, status: "under_review", logged_at }
   ▼
 Client
```

---

## Detection Signals

The pipeline uses **two distinct signals chosen specifically because their blind spots do
not overlap** — a holistic/semantic judge and a mechanical/statistical analyzer. When two
methods that fail in *different* ways agree, we can be confident; when they disagree, that
disagreement is itself a strong signal of *uncertainty* (see Confidence Scoring).

### Signal 1 — LLM semantic judge (Groq `llama-3.3-70b-versatile`)

- **What property it measures:** the high-level *gestalt* of the writing — coherence,
  specificity, voice, idiomatic risk-taking, and the subtle "feel" that a large model is good
  at recognizing because it has seen enormous amounts of both human and machine text.
- **Why it differs human vs AI:** AI prose tends to be fluent-but-generic — evenly hedged,
  low in concrete personal specifics, structurally balanced, and "safe" in register. Human
  writing more often takes idiosyncratic risks, includes lived-in specifics, and varies in
  quality. The LLM can perceive these holistic patterns that are hard to reduce to a formula.
- **Blind spot:** LLM judges are **biased toward flagging polished or formal human writing as
  AI** (this disproportionately harms non-native English writers and careful editors); they
  are **easily fooled by lightly-edited AI text**; they are **non-deterministic / prompt-
  sensitive**; and they can only see *style*, never actual provenance. On a very short text
  (a haiku) they have little to judge.

### Signal 2 — Stylometric heuristics (pure Python)

- **What property it measures:** quantifiable surface statistics, computed with no external
  libraries:
  - **Burstiness** — variance/standard deviation of sentence length.
  - **Lexical diversity** — type–token ratio (unique words / total words).
  - **Transition-phrase density** — frequency of "Moreover / Furthermore / However / In
    conclusion / It is important to note."
  - **AI-favored vocabulary** — counts of words/phrases empirically over-used by LLMs
    ("delve, tapestry, testament, boundless, navigate the complexities, in the realm of").
  - **Punctuation / structure regularity** — em-dash and balanced-list patterns.
- **Why it differs human vs AI:** AI text statistically **regresses toward the mean** — low
  sentence-length variance (low burstiness), repetitive transition scaffolding, and a
  recognizable preferred vocabulary. Human text is **burstier and more lexically uneven**:
  long sentences next to fragments, surprising word choices, inconsistent structure.
- **Blind spot:** it is **genre-dependent and trivially gamed.** Formal/technical human
  writing looks "AI-like" (low burstiness, formal transitions); **poetry and song lyrics
  violate the prose assumptions entirely** (intentional repetition, short lines); **very
  short texts** lack enough tokens for stable statistics; and a user can defeat it by
  deliberately varying sentence length or inserting typos. It is also tuned to English prose
  norms and is biased against stylized or non-native writing.

### Why these two together

They are **complementary, not redundant.** The LLM is strong exactly where stylometry is weak
(it understands meaning and poetry) and stylometry is objective exactly where the LLM is
flaky (it is deterministic and unbiased by "polish"). Crucially, the cases where each one is
*most wrong* are different — so **their disagreement is the most reliable indicator that we
should not be confident**, which is wired directly into the confidence score below.

---

## Confidence Scoring & Genuine Uncertainty

A binary label would be irresponsible: this task is inherently uncertain, and a false
"AI-generated" stamp damages a real creator. So the system returns a **continuous confidence
that is deliberately suppressed in ambiguous cases.**

### How the score is computed

1. Each signal returns `p_ai ∈ [0,1]` (probability the text is AI).
2. **Blend:** `p_ai = 0.6·p_llm + 0.4·p_stylo`. The LLM is weighted higher (holistic, harder
   to fool by mechanical tricks) but cannot dominate — stylometry can still pull a verdict.
3. **Raw certainty:** `r = 2·|p_ai − 0.5|` → 0 at a coin-flip (0.5), 1 at the extremes.
4. **Disagreement penalty:** `d = |p_llm − p_stylo|`. Final confidence is scaled by
   `(1 − 0.5·d)` — when the two signals fully disagree, confidence is **halved**.
5. **Short-text penalty:** texts under ~60 words get a sufficiency factor `s < 1` (scaling
   toward 0.5 for very short inputs), because neither signal is reliable on a fragment.
6. **`confidence = clamp( r · (1 − 0.5·d) · s , 0, 1 )`**.

### Mapping confidence → the three label bands

| Condition | Verdict | Label variant |
|---|---|---|
| `confidence ≥ 0.70` and `p_ai ≥ 0.5` | `ai` | **High-confidence AI** |
| `confidence ≥ 0.70` and `p_ai < 0.5` | `human` | **High-confidence human** |
| `confidence < 0.70` | `uncertain` | **Uncertain** |

This makes the spec's example real: a **0.95** confidence clears the 0.70 bar and shows a
definitive variant; a **0.51** confidence falls below it and shows the *Uncertain* variant —
**a different label, not just a different number.** (Threshold = 0.70, weights 0.6/0.4, and
the penalty factors are the **tunable knobs**; Milestone 2 calibrates them against a labeled
set — see below.)

### How I'll test that the scores are *meaningful* (executed + reported in README)

- **Labeled mini-corpus:** known-human texts (public-domain classics written before LLMs
  existed) + known-AI texts (freshly generated) + deliberately **ambiguous** texts
  (AI lightly human-edited; formal human writing).
- **Band accuracy:** high-confidence bands should be *more accurate* than the uncertain band —
  if not, the score is noise.
- **Uncertain band is used:** ambiguous/edited samples should actually land in *uncertain*,
  not get force-sorted into AI/human (proves the middle of the scale is meaningful).
- **Ablation:** each signal alone vs. fused — the fused score should beat either signal alone,
  justifying the multi-signal design.
- **Disagreement → uncertainty check:** confirm the false-positive scenario below resolves to
  *uncertain*, not a false accusation.

---

## The False-Positive Problem (traced end-to-end)

**Scenario:** a careful human essayist submits a polished, formal piece.

1. **Signal 1 (LLM)** over-flags polish as machine-like → `p_llm = 0.80` ("reads AI-ish").
2. **Signal 2 (stylometry)** sees genuine human burstiness and idiosyncratic vocabulary →
   `p_stylo = 0.30` ("reads human").
3. **Fusion:** `p_ai = 0.6·0.80 + 0.4·0.30 = 0.60`; `r = 2·|0.60−0.5| = 0.20`;
   disagreement `d = 0.50` → `confidence = 0.20·(1 − 0.25) = 0.15`.
4. **Band:** `0.15 < 0.70` → **Uncertain**. The system **does not** accuse the writer. The
   label reads "we couldn't confidently determine…", explicitly inconclusive.
5. **Appeal:** the creator clicks appeal, writes their reasoning ("I wrote this — here are my
   drafts/voice"); `POST /appeal` sets status `under_review` and logs the appeal next to the
   original decision in the audit log for a human to review.

**The design principle this proves:** the system's response to *its own internal disagreement*
is to **soften the claim and make recourse easy**, not to harden a guess into an accusation.
This directly informs Milestone 2 — the penalties above exist precisely so that the
false-positive case degrades gracefully into *uncertain + appealable*.

---

## API Surface (the contract all code implements)

> Drafted before implementation. Request/response shapes are the contract; field names may be
> refined but the structure is fixed.

### `POST /submit`  — classify a piece of text *(rate-limited)*
**Request**
```json
{ "content": "<the text>", "content_type": "poem|story|blog", "author_id": "optional-string" }
```
**Response `200`**
```json
{
  "content_id": "c_ab12cd34",
  "attribution": "ai | human | uncertain",
  "p_ai": 0.83,
  "confidence": 0.78,
  "signals": {
    "llm":         { "p_ai": 0.88, "rationale": "…" },
    "stylometric": { "p_ai": 0.76, "features": { "burstiness": 0.12, "lexical_diversity": 0.41, "...": "..." } }
  },
  "label": { "variant": "high_confidence_ai", "text": "🤖 Likely AI-generated — …" },
  "status": "classified",
  "timestamp": "2026-06-25T12:00:00Z"
}
```
**Errors:** `400` (empty/invalid content), `429` (rate limit exceeded).

### `POST /appeal`  — contest a classification
**Request**
```json
{ "content_id": "c_ab12cd34", "reason": "I wrote this myself; here is my evidence…" }
```
**Response `200`**
```json
{ "content_id": "c_ab12cd34", "appeal_id": "a_77ff", "status": "under_review", "logged_at": "2026-06-25T12:05:00Z" }
```
**Errors:** `400` (missing reason), `404` (unknown content_id).

### `GET /log`  — structured audit log (≥3 entries)
**Response `200`**
```json
{ "entries": [ { "content_id": "…", "timestamp": "…", "attribution": "…", "confidence": 0.78,
                 "signals": { "...": "..." }, "status": "classified|under_review",
                 "appeal": { "appeal_id": "…", "reason": "…", "logged_at": "…" } | null } ] }
```

### `GET /content/<content_id>`  *(optional convenience)* — fetch one record + current status.
### `GET /health`  *(optional)* — liveness probe.

---

## Transparency Label Variants (design draft)

There are exactly **three** display variants. Each is plain-language, names the result, and
makes the confidence meaningful to a non-technical reader by stating the percentage *and*
framing it as an estimate, not a verdict. (The **final verbatim text** is mirrored in the
`README`; the code emits these same strings with `{confidence}` interpolated.)

- **High-confidence AI** —
  `🤖 Likely AI-generated. Provenance Guard's analysis strongly suggests this was produced with generative AI ({confidence}% confidence). This is an automated estimate, not a certainty — if you're the creator and disagree, you can appeal.`
- **High-confidence human** —
  `✍️ Likely human-written. Provenance Guard's analysis strongly suggests a person wrote this ({confidence}% confidence). This is an automated estimate, not a certainty.`
- **Uncertain** —
  `🤔 Origin unclear. Provenance Guard could not confidently determine whether this was written by a person or generated by AI ({confidence}% confidence). Treat this as inconclusive. If you're the creator, you can add context by appealing.`

---

## Component → Feature traceability

| Required feature | Component(s) responsible |
|---|---|
| Content Submission Endpoint | Flask API layer · `POST /submit` |
| Multi-Signal Detection | Detection Orchestrator · LLM judge · Stylometric analyzer |
| Confidence Scoring w/ Uncertainty | Confidence Scorer (fusion + penalties) |
| Transparency Label | Label Renderer (3 variants) |
| Appeals Workflow | Appeals Handler · `POST /appeal` |
| Rate Limiting | Flask API layer (Flask-Limiter) |
| Audit Log | Audit Store (SQLite) · `GET /log` |
