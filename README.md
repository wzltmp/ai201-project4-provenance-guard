# Provenance Guard

A pluggable backend service that helps creative-sharing platforms protect **attribution and trust** by classifying submitted content (writing, music, art) as human-made vs. AI-generated, scoring confidence in that classification, surfacing a transparency label to audiences, and giving creators a path to appeal misclassifications.

> The goal is **transparency and creator recourse**, not policing creativity.

## Status

✅ **Fully implemented.** All endpoints, both detection signals, confidence fusion, the three
transparency labels, the appeals workflow, rate limiting, and the SQLite audit log are working
end-to-end. Architecture and the implementation-ready spec are in [`planning.md`](./planning.md).

Run it: `flask --app app run` (or `python app.py`) on `http://127.0.0.1:5000`.

## Stack

| Component | Tool |
|---|---|
| API framework | Flask |
| Detection signal 1 | Groq (`llama-3.3-70b-versatile`) |
| Detection signal 2 | Stylometric heuristics (pure Python) |
| Rate limiting | Flask-Limiter |
| Audit log | SQLite (built-in) |

## Architecture (summary)

A submitted piece of text flows through these components (full narrative + ASCII diagrams of **both** the submission and appeal flows are in [`planning.md` → `## Architecture`](./planning.md#architecture)):

1. **Flask API layer** — validates the request and enforces **rate limiting** before any work; assigns a `content_id`.
2. **Detection Orchestrator** — runs the two signals and collects their scores.
3. **Confidence Scorer (fusion)** — blends the signals into `p_ai`, then derives a `confidence` that is *reduced* when signals disagree or the text is too short.
4. **Label Renderer** — maps `(verdict, confidence)` to one of three transparency labels.
5. **Audit Store (SQLite)** — records every decision (scores, signals, label, status) and links any later appeal.
6. **Appeals Handler** — on `POST /appeal`, sets status → `under_review`, stores the creator's reasoning, and logs the appeal next to the original decision.

## Detection Signals

The pipeline uses **two distinct signals chosen because their blind spots do not overlap.** When two methods that fail in *different* ways agree we can be confident; when they disagree, that disagreement itself drives the score toward *uncertain*.

### Signal 1 — LLM semantic judge (Groq `llama-3.3-70b-versatile`)
- **Captures:** the holistic *feel* of the writing — coherence, specificity, voice, idiomatic risk-taking — patterns a large model recognizes from having seen vast amounts of human and machine text.
- **Why it differs:** AI prose tends to be fluent-but-generic (evenly hedged, low on concrete personal specifics, structurally balanced); human writing takes more idiosyncratic risks and varies in quality.
- **Blind spot:** biased toward flagging *polished/formal human writing* as AI (harms non-native writers and careful editors); easily fooled by lightly-edited AI; non-deterministic / prompt-sensitive; sees only style, never true provenance.

### Signal 2 — Stylometric heuristics (pure Python)
- **Captures:** measurable surface statistics — sentence-length variance (**burstiness**), lexical diversity (type–token ratio), transition-phrase density ("Moreover/Furthermore/However"), AI-favored vocabulary ("delve, tapestry, testament"), and punctuation/structure regularity.
- **Why it differs:** AI text statistically regresses toward the mean — low burstiness, repetitive transition scaffolding, a recognizable preferred vocabulary; human text is burstier and more lexically uneven.
- **Blind spot:** genre-dependent and trivially gamed — formal/technical human writing looks "AI-like," poetry/lyrics break the prose assumptions entirely, very short texts lack stable statistics, and a user can defeat it by varying sentence length or adding typos.

**Why these two together:** they are complementary — the LLM is strong where stylometry is weak (meaning, poetry) and stylometry is objective where the LLM is flaky (deterministic, unbiased by "polish"). Because their *most-wrong* cases differ, **their disagreement is the most reliable indicator that the system should not be confident** — wired directly into the confidence score.

## Confidence Scoring

Both signals emit `p_ai ∈ [0,1]`. They are fused (`scoring.py`) into one **`confidence`** —
*certainty of the verdict's direction*, not the probability of AI:

```
p_ai       = 0.6·p_llm + 0.4·p_stylo          # LLM weighted higher, can't dominate
raw        = 2·|p_ai − 0.5|                    # 0 at a coin-flip, 1 at the extremes
disagree   = |p_llm − p_stylo|                 # signals conflict → back off
confidence = clamp( raw · (1 − 0.5·disagree) · short_text_penalty , 0, 1 )
```

`confidence ≥ 0.70` → a definitive verdict (`ai`/`human`, by which side of 0.5 `p_ai` is on);
below 0.70 → `uncertain`. The disagreement term is the safety mechanism: when the two
differently-biased signals conflict, confidence is suppressed toward *uncertain* rather than
forced into a guess.

**How I validated the scores are meaningful** (harness: `scripts/try_scoring.py`): I ran a small
labeled set — clearly-AI, clearly-human, and two borderline cases (formal human, lightly-edited AI)
— and checked the bands separate. They do: clear AI → `0.78` (*Likely AI*), clear casual human →
`0.77` (*Likely human*), while both borderline cases fall to *Uncertain* (`0.16` / `0.24`). The
first run instead collapsed *every* sample to *Uncertain* — the LLM clustered clear cases at ~0.8/0.2,
which `2·|p_ai−0.5|` caps at ~0.64. The fix held the 0.70 threshold and instead instructed the LLM
to use the full 0–1 range when evidence is clear; the bands then separated as intended. (More detail
in [`planning.md` §2](./planning.md).)

## Transparency Label — the three variants (verbatim)

These are the exact strings shown to a reader. `{confidence}` is filled with the score as a whole-number percentage. The same three strings are emitted by the code and drafted in [`planning.md`](./planning.md).

| Variant | Exact label text |
|---|---|
| **High-confidence AI** | `🤖 Likely AI-generated. Provenance Guard's analysis strongly suggests this was produced with generative AI ({confidence}% confidence). This is an automated estimate, not a certainty — if you're the creator and disagree, you can appeal.` |
| **High-confidence human** | `✍️ Likely human-written. Provenance Guard's analysis strongly suggests a person wrote this ({confidence}% confidence). This is an automated estimate, not a certainty.` |
| **Uncertain** | `🤔 Origin unclear. Provenance Guard could not confidently determine whether this was written by a person or generated by AI ({confidence}% confidence). Treat this as inconclusive. If you're the creator, you can add context by appealing.` |

**Rendered examples** (how a non-technical reader sees them):

> 🤖 Likely AI-generated. Provenance Guard's analysis strongly suggests this was produced with generative AI (92% confidence). This is an automated estimate, not a certainty — if you're the creator and disagree, you can appeal.

> ✍️ Likely human-written. Provenance Guard's analysis strongly suggests a person wrote this (91% confidence). This is an automated estimate, not a certainty.

> 🤔 Origin unclear. Provenance Guard could not confidently determine whether this was written by a person or generated by AI (54% confidence). Treat this as inconclusive. If you're the creator, you can add context by appealing.

**Which variant shows when:** `confidence ≥ 0.70` → a definitive variant (AI or human, by which side of 0.5 `p_ai` is on); `confidence < 0.70` → *Uncertain*. So a 0.95 confidence and a 0.51 confidence produce **different labels, not just different numbers**. (Thresholds are tunable and calibrated in Milestone 2 — see `planning.md`.)

## API (contract)

| Endpoint | Purpose |
|---|---|
| `POST /submit` | Classify a piece of text (rate-limited). Returns attribution, `p_ai`, confidence, per-signal breakdown, and the label text. |
| `POST /appeal` | Contest a classification — captures reasoning, sets status `under_review`, logs the appeal. |
| `GET /log` | Structured audit log of every decision (and appeals). |

Full request/response shapes are in [`planning.md` → API Surface](./planning.md#api-surface-the-contract-all-code-implements).

## Rate Limiting

`POST /submit` is limited to **10 requests/minute and 100 requests/day per client IP**
(Flask-Limiter, `memory://` storage). Only `/submit` is limited — it's the endpoint that spends a
Groq API call on every request, so it's both the costly path and the abuse target.

**Why these numbers (tied to real writing-platform usage):**
- A creator checks their *own* work. Even while iterating — submit, edit, resubmit — a person
  realistically touches the endpoint a handful of times in a sitting. **10/minute** leaves
  comfortable headroom for that burst while a script firing continuously trips it almost immediately.
- **100/day** sits far above any plausible human daily output (nobody hand-submits 100 pieces) but
  caps a slow-drip scraper trying to stay under the per-minute limit.
- Keyed on **IP** here because this is a standalone backend; in a real deployment behind the
  platform's auth you'd key on the authenticated `creator_id` so one abusive IP can't exhaust a
  shared limit and one user's flooding doesn't affect others.

**Evidence** — 12 rapid requests against the 10/min limit (`200` for the first 10, then `429`):

```
request 1  -> 200      request 7  -> 200
request 2  -> 200      request 8  -> 200
request 3  -> 200      request 9  -> 200
request 4  -> 200      request 10 -> 200
request 5  -> 200      request 11 -> 429
request 6  -> 200      request 12 -> 429
```
```json
// body of a 429 response
{ "error": "rate_limit_exceeded", "detail": "Too many requests (10 per 1 minute). Try again shortly." }
```

## Audit Log

Every decision is written to SQLite (`decisions` table) and surfaced as JSON via **`GET /log`**
(newest first). Each entry carries the timestamp, `content_id`, attribution, combined `confidence`,
**both individual signal scores**, the rendered label, the status, and — if filed — the appeal. A
real `GET /log` entry (the appealed one):

```json
{
  "content_id": "c_b4780b3c4a9d",
  "timestamp": "2026-06-26T21:39:22.451Z",
  "creator_id": "writer-formal",
  "attribution": "uncertain",
  "p_ai": 0.618,
  "confidence": 0.157,
  "signals": {
    "llm": { "p_ai": 0.8, "rationale": "Well-structured, technical terms, but lacks distinctive voice." },
    "stylometric": { "p_ai": 0.346, "features": { "burstiness": 0.26, "transition_density": 0.0,
                     "ai_vocab_hits": 0, "lexical_diversity": 0.86, "word_count": 43 } }
  },
  "label": { "variant": "uncertain", "text": "🤔 Origin unclear. … (16% confidence) …" },
  "status": "under_review",
  "appeal_reasoning": "I wrote this myself … I am a non-native English speaker …",
  "appeal": { "appeal_id": "a_d0770f20", "logged_at": "2026-06-26T21:39:44.340Z", "reasoning": "…" }
}
```

## Appeals

A creator contests a classification with **`POST /appeal`** (`content_id` + `creator_reasoning`).
The handler logs the reasoning in the `appeals` table **beside** the original decision (never
overwriting it), flips that decision's status to `under_review`, and returns a confirmation. There
is **no automatic re-classification** — a human works the queue via `GET /appeals?status=under_review`.

```bash
curl -s -X POST http://127.0.0.1:5000/appeal -H "Content-Type: application/json" \
  -d '{"content_id": "c_b4780b3c4a9d",
       "creator_reasoning": "I wrote this myself … I am a non-native English speaker …"}'
```
```json
{ "content_id": "c_b4780b3c4a9d", "appeal_id": "a_d0770f20", "status": "under_review",
  "logged_at": "2026-06-26T21:39:44.340Z",
  "message": "Appeal received. This classification is now under review by a human." }
```
The appeal then appears in `GET /log` on that content's entry (see the sample above) and in the
reviewer queue with the original decision shown beside the creator's case.

## Known Limitations

Detection is an unsolved problem; this system is explicitly built to be honest about that. Specific
content it will misclassify, tied to the signals:

- **Repetitive poetry / song lyrics → stylometry false-positive.** A folk song with a repeated
  refrain and short, even lines reads as low burstiness + low lexical diversity + heavy repetition —
  exactly stylometry's "AI-like" fingerprint, so Signal 2 scores it high. The LLM usually recognizes
  it as a poem and scores it human, so **the two disagree and the disagreement penalty pushes the
  result to *Uncertain*** rather than a false accusation. Poetry remains a genuine weak spot.
- **Formal / academic / non-native-English human writing → LLM over-flags it as AI.** The LLM judge
  is biased to read polish and formal structure as machine-written (this harms careful editors and
  non-native writers). Stylometry often disagrees, again pulling the result to *Uncertain* — but
  this is the hardest case, and it's the reason the **appeals path exists**.
- **Lightly human-edited AI → false negative (by design).** A few human rewrites pull both signals
  toward *human*. We accept this asymmetry on purpose: consistent with "protect attribution, don't
  police creativity," the system errs toward *not* accusing.

This is why **a false positive (flagging a human's work as AI) is treated as worse than a false
negative**: the verdict never hardens on signal disagreement, *Uncertain* is a first-class outcome,
and every fallible verdict offers an appeal.

## Spec Reflection

The biggest divergence from `planning.md` was in **confidence calibration**, and it happened twice.
The M2 spec defined a single **symmetric `0.70`** confidence bar. Going into M4 I argued for making
it *asymmetric* (a higher bar to say "AI" than "human") to encode the false-positive priority above —
it's a defensible design. But when I built the calibration harness, two things became clear: (1) the
priority is better served by the **appeals workflow and the *Uncertain* band** than by a threshold
nudge, and (2) the real bug wasn't the threshold at all — it was that the **LLM clustered its
probabilities at ~0.8/0.2**, so *nothing* cleared 0.70 and every input read as *Uncertain*. So I
**kept the symmetric 0.70** (matching the committed spec) and fixed the input instead, prompting the
LLM to use the full 0–1 range when evidence is clear. Lesson: I'd specified a threshold value in M2
before I had any data on what scores the pipeline would actually produce; the threshold only became
real once the calibration harness existed.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Mac/Linux
pip install -r requirements.txt

cp .env.example .env               # then add your Groq key
# get a free key at https://console.groq.com/keys
```

## License

MIT
