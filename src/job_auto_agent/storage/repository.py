from __future__ import annotations

import json
import sqlite3

from job_auto_agent.models import EmailMessage, JobOpportunity, MatchResult


def save_email(conn: sqlite3.Connection, message: EmailMessage) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO email_messages (
            gmail_id, thread_id, sender, subject, snippet, body_text, received_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            message.gmail_id,
            message.thread_id,
            message.sender,
            message.subject,
            message.snippet,
            message.body_text,
            message.received_at.isoformat() if message.received_at else None,
        ),
    )


def save_job(conn: sqlite3.Connection, job: JobOpportunity) -> int:
    conn.execute(
        """
        INSERT OR IGNORE INTO job_opportunities (
            source_message_id, company, title, location, url, description, received_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job.source_message_id,
            job.company,
            job.title,
            job.location,
            job.url,
            job.description,
            job.received_at.isoformat() if job.received_at else None,
        ),
    )
    row = conn.execute(
        "SELECT id FROM job_opportunities WHERE source_message_id = ?",
        (job.source_message_id,),
    ).fetchone()
    return int(row["id"])


def list_jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT j.*, m.score, m.matched_terms, m.notes
        FROM job_opportunities j
        LEFT JOIN match_scores m ON m.job_id = j.id
        ORDER BY COALESCE(m.score, 0) DESC, j.received_at DESC
        """
    ).fetchall()


def save_match(conn: sqlite3.Connection, result: MatchResult) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO match_scores (job_id, score, matched_terms, notes)
        VALUES (?, ?, ?, ?)
        """,
        (result.job_id, result.score, json.dumps(result.matched_terms), result.notes),
    )
