from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from job_auto_agent.config import Settings, resolve_openai_api_key
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


class CoverLetterValidationError(CoverLetterGenerationError):
    """Raised when generated cover letter content fails safety validation."""


@dataclass(frozen=True)
class CoverLetterResult:
    job_id: int
    output_path: Path
    analysis_path: Path
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
    analysis_path = _analysis_path(output_dir, job_id)
    _write_analysis(analysis_path, job, matched_keywords, missing_keywords, warnings)
    return CoverLetterResult(
        job_id=job_id,
        output_path=output_path,
        analysis_path=analysis_path,
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
    api_key = resolve_openai_api_key(settings)
    if not api_key:
        raise OpenAIAPIKeyMissingError(
            "OPENAI_API_KEY is missing. Set it in your environment before using --ai."
        )

    job = _load_cover_letter_inputs(conn, job_id, master_resume_path)
    resume_text = master_resume_path.read_text(encoding="utf-8")
    matched_keywords, missing_keywords = _keyword_overlap(job, resume_text)
    warnings = _build_warnings(job, matched_keywords, missing_keywords)
    candidate_name = _extract_candidate_name(resume_text)
    recruiter_sender = job["sender"] if "sender" in job.keys() else None
    recruiter_name = _extract_recruiter_name(recruiter_sender)
    prompt = _build_ai_cover_letter_prompt(
        job,
        resume_text,
        matched_keywords,
        missing_keywords,
        candidate_name,
        recruiter_name,
    )
    draft = _call_openai_compatible_provider(
        api_key=api_key,
        base_url=settings.openai_base_url,
        model=settings.openai_model,
        prompt=prompt,
        timeout_seconds=settings.ai_provider_timeout_seconds,
    )
    _validate_raw_cover_letter_signature(draft, recruiter_name, recruiter_sender)
    output_path = _output_path(output_dir, job_id)
    letter = _prepare_ai_cover_letter_output(
        draft,
        resume_text,
        recruiter_name,
        job["title"],
        job["company"] or "your team",
    )
    _validate_cover_letter_identity(letter, candidate_name, recruiter_name, recruiter_sender)
    output_path.write_text(letter, encoding="utf-8")
    analysis_path = _analysis_path(output_dir, job_id)
    _write_analysis(analysis_path, job, matched_keywords, missing_keywords, warnings)
    return CoverLetterResult(
        job_id=job_id,
        output_path=output_path,
        analysis_path=analysis_path,
        missing_keywords=missing_keywords,
        warnings=warnings,
    )


def _load_cover_letter_inputs(
    conn: sqlite3.Connection,
    job_id: int,
    master_resume_path: Path,
) -> sqlite3.Row:
    job = conn.execute(
        """
        SELECT j.*, e.sender
        FROM job_opportunities j
        LEFT JOIN email_messages e ON e.gmail_id = j.source_message_id
        WHERE j.id = ?
        """,
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


def _analysis_path(output_dir: Path, job_id: int) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"job_{job_id}_analysis.md"


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
    highlights = _select_resume_highlights(resume_text, matched_keywords, limit=3)
    company_context = _company_context(resume_text)
    highlight_sentence = _highlight_sentence(highlights)
    role_focus = _role_focus_sentence(matched_keywords)

    letter = f"""
Dear Hiring Team,

I am writing to express my interest in the {job["title"]} role at {company}. The opportunity stands out because it aligns with the platform, reliability, cloud, and security engineering work I have focused on throughout my career. I enjoy roles where dependable systems, secure automation, and practical operational improvements directly support engineering teams and production outcomes.

My experience includes {relevant_phrase}, with hands-on work across DevOps, DevSecOps, SRE, Cloud, Kubernetes, CI/CD, Platform Engineering, PKI, Security, and Reliability where those areas are reflected in my resume. {company_context} {highlight_sentence}

What I would bring to this role is a balance of production judgment and delivery focus. I am comfortable working across infrastructure as code, deployment automation, observability, incident response, secrets and certificate practices, and secure platform operations. {role_focus}

I am especially interested in contributing to {company} because the role appears to require someone who can connect reliability, security, and platform execution rather than treating them as separate concerns. I would bring a practical mindset, strong ownership, and the ability to collaborate across application, infrastructure, and security teams.

Thank you for your time and consideration. I would welcome the opportunity to discuss how my background can help {company} strengthen its platforms, improve delivery workflows, and support secure, reliable operations.

Sincerely,

Sridath Jeelugula
"""
    return _sanitize_cover_letter_text(letter)


def _build_ai_cover_letter_prompt(
    job: sqlite3.Row,
    resume_text: str,
    matched_keywords: list[str],
    missing_keywords: list[str],
    candidate_name: str = "Sridath Jeelugula",
    recruiter_name: str | None = None,
) -> str:
    matched_section = "\n".join(f"- {term}" for term in matched_keywords) or "- None"
    missing_section = "\n".join(f"- {term}" for term in missing_keywords) or "- None"
    return f"""Generate a professional 250-400 word recruiter-ready cover letter in Markdown.

Hard safety rules:
- Do not fabricate experience.
- Do not invent employers.
- Do not invent dates.
- Do not invent certifications.
- Do not invent achievements.
- Only use information from master_resume.md.
- Do not auto-apply, send emails, or upload files.
- Do not claim missing job keywords as experience.
- Do not include phone numbers, email addresses, LinkedIn URLs, home location, safety notes, debug sections, keyword analysis sections, Missing Keywords sections, or Missing Information Warnings sections.
- Candidate identity must come only from master_resume.md.
- Applicant/candidate name is: {candidate_name}.
- Recruiter identity must never be used as applicant name, applicant signature, applicant email, or applicant contact info.
- If recruiter name is known, greet them as: Dear {recruiter_name or "Hiring Manager"},
- Use neutral source wording: I am writing to express my interest in the {job["title"]} opportunity at {job["company"] or "your team"}.
- Do not say the role was advertised on LinkedIn unless the source is explicitly configured as LinkedIn.
- Do not use generic phrases like "align perfectly" or "honed a strong set of skills".
- Signature must always be exactly:
  Sincerely,

  {candidate_name}

The output must contain only recruiter-facing cover letter content.

Structure the letter naturally with:
- Introduction
- Why I am interested
- Relevant experience
- Why I fit the role
- Closing

Target job:
- Title: {job["title"]}
- Company: {job["company"] or "Unknown"}
- Location: {job["location"] or "Unknown"}
- URL: {job["url"] or "Not available"}
- Recruiter name: {recruiter_name or "Unknown"}

Job description:
{job["description"]}

Keywords found in master resume:
{matched_section}

Missing job keywords that must not be added as claimed experience:
{missing_section}

Master resume:
{resume_text}
"""


def _prepare_ai_cover_letter_output(
    draft: str,
    resume_text: str | None = None,
    recruiter_name: str | None = None,
    role: str | None = None,
    company: str | None = None,
) -> str:
    candidate_name = _extract_candidate_name(resume_text or "")
    sanitized = _sanitize_cover_letter_text(_remove_internal_sections(_strip_code_fences(draft)))
    sanitized = _ensure_cover_letter_greeting(sanitized, recruiter_name)
    if role and company:
        sanitized = _ensure_neutral_opening(sanitized, role, company)
    sanitized = _ensure_candidate_signature(sanitized, candidate_name)
    return sanitized


def _extract_candidate_name(resume_text: str) -> str:
    for raw_line in resume_text.splitlines():
        line = raw_line.strip().lstrip("#").strip()
        if line:
            return line
    return "Sridath Jeelugula"


def _extract_recruiter_name(sender: str | None) -> str | None:
    if not sender:
        return None
    name = sender.split("<", maxsplit=1)[0].strip().strip('"')
    name = re.sub(r"\s+via\s+LinkedIn\b", "", name, flags=re.IGNORECASE).strip()
    if not name or "@" in name:
        return None
    blocked_words = {"recruiter", "jobs", "careers", "talent", "hiring", "team"}
    if name.lower() in blocked_words:
        return None
    return name


def _ensure_cover_letter_greeting(text: str, recruiter_name: str | None) -> str:
    greeting = f"Dear {recruiter_name}," if recruiter_name else "Dear Hiring Manager,"
    lines = [line for line in text.splitlines()]
    first_content_index = next((index for index, line in enumerate(lines) if line.strip()), None)
    if first_content_index is None:
        return greeting + "\n\n"
    if re.match(r"^\s*Dear\s+.+,\s*$", lines[first_content_index], flags=re.IGNORECASE):
        lines[first_content_index] = greeting
    else:
        lines.insert(first_content_index, greeting)
        lines.insert(first_content_index + 1, "")
    return "\n".join(lines).strip() + "\n"


def _ensure_candidate_signature(text: str, candidate_name: str) -> str:
    body = _remove_closing_blocks(text)
    return f"{body.rstrip()}\n\nSincerely,\n\n{candidate_name}\n"


def _ensure_neutral_opening(text: str, role: str, company: str) -> str:
    lines = text.splitlines()
    first_content_index = next((index for index, line in enumerate(lines) if line.strip()), None)
    if first_content_index is None:
        return text
    paragraph_index = next(
        (index for index in range(first_content_index + 1, len(lines)) if lines[index].strip()),
        None,
    )
    neutral_sentence = f"I am writing to express my interest in the {role} opportunity at {company}."
    if paragraph_index is None:
        return "\n".join(lines + ["", neutral_sentence]).strip() + "\n"
    lines[paragraph_index] = neutral_sentence
    return "\n".join(lines).strip() + "\n"


def _validate_cover_letter_identity(
    letter: str,
    candidate_name: str,
    recruiter_name: str | None,
    recruiter_sender: str | None,
) -> None:
    signature = _signature_block(letter)
    if candidate_name not in signature:
        raise CoverLetterValidationError("AI cover letter failed validation: candidate signature is missing.")
    forbidden_values = [
        value
        for value in (
            recruiter_name,
            _extract_email(recruiter_sender or ""),
            recruiter_sender,
        )
        if value
    ]
    forbidden_in_signature = [value for value in forbidden_values if value in signature and value != candidate_name]
    if forbidden_in_signature:
        raise CoverLetterValidationError(
            "AI cover letter failed validation: recruiter identity appears in signature."
        )
    closing_count = len(re.findall(r"(?im)^\s*(Sincerely|Best regards|Regards|Thank you),?\s*$", letter))
    if closing_count != 1:
        raise CoverLetterValidationError("AI cover letter failed validation: duplicate signature closings.")


def _validate_raw_cover_letter_signature(
    draft: str,
    recruiter_name: str | None,
    recruiter_sender: str | None,
) -> None:
    signature = _signature_block(draft)
    forbidden_values = [
        value
        for value in (
            recruiter_name,
            _extract_email(recruiter_sender or ""),
            recruiter_sender,
        )
        if value
    ]
    if any(value in signature for value in forbidden_values):
        raise CoverLetterValidationError(
            "AI cover letter failed validation: recruiter identity appears in signature."
        )


def _signature_block(letter: str) -> str:
    match = re.search(r"(?is)\bSincerely,?\s*(.*)$", letter)
    return match.group(0) if match else ""


def _extract_email(text: str) -> str:
    match = re.search(r"[\w.\-+]+@[\w.\-]+\.\w+", text)
    return match.group(0) if match else ""


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


def _select_resume_highlights(
    resume_text: str,
    matched_keywords: list[str],
    limit: int = 4,
) -> list[str]:
    highlights: list[str] = []
    lowered_keywords = [term.lower() for term in matched_keywords]
    for raw_line in resume_text.splitlines():
        line = raw_line.strip().lstrip("- ").strip()
        if not line or line.startswith("#") or _contains_contact_info(line):
            continue
        lowered_line = line.lower()
        if any(term in lowered_line for term in lowered_keywords):
            highlights.append(_sanitize_sentence(line.rstrip(".") + "."))
    return highlights[:limit]


def _human_join(terms: list[str]) -> str:
    titled_terms = [term.upper() if term in {"sre", "pki"} else term for term in terms]
    if len(titled_terms) == 1:
        return titled_terms[0]
    return ", ".join(titled_terms[:-1]) + f", and {titled_terms[-1]}"


def _company_context(resume_text: str) -> str:
    companies = [
        company
        for company in ("Intact", "Morgan Stanley", "Cognizant")
        if re.search(rf"\b{re.escape(company)}\b", resume_text, flags=re.IGNORECASE)
    ]
    if not companies:
        return "My resume reflects experience across production engineering environments."
    return "My resume includes experience with " + _human_join(companies) + "."


def _highlight_sentence(highlights: list[str]) -> str:
    if not highlights:
        return (
            "That experience includes operating production platforms, improving automation, "
            "and supporting reliable engineering workflows."
        )
    return " ".join(highlights)


def _role_focus_sentence(matched_keywords: list[str]) -> str:
    focus_terms = _select_relevant_terms(matched_keywords)
    if not focus_terms:
        return "I would use that background to help improve platform quality and operational confidence."
    return (
        "I would use that background to contribute quickly in areas such as "
        f"{_human_join(focus_terms[:5])}."
    )


def _write_analysis(
    analysis_path: Path,
    job: sqlite3.Row,
    matched_keywords: list[str],
    missing_keywords: list[str],
    warnings: list[str],
) -> None:
    matched_section = "\n".join(f"- {term}" for term in matched_keywords) or "- None"
    missing_section = "\n".join(f"- {term}" for term in missing_keywords) or "- None"
    warning_section = "\n".join(f"- {warning}" for warning in warnings) or "- None"
    analysis_path.write_text(
        f"""# Cover Letter Analysis for Job {job["id"]}

Target role: {job["title"]}

Company: {job["company"] or "Unknown"}

## Matched Keywords

{matched_section}

## Missing Keywords To Review

{missing_section}

## Missing Information Warnings

{warning_section}
""",
        encoding="utf-8",
    )


def _remove_internal_sections(text: str) -> str:
    stop_headings = {
        "Missing Keywords To Review",
        "Missing Information Warnings",
        "Safety Notes",
        "Truthfulness Notes",
        "Debug",
        "Keyword Analysis",
    }
    lines: list[str] = []
    skipping = False
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped in stop_headings:
            skipping = True
            continue
        if skipping and line.strip().startswith("#"):
            stripped_heading = line.strip().lstrip("#").strip()
            skipping = stripped_heading in stop_headings
            if skipping:
                continue
        if not skipping:
            lines.append(line)
    return "\n".join(lines)


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines)
    return text.replace("```markdown", "").replace("```", "")


def _sanitize_cover_letter_text(text: str) -> str:
    sanitized_lines = [
        _sanitize_sentence(line)
        for line in text.splitlines()
        if not _contains_contact_info(line) and not _is_internal_heading(line)
    ]
    sanitized = "\n".join(sanitized_lines)
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized).strip()
    return sanitized + "\n"


def _sanitize_sentence(text: str) -> str:
    text = re.sub(r"\bvia\s+LinkedIn\b", "", text, flags=re.IGNORECASE)
    for bad_phrase in (
        "Thank you for your interest in my resume",
        "I have been selected as",
        "as advertised on LinkedIn",
        "align perfectly",
        "honed a strong set of skills",
    ):
        text = re.sub(re.escape(bad_phrase), "", text, flags=re.IGNORECASE)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\b[\w.\-+]+@[\w.\-]+\.\w+\b", "", text)
    text = re.sub(r"(?:\+?\d[\d\s().-]{7,}\d)", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _remove_closing_blocks(text: str) -> str:
    lines = text.splitlines()
    first_closing_index = next(
        (
            index
            for index, line in enumerate(lines)
            if re.match(r"^\s*(Sincerely|Best regards|Regards|Thank you),?\s*$", line, flags=re.IGNORECASE)
        ),
        None,
    )
    if first_closing_index is None:
        return text
    return "\n".join(lines[:first_closing_index]).rstrip()


def _contains_contact_info(text: str) -> bool:
    lowered = text.lower()
    return bool(
        re.search(r"https?://|linkedin\.com|[\w.\-+]+@[\w.\-]+\.\w+|(?:\+?\d[\d\s().-]{7,}\d)", text)
        or "montreal" in lowered
        or "home location" in lowered
    )


def _is_internal_heading(text: str) -> bool:
    stripped = text.strip().lstrip("#").strip().lower()
    return stripped.startswith("cover letter draft for job")
