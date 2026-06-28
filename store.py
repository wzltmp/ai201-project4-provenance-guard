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
    "read_p_ai",
    "read_features",
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
    read_p_ai      REAL,
    read_features  TEXT,
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
        for col, decl in (
            ("stylo_p_ai", "REAL"),
            ("stylo_features", "TEXT"),
            ("read_p_ai", "REAL"),
            ("read_features", "TEXT"),
        ):
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
    signals: dict[str, Any] = {
        "llm": {
            "p_ai": row["llm_p_ai"],
            "rationale": row["llm_rationale"],
        },
    }
    if row["stylo_p_ai"] is not None:
        signals["stylometric"] = {
            "p_ai": row["stylo_p_ai"],
            "features": _load_json(row["stylo_features"]),
        }
    if row["read_p_ai"] is not None:
        signals["readability"] = {
            "p_ai": row["read_p_ai"],
            "features": _load_json(row["read_features"]),
        }
    return {
        "content_id": row["content_id"],
        "creator_id": row["creator_id"],
        "timestamp": row["timestamp"],
        "content_type": row["content_type"],
        "attribution": row["attribution"],
        "p_ai": row["p_ai"],
        "confidence": row["confidence"],
        "signals": signals,
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


def analytics_summary() -> dict[str, Any]:
    """Return aggregated detection statistics for ``GET /analytics``.

    Three sections: detection patterns, appeal rate, and signal disagreement rate.
    All divisions guard against an empty database (total == 0 / prose_count == 0).
    """
    with _connect() as conn:
        total: int = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        verdict_rows = conn.execute(
            "SELECT attribution, COUNT(*), AVG(confidence) FROM decisions GROUP BY attribution"
        ).fetchall()
        type_rows = conn.execute(
            "SELECT content_type, COUNT(*) FROM decisions GROUP BY content_type"
        ).fetchall()
        total_appeals: int = conn.execute("SELECT COUNT(*) FROM appeals").fetchone()[0]
        open_appeals: int = conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE status = 'under_review'"
        ).fetchone()[0]
        mean_p_ai_row = conn.execute(
            "SELECT AVG(d.p_ai) FROM appeals a JOIN decisions d ON a.content_id = d.content_id"
        ).fetchone()
        discord_count: int = conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE ABS(llm_p_ai - stylo_p_ai) > 0.3"
        ).fetchone()[0]
        prose_count: int = conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE stylo_p_ai IS NOT NULL"
        ).fetchone()[0]

    mean_p_ai_appealed = mean_p_ai_row[0]

    verdict_dist: dict[str, Any] = {}
    for attribution, count, mean_conf in verdict_rows:
        verdict_dist[attribution] = {
            "count": count,
            "pct": round(count / total * 100, 1) if total else 0.0,
            "mean_confidence": round(mean_conf, 3) if mean_conf is not None else None,
        }

    return {
        "detection_patterns": {
            "total_decisions": total,
            "verdict_distribution": verdict_dist,
            "by_content_type": {
                (ct or "unspecified"): cnt for ct, cnt in type_rows
            },
        },
        "appeal_rate": {
            "total_appeals": total_appeals,
            "rate": round(total_appeals / total, 4) if total else 0,
            "open_appeals": open_appeals,
            "mean_p_ai_appealed": (
                round(mean_p_ai_appealed, 3) if mean_p_ai_appealed is not None else None
            ),
        },
        "signal_disagreement": {
            "discord_rate": round(discord_count / prose_count, 4) if prose_count else 0,
            "discord_threshold": 0.3,
            "note": (
                "Fraction of prose decisions where |llm_p_ai - stylo_p_ai| > 0.3. "
                "High discord means the two signals fundamentally disagree — those decisions "
                "resolve to uncertain rather than a definitive label."
            ),
        },
    }


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
                        **({"stylometric": {"p_ai": row["stylo_p_ai"], "features": _load_json(row["stylo_features"])}} if row["stylo_p_ai"] is not None else {}),
                        **({"readability": {"p_ai": row["read_p_ai"], "features": _load_json(row["read_features"])}} if row["read_p_ai"] is not None else {}),
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
