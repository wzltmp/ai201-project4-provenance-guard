# Provenance Guard — Planning & Spec

---

## Stretch Feature A — Ensemble Detection (Signal 3: Readability)

**Goal:** Add a third pure-Python detection signal orthogonal to both existing ones. Signal 1
(LLM) captures holistic semantics; Signal 2 (stylometry) captures sentence-level variance,
transitions, and known AI vocabulary. Signal 3 (readability) captures **n-gram predictability,
structural formatting density, and prose complexity** — patterns neither of the other signals
measure.

### Signal 3: Readability heuristics (`signals/readability.py`)

`score_readability(text: str) -> dict` — never raises; falls back to `p_ai=0.5` on any error.

Sub-scores (weighted sum, weights sum to 1.0):

| Sub-score | Weight | AI indicator | Threshold |
|---|---|---|---|
| `pronoun_absence` | 0.40 | `1 - pronoun_density` (inverted). AI avoids first/second-person voice. | ≤0.5% pronouns → fully AI; ≥5% → fully human |
| `punct_monotony` | 0.35 | `1 - punct_variety`. AI ends nearly all sentences with periods. | 0 variety → 1.0 AI; rising variety → lower |
| `bigram_repeat_rate` | 0.25 | `1 - unique_bigrams/total_bigrams`. More repetitive = AI-like. | Meaningful on longer text; 0 on short texts (neutral) |

Return shape:
```json
{"signal": "readability", "p_ai": 0.75,
 "features": {"bigram_repeat_rate": 0.0, "punct_variety": 0.0,
               "pronoun_density": 0.0, "word_count": 65}}
```

**Calibration note:** A Fog-index [8,12] comfort-zone approach was tested and rejected —
corpus samples (both AI and human) scored Fog 22–32, so the zone provided no discrimination.
Personal pronoun absence proved far more robust: AI text is consistently impersonal; casual
human text is pronoun-rich; formal human text is borderline (acceptable).

### Updated fusion (`scoring.py`)

`fuse(p_llm, p_stylo, word_count, p_read=None)`

| Path | Condition | Weights | Disagreement |
|---|---|---|---|
| 3-signal (prose) | `p_read is not None` | LLM 0.50 · Stylo 0.30 · Read 0.20 | mean of 3 pairwise \|pi−pj\| |
| 2-signal (metadata / fallback) | `p_read is None` | LLM 0.60 · other 0.40 | \|p_llm − p_stylo\| |

Remove the old top-level `WEIGHT_LLM = 0.6` constant; inline weights inside each branch.

### DB changes (`store.py`)

New columns on `decisions`: `read_p_ai REAL`, `read_features TEXT` (JSON).
Added to `_DECISION_COLUMNS`, `init_db()` migration loop, `_decision_entry()` (conditional on
`row["read_p_ai"] is not None`), and `get_appeals()` signals block.

### API impact

`POST /submit` response `signals` block gains:
```json
"readability": {"p_ai": 0.74, "features": {"bigram_repeat_rate": 0.31, ...}}
```
`GET /log` and `GET /appeals` expose the same block per entry.

---

This is the implementation-ready specification for Provenance Guard. It is written **before**
the application code and is the primary reference fed to AI tools during Milestones 3–5. The
verbatim transparency-label text and the final rate-limit numbers are mirrored in the `README`;
this file is where the *reasoning* and *contracts* live.

Document map: **Architecture** · **(1) Detection Signals** · **(2) Uncertainty
Representation** · **(3) Transparency Label Design** · **(4) Appeals Workflow** ·
**(5) Edge Cases** · **API Surface** · **AI Tool Plan** · **Traceability**.

---

## Stretch Feature D — Analytics Dashboard

**Goal:** A single `GET /analytics` endpoint returning aggregated detection intelligence with no
schema changes — all metrics are derived from existing columns via SQL aggregates.

### Metrics returned

**Detection patterns:**
- `total_decisions`: total rows in `decisions`
- `verdict_distribution`: per-verdict `{count, pct, mean_confidence}` for `ai`, `human`, `uncertain`
- `by_content_type`: count of decisions per `content_type` value

**Appeal rate:**
- `total_appeals`: total rows in `appeals`
- `rate`: `total_appeals / total_decisions` (float, 0 if no decisions)
- `open_appeals`: count of decisions with `status = 'under_review'`
- `mean_p_ai_appealed`: average `p_ai` of appealed decisions (NULL-safe; null when no appeals)

**Signal disagreement rate** (custom metric):
- `discord_rate`: fraction of prose decisions where `|llm_p_ai − stylo_p_ai| > 0.3`
- SQLite NULL propagation automatically excludes metadata decisions (where `stylo_p_ai IS NULL`)
- Denominator: `COUNT(*) WHERE stylo_p_ai IS NOT NULL` (prose-only)
- `discord_threshold`: 0.3 (documented for transparency)

### Implementation

`store.py`: new `analytics_summary() -> dict`. Single connection, multiple `fetchone`/`fetchall`
queries. Guard all divisions: `if total else 0`. Guard `mean_p_ai_appealed` SQL NULL with
`round(x, 3) if x is not None else None`.

`app.py`: `@app.get("/analytics")` route — no rate limiting.

---

## Architecture

**Submission flow (2–3 sentence narrative):** A client calls `POST /submit` with text; the
Flask layer validates it and applies rate limiting, then the Detection Orchestrator runs both
signals (LLM judge + stylometry) in the same request. Their scores are fused into a single
`p_ai` and a calibrated `confidence`, the Label Renderer turns `(verdict, confidence)` into one
of three reader-facing strings, the whole decision is written to the SQLite audit store, and
the structured result is returned.

**Appeal flow:** A creator calls `POST /appeal` with the `content_id` and their written
reasoning; the Appeals Handler looks up the original decision, flips its status to
`under_review`, stores the reasoning, and writes a linked appeal row to the audit store (no
automatic re-classification — a human reviews it via the appeal queue).

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
│    short-text penalties → confidence ;  pick verdict           │
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
 Client (creator)
   │  POST /appeal  { content_id, reason, author_id? }
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
 Client            (human reviewer later reads the queue via GET /appeals)
```

---

## 1. Detection Signals

Two distinct signals are chosen **because their blind spots do not overlap** (a holistic
semantic judge + a mechanical statistical analyzer). When two methods that fail in *different*
ways agree, we can be confident; when they disagree, that disagreement drives the score toward
*uncertain*.

### Signal 1 — LLM semantic judge (Groq `llama-3.3-70b-versatile`)

- **What it measures:** the holistic *feel* of the text — coherence, specificity, voice,
  idiomatic risk-taking — patterns a large model recognizes from vast exposure to human and
  machine text.
- **Why it differs (human vs AI):** AI prose tends to be fluent-but-generic (evenly hedged,
  low on concrete personal specifics, structurally balanced); human writing takes more
  idiosyncratic risks and varies in quality.
- **Output shape:** the model is prompted to return strict JSON, parsed into:
  ```json
  { "signal": "llm", "p_ai": 0.88, "rationale": "Even, hedged phrasing; no concrete specifics." }
  ```
  `p_ai` is a float in [0,1] (probability the text is AI). If the model returns malformed
  output, we retry once, then fall back to `p_ai = 0.5` with `rationale: "parse_failed"` (a
  non-committal value that the fusion step treats as low-information).
- **Blind spot:** biased toward flagging *polished/formal human writing* as AI (harms
  non-native writers and careful editors); easily fooled by lightly-edited AI; non-deterministic
  / prompt-sensitive; sees only style, never true provenance.

### Signal 2 — Stylometric heuristics (pure Python, no external libraries)

- **What it measures:** quantifiable surface statistics:
  | Feature | Definition | AI tends to… |
  |---|---|---|
  | `burstiness` | stdev of sentence lengths ÷ mean (coefficient of variation) | be **low** (uniform sentences) |
  | `lexical_diversity` | unique words ÷ total words (type–token ratio) | be **lower** (repetitive) on long text |
  | `transition_density` | rate of "Moreover/Furthermore/However/In conclusion…" | be **high** |
  | `ai_vocab_hits` | count of LLM-favored words ("delve, tapestry, testament, boundless…") | be **higher** |
  | `mean_sentence_len`, `word_count` | support metrics (also gate the short-text penalty) | — |
- **Why it differs:** AI text statistically regresses toward the mean — low burstiness,
  repetitive transition scaffolding, recognizable preferred vocabulary; human text is burstier
  and more lexically uneven.
- **Output shape:**
  ```json
  { "signal": "stylometric", "p_ai": 0.76,
    "features": { "burstiness": 0.18, "lexical_diversity": 0.41, "transition_density": 0.07,
                  "ai_vocab_hits": 3, "mean_sentence_len": 19.4, "word_count": 220 } }
  ```
  `p_ai` is a float in [0,1], computed as a weighted sum of the per-feature sub-scores (each
  feature normalized to a 0–1 "AI-ness" contribution), then clamped. **Implemented weights
  (M4, `signals/stylometry.py`):** burstiness 0.35, transition-density 0.25, AI-vocab 0.25,
  lexical-diversity 0.15 — burstiness is the most robust discriminator, so it leads; lexical
  diversity is length-sensitive and noisy, so it trails. Each sub-score interpolates linearly
  between an "AI-end" and "human-end" cutoff (e.g. burstiness ≤ 0.25 → fully AI-like, ≥ 0.75 →
  fully human-like). Cutoffs/weights were calibrated against the §2 mini-corpus in
  `scripts/try_scoring.py`.
- **Blind spot:** genre-dependent and trivially gamed — formal/technical human writing looks
  "AI-like," poetry/lyrics break the prose assumptions entirely, very short texts lack stable
  statistics, and a user can defeat it by varying sentence length or adding typos.

### Combining the two signals → a single confidence

Both signals emit `p_ai ∈ [0,1]`. They are fused (see §2 for the full derivation):

```
p_ai       = 0.6 · p_llm + 0.4 · p_stylo          # LLM weighted higher but cannot dominate
raw        = 2 · |p_ai − 0.5|                      # certainty: 0 at coin-flip, 1 at extremes
disagree   = |p_llm − p_stylo|                     # how much the signals conflict
short_pen  = 1.0 if word_count ≥ 60 else (0.5 … 1.0 scaled by length)
confidence = clamp( raw · (1 − 0.5·disagree) · short_pen , 0, 1 )
```

---

## 2. Uncertainty Representation

**What the system reports:** every decision returns two numbers and a verdict —
`p_ai` (which *direction*: ≥ 0.5 leans AI, < 0.5 leans human) and **`confidence`** (how
*certain* we are of that direction, in [0,1]). `confidence` is the number shown to readers as a
percentage; `p_ai` is exposed in the API for transparency.

**What a `confidence` of 0.6 means — concretely.** `confidence` is certainty in the verdict,
*not* the probability of AI. `0.0` = a coin-flip (no information, or the two signals cancel).
`1.0` = maximal certainty. **0.6 means "we have a real lean but not a strong one"** — it sits
**below our 0.70 bar for making a definitive public claim**, so the reader sees the *Uncertain*
label and an invitation to add context, never an accusation. In plain language for a
non-technical user: *"We have a hunch, but we're not sure enough to put a confident label on it."*

**Mapping raw signal outputs → a calibrated score.** Raw signal `p_ai`s are fused into one
`p_ai`, converted to a base certainty `raw = 2·|p_ai−0.5|`, then **deliberately suppressed** in
two situations that should *not* read as confident:
- **Signal disagreement** (`disagree = |p_llm − p_stylo|`): full disagreement halves the score
  via `(1 − 0.5·disagree)`. This is the core safety mechanism — when our two differently-biased
  signals conflict, we back off.
- **Short text** (`word_count < 60`): a sufficiency factor scales the score toward 0, because
  neither signal is reliable on a fragment.

Calibration itself happens in **M4**: I run a labeled mini-corpus, bucket decisions by
`confidence`, and adjust the weights (0.6/0.4), the 0.70 threshold, and the penalty strengths
until each confidence bucket's *observed accuracy* roughly matches its *stated* confidence.

**M4 calibration result (`scripts/try_scoring.py`):** the first run collapsed *every* sample to
*Uncertain* — the LLM judge clustered clear cases at ~0.8/0.2 instead of the extremes, and since
`raw = 2·|p_ai−0.5|`, a fused `p_ai ≈ 0.82` caps certainty at ~0.64, under the 0.70 bar. Fix that
held the **0.70 threshold fixed** (kept the symmetric bar as specced): the LLM prompt now instructs
full-range calibration (≥0.9 / ≤0.1 when evidence is clear, ~0.5 only when genuinely ambiguous).
After this, clear AI → conf ≈ 0.78 (*ai*), clear casual human → ≈ 0.77 (*human*), while a formal-
human passage and a lightly-edited-AI passage both fall to *Uncertain* (≈ 0.16 / 0.24) — the bands
now separate as intended.

**Thresholds — what separates the three outcomes.** The decision variable is `confidence`,
with `p_ai` choosing the side:

| Condition | Verdict | Label variant |
|---|---|---|
| `confidence ≥ 0.70` **and** `p_ai ≥ 0.5` | `ai` | **Likely AI** |
| `confidence ≥ 0.70` **and** `p_ai < 0.5` | `human` | **Likely human** |
| `confidence < 0.70` | `uncertain` | **Uncertain** |

So **0.51 and 0.95 produce different labels, not just different numbers**: 0.51 → *Uncertain*,
0.95 → a definitive variant. (As an intuition on the `p_ai` axis, when signals agree the
uncertain zone is roughly `p_ai ∈ [0.15, 0.85]`; outside it we clear the 0.70 bar.)

### Worked example — explaining a 0.62 to a user

Both signals lean AI and mostly agree: `p_llm = 0.85`, `p_stylo = 0.79`, text is long.
`p_ai = 0.6·0.85 + 0.4·0.79 = 0.826` → `raw = 2·0.326 = 0.652` → `disagree = 0.06` →
`confidence = 0.652·(1 − 0.03) ≈ 0.63`. Even though `p_ai` is a fairly high 0.83, the
**certainty is only 0.63 — under the 0.70 bar — so the result is *Uncertain***, not "Likely AI."
We tell the creator: *"Our analysis leans toward AI, but not strongly enough to label it with
confidence — it's inconclusive, and you can add context."* This conservative-by-design behavior
(high bar before a public accusation) is exactly what protects real creators (see §5).

### How I'll test that scores are *meaningful* (run + reported in README)

- **Labeled mini-corpus:** public-domain pre-AI human classics + freshly generated AI text +
  deliberately **ambiguous** samples (AI lightly human-edited; formal human writing).
- **Band accuracy:** high-confidence bands must be *more accurate* than the uncertain band.
- **Uncertain band is actually used:** ambiguous samples should land in *uncertain*.
- **Ablation:** fused score should beat either signal alone (justifies multi-signal).
- **Disagreement → uncertainty:** confirm the §5 false-positive case resolves to *uncertain*.

---

## 3. Transparency Label Design

Exactly **three** display variants. Each names the result in plain language and makes the
confidence meaningful to a non-technical reader by stating the percentage *and* framing it as an
estimate, not a verdict. `{confidence}` is filled with `confidence` as a whole-number percent.
The **final verbatim text** below is mirrored in the `README`; the code emits these same strings.

- **High-confidence AI** —
  `🤖 Likely AI-generated. Provenance Guard's analysis strongly suggests this was produced with generative AI ({confidence}% confidence). This is an automated estimate, not a certainty — if you're the creator and disagree, you can appeal.`
- **High-confidence human** —
  `✍️ Likely human-written. Provenance Guard's analysis strongly suggests a person wrote this ({confidence}% confidence). This is an automated estimate, not a certainty.`
- **Uncertain** —
  `🤔 Origin unclear. Provenance Guard could not confidently determine whether this was written by a person or generated by AI ({confidence}% confidence). Treat this as inconclusive. If you're the creator, you can add context by appealing.`

**Review note (M2):** I reviewed the three variants and kept them. They satisfy the design
goals — each states the outcome, shows a meaningful percentage, explicitly says "estimate, not
certainty," and the two fallible outcomes (AI, uncertain) both point to the appeal path. No
revision needed; changing the strings would require re-syncing the `README` mirror.

---

## 4. Appeals Workflow

- **Who can submit:** the **content's creator**. This is a backend service, so the *platform*
  authenticates the user and forwards their identity; the backend records `author_id` when
  supplied and requires the `content_id` of the original decision. (We do not gate on identity
  at the API level — the platform owns auth — but `author_id` is logged for the reviewer.)
- **What they provide:** `content_id` (required), `reason` — the creator's written argument
  (required), and optionally `author_id` and an `evidence_url` (e.g., link to drafts).
- **What the system does on receipt:**
  1. Look up the original decision by `content_id` (404 if unknown).
  2. **Status change:** `classified → under_review` on that content record.
  3. **Logging:** insert an `appeals` row linked (FK) to the original decision, capturing
     `appeal_id`, `content_id`, `reason`, `author_id?`, `evidence_url?`, and `logged_at`. The
     original decision row is **never overwritten** — the appeal sits *alongside* it.
  4. No automatic re-classification (a human decides).
- **What a human reviewer sees in the appeal queue (`GET /appeals?status=under_review`):** one
  row per open appeal, showing **side by side** — *what we decided and why* vs *what the creator
  says*:
  ```json
  { "appeal_id": "a_77ff", "content_id": "c_ab12cd34", "status": "under_review",
    "submitted_at": "2026-06-25T12:05:00Z",
    "original_decision": { "attribution": "ai", "p_ai": 0.83, "confidence": 0.78,
      "signals": { "llm": { "p_ai": 0.88, "rationale": "…" },
                   "stylometric": { "p_ai": 0.76, "features": { "...": "..." } } },
      "decided_at": "2026-06-25T12:00:00Z" },
    "content_excerpt": "first ~200 chars of the submitted text…",
    "creator_reason": "I wrote this myself; here are my drafts.",
    "author_id": "user_42", "evidence_url": "https://…" }
  ```
  This gives the reviewer everything needed to adjudicate without re-running detection: the
  verdict, the confidence, *both* signal breakdowns, the content, and the creator's case.

---

## 5. Anticipated Edge Cases

Specific content types this system will likely handle **poorly**, and how the design degrades:

1. **Repetitive, simple-vocabulary poetry / song lyrics** (e.g., a folk song with a repeated
   refrain and short lines). Stylometry reads low burstiness + low lexical diversity + heavy
   repetition as **AI-like → false positive**. The LLM usually recognizes it as a poem and
   scores it human, so **the two disagree → the disagreement penalty pushes the result to
   *Uncertain*** rather than a false "Likely AI." Poetry remains a known weak spot; we rely on
   disagreement + the appeal path. (Stretch: let `content_type="poem"` down-weight stylometry.)

2. **Very short content** (a haiku, a two-line bio, microfiction under ~40 words). Neither
   signal has enough tokens to be stable. The **short-text penalty** drives `confidence` low so
   the result is **Uncertain by construction** — we explicitly refuse to make confident claims
   about fragments.

3. **Formal / academic / non-native-English human writing.** The LLM over-flags polish and
   formal structure as AI, and stylometry may also see formal transition scaffolding — so
   **both can lean AI on genuinely human text** (the hardest false-positive case). Mitigated by
   the conservative 0.70 bar (it takes strong agreement to accuse) plus the appeals workflow.
   This is the scenario traced in detail below.

4. **Lightly human-edited AI text** (AI draft, a few sentences rewritten by a person). Both
   signals get pulled toward *human* → **false negative**. We accept this asymmetry on purpose:
   consistent with "protect attribution, don't police," the system errs toward *not* accusing.

### False-positive trace (edge case 3, end-to-end)

A careful human essayist submits a polished, formal piece. **Signal 1 (LLM)** over-flags polish
→ `p_llm = 0.80`. **Signal 2 (stylometry)** sees real human burstiness/idiosyncrasy →
`p_stylo = 0.30`. **Fusion:** `p_ai = 0.60`, `raw = 0.20`, `disagree = 0.50` →
`confidence = 0.20·(1 − 0.25) = 0.15` → **Uncertain** (the system does *not* accuse). The
creator clicks appeal, writes their reasoning; status → `under_review`, the appeal is logged
beside the original decision for a human. **Principle:** the system's response to its own
internal disagreement is to *soften the claim and make recourse easy*, not to harden a guess.

---

## API Surface (the contract all code implements)

### `POST /submit` — classify text *(rate-limited)*
**Request** `{ "content": "<text>", "content_type": "poem|story|blog", "author_id": "optional" }`
**Response 200**
```json
{ "content_id": "c_ab12cd34", "attribution": "ai|human|uncertain", "p_ai": 0.83,
  "confidence": 0.78,
  "signals": { "llm": { "p_ai": 0.88, "rationale": "…" },
               "stylometric": { "p_ai": 0.76, "features": { "...": "..." } } },
  "label": { "variant": "high_confidence_ai", "text": "🤖 Likely AI-generated. …" },
  "status": "classified", "timestamp": "2026-06-25T12:00:00Z" }
```
**Errors:** `400` (empty/invalid content), `429` (rate limit exceeded).

### `POST /appeal` — contest a classification
**Request** `{ "content_id": "c_ab12cd34", "reason": "…", "author_id": "optional", "evidence_url": "optional" }`
**Response 200** `{ "content_id": "c_ab12cd34", "appeal_id": "a_77ff", "status": "under_review", "logged_at": "…" }`
**Errors:** `400` (missing reason), `404` (unknown content_id).

### `GET /log` — structured audit log (≥3 entries)
Returns every decision with `content_id`, `timestamp`, `attribution`, `confidence`, `signals`,
`status`, and any `appeal`.

### `GET /appeals?status=under_review` — reviewer appeal queue (see §4 for the row shape).
### `GET /content/<content_id>` *(optional)* · `GET /health` *(optional)*.

---

## AI Tool Plan

How `planning.md` sections feed each implementation milestone. The **Architecture diagram**
travels into all three.

### M3 — Submission endpoint + first signal (LLM judge)
- **Spec sections provided:** `## Architecture` (diagram + narrative) · `(1) Detection Signals`
  → Signal 1 + its output shape · `API Surface` → `POST /submit`.
- **Ask the AI tool to generate:** a Flask skeleton (`app.py`) with `POST /submit` returning
  the structured JSON, and a standalone `signals/llm.py` → `score_llm(text) -> {p_ai, rationale}`
  that calls Groq `llama-3.3-70b-versatile` with a strict-JSON prompt and robust parse/fallback;
  plus `.env` loading via python-dotenv.
- **How I'll verify:** call `score_llm()` **directly** on a few known inputs (a clearly-AI
  paragraph, a public-domain human passage) *before* wiring it into the endpoint — confirm
  `p_ai` is high for AI / low for human and that malformed model output is handled. Then `curl`
  `POST /submit` and check the response shape.

### M4 — Second signal + confidence scoring
- **Spec sections provided:** `(1) Detection Signals` → Signal 2 (feature table + output shape)
  and the fusion block · `(2) Uncertainty Representation` (mapping, penalties, thresholds) · the
  diagram.
- **Ask for:** `signals/stylometry.py` → `score_stylometry(text) -> {p_ai, features}` (pure
  Python, the named features), and `scoring.py` → `fuse(p_llm, p_stylo, word_count) ->
  {p_ai, confidence, verdict}` implementing the blend + disagreement + short-text penalties +
  thresholds.
- **What I'll check:** scores **vary meaningfully** between clearly-AI and clearly-human text;
  the disagreement case lands in *uncertain*; run the labeled mini-corpus and confirm
  high-confidence bands beat the uncertain band (calibration). Tune weights/threshold if needed.

### M5 — Production layer (labels + appeals + audit log + rate limit)
- **Spec sections provided:** `(3) Transparency Label Design` (3 variants) · `(4) Appeals
  Workflow` · the diagram · `API Surface`.
- **Ask for:** `labels.py` → `render_label(verdict, confidence) -> {variant, text}` emitting the
  three exact strings; SQLite audit store (decisions + appeals tables) with `POST /appeal`,
  `GET /log`, `GET /appeals`; Flask-Limiter on `/submit`.
- **How I'll verify:** craft inputs that reach **all three** label variants; submit an appeal
  and confirm status flips to `under_review` and the appeal is logged **alongside** the original
  decision in `GET /log`; confirm the rate limit returns `429` after the configured count; and
  confirm `GET /log` shows **≥3 entries**.

---

## Component → Feature Traceability

| Required feature | Component(s) | Spec § |
|---|---|---|
| Content Submission Endpoint | Flask API · `POST /submit` | API Surface |
| Multi-Signal Detection | Orchestrator · LLM judge · Stylometry | §1 |
| Confidence Scoring w/ Uncertainty | Confidence Scorer (fusion + penalties) | §2 |
| Transparency Label | Label Renderer (3 variants) | §3 |
| Appeals Workflow | Appeals Handler · `POST /appeal` · `GET /appeals` | §4 |
| Rate Limiting | Flask API layer (Flask-Limiter) | API Surface |
| Audit Log | Audit Store (SQLite) · `GET /log` | API Surface, §4 |
| Edge-case handling | penalties + appeals path | §5 |
