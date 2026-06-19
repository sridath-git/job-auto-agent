from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from job_auto_agent.matching.engine import (
    BROAD_PROFILE_TERMS,
    SECURITY_TERMS,
    TARGET_JOB_TITLES,
    TECHNOLOGY_TERMS,
)


DEFAULT_MASTER_RESUME_PATH = Path("data/profile/master_resume.md")
DEFAULT_OUTPUT_DIR = Path("data/generated_resumes")


class ResumeTailoringError(Exception):
    """Base error for manual resume tailoring failures."""


class MasterResumeMissingError(ResumeTailoringError):
    """Raised when the real master resume file is missing."""


class JobNotFoundError(ResumeTailoringError):
    """Raised when a job ID does not exist in SQLite."""


class TailoredResumeExistsError(ResumeTailoringError):
    """Raised when a tailored resume already exists and overwrite is not confirmed."""


@dataclass(frozen=True)
class TailoredResumeResult:
    job_id: int
    output_path: Path
    matched_keywords: list[str]
    missing_keywords: list[str]


def tailor_resume_for_job(
    conn: sqlite3.Connection,
    job_id: int,
    master_resume_path: Path = DEFAULT_MASTER_RESUME_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    overwrite: bool = False,
) -> TailoredResumeResult:
    job = _get_job(conn, job_id)
    if job is None:
        raise JobNotFoundError(f"Job ID {job_id} was not found.")

    if not master_resume_path.exists():
        raise MasterResumeMissingError(
            f"Missing master resume at {master_resume_path}. "
            "Create it from data/profile/master_resume.example.md."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"job_{job_id}_tailored_resume.md"
    if output_path.exists() and not overwrite:
        raise TailoredResumeExistsError(
            f"{output_path} already exists. Re-run with --overwrite to replace it."
        )

    resume_text = master_resume_path.read_text(encoding="utf-8")
    job_keywords = extract_job_keywords(job["title"], job["description"])
    matched_keywords, missing_keywords = compare_keywords(job_keywords, resume_text)
    relevant_lines = _rank_resume_lines(resume_text, matched_keywords)
    draft = _render_tailored_resume(job, resume_text, relevant_lines, matched_keywords, missing_keywords)
    output_path.write_text(draft, encoding="utf-8")

    return TailoredResumeResult(
        job_id=job_id,
        output_path=output_path,
        matched_keywords=matched_keywords,
        missing_keywords=missing_keywords,
    )


def extract_job_keywords(title: str, description: str) -> list[str]:
    text = _normalize(f"{title}\n{description}")
    weighted_terms = {
        **TARGET_JOB_TITLES,
        **BROAD_PROFILE_TERMS,
        **TECHNOLOGY_TERMS,
        **SECURITY_TERMS,
    }
    matches = [
        term
        for term in weighted_terms
        if _contains_term(text, term)
    ]
    return sorted(set(matches), key=lambda term: (-weighted_terms[term], term))


def compare_keywords(job_keywords: list[str], resume_text: str) -> tuple[list[str], list[str]]:
    normalized_resume = _normalize(resume_text)
    matched = [term for term in job_keywords if _contains_term(normalized_resume, term)]
    missing = [term for term in job_keywords if term not in matched]
    return matched, missing


def _get_job(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT j.*, m.score, m.matched_terms, m.notes
        FROM job_opportunities j
        LEFT JOIN match_scores m ON m.job_id = j.id
        WHERE j.id = ?
        """,
        (job_id,),
    ).fetchone()


def _rank_resume_lines(resume_text: str, matched_keywords: list[str]) -> list[str]:
    scored_lines: list[tuple[int, int, str]] = []
    for index, line in enumerate(resume_text.splitlines()):
        clean_line = line.strip()
        if not clean_line or clean_line.startswith("#"):
            continue
        normalized_line = _normalize(clean_line)
        score = sum(1 for term in matched_keywords if _contains_term(normalized_line, term))
        if score:
            scored_lines.append((score, -index, clean_line))
    scored_lines.sort(reverse=True)
    return [line for _, _, line in scored_lines[:12]]


def _render_tailored_resume(
    job: sqlite3.Row,
    resume_text: str,
    relevant_lines: list[str],
    matched_keywords: list[str],
    missing_keywords: list[str],
) -> str:
    matched_section = "\n".join(f"- {term}" for term in matched_keywords) or "- None found"
    missing_section = "\n".join(f"- {term}" for term in missing_keywords) or "- None"
    relevant_section = "\n".join(f"- {line.lstrip('- ')}" for line in relevant_lines) or (
        "- No directly matching resume lines found. Review the master resume manually."
    )

    return f"""# Tailored Resume Draft for Job {job["id"]}

Target role: {job["title"]}

Company: {job["company"] or "Unknown"}

Location: {job["location"] or "Unknown"}

Job URL: {job["url"] or "Not available"}

## Safety Notice

This draft is generated for manual review only. It does not auto-apply, send emails, upload files, or claim skills that were not found in the master resume.

## Relevant Existing Keywords Found in Master Resume

{matched_section}

## Missing Job Keywords to Review Manually

These keywords appeared in the job description but were not found in the master resume. Add them only if they are truthful and supported by real experience.

{missing_section}

## Relevant Existing Resume Highlights

The lines below are copied or reordered from the master resume based on overlap with the job description.

{relevant_section}

## Master Resume Content for Manual Editing

The content below is copied from the master resume. Edit manually before submitting.

{resume_text.rstrip()}
"""


def _normalize(value: str) -> str:
    normalized = value.lower()
    normalized = normalized.replace("ci/cd", "ci cd")
    normalized = normalized.replace("x.509", "x 509")
    normalized = normalized.replace("cert-manager", "cert manager")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _contains_term(normalized_text: str, term: str) -> bool:
    normalized_term = _normalize(term)
    return re.search(rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])", normalized_text) is not None
