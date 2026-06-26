"""Provenance Guard — Flask API (Milestone 3).

Endpoints live here:
  POST /submit   classify a piece of text with Signal 1 (LLM judge); write an audit row
  GET  /log      most-recent audit entries as JSON (documentation / grading visibility)
  GET  /health   liveness check

Milestone 3 is deliberately single-signal. ``confidence`` and ``label`` are **placeholders**
(clearly marked) — real fusion lands in M4 and the 3-variant transparency labels in M5. We do
not emit a confident-looking number from one immature signal.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from dotenv import load_dotenv
from flask import Flask, jsonify, request

load_dotenv()  # pull GROQ_API_KEY from .env before any signal import needs it

from signals.llm import score_llm  # noqa: E402
from store import init_db, insert_decision, recent_decisions  # noqa: E402

app = Flask(__name__)
init_db()  # ensure the audit table exists at startup

# Placeholder label shown until M5 ships the real transparency labels.
_PLACEHOLDER_LABEL = {
    "variant": "placeholder",
    "text": "Provisional single-signal result — calibrated confidence (M4) and the final "
    "transparency label (M5) are not applied yet.",
}


def _now_iso() -> str:
    """Current UTC time as an ISO-8601 string with a trailing Z."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@app.post("/submit")
def submit():
    """Classify text with the LLM judge and record the decision.

    Accepts both the milestone field names (``text``/``creator_id``) and the spec aliases
    (``content``/``author_id``); ``content_type`` is optional.
    """
    body = request.get_json(silent=True) or {}

    text = body.get("text") or body.get("content")
    if not isinstance(text, str) or not text.strip():
        return jsonify({"error": "Field 'text' (or 'content') is required and must be non-empty."}), 400

    creator_id = body.get("creator_id") or body.get("author_id")
    content_type = body.get("content_type")

    # Signal 1 — LLM judge. score_llm never raises; failures come back as p_ai=0.5.
    llm = score_llm(text)
    p_ai = llm["p_ai"]

    # M3 provisional verdict from the single signal. The asymmetric "harder to accuse than to
    # exonerate" verdict logic (and the 'uncertain' class) arrives with fusion in M4.
    attribution = "likely_ai" if p_ai >= 0.5 else "likely_human"

    content_id = "c_" + uuid4().hex[:12]
    timestamp = _now_iso()

    # confidence is a *placeholder* in M3: we surface the raw signal value, not a calibrated
    # certainty. It is explicitly flagged so it is never mistaken for a real confidence score.
    confidence_placeholder = round(p_ai, 2)

    insert_decision(
        {
            "content_id": content_id,
            "creator_id": creator_id,
            "timestamp": timestamp,
            "content": text,
            "content_type": content_type,
            "attribution": attribution,
            "p_ai": p_ai,
            "confidence": confidence_placeholder,
            "llm_p_ai": p_ai,
            "llm_rationale": llm["rationale"],
            "label_variant": _PLACEHOLDER_LABEL["variant"],
            "label_text": _PLACEHOLDER_LABEL["text"],
            "status": "classified",
        }
    )

    return jsonify(
        {
            "content_id": content_id,
            "creator_id": creator_id,
            "attribution": attribution,
            "p_ai": p_ai,
            "confidence": confidence_placeholder,
            "confidence_note": "placeholder — calibrated confidence is computed in Milestone 4",
            "signals": {"llm": {"p_ai": p_ai, "rationale": llm["rationale"]}},
            "label": _PLACEHOLDER_LABEL,
            "status": "classified",
            "timestamp": timestamp,
        }
    )


@app.get("/log")
def log():
    """Return the most recent audit entries as JSON. ``?limit=`` caps the count (default 20)."""
    try:
        limit = max(1, min(100, int(request.args.get("limit", 20))))
    except (TypeError, ValueError):
        limit = 20
    return jsonify({"entries": recent_decisions(limit)})


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    # Port 5000 matches the milestone's grading curl. (macOS note: if AirPlay Receiver holds
    # 5000, disable it in System Settings → General → AirDrop & Handoff.)
    app.run(host="127.0.0.1", port=5000, debug=True)
