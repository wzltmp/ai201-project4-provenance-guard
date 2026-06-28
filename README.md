# Provenance Guard

A pluggable backend service that helps creative-sharing platforms protect **attribution and trust**
by classifying submitted writing as human vs. AI-generated, scoring confidence in that
classification, surfacing a transparency label to audiences, and giving creators a path to appeal
misclassifications.

> The goal is **transparency and creator recourse**, not policing creativity.

**Repository:** https://github.com/wzltmp/ai201-project4-provenance-guard

---

## Status

✅ **Fully implemented**, including stretch features. All endpoints, three detection signals,
confidence fusion, three transparency labels, appeals workflow, rate limiting, SQLite audit log,
and an analytics dashboard are working end-to-end.

Run it: `python app.py` or `flask --app app run` → `http://127.0.0.1:5000`

---

## Stack

| Component | Tool |
|---|---|
| API framework | Flask 3.x |
| Detection — Signal 1 | Groq `llama-3.3-70b-versatile` (LLM semantic judge) |
| Detection — Signal 2 | Stylometric heuristics (pure Python) |
| Detection — Signal 3 | Readability / voice heuristics (pure Python) *(stretch)* |
| Confidence fusion | Custom weighted formula (`scoring.py`) |
| Rate limiting | Flask-Limiter |
| Persistence | SQLite — `decisions` + `appeals` tables |

---

## Architecture

### Design philosophy

The core design choice is to make **uncertainty a first-class output** rather than forcing every
input into a binary answer. Every component is built around this:

- The confidence score is derived from signal *agreement*, not just signal *strength*. Two signals
  both saying 0.75 produces lower confidence than one signal saying 0.95 because the first case
  might be systematic bias and the second has a strong clear signal.
- The 0.70 confidence bar before a definitive label is deliberately high — the system declines to
  make a public accusation unless it is well above a coin-flip.
- The *Uncertain* label is not a failure state; it is the correct answer when evidence is mixed.
- Every verdict that could harm a creator (AI accusations) surfaces an appeal path.

### Submission flow

```
 Client
   │  POST /submit  { content, content_type, author_id? }
   ▼
┌─────────────────────────────────────────────────────────┐
│ Flask API — validate + rate-limit (10/min · 100/day)     │
└─────────────────────────────────────────────────────────┘
   │ text + content_id
   ▼
┌─────────────────────────────────────────────────────────┐
│ Detection Orchestrator (app.py)                          │
│   ├─► Signal 1: LLM judge        → p_llm ∈ [0,1]        │
│   ├─► Signal 2: Stylometry       → p_stylo ∈ [0,1]      │
│   └─► Signal 3: Readability      → p_read ∈ [0,1]       │
└─────────────────────────────────────────────────────────┘
   │ three p_ai floats + word_count
   ▼
┌─────────────────────────────────────────────────────────┐
│ Confidence Scorer (scoring.py)                           │
│   blend → p_ai · disagreement penalty · short-text pen  │
│   → confidence ∈ [0,1] · verdict ∈ {ai, human, uncertain}│
└─────────────────────────────────────────────────────────┘
   │ verdict + confidence
   ▼
┌──────────────────────────────────┐
│ Label Renderer (labels.py)       │
│   → 1 of 3 exact label strings   │
└──────────────────────────────────┘
   │ full decision record
   ├─────────────────────────────► SQLite audit store (INSERT decisions row)
   ▼
 Response { content_id, attribution, p_ai, confidence, signals{}, label{}, status, timestamp }
```

### Appeal flow

```
 Client (creator)
   │  POST /appeal  { content_id, reason }
   ▼
┌─────────────────────────────────────────────┐
│ Appeals Handler (app.py)                     │
│   1. Look up original decision (404 if gone) │
│   2. status: classified → "under_review"     │
│   3. INSERT appeal row alongside decision    │
└─────────────────────────────────────────────┘
   ▼
 Response { content_id, appeal_id, status: "under_review", logged_at }

 Human reviewer later reads GET /appeals?status=under_review
```

The original decision is **never overwritten**. The appeal sits alongside it so a reviewer sees
both the system's reasoning and the creator's response. There is no automatic re-classification.

---

## Detection Signals

### Why three signals, and why these three

The fundamental problem with AI detection is that *every* signal has a class of inputs that will
fool it. The LLM judge is biased by polish and formality. Stylometry is biased by genre conventions.
No single signal is reliable across all content types.

The solution is to combine signals whose *failure modes differ*. When two independently-biased
signals agree, their agreement is meaningful evidence. When they disagree, the disagreement itself
is the most honest thing the system can say — and it drives the score toward *Uncertain* rather
than forcing a guess.

The three signals chosen cover three orthogonal dimensions of AI-ness:

| Signal | Dimension | What AI looks like |
|---|---|---|
| LLM judge | Holistic semantics and "feel" | Fluent-but-generic; hedged; low personal specificity |
| Stylometry | Mechanical surface statistics | Uniform sentence lengths; transition-heavy; recognizable vocab |
| Readability | Register and personal voice | Impersonal; no pronouns; monotone period-only endings |

---

### Signal 1 — LLM semantic judge (`signals/llm.py`)

**What it measures:** The holistic *feel* of the writing — coherence, specificity, voice,
idiomatic risk-taking. The model has seen vast amounts of both human and machine-generated text
and pattern-matches against that exposure. Prompted to return strict JSON:
```json
{ "p_ai": 0.88, "rationale": "Even, hedged phrasing; no concrete personal specifics." }
```
`p_ai` is the model's estimate of the probability the text is AI-generated. On parse failure after
one retry, falls back to `p_ai = 0.5` (non-committal) with `rationale: "parse_failed"`.

**Why it's the primary signal (weight 0.50):** It is the only signal that reads *meaning* rather
than surface statistics — it can recognize that a poem is a poem even if stylometry flags it for
low lexical diversity. It is also the most directly trained for this task.

**What I'd change for production:** The LLM signal is non-deterministic and prompt-sensitive. In a
production system I would run the prompt through a versioned prompt registry, log the raw model
output alongside the parsed result, and treat the model's temperature as a tunable parameter with
AB testing. I'd also add a second LLM provider as a fallback rather than defaulting to 0.5 on API
failure — one retry is fragile.

**Blind spot:** Biased against polished, formal, or non-native-English human writing. A careful
essayist or a non-native speaker who writes very correctly gets flagged as AI. This is the hardest
false-positive case and the primary reason the appeal workflow exists.

---

### Signal 2 — Stylometric heuristics (`signals/stylometry.py`)

**What it measures:** Quantifiable surface statistics that AI text is statistically known to
exhibit. Each feature is normalized to a 0–1 AI-ness sub-score and combined by weighted sum:

| Feature | Weight | AI indicator |
|---|---|---|
| `burstiness` (sentence-length CoV) | 0.35 | Low = uniform, machine-like |
| `transition_density` | 0.25 | High = "Moreover/Furthermore" scaffolding |
| `ai_vocab_hits` | 0.25 | "delve, tapestry, testament, boundless…" |
| `lexical_diversity` (type-token ratio) | 0.15 | Low = repetitive |

**Why it's the secondary signal (weight 0.30):** It is deterministic (same input always produces
the same output), objective (not swayed by a polished register), and free of API calls. It is most
reliable on longer texts where statistics are stable, and most complementary to the LLM — the LLM
fails on formal register; stylometry fails on genre conventions (poetry, legal text) — so their
failure modes don't strongly overlap.

**What I'd change for production:** The AI vocab list (`_AI_VOCAB`) is hand-curated and will
drift as LLMs update their style. In production I would treat it as a data file updated from
community-sourced corpora rather than a code constant. I would also weight `burstiness` differently
per content type — the signal means something different for poetry vs. blog posts.

**Blind spot:** Genre-dependent. Poetry deliberately has short, even lines and repetition — exactly
what stylometry reads as AI. Technical documentation uses heavy transition phrases that inflate the
score. It is trivially gameable by varying sentence lengths.

---

### Signal 3 — Readability / voice heuristics (`signals/readability.py`) *(stretch feature)*

**What it measures:** Personal voice and structural predictability patterns that neither of the
other signals captures:

| Feature | Weight | AI indicator |
|---|---|---|
| Personal pronoun absence | 0.40 | AI text avoids I/me/my/we/you — persistently impersonal |
| Punctuation monotony | 0.35 | AI ends nearly all sentences with periods |
| Bigram repetition rate | 0.25 | AI reuses more consecutive word pairs |

**Why this dimension:** Signal 2 already measures sentence structure and vocabulary; Signal 1
already measures holistic meaning. Signal 3 adds *register* — the formal/informal, personal/
impersonal axis that both other signals largely ignore. A text can be stylistically varied (human-
like by Signal 2) and semantically sophisticated (human-like by Signal 1) but still be entirely
impersonal and period-only (AI-like by Signal 3).

**Calibration note:** I originally implemented a Fog-index approach (Gunning Fog Index proximity to
an assumed AI "comfort zone" of [8–12]). After running the calibration corpus, both AI and human
samples scored Fog 22–32 — far outside the zone, providing zero discrimination. This is noted
because it's a real calibration failure, not a known limitation: the design had to be changed after
testing. Personal pronoun density replaced the fog feature because it discriminated correctly across
all test cases.

**What I'd change for production:** The bigram repetition feature is effectively a no-op on texts
under ~200 words (short texts have zero repeated bigrams). On longer text it becomes meaningful,
but most submissions will be shorter. A trigram or 4-gram approach, or a normalized entropy measure
over the word distribution, would be more reliable. The pronoun feature is also culturally biased —
academic and formal prose conventions in many languages avoid first-person even in human writing.

**Blind spot:** Formal academic and third-person human writing avoids first-person pronouns and
uses only periods — it looks identical to AI on this signal. This is acceptable because such text
should land in *Uncertain* anyway (the LLM also struggles with it), and the appeals path handles it.

---

## Confidence Scoring

### The formula

All three signals emit `p_ai ∈ [0,1]`. They are fused in `scoring.py` into one number:

```
# 3-signal prose path:
p_ai       = 0.50·p_llm + 0.30·p_stylo + 0.20·p_read

raw        = 2·|p_ai − 0.5|
             # certainty of the direction: 0 at a coin-flip, 1 at the extremes

disagree   = mean(|p_llm−p_stylo|, |p_llm−p_read|, |p_stylo−p_read|)
             # average pairwise conflict across all three signals

short_pen  = 1.0 if word_count ≥ 60, else scales down to 0.5 at 0 words
             # short texts don't have stable statistics

confidence = clamp( raw · (1 − 0.5·disagree) · short_pen , 0, 1 )
```

**Why this formula, not a simpler average:**

The formula deliberately separates *which direction the evidence points* (`p_ai`, which chooses the
verdict side) from *how certain we are about that direction* (`confidence`, which determines
whether to make a public claim). A text with `p_ai = 0.85` where the signals strongly disagree
will have lower confidence than one with `p_ai = 0.80` where all three signals agree. This is the
correct behavior: inter-signal disagreement is the most honest measure of when our pipeline is
operating in its uncertain zone.

The 0.70 confidence bar before a definitive verdict was chosen to require clear signal agreement
before making an accusation. A text needs both a strong `p_ai` direction *and* low inter-signal
disagreement to clear it.

### Two worked examples

**Example 1 — High-confidence AI detection**

Input: a piece of AI-generated marketing prose heavy in "leverage/holistic/transformative" vocabulary
with uniform sentence structure and no first-person voice.

```
Signal 1 (LLM):       p_ai = 0.95   rationale: "Generic marketing language, no concrete specifics,
                                                  uniform hedged phrasing."
Signal 2 (Stylometry): p_ai = 0.85   burstiness=0.25, transition_density=0.80, ai_vocab_hits=13
Signal 3 (Readability): p_ai = 0.75  pronoun_density=0.0, punct_variety=0.0

Fused p_ai = 0.50·0.95 + 0.30·0.85 + 0.20·0.75 = 0.88
raw        = 2·|0.88 − 0.5| = 0.76
disagree   = mean(|0.95−0.85|, |0.95−0.75|, |0.85−0.75|) = mean(0.10, 0.20, 0.10) = 0.133
confidence = 0.76 · (1 − 0.5·0.133) · 1.0 = 0.71
```

Verdict: **`ai`** → label: `🤖 Likely AI-generated. … (71% confidence) …`

All three signals agree strongly. The small remaining disagreement only slightly reduces confidence;
the system has enough conviction to make a definitive claim.

---

**Example 2 — Low-confidence case (formal human writing)**

Input: a polished academic paragraph — no transition phrases, no AI vocab, but formally structured
with no first-person voice.

```
Signal 1 (LLM):       p_ai = 0.80   rationale: "Well-structured, technical, lacks personal voice —
                                                  reads as machine-written."
Signal 2 (Stylometry): p_ai = 0.35   burstiness=0.26, transition_density=0.0, ai_vocab_hits=0
Signal 3 (Readability): p_ai = 0.75  pronoun_density=0.0 (academic avoids first-person)

Fused p_ai = 0.50·0.80 + 0.30·0.35 + 0.20·0.75 = 0.65
raw        = 2·|0.65 − 0.5| = 0.30
disagree   = mean(|0.80−0.35|, |0.80−0.75|, |0.35−0.75|) = mean(0.45, 0.05, 0.40) = 0.30
confidence = 0.30 · (1 − 0.5·0.30) · 0.958 = 0.22
```

Verdict: **`uncertain`** → label: `🤔 Origin unclear. … (22% confidence) …`

The LLM over-flags formal polish as AI; stylometry correctly reads natural sentence variation as
human. Their major disagreement (0.45 gap) suppresses confidence to 0.22 — well below 0.70. The
system correctly refuses to make an accusation and instead surfaces the appeal path. This is the
intended behavior for the hardest false-positive case.

**What the contrast shows:** a `p_ai` of 0.88 vs 0.65 produces confidence of 0.71 vs 0.22 — not
a modest difference but a categorical one. The confidence formula produces meaningful variation, not
a near-constant output, because the disagreement penalty is the dominant variable, not just the
direction of the signal.

---

## Transparency Labels

### Design rationale

Every label must satisfy three properties:
1. Name the result plainly so a non-technical reader understands it.
2. State the confidence as a percentage so the uncertainty is visible, not hidden.
3. Make clear it is an estimate, not a fact — and point to recourse where appropriate.

The three variants are fixed strings in `labels.py` and mirrored verbatim in `planning.md`.
`{pct}` is filled with `round(confidence × 100)` as a whole-number percentage.

### All three variants — exact text

**High-confidence AI** (`confidence ≥ 0.70`, `p_ai ≥ 0.50`):
> 🤖 Likely AI-generated. Provenance Guard's analysis strongly suggests this was produced with generative AI ({pct}% confidence). This is an automated estimate, not a certainty — if you're the creator and disagree, you can appeal.

**High-confidence human** (`confidence ≥ 0.70`, `p_ai < 0.50`):
> ✍️ Likely human-written. Provenance Guard's analysis strongly suggests a person wrote this ({pct}% confidence). This is an automated estimate, not a certainty.

**Uncertain** (`confidence < 0.70`):
> 🤔 Origin unclear. Provenance Guard could not confidently determine whether this was written by a person or generated by AI ({pct}% confidence). Treat this as inconclusive. If you're the creator, you can add context by appealing.

### Rendered examples

> 🤖 Likely AI-generated. Provenance Guard's analysis strongly suggests this was produced with generative AI (71% confidence). This is an automated estimate, not a certainty — if you're the creator and disagree, you can appeal.

> ✍️ Likely human-written. Provenance Guard's analysis strongly suggests a person wrote this (77% confidence). This is an automated estimate, not a certainty.

> 🤔 Origin unclear. Provenance Guard could not confidently determine whether this was written by a person or generated by AI (22% confidence). Treat this as inconclusive. If you're the creator, you can add context by appealing.

### Why *Uncertain* is a first-class label, not a fallback

Most AI detectors collapse borderline cases into whichever side is more probable. This system
does not. *Uncertain* deliberately withholds the verdict when confidence is below 0.70 because:

- A 65% confident "Likely AI" sounds authoritative but is wrong one in three times — far too often
  for something that can damage a creator's reputation.
- The label text ("treat this as inconclusive") is honest to a reader in a way that "Likely AI
  (65%)" is not.
- The appeals path exists precisely for these cases — *Uncertain* is an invitation, not a dead end.

---

## API

| Endpoint | Purpose |
|---|---|
| `POST /submit` | Classify text (rate-limited). Returns `attribution`, `p_ai`, `confidence`, per-signal scores, and label text. |
| `POST /appeal` | Contest a classification. Logs reasoning, flips status to `under_review`. |
| `GET /log` | Full audit log, newest first. Each entry includes all signal scores and any attached appeal. |
| `GET /appeals` | Reviewer queue — open appeals paired with the original decision side-by-side. |
| `GET /analytics` | Aggregated statistics: verdict distribution, appeal rate, signal disagreement rate. |
| `GET /health` | Liveness check. |

Full request/response shapes are in [`planning.md` → API Surface](./planning.md).

---

## Rate Limiting

`POST /submit` is limited to **10 requests/minute and 100 requests/day per client IP**.
Only `/submit` is limited because it is the only endpoint that makes a Groq API call — it is
simultaneously the most expensive path and the most attractive abuse target.

**Why 10/min and 100/day:** these numbers are tied to realistic creator behavior. A person editing
their own work might submit a handful of times in a session — 10/min is comfortable headroom.
Nobody hand-submits 100 pieces a day, so 100/day caps slow-drip automated scraping without
touching real usage. In a production deployment behind platform auth, the key would be
`creator_id`, not IP, so one abusive client can't exhaust a shared limit.

**Evidence** — 12 rapid requests to a running server:

```
request  1 → 200      request  7 → 200
request  2 → 200      request  8 → 200
request  3 → 200      request  9 → 200
request  4 → 200      request 10 → 200
request  5 → 200      request 11 → 429
request  6 → 200      request 12 → 429
```
```json
{ "error": "rate_limit_exceeded",
  "detail": "Too many requests (10 per 1 minute). Try again shortly." }
```

---

## Audit Log

Every decision is written to SQLite and available via `GET /log` (newest first). Each entry
carries the timestamp, `content_id`, attribution, confidence, **all three signal scores**, the
rendered label, the current status, and — if filed — the creator's appeal attached alongside.

Real `GET /log` entry (with an attached appeal):
```json
{
  "content_id": "c_b4780b3c4a9d",
  "timestamp": "2026-06-26T21:39:22.451Z",
  "creator_id": "writer-formal",
  "attribution": "uncertain",
  "p_ai": 0.618,
  "confidence": 0.157,
  "signals": {
    "llm":        { "p_ai": 0.80, "rationale": "Well-structured, technical, lacks personal voice." },
    "stylometric": { "p_ai": 0.346, "features": { "burstiness": 0.26, "transition_density": 0.0,
                      "ai_vocab_hits": 0, "lexical_diversity": 0.86, "word_count": 43 } },
    "readability": { "p_ai": 0.75,  "features": { "pronoun_density": 0.0, "punct_variety": 0.0,
                      "bigram_repeat_rate": 0.0, "word_count": 43 } }
  },
  "label": { "variant": "uncertain",
              "text": "🤔 Origin unclear. … (16% confidence) …" },
  "status": "under_review",
  "appeal_reasoning": "I wrote this myself. I am a non-native English speaker …",
  "appeal": { "appeal_id": "a_d0770f20", "logged_at": "2026-06-26T21:39:44.340Z" }
}
```

---

## Appeals

```bash
curl -s -X POST http://127.0.0.1:5000/appeal \
  -H "Content-Type: application/json" \
  -d '{"content_id": "c_b4780b3c4a9d",
       "creator_reasoning": "I wrote this myself. I am a non-native English speaker and write
        formally. Here is a link to my draft history."}'
```
```json
{ "content_id": "c_b4780b3c4a9d", "appeal_id": "a_d0770f20",
  "status": "under_review", "logged_at": "2026-06-26T21:39:44.340Z",
  "message": "Appeal received. This classification is now under review by a human." }
```

The appeal row is inserted **beside** the original decision row — the original is never modified.
A human reviewer works the queue at `GET /appeals?status=under_review` and sees the verdict, both
signal breakdowns, and the creator's written case side by side.

---

## Analytics Dashboard *(stretch feature)*

`GET /analytics` returns three sections derived from the SQLite audit log — no schema changes,
pure aggregate reads.

```json
{
  "detection_patterns": {
    "total_decisions": 20,
    "verdict_distribution": {
      "ai":       { "count": 2,  "pct": 10.0, "mean_confidence": 0.777 },
      "human":    { "count": 2,  "pct": 10.0, "mean_confidence": 0.768 },
      "uncertain":{ "count": 13, "pct": 65.0, "mean_confidence": 0.127 }
    },
    "by_content_type": { "unspecified": 17, "blog": 2, "story": 1 }
  },
  "appeal_rate": {
    "total_appeals": 1,     "rate": 0.05,
    "open_appeals": 1,      "mean_p_ai_appealed": 0.618
  },
  "signal_disagreement": {
    "discord_rate": 0.7059,
    "discord_threshold": 0.3,
    "note": "Fraction of prose decisions where |llm_p_ai - stylo_p_ai| > 0.3."
  }
}
```

**Why these three metrics:**
- **Verdict distribution** tells an operator what fraction of content is landing in each bucket and
  how confident those calls are. A 65% *uncertain* rate is the expected behavior of a conservative
  system, not a failure mode.
- **Appeal rate** surfaces creator dissatisfaction directly. `mean_p_ai_appealed = 0.618` means
  appeals cluster near the uncertain zone — the system is being challenged where it is weakest,
  which is expected and healthy.
- **Signal disagreement rate** (custom metric) is the most diagnostic: 70.6% of prose decisions
  have a >0.3 gap between the LLM and stylometry. This explains *why* most results are *uncertain*
  — it's not that the signals are weak, it's that they frequently pull in opposite directions.
  This metric is more actionable than the verdict distribution alone.

---

## Known Limitations

Detection is an unsolved problem and this system is explicit about it. The known failure cases are
tied to specific signal properties, not generic "needs more data" caveats:

**1. Formal / academic / non-native-English human writing → most likely false positive**

The LLM judge (Signal 1) is biased against polish and formal structure — it has learned that
fluent, evenly-paced, well-organized writing often comes from AI, because most training examples
of AI text are exactly that. Signal 3 (readability) makes this worse: academic writing avoids
first-person pronouns just as much as AI writing does. So both Signals 1 and 3 can push toward AI
on a carefully-written human essay, even when Signal 2 (stylometry) reads natural sentence variance
and correctly pulls back. This is the case most likely to produce a real false positive.

*Why the system still handles it acceptably:* When Signal 1 leans AI but Signal 2 does not, the
pairwise disagreement term suppresses confidence, typically below 0.70. The result is *Uncertain*
rather than an accusation — and the label explicitly offers an appeal path. The false-positive
problem is real, but the system's response to its own internal conflict is to soften the claim, not
harden it. (Worked example in the Confidence Scoring section above.)

**2. Repetitive poetry and song lyrics → stylometry false positive**

A folk song with a refrain and short even lines has low burstiness, low lexical diversity, and
heavy repetition — the exact fingerprint Signal 2 reads as AI. This will typically score
`p_stylo ≈ 0.7+`. The LLM usually recognizes it as poetry and scores it low, creating strong
disagreement and landing on *Uncertain* rather than *Likely AI*. Poetry is a known weak spot that
a future `content_type=poem` routing could mitigate by down-weighting stylometry for that type.

**3. Lightly human-edited AI → false negative, by design**

A piece of AI-generated text with a few paragraphs rewritten by a human will pull both signals
toward *human*. This is an accepted asymmetry: the false-negative (missing AI) is treated as less
harmful than the false-positive (accusing a human). The system errs toward protecting creators.

**4. Short texts under ~60 words → structural uncertainty**

Neither stylometric statistics nor bigram repetition are stable on fragments. The short-text
penalty in `scoring.py` drives confidence down on texts below 60 words so the result is
*Uncertain* by construction — the system explicitly refuses to make confident claims about content
it cannot reliably analyze.

---

## Spec Reflection

### One way the spec helped

Writing `planning.md` before any implementation code forced me to commit to the confidence formula
(§2) and the label variants (§3) in writing *before* I saw how the pipeline actually behaved. That
pre-commitment was valuable: it meant the implementation had a target to hit rather than
reverse-engineering a justification for whatever the code produced. The appeal workflow design in
particular (§4) came out of writing the spec first — the requirement that "the original decision
is never overwritten" emerged naturally from reasoning about what a human reviewer needs to see,
not from the code.

### One way implementation diverged from the spec

The spec specified a **symmetric 0.70 confidence bar**. During M4 design I argued for an
*asymmetric* bar — a higher threshold to declare "AI" than "human" — as a way to encode the
false-positive priority directly into the threshold. This was a reasonable design argument.

When I built the calibration harness, it became clear that the asymmetric threshold was solving
the wrong problem: *nothing* was clearing even the 0.70 bar because the LLM was clustering its
outputs at ~0.8/0.2, making `raw = 2·|p_ai−0.5|` cap out around 0.64. Every sample was reading
as *Uncertain*. The real fix was not a threshold nudge but a prompt fix — instructing the LLM to
use the full 0–1 range when evidence is clear. Once that was done, the symmetric 0.70 worked
correctly.

**The lesson:** I specified a threshold in M2 before having any data on what scores the pipeline
would actually produce. Threshold values only become meaningful once you have a calibration corpus.
The spec commitment was worth making because it forced early thinking about false positives, but
the specific number needed to be treated as provisional until M4 showed real outputs.

---

## AI Usage

### Instance 1 — Flask skeleton and LLM signal (Milestone 3)

**What I directed the AI to do:** I provided the Architecture diagram and Signal 1 spec from
`planning.md` and asked the AI to generate `app.py` (Flask skeleton with `POST /submit`) and
`signals/llm.py` (`score_llm()` calling Groq with strict-JSON output and a parse/retry/fallback
pattern).

**What it produced:** A working scaffold matching the spec structure — the retry loop, the 0.5
fallback on failure, and the `response_format: {"type": "json_object"}` parameter were all
correctly placed.

**What I revised:** After running the calibration harness, every test case returned *Uncertain*
because the LLM was clustering probabilities at ~0.8/0.2. The AI-generated prompt had asked the
model to return a probability, but did not specify the expected range behavior. I revised the
system prompt to explicitly instruct the model to use extreme values (≥0.9 / ≤0.1) when evidence
is clear and reserve ~0.5 for genuinely ambiguous cases. This single prompt revision restored the
calibration. The scaffold was correct; the calibration of the LLM's output range was not addressed
by the initial generation.

### Instance 2 — Signal 3 readability feature design (stretch feature)

**What I directed the AI to do:** I asked for a third pure-Python detection signal measuring
"readability and structural predictability," providing the architecture context and the requirement
that it be orthogonal to the existing two signals. I specified a Fog-index-based approach as the
primary feature, with bigram repetition and bullet-structure density as supporting features.

**What it produced:** A working implementation of the Fog-index calculation (syllable-cluster
heuristic, edge-case guards for zero sentences/words), bigram repetition, bullet-line detection,
and the weighted fusion. The code was syntactically correct and followed the signal contract.

**What I overrode:** After running the calibration corpus, every sample — both AI and human —
scored Fog 22–32, far outside the assumed [8–12] comfort zone. The Fog feature was producing a
sub-score of essentially zero for all inputs and contributing nothing to the signal. I overrode the
Fog-index feature entirely and replaced it with **personal pronoun density**: AI text avoids
first- and second-person pronouns (persistently impersonal register), which discriminated correctly
across all test cases. The structural skeleton the AI generated was kept; the primary feature was
replaced after empirical testing showed the original design assumption was wrong.

---

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Add GROQ_API_KEY=your_key_here
# Get a free key at https://console.groq.com/keys
```

Run the server:
```bash
python app.py
# or: flask --app app run
```

Run the calibration harness (Pass 1 needs no key; Pass 2 needs GROQ_API_KEY):
```bash
python scripts/try_scoring.py
```

Run the end-to-end demo:
```bash
bash scripts/demo.sh
```

---

## License

MIT
