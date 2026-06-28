"""Provenance Guard — Flask API.

Endpoints:
  POST /submit    classify text with ALL signals, fuse → confidence + verdict + label,
                  write an audit row  (rate-limited)
  POST /appeal    contest a classification: log the creator's reasoning, flip status →
                  under_review (no automatic re-classification)
  GET  /log       most-recent audit entries as JSON (appeals attached)
  GET  /appeals   reviewer queue: open appeals with the original decision beside the creator's case
  GET  /health    liveness check
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

load_dotenv()  # pull GROQ_API_KEY from .env before any signal import needs it

from labels import render_label  # noqa: E402
from scoring import fuse  # noqa: E402
from signals.llm import score_llm  # noqa: E402
from signals.readability import score_readability  # noqa: E402
from signals.stylometry import score_stylometry  # noqa: E402
from store import (  # noqa: E402
    analytics_summary,
    get_appeals,
    get_decision,
    init_db,
    insert_appeal,
    insert_decision,
    recent_decisions,
    set_status,
)

app = Flask(__name__)
init_db()  # ensure the decisions + appeals tables exist (and migrate older dbs)

# Rate limiting (planning.md §"Rate Limiting"; reasoning documented in README). In-memory storage
# is fine for a single-process demo; a real deployment would use Redis and key on creator identity.
limiter = Limiter(get_remote_address, app=app, storage_uri="memory://")


def _now_iso() -> str:
    """Current UTC time as an ISO-8601 string with a trailing Z."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@app.post("/submit")
@limiter.limit("10 per minute;100 per day")
def submit():
    """Classify text with both signals and record the decision (rate-limited).

    Accepts both the milestone field names (``text``/``creator_id``) and the spec aliases
    (``content``/``author_id``); ``content_type`` is optional.
    """
    body = request.get_json(silent=True) or {}

    text = body.get("text") or body.get("content")
    if not isinstance(text, str) or not text.strip():
        return jsonify({"error": "Field 'text' (or 'content') is required and must be non-empty."}), 400

    creator_id = body.get("creator_id") or body.get("author_id")
    content_type = body.get("content_type")

    # Run all three signals. Each is independent and never raises (failures → p_ai≈0.5).
    llm = score_llm(text)                    # Signal 1 — holistic LLM judge
    stylo = score_stylometry(text)           # Signal 2 — mechanical stylometry
    read = score_readability(text)           # Signal 3 — n-gram predictability + Fog index
    word_count = stylo["features"]["word_count"]

    # Fuse → calibrated confidence + 3-way verdict (ai | human | uncertain). See scoring.py.
    fused = fuse(llm["p_ai"], stylo["p_ai"], word_count, read["p_ai"])
    verdict = fused["verdict"]
    label = render_label(verdict, fused["confidence"])

    content_id = "c_" + uuid4().hex[:12]
    timestamp = _now_iso()

    insert_decision(
        {
            "content_id": content_id,
            "creator_id": creator_id,
            "timestamp": timestamp,
            "content": text,
            "content_type": content_type,
            "attribution": verdict,
            "p_ai": fused["p_ai"],
            "confidence": fused["confidence"],
            "llm_p_ai": llm["p_ai"],
            "llm_rationale": llm["rationale"],
            "stylo_p_ai": stylo["p_ai"],
            "stylo_features": json.dumps(stylo["features"]),
            "read_p_ai": read["p_ai"],
            "read_features": json.dumps(read["features"]),
            "label_variant": label["variant"],
            "label_text": label["text"],
            "status": "classified",
        }
    )

    return jsonify(
        {
            "content_id": content_id,
            "creator_id": creator_id,
            "attribution": verdict,
            "p_ai": fused["p_ai"],
            "confidence": fused["confidence"],
            "signals": {
                "llm": {"p_ai": llm["p_ai"], "rationale": llm["rationale"]},
                "stylometric": {"p_ai": stylo["p_ai"], "features": stylo["features"]},
                "readability": {"p_ai": read["p_ai"], "features": read["features"]},
            },
            "label": label,
            "status": "classified",
            "timestamp": timestamp,
        }
    )


@app.post("/appeal")
def appeal():
    """Contest a classification. Logs the creator's reasoning beside the original decision and
    flips that decision's status to ``under_review``. No automatic re-classification — a human
    reviews the queue. Accepts ``creator_reasoning`` (milestone) or ``reason`` (spec alias).
    """
    body = request.get_json(silent=True) or {}

    content_id = body.get("content_id")
    reason = body.get("creator_reasoning") or body.get("reason")
    if not content_id or not isinstance(content_id, str):
        return jsonify({"error": "Field 'content_id' is required."}), 400
    if not reason or not isinstance(reason, str) or not reason.strip():
        return jsonify({"error": "Field 'creator_reasoning' (or 'reason') is required."}), 400

    if get_decision(content_id) is None:
        return jsonify({"error": f"No decision found for content_id '{content_id}'."}), 404

    appeal_id = "a_" + uuid4().hex[:8]
    logged_at = _now_iso()
    insert_appeal(
        {
            "appeal_id": appeal_id,
            "content_id": content_id,
            "reason": reason,
            "author_id": body.get("author_id") or body.get("creator_id"),
            "evidence_url": body.get("evidence_url"),
            "logged_at": logged_at,
        }
    )
    set_status(content_id, "under_review")

    return jsonify(
        {
            "content_id": content_id,
            "appeal_id": appeal_id,
            "status": "under_review",
            "logged_at": logged_at,
            "message": "Appeal received. This classification is now under review by a human.",
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


@app.get("/appeals")
def appeals():
    """Reviewer queue. ``?status=under_review`` filters to open appeals (default: all appealed)."""
    return jsonify({"appeals": get_appeals(request.args.get("status"))})


@app.get("/analytics")
def analytics():
    """Aggregated detection statistics: verdict distribution, appeal rate, signal disagreement."""
    return jsonify(analytics_summary())


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.errorhandler(429)
def rate_limited(err):
    """Return rate-limit rejections as clean JSON instead of Flask's HTML page."""
    return (
        jsonify(
            {
                "error": "rate_limit_exceeded",
                "detail": f"Too many requests ({err.description}). Try again shortly.",
            }
        ),
        429,
    )


if __name__ == "__main__":
    # Port 5000 matches the milestone's grading curl. (macOS note: if AirPlay Receiver holds
    # 5000, disable it in System Settings → General → AirDrop & Handoff.)
    app.run(host="127.0.0.1", port=5000, debug=True)
