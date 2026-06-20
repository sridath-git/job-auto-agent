from __future__ import annotations

import re
import sqlite3
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from job_auto_agent.config import Settings
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


class AITailoringDisabledError(ResumeTailoringError):
    """Raised when AI tailoring is requested but disabled."""


class OpenAIAPIKeyMissingError(ResumeTailoringError):
    """Raised when AI tailoring is requested without an API key."""


class AITailoringProviderError(ResumeTailoringError):
    """Raised when the configured AI provider returns an error."""


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


def tailor_resume_with_ai_for_job(
    conn: sqlite3.Connection,
    job_id: int,
    settings: Settings,
    master_resume_path: Path = DEFAULT_MASTER_RESUME_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    overwrite: bool = False,
) -> TailoredResumeResult:
    if not settings.ai_tailoring_enabled:
        raise AITailoringDisabledError(
            "AI tailoring is disabled. Set AI_TAILORING_ENABLED=true to use --ai."
        )
    if not settings.openai_api_key:
        raise OpenAIAPIKeyMissingError(
            "OPENAI_API_KEY is missing. Set it in your environment before using --ai."
        )

    job = _load_tailoring_inputs(conn, job_id, master_resume_path, output_dir, overwrite)
    resume_text = master_resume_path.read_text(encoding="utf-8")
    job_keywords = extract_job_keywords(job["title"], job["description"])
    matched_keywords, missing_keywords = compare_keywords(job_keywords, resume_text)
    prompt = build_ai_tailoring_prompt(job, resume_text, matched_keywords, missing_keywords)
    draft = _call_openai_compatible_provider(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        prompt=prompt,
    )

    output_path = output_dir / f"job_{job_id}_tailored_resume.md"
    output_path.write_text(_ensure_required_ai_sections(draft, missing_keywords), encoding="utf-8")
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


def build_ai_tailoring_prompt(
    job: sqlite3.Row,
    resume_text: str,
    matched_keywords: list[str],
    missing_keywords: list[str],
) -> str:
    missing_section = "\n".join(f"- {term}" for term in missing_keywords) or "- None"
    matched_section = "\n".join(f"- {term}" for term in matched_keywords) or "- None"
    return f"""You are helping create a manual tailored resume draft.

Hard safety rules:
- Do not fabricate anything.
- Do not invent companies, dates, roles, tools, metrics, certifications, degrees, or skills.
- Do not add skills that are not present in the master resume.
- Preserve truthful experience only.
- Reorder, emphasize, and rewrite existing resume content for relevance.
- Suggest missing job keywords separately instead of adding them into the resume.
- Do not auto-apply, send emails, or upload anything.

The output must be Markdown and must include these sections:
1. # AI-Tailored Resume Draft for Job {job["id"]}
2. ## Truthfulness Notes
3. ## Missing Keywords To Review
4. ## Tailored Resume Draft

In Truthfulness Notes, explain that the draft only uses information present in the master resume and must be manually reviewed.
In Missing Keywords To Review, list missing job keywords exactly from the provided missing keyword list.

Target job:
- Title: {job["title"]}
- Company: {job["company"] or "Unknown"}
- Location: {job["location"] or "Unknown"}
- URL: {job["url"] or "Not available"}

Job description:
{job["description"]}

Keywords present in the master resume:
{matched_section}

Missing job keywords that must not be added as claimed experience:
{missing_section}

Master resume:
{resume_text}
"""


def _load_tailoring_inputs(
    conn: sqlite3.Connection,
    job_id: int,
    master_resume_path: Path,
    output_dir: Path,
    overwrite: bool,
) -> sqlite3.Row:
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
    return job


def _call_openai_compatible_provider(api_key: str, model: str, prompt: str) -> str:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You produce truthful resume drafts using only the provided master resume.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")
        raise AITailoringProviderError(f"AI provider returned HTTP {exc.code}: {message}") from exc
    except urllib.error.URLError as exc:
        raise AITailoringProviderError(f"Unable to reach AI provider: {exc.reason}") from exc

    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AITailoringProviderError("AI provider response did not include message content.") from exc


def _ensure_required_ai_sections(draft: str, missing_keywords: list[str]) -> str:
    output = draft.strip()
    if "## Missing Keywords To Review" not in output:
        missing_section = "\n".join(f"- {term}" for term in missing_keywords) or "- None"
        output += f"\n\n## Missing Keywords To Review\n\n{missing_section}"
    if "## Truthfulness Notes" not in output:
        output += (
            "\n\n## Truthfulness Notes\n\n"
            "This draft must be manually reviewed. It should only use experience, skills, "
            "companies, dates, roles, tools, metrics, and certifications present in the master resume."
        )
    return output + "\n"


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
