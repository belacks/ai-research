"""
CRN — SQLite Intelligence Database
=====================================
Structured storage for crawl runs and briefing items.
Uses Python's built-in ``sqlite3`` — no ORM, no external dependencies.
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = _PROJECT_ROOT / "shared_workspace" / "crn_intelligence.db"

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS crawl_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    trigger     TEXT NOT NULL,
    model_used  TEXT NOT NULL,
    total_items INTEGER DEFAULT 0,
    sent_items  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS briefing_items (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                INTEGER NOT NULL REFERENCES crawl_runs(id),
    crawled_at            TEXT NOT NULL,
    url                   TEXT NOT NULL,
    category              TEXT NOT NULL,
    scope                 TEXT NOT NULL,
    title                 TEXT,
    summary               TEXT,
    key_insights          TEXT,
    relevance_tags        TEXT,
    relevance_score       INTEGER,
    global_local          TEXT,
    action_for_researcher TEXT,
    raw_char_count        INTEGER,
    was_notified          INTEGER NOT NULL DEFAULT 0
);
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create tables if they don't already exist. Idempotent."""
    try:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(_DB_PATH) as conn:
            conn.executescript(_SCHEMA_SQL)
        logger.info("Database ready at %s", _DB_PATH)
    except Exception as exc:
        logger.error("Failed to initialise database: %s", exc)


def insert_run(trigger: str, model_used: str) -> int:
    """Insert a new crawl_run row and return its id. Returns -1 on failure."""
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            cursor = conn.execute(
                "INSERT INTO crawl_runs (started_at, trigger, model_used) VALUES (?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), trigger, model_used),
            )
            run_id = cursor.lastrowid
            logger.info("Crawl run #%d created (trigger=%s, model=%s).", run_id, trigger, model_used)
            return run_id
    except Exception as exc:
        logger.error("Failed to insert crawl run: %s", exc)
        return -1


def insert_item(run_id: int, item: dict) -> None:
    """Insert one briefing item row. All DB errors are logged, never raised."""
    try:
        parsed = item.get("parsed", {})
        with sqlite3.connect(_DB_PATH) as conn:
            conn.execute(
                """INSERT INTO briefing_items
                   (run_id, crawled_at, url, category, scope,
                    title, summary, key_insights, relevance_tags,
                    relevance_score, global_local, action_for_researcher,
                    raw_char_count, was_notified)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    datetime.now(timezone.utc).isoformat(),
                    item.get("url", ""),
                    item.get("category", ""),
                    item.get("scope", ""),
                    parsed.get("title", ""),
                    parsed.get("summary", item.get("summary", "")),
                    json.dumps(parsed.get("key_insights", []), ensure_ascii=False),
                    json.dumps(parsed.get("relevance_tags", []), ensure_ascii=False),
                    parsed.get("relevance_score"),
                    parsed.get("global_local", ""),
                    parsed.get("action_for_researcher", ""),
                    item.get("raw_char_count", 0),
                    1 if item.get("was_notified", False) else 0,
                ),
            )
    except Exception as exc:
        logger.error("Failed to insert briefing item for %s: %s", item.get("url", "?"), exc)


def update_run_totals(run_id: int, total: int, sent: int) -> None:
    """Update total_items and sent_items on a completed crawl run."""
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.execute(
                "UPDATE crawl_runs SET total_items = ?, sent_items = ? WHERE id = ?",
                (total, sent, run_id),
            )
            logger.info("Crawl run #%d finalised — %d total, %d sent.", run_id, total, sent)
    except Exception as exc:
        logger.error("Failed to update run totals for run #%d: %s", run_id, exc)


# ---------------------------------------------------------------------------
# Auto-initialise on import
# ---------------------------------------------------------------------------
init_db()
