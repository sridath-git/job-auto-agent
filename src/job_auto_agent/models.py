from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class EmailMessage:
    gmail_id: str
    thread_id: str
    sender: str
    subject: str
    snippet: str
    body_text: str
    received_at: datetime | None


@dataclass(frozen=True)
class JobOpportunity:
    source_message_id: str
    company: str | None
    title: str
    location: str | None
    url: str | None
    description: str
    received_at: datetime | None


@dataclass(frozen=True)
class MatchResult:
    job_id: int
    score: int
    matched_terms: list[str]
    notes: str
