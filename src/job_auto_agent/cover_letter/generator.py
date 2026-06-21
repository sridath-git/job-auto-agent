from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from job_auto_agent.config import Settings
from job_auto_agent.resume.tailor import (
    AITailoringDisabledError,
    DEFAULT_MASTER_RESUME_PATH,
    MasterResumeMissingError,
    OpenAIAPIKeyMissingError,
    _call_openai_compatible_provider,
    compare_keywords,
    extract_job_keywords,
)


DEFAULT_COVER_LETTER_OUTPUT_DIR = Path("data/generated_cover_letters")


class CoverLetterGenerationError(Exception):
    """Base error for cover letter generation failures."""


class CoverLetterJobNotFoundError(CoverLetterGenerationError):
    """Raised when a job ID does not exist in SQLite."""


@dataclass(frozen=True)
class CoverLetterResult:
    job_id: int
    output_path: Path
    missing_keywords: list[str]
    warnings: list[str]


def generate_cover_letter_for_job(
    conn: sqlite3.Connection,
    job_id: int,
    master_resume_path: Path = DEFAULT_MASTER_RESUME_PATH,
    output_dir: Path = DEFAULT_COVER_LETTER_OUTPUT_DIR,
) -> CoverLetterResult:
    job = _load_cover_letter_inputs(conn, job_id, master_resume_path)
    resume_text = master_resume_path.read_text(encoding="utf-8")
    matched_keywords, missing_keywords = _keyword_overlap(job, resume_text)
    warnings = _build_warnings(job, matched_keywords, missing_keywords)
    output_path = _output_path(output_dir, job_id)
    letter = _render_rule_based_cover_letter(job, resume_text, matched_keywords, missing_keywords, warnings)
    output_path.write_text(letter, encoding="utf-8")
    return CoverLetterResult(
        job_id=job_id,
        output_path=output_path,
        missing_keywords=missing_keywords,
        warnings=warnings,
    )


def generate_ai_cover_letter_for_job(
    conn: sqlite3.Connection,
    job_id: int,
    settings: Settings,
    master_resume_path: Path = DEFAULT_MASTER_RESUME_PATH,
    output_dir: Path = DEFAULT_COVER_LETTER_OUTPUT_DIR,
) -> CoverLetterResult:
    if not settings.ai_tailoring_enabled:
        raise AITailoringDisabledError(
            "AI cover letter generation is disabled. Set AI_TAILORING_ENABLED=true to use --ai."
        )
    if not settings.openai_api_key:
        raise OpenAIAPIKeyMissingError(
            "OPENAI_API_KEY is missing. Set it in your environment before using --ai."
        )

    job = _load_cover_letter_inputs(conn, job_id, master_resume_path)
    resume_text = master_resume_path.read_text(encoding="utf-8")
    matched_keywords, missing_keywords = _keyword_overlap(job, resume_text)
    warnings = _build_warnings(job, matched_keywords, missing_keywords)
    prompt = _build_ai_cover_letter_prompt(job, resume_text, matched_keywords, missing_keywords)
    draft = _call_openai_compatible_provider(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        model=settings.openai_model,
        prompt=prompt,
    )
    output_path = _output_path(output_dir, job_id)
    output_path.write_text(_ensure_ai_cover_letter_sections(draft, missing_keywords), encoding="utf-8")
    return CoverLetterResult(
        job_id=job_id,
        output_path=output_path,
        missing_keywords=missing_keywords,
        warnings=warnings,
    )


def _load_cover_letter_inputs(
    conn: sqlite3.Connection,
    job_id: int,
    master_resume_path: Path,
) -> sqlite3.Row:
    job = conn.execute(
        "SELECT * FROM job_opportunities WHERE id = ?",
        (job_id,),
    ).fetchone()
    if job is None:
        raise CoverLetterJobNotFoundError(f"Job ID {job_id} was not found.")
    if not master_resume_path.exists():
        raise MasterResumeMissingError(
            f"Missing master resume at {master_resume_path}. "
            "Create it from data/profile/master_resume.example.md."
        )
    return job


def _output_path(output_dir: Path, job_id: int) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"job_{job_id}_cover_letter.md"


def _keyword_overlap(job: sqlite3.Row, resume_text: str) -> tuple[list[str], list[str]]:
    job_keywords = extract_job_keywords(job["title"], job["description"])
    return compare_keywords(job_keywords, resume_text)


def _build_warnings(
    job: sqlite3.Row,
    matched_keywords: list[str],
    missing_keywords: list[str],
) -> list[str]:
    warnings: list[str] = []
    if not job["company"]:
        warnings.append("Company name is missing; the cover letter uses a generic greeting.")
    if not matched_keywords:
        warnings.append("No direct keyword overlap was found between the resume and job description.")
    if missing_keywords:
        warnings.append("Some job keywords are absent from the master resume and were not claimed.")
    return warnings


def _render_rule_based_cover_letter(
    job: sqlite3.Row,
    resume_text: str,
    matched_keywords: list[str],
    missing_keywords: list[str],
    warnings: list[str],
) -> str:
    company = job["company"] or "your team"
    relevant_terms = _select_relevant_terms(matched_keywords)
    relevant_phrase = _human_join(relevant_terms) if relevant_terms else "relevant engineering experience"
    highlights = _select_resume_highlights(resume_text, matched_keywords)
    highlight_sentence = " ".join(highlights[:3]) if highlights else (
        "My background includes production engineering, automation, reliability, and security work "
        "described in my master resume."
    )
    missing_section = "\n".join(f"- {term}" for term in missing_keywords) or "- None"
    warning_section = "\n".join(f"- {warning}" for warning in warnings) or "- None"

    return f"""# Cover Letter Draft for Job {job["id"]}

Target role: {job["title"]}

Company: {company}

## Cover Letter

Dear Hiring Team,

I am writing to express my interest in the {job["title"]} role at {company}. My background aligns well with this opportunity through hands-on experience in {relevant_phrase}. I have focused on building, operating, and improving reliable production platforms while keeping security, automation, and operational clarity at the center of the work.

{highlight_sentence}

The role appears to call for a practical mix of engineering ownership, production judgment, and cross-functional execution. I can bring experience across DevOps, DevSecOps, SRE, Cloud, Kubernetes, CI/CD, Platform Engineering, PKI, Security, and Reliability where those areas are supported by my master resume. I value clear runbooks, measurable reliability improvements, secure automation, and collaboration with application, security, and infrastructure teams.

What interests me most about this opportunity is the chance to apply hands-on platform and reliability experience to problems that matter in production. I am comfortable working across incident response, infrastructure as code, deployment automation, observability, certificate and secrets practices, and secure delivery workflows. I would approach this role with a bias toward dependable systems, thoughtful documentation, and practical improvements that reduce operational risk.

I would be glad to discuss how my background can help {company} strengthen its platforms, improve delivery workflows, and support secure, reliable operations.

Thank you for your time and consideration. I look forward to the opportunity to speak with you.

Sincerely,

Sridath Jeelugula

## Missing Information Warnings

{warning_section}

## Missing Keywords To Review

These job keywords were not found in the master resume and were not added as claimed experience:

{missing_section}

## Safety Notes

- This draft is for manual review only.
- It does not auto-apply, send emails, or upload files.
- It only emphasizes information found in the local master resume.
"""


def _build_ai_cover_letter_prompt(
    job: sqlite3.Row,
    resume_text: str,
    matched_keywords: list[str],
    missing_keywords: list[str],
) -> str:
    matched_section = "\n".join(f"- {term}" for term in matched_keywords) or "- None"
    missing_section = "\n".join(f"- {term}" for term in missing_keywords) or "- None"
    return f"""Generate a professional 250-400 word cover letter in Markdown.

Hard safety rules:
- Do not fabricate experience.
- Do not invent employers.
- Do not invent dates.
- Do not invent certifications.
- Do not invent achievements.
- Only use information from master_resume.md.
- Do not auto-apply, send emails, or upload files.
- Do not claim missing job keywords as experience.

The output must include these Markdown sections:
1. # Cover Letter Draft for Job {job["id"]}
2. ## Cover Letter
3. ## Missing Keywords To Review
4. ## Truthfulness Notes

Target job:
- Title: {job["title"]}
- Company: {job["company"] or "Unknown"}
- Location: {job["location"] or "Unknown"}
- URL: {job["url"] or "Not available"}

Job description:
{job["description"]}

Keywords found in master resume:
{matched_section}

Missing job keywords that must not be added as claimed experience:
{missing_section}

Master resume:
{resume_text}
"""


def _ensure_ai_cover_letter_sections(draft: str, missing_keywords: list[str]) -> str:
    output = draft.strip()
    if "## Missing Keywords To Review" not in output:
        missing_section = "\n".join(f"- {term}" for term in missing_keywords) or "- None"
        output += f"\n\n## Missing Keywords To Review\n\n{missing_section}"
    if "## Truthfulness Notes" not in output:
        output += (
            "\n\n## Truthfulness Notes\n\n"
            "This draft must be manually reviewed. It should only use information present "
            "in the local master resume."
        )
    return output + "\n"


def _select_relevant_terms(matched_keywords: list[str]) -> list[str]:
    priority_terms = [
        "devops",
        "devsecops",
        "sre",
        "cloud engineering",
        "kubernetes",
        "ci cd",
        "platform engineering",
        "pki",
        "security",
        "reliability",
    ]
    selected = [term for term in priority_terms if term in matched_keywords]
    selected.extend(term for term in matched_keywords if term not in selected)
    return selected[:8]


def _select_resume_highlights(resume_text: str, matched_keywords: list[str]) -> list[str]:
    highlights: list[str] = []
    lowered_keywords = [term.lower() for term in matched_keywords]
    for raw_line in resume_text.splitlines():
        line = raw_line.strip().lstrip("- ").strip()
        if not line or line.startswith("#"):
            continue
        lowered_line = line.lower()
        if any(term in lowered_line for term in lowered_keywords):
            highlights.append(line.rstrip(".") + ".")
    return highlights[:4]


def _human_join(terms: list[str]) -> str:
    titled_terms = [term.upper() if term in {"sre", "pki"} else term for term in terms]
    if len(titled_terms) == 1:
        return titled_terms[0]
    return ", ".join(titled_terms[:-1]) + f", and {titled_terms[-1]}"
