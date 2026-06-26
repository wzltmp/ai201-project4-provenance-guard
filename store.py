"""SQLite audit store for Provenance Guard.

Two tables: ``decisions`` (one row per ``/submit``) and ``appeals`` (one row per ``/appeal``,
linked by ``content_id``). An appeal never overwrites the original decision — it is logged
alongside it and only flips the decision's ``status`` to ``under_review`` (planning.md §4).
``GET /log`` joins the two so a reviewer sees the verdict and the creator's case together.

Pure standard library (``sqlite3``); no ORM.
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

# DB lives next to this module by default; override with PROVENANCE_DB for tests.
DB_PATH = os.environ.get(
    "PROVENANCE_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "provenance_guard.db"),
)

# Columns persisted per decision. Listed once so insert/read stay in sync.
_DECISION_COLUMNS = (
    "content_id",
    "creator_id",
    "timestamp",
    "content",
    "content_type",
    "attribution",
    "p_ai",
    "confidence",
    "llm_p_ai",
    "llm_rationale",
    "stylo_p_ai",
    "stylo_features",
    "label_variant",
    "label_text",
    "status",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    content_id     TEXT PRIMARY KEY,
    creator_id     TEXT,
    timestamp      TEXT NOT NULL,
    content        TEXT NOT NULL,
    content_type   TEXT,
    attribution    TEXT NOT NULL,
    p_ai           REAL,
    confidence     REAL,
    llm_p_ai       REAL,
    llm_rationale  TEXT,
    stylo_p_ai     REAL,
    stylo_features TEXT,
    label_variant  TEXT,
    label_text     TEXT,
    status         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS appeals (
    appeal_id    TEXT PRIMARY KEY,
    content_id   TEXT NOT NULL,
    reason       TEXT NOT NULL,
    author_id    TEXT,
    evidence_url TEXT,
    logged_at    TEXT NOT NULL,
    FOREIGN KEY (content_id) REFERENCES decisions(content_id)
);
"""

_APPEAL_COLUMNS = (
    "appeal_id",
    "content_id",
    "reason",
    "author_id",
    "evidence_url",
    "logged_at",
)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # rows behave like dicts
    return conn


def _load_json(value: str | None) -> Any:
    """Decode a JSON column back into a dict; tolerate NULL / legacy rows."""
    if not value:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


def init_db() -> None:
    """Create the audit table if needed and add any newer columns. Safe to call on every startup."""
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        # Idempotent migration: upgrade a pre-M4 db (without the stylometry columns) in place.
        existing = {r["name"] for r in conn.execute("PRAGMA table_info(decisions)")}
        for col, decl in (("stylo_p_ai", "REAL"), ("stylo_features", "TEXT")):
            if col not in existing:
                conn.execute(f"ALTER TABLE decisions ADD COLUMN {col} {decl}")


def insert_decision(record: dict[str, Any]) -> None:
    """Persist one decision. Missing keys are stored as NULL; extra keys are ignored."""
    values = [record.get(col) for col in _DECISION_COLUMNS]
    placeholders = ", ".join("?" for _ in _DECISION_COLUMNS)
    columns = ", ".join(_DECISION_COLUMNS)
    with _connect() as conn:
        conn.execute(
            f"INSERT INTO decisions ({columns}) VALUES ({placeholders})",
            values,
        )


def get_decision(content_id: str) -> dict[str, Any] | None:
    """Return one decision as a /log-shaped dict (with any appeal attached), or None if absent."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM decisions WHERE content_id = ?", (content_id,)
        ).fetchone()
    if row is None:
        return None
    return _decision_entry(row, _appeals_by_content())


def set_status(content_id: str, status: str) -> None:
    """Update a decision's status in place (e.g. classified → under_review)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE decisions SET status = ? WHERE content_id = ?", (status, content_id)
        )


def insert_appeal(record: dict[str, Any]) -> None:
    """Persist one appeal alongside (never overwriting) its original decision."""
    values = [record.get(col) for col in _APPEAL_COLUMNS]
    placeholders = ", ".join("?" for _ in _APPEAL_COLUMNS)
    columns = ", ".join(_APPEAL_COLUMNS)
    with _connect() as conn:
        conn.execute(
            f"INSERT INTO appeals ({columns}) VALUES ({placeholders})", values
        )


def _appeals_by_content() -> dict[str, dict[str, Any]]:
    """Map content_id -> its most recent appeal (newest wins if a creator appeals twice)."""
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM appeals ORDER BY logged_at ASC").fetchall()
    return {
        r["content_id"]: {
            "appeal_id": r["appeal_id"],
            "reasoning": r["reason"],
            "author_id": r["author_id"],
            "evidence_url": r["evidence_url"],
            "logged_at": r["logged_at"],
        }
        for r in rows
    }


def _decision_entry(row: sqlite3.Row, appeals: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Shape one decision row into the /log entry, attaching its appeal if one exists."""
    appeal = appeals.get(row["content_id"])
    return {
        "content_id": row["content_id"],
        "creator_id": row["creator_id"],
        "timestamp": row["timestamp"],
        "content_type": row["content_type"],
        "attribution": row["attribution"],
        "p_ai": row["p_ai"],
        "confidence": row["confidence"],
        "signals": {
            "llm": {
                "p_ai": row["llm_p_ai"],
                "rationale": row["llm_rationale"],
            },
            "stylometric": {
                "p_ai": row["stylo_p_ai"],
                "features": _load_json(row["stylo_features"]),
            },
        },
        "label": {
            "variant": row["label_variant"],
            "text": row["label_text"],
        },
        "status": row["status"],
        # Flat field the milestone checks for; full object kept under "appeal".
        "appeal_reasoning": appeal["reasoning"] if appeal else None,
        "appeal": appeal,
    }


def recent_decisions(limit: int = 20) -> list[dict[str, Any]]:
    """Return the most recent decisions, newest first, shaped for the ``/log`` response.

    Per-signal scores nest under ``signals``; a filed appeal is attached under ``appeal`` (and
    its text mirrored to ``appeal_reasoning``) so the log shows the verdict and the creator's case.
    """
    appeals = _appeals_by_content()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM decisions ORDER BY timestamp DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_decision_entry(row, appeals) for row in rows]


def get_appeals(status: str | None = None) -> list[dict[str, Any]]:
    """Reviewer queue: decisions that have an appeal, each paired with the creator's case.

    Optional ``status`` filter (e.g. ``under_review``) matches the decision's current status.
    """
    appeals = _appeals_by_content()
    if not appeals:
        return []
    placeholders = ", ".join("?" for _ in appeals)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM decisions WHERE content_id IN ({placeholders})",
            tuple(appeals),
        ).fetchall()

    queue: list[dict[str, Any]] = []
    for row in rows:
        if status is not None and row["status"] != status:
            continue
        appeal = appeals[row["content_id"]]
        queue.append(
            {
                "appeal_id": appeal["appeal_id"],
                "content_id": row["content_id"],
                "status": row["status"],
                "submitted_at": appeal["logged_at"],
                "original_decision": {
                    "attribution": row["attribution"],
                    "p_ai": row["p_ai"],
                    "confidence": row["confidence"],
                    "decided_at": row["timestamp"],
                    "signals": {
                        "llm": {"p_ai": row["llm_p_ai"], "rationale": row["llm_rationale"]},
                        "stylometric": {
                            "p_ai": row["stylo_p_ai"],
                            "features": _load_json(row["stylo_features"]),
                        },
                    },
                },
                "content_excerpt": (row["content"] or "")[:200],
                "creator_reason": appeal["reasoning"],
                "author_id": appeal["author_id"],
                "evidence_url": appeal["evidence_url"],
            }
        )
    queue.sort(key=lambda q: q["submitted_at"], reverse=True)
    return queue
