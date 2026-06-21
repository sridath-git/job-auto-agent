from __future__ import annotations

import re
import sqlite3
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from job_auto_agent.config import Settings, resolve_openai_api_key
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
    output_path: Path | None
    analysis_path: Path
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

    _ = overwrite
    output_dir.mkdir(parents=True, exist_ok=True)
    analysis_path = _analysis_path(output_dir, job_id)

    resume_text = master_resume_path.read_text(encoding="utf-8")
    job_keywords = extract_job_keywords(job["title"], job["description"])
    matched_keywords, missing_keywords = compare_keywords(job_keywords, resume_text)
    _write_resume_analysis(analysis_path, job, matched_keywords, missing_keywords)

    return TailoredResumeResult(
        job_id=job_id,
        output_path=None,
        analysis_path=analysis_path,
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
    api_key = resolve_openai_api_key(settings)
    if not api_key:
        raise OpenAIAPIKeyMissingError(
            "OPENAI_API_KEY is missing. Set it in your environment before using --ai."
        )

    job = _load_tailoring_inputs(conn, job_id, master_resume_path, output_dir, overwrite)
    resume_text = master_resume_path.read_text(encoding="utf-8")
    job_keywords = extract_job_keywords(job["title"], job["description"])
    matched_keywords, missing_keywords = compare_keywords(job_keywords, resume_text)
    prompt = build_ai_tailoring_prompt(job, resume_text, matched_keywords, missing_keywords)
    draft = _call_openai_compatible_provider(
        api_key=api_key,
        base_url=settings.openai_base_url,
        model=settings.openai_model,
        prompt=prompt,
        timeout_seconds=settings.ai_provider_timeout_seconds,
    )

    output_path = output_dir / f"job_{job_id}_tailored_resume.md"
    analysis_path = _analysis_path(output_dir, job_id)
    output_path.write_text(_prepare_ai_tailored_resume(draft), encoding="utf-8")
    _write_resume_analysis(analysis_path, job, matched_keywords, missing_keywords)
    return TailoredResumeResult(
        job_id=job_id,
        output_path=output_path,
        analysis_path=analysis_path,
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
    matched_section = "\n".join(f"- {term}" for term in matched_keywords) or "- None"
    missing_section = "\n".join(f"- {term}" for term in missing_keywords) or "- None"
    return f"""You are helping create a manual tailored resume draft.

Hard safety rules:
- Do not fabricate anything.
- Do not invent companies, dates, roles, tools, metrics, certifications, degrees, or skills.
- Do not add skills that are not present in the master resume.
- Do not include phone numbers, email addresses, LinkedIn URLs, or home locations anywhere except the top contact header if they already appear in the master resume.
- Do not include safety notes, missing keyword sections, keyword analysis, debug notes, or manual editing sections in the resume output.
- Preserve truthful experience only.
- Reorder, emphasize, and rewrite existing resume content for relevance.
- Do not auto-apply, send emails, or upload anything.

The output must be recruiter-facing Markdown only and must use this structure:
1. # Name and contact header
2. ## Professional Summary
3. ## Core Skills
4. ## Professional Experience
5. ## Education
6. ## Languages

Do not include the missing keyword list in the resume. It is provided only so you know what not to claim.

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


def _analysis_path(output_dir: Path, job_id: int) -> Path:
    return output_dir / f"job_{job_id}_analysis.md"


def _call_openai_compatible_provider(
    api_key: str,
    base_url: str,
    model: str,
    prompt: str,
    timeout_seconds: int = 60,
) -> str:
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
    chat_completions_url = f"{base_url.rstrip('/')}/chat/completions"
    request = urllib.request.Request(
        chat_completions_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
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


def _prepare_ai_tailored_resume(draft: str) -> str:
    cleaned = _remove_internal_resume_sections(draft)
    return _remove_body_contact_details(cleaned).strip() + "\n"


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


def _render_recruiter_ready_resume(
    job: sqlite3.Row,
    resume_text: str,
    matched_keywords: list[str],
) -> str:
    header = _extract_resume_header(resume_text)
    sections = _extract_resume_sections(resume_text)
    summary_lines = _select_summary_lines(sections, matched_keywords)
    skill_lines = _select_skill_lines(sections, matched_keywords)
    experience_lines = _select_experience_lines(sections, matched_keywords)
    education_lines = _clean_body_lines(sections.get("education", [])) or ["Not specified in master resume."]
    language_lines = _clean_body_lines(sections.get("languages", [])) or ["Not specified in master resume."]

    output = "\n\n".join(
        [
            header,
            "## Professional Summary\n\n" + _render_bullets(summary_lines),
            "## Core Skills\n\n" + _render_bullets(skill_lines),
            "## Professional Experience\n\n" + _render_bullets(experience_lines),
            "## Education\n\n" + _render_bullets(education_lines),
            "## Languages\n\n" + _render_bullets(language_lines),
        ]
    )
    return _remove_body_contact_details(output).strip() + "\n"


def _write_resume_analysis(
    analysis_path: Path,
    job: sqlite3.Row,
    matched_keywords: list[str],
    missing_keywords: list[str],
) -> None:
    matched_section = "\n".join(f"- {term}" for term in matched_keywords) or "- None found"
    missing_section = "\n".join(f"- {term}" for term in missing_keywords) or "- None"
    analysis_path.write_text(
        f"""# Resume Tailoring Analysis for Job {job["id"]}

Target role: {job["title"]}

Company: {job["company"] or "Unknown"}

Location: {job["location"] or "Unknown"}

Job URL: {job["url"] or "Not available"}

## Relevant Existing Keywords

{matched_section}

## Missing Job Keywords

{missing_section}
""",
        encoding="utf-8",
    )


def _extract_resume_header(resume_text: str) -> str:
    lines = [line.strip() for line in resume_text.splitlines() if line.strip()]
    if not lines:
        return "# Candidate"

    first_line = lines[0].strip()
    if first_line.startswith("#"):
        candidate_name = first_line.lstrip("#").strip()
    elif _looks_like_name(first_line):
        candidate_name = first_line
    else:
        candidate_name = "Candidate"
    name = candidate_name if not _is_contact_line(candidate_name) else "Candidate"
    contact_lines: list[str] = []
    for line in lines[:10]:
        clean_line = line.lstrip("- ").strip()
        if _is_contact_line(clean_line) and clean_line not in contact_lines:
            contact_lines.append(clean_line)

    if contact_lines:
        return f"# {name}\n\n" + " | ".join(contact_lines)
    return f"# {name}"


def _extract_resume_sections(resume_text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {
        "summary": [],
        "skills": [],
        "experience": [],
        "education": [],
        "languages": [],
        "other": [],
    }
    current = "other"
    for raw_line in resume_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            current = _classify_heading(line)
            continue
        sections.setdefault(current, []).append(line)
    return sections


def _classify_heading(line: str) -> str:
    heading = _normalize(line)
    if any(term in heading for term in ("summary", "profile", "objective")):
        return "summary"
    if any(term in heading for term in ("skill", "technology", "technical")):
        return "skills"
    if any(term in heading for term in ("experience", "employment", "work history", "projects")):
        return "experience"
    if "education" in heading:
        return "education"
    if "language" in heading:
        return "languages"
    return "other"


def _select_summary_lines(sections: dict[str, list[str]], matched_keywords: list[str]) -> list[str]:
    summary = _clean_body_lines(sections.get("summary", []))
    if summary:
        return summary[:3]
    relevant = _rank_clean_lines(sections.get("experience", []) + sections.get("other", []), matched_keywords, limit=3)
    return relevant or ["Professional summary not specified in master resume."]


def _select_skill_lines(sections: dict[str, list[str]], matched_keywords: list[str]) -> list[str]:
    skill_lines = _clean_body_lines(sections.get("skills", []))
    matched_skill_lines = _rank_clean_lines(skill_lines, matched_keywords, limit=8)
    resume_body = " ".join(line for lines in sections.values() for line in lines)
    matched_terms = [term for term in matched_keywords if _contains_term(_normalize(resume_body), term)]
    formatted_terms = [term.upper() if term in {"aws", "pki", "sre"} else term.title() for term in matched_terms[:12]]
    combined = _dedupe_lines(formatted_terms + matched_skill_lines + skill_lines[:6])
    return combined or ["Skills not specified in master resume."]


def _select_experience_lines(sections: dict[str, list[str]], matched_keywords: list[str]) -> list[str]:
    experience = sections.get("experience", []) + sections.get("other", [])
    ranked = _rank_clean_lines(experience, matched_keywords, limit=14)
    clean_experience = _clean_body_lines(experience)
    company_lines = [
        line
        for line in clean_experience
        if any(company in line.lower() for company in ("intact", "morgan stanley", "cognizant"))
    ]
    combined = _dedupe_lines(company_lines + ranked + clean_experience[:10])
    return combined[:18] or ["Professional experience not specified in master resume."]


def _rank_clean_lines(lines: list[str], matched_keywords: list[str], limit: int) -> list[str]:
    scored_lines: list[tuple[int, int, str]] = []
    for index, line in enumerate(_clean_body_lines(lines)):
        normalized_line = _normalize(line)
        score = sum(1 for term in matched_keywords if _contains_term(normalized_line, term))
        if score:
            scored_lines.append((score, -index, line))
    scored_lines.sort(reverse=True)
    return [line for _, _, line in scored_lines[:limit]]


def _clean_body_lines(lines: list[str]) -> list[str]:
    return _dedupe_lines(
        clean_line
        for line in lines
        if (clean_line := _clean_body_line(line))
    )


def _clean_body_line(line: str) -> str:
    clean_line = line.strip()
    if not clean_line or _is_contact_line(clean_line):
        return ""
    clean_line = clean_line.lstrip("- ").strip()
    clean_line = re.sub(r"https?://\S+", "", clean_line)
    clean_line = re.sub(r"\b[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}\b", "", clean_line)
    clean_line = re.sub(r"\+?\d[\d\s().-]{7,}\d", "", clean_line)
    clean_line = re.sub(r"\s+", " ", clean_line).strip(" -|")
    return clean_line


def _dedupe_lines(lines) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        clean_line = str(line).strip()
        key = _normalize(clean_line)
        if clean_line and key and key not in seen:
            seen.add(key)
            output.append(clean_line)
    return output


def _render_bullets(lines: list[str]) -> str:
    return "\n".join(f"- {line}" for line in lines)


def _remove_internal_resume_sections(draft: str) -> str:
    blocked_headings = (
        "safety notice",
        "truthfulness notes",
        "missing keywords",
        "missing job keywords",
        "relevant existing keywords",
        "relevant existing resume highlights",
        "master resume content",
        "missing information warnings",
        "keyword analysis",
        "debug",
        "analysis",
        "tailored resume draft",
    )
    output_lines: list[str] = []
    skipping = False
    for line in draft.splitlines():
        normalized = _normalize(line.lstrip("#").strip())
        if line.lstrip().startswith("#") and any(blocked in normalized for blocked in blocked_headings):
            skipping = "tailored resume draft" not in normalized
            if "tailored resume draft" in normalized:
                skipping = False
            continue
        if skipping and line.lstrip().startswith("#"):
            skipping = False
        if not skipping:
            output_lines.append(line)
    return "\n".join(output_lines)


def _remove_body_contact_details(draft: str) -> str:
    lines = draft.splitlines()
    body_started = False
    output_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            body_started = True
        if body_started and _is_contact_line(stripped):
            continue
        output_lines.append(line)
    return "\n".join(output_lines)


def _is_contact_line(line: str) -> bool:
    normalized = line.lower()
    return (
        bool(re.search(r"\b[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}\b", line))
        or bool(re.search(r"\+?\d[\d\s().-]{7,}\d", line))
        or "linkedin.com" in normalized
        or "github.com" in normalized
        or any(location in normalized for location in ("montreal", "toronto", "canada", "quebec"))
    )


def _looks_like_name(line: str) -> bool:
    words = line.strip().split()
    if not 2 <= len(words) <= 5:
        return False
    if any(char in line for char in (":", "|", "@", "/", "\\")):
        return False
    normalized = _normalize(line)
    technical_terms = (
        "built",
        "engineer",
        "developer",
        "terraform",
        "kubernetes",
        "platform",
        "devops",
        "security",
        "resume",
    )
    return not any(term in normalized for term in technical_terms)


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
