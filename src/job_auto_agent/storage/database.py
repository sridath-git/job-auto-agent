from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS email_messages (
    gmail_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    sender TEXT NOT NULL,
    subject TEXT NOT NULL,
    snippet TEXT NOT NULL,
    body_text TEXT NOT NULL,
    received_at TEXT
);

CREATE TABLE IF NOT EXISTS job_opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_message_id TEXT NOT NULL UNIQUE,
    company TEXT,
    title TEXT NOT NULL,
    location TEXT,
    source TEXT NOT NULL DEFAULT 'Gmail',
    status TEXT NOT NULL DEFAULT 'New',
    url TEXT,
    description TEXT NOT NULL,
    received_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_message_id) REFERENCES email_messages(gmail_id)
);

CREATE TABLE IF NOT EXISTS match_scores (
    job_id INTEGER PRIMARY KEY,
    score INTEGER NOT NULL,
    matched_terms TEXT NOT NULL,
    notes TEXT NOT NULL,
    scored_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES job_opportunities(id)
);
"""

JOB_OPPORTUNITY_COLUMNS = {
    "source": "ALTER TABLE job_opportunities ADD COLUMN source TEXT NOT NULL DEFAULT 'Gmail'",
    "status": "ALTER TABLE job_opportunities ADD COLUMN status TEXT NOT NULL DEFAULT 'New'",
}


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        _ensure_job_opportunity_columns(conn)


def _ensure_job_opportunity_columns(conn: sqlite3.Connection) -> None:
    existing_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(job_opportunities)").fetchall()
    }
    for column_name, statement in JOB_OPPORTUNITY_COLUMNS.items():
        if column_name not in existing_columns:
            conn.execute(statement)
