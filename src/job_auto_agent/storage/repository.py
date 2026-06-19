from __future__ import annotations

import json
import sqlite3
from urllib.parse import urlparse

from job_auto_agent.models import EmailMessage, JobOpportunity, MatchResult


JOB_STATUSES = ("New", "Interested", "Rejected", "Applied", "Follow-up")


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
            source_message_id, company, title, location, source, url, description, received_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job.source_message_id,
            job.company,
            job.title,
            job.location,
            job.source or _infer_source(job.url),
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


def update_job_status(conn: sqlite3.Connection, job_id: int, status: str) -> None:
    if status not in JOB_STATUSES:
        raise ValueError(f"Unsupported job status: {status}")
    conn.execute(
        "UPDATE job_opportunities SET status = ? WHERE id = ?",
        (status, job_id),
    )


def save_match(conn: sqlite3.Connection, result: MatchResult) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO match_scores (job_id, score, matched_terms, notes)
        VALUES (?, ?, ?, ?)
        """,
        (result.job_id, result.score, json.dumps(result.matched_terms), result.notes),
    )


def _infer_source(url: str | None) -> str:
    if not url:
        return "Gmail"
    host = urlparse(url).netloc.lower()
    source_map = {
        "linkedin": "LinkedIn",
        "indeed": "Indeed",
        "dice": "Dice",
        "glassdoor": "Glassdoor",
        "workday": "Workday",
        "greenhouse": "Greenhouse",
        "lever": "Lever",
    }
    for domain_hint, source in source_map.items():
        if domain_hint in host:
            return source
    return "Company Career Portal"
