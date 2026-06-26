"""SQLite audit store for Provenance Guard.

Every classification decision is written here so it can be surfaced via ``GET /log``
(this milestone) and linked to appeals later (Milestone 5). The schema is intentionally
created wider than Milestone 3 needs — ``confidence``/``label_*`` are placeholders now and
become real in M4/M5 — so we never have to migrate the table.

Pure standard library (``sqlite3``); no ORM.
"""

from __future__ import annotations

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
    "label_variant",
    "label_text",
    "status",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    content_id    TEXT PRIMARY KEY,
    creator_id    TEXT,
    timestamp     TEXT NOT NULL,
    content       TEXT NOT NULL,
    content_type  TEXT,
    attribution   TEXT NOT NULL,
    p_ai          REAL,
    confidence    REAL,
    llm_p_ai      REAL,
    llm_rationale TEXT,
    label_variant TEXT,
    label_text    TEXT,
    status        TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # rows behave like dicts
    return conn


def init_db() -> None:
    """Create the audit table if it does not exist. Safe to call on every startup."""
    with _connect() as conn:
        conn.executescript(_SCHEMA)


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


def recent_decisions(limit: int = 20) -> list[dict[str, Any]]:
    """Return the most recent decisions, newest first, shaped for the ``/log`` response.

    Per-signal scores are nested under ``signals`` so the log mirrors the ``/submit`` body.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM decisions ORDER BY timestamp DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()

    entries: list[dict[str, Any]] = []
    for row in rows:
        entries.append(
            {
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
                    }
                },
                "label": {
                    "variant": row["label_variant"],
                    "text": row["label_text"],
                },
                "status": row["status"],
            }
        )
    return entries
