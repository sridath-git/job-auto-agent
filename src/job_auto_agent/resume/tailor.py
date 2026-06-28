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


@dataclass(frozen=True)
class ResumeExperience:
    heading: str
    bullets: list[str]


@dataclass(frozen=True)
class ParsedResume:
    header_lines: list[str]
    summary_lines: list[str]
    skills: list[str]
    experiences: list[ResumeExperience]
    education_lines: list[str]
    language_lines: list[str]


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
    parsed_resume = parse_master_resume(resume_text)
    job_keywords = extract_job_keywords(job["title"], job["description"])
    matched_keywords, missing_keywords = compare_keywords(job_keywords, resume_text)
    prompt = build_ai_tailoring_prompt(job, parsed_resume, matched_keywords, missing_keywords)
    draft = _call_openai_compatible_provider(
        api_key=api_key,
        base_url=settings.openai_base_url,
        model=settings.openai_model,
        prompt=prompt,
        timeout_seconds=settings.ai_provider_timeout_seconds,
    )

    ai_rewrites = _parse_ai_resume_rewrites(draft, parsed_resume)
    prepared_resume = _render_ai_tailored_resume_v2(parsed_resume, ai_rewrites, matched_keywords)
    _validate_ai_tailored_resume(prepared_resume, resume_text)

    output_path = output_dir / f"job_{job_id}_tailored_resume.md"
    analysis_path = _analysis_path(output_dir, job_id)
    output_path.write_text(prepared_resume, encoding="utf-8")
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
    parsed_resume: ParsedResume | str,
    matched_keywords: list[str],
    missing_keywords: list[str],
) -> str:
    if isinstance(parsed_resume, str):
        parsed_resume = parse_master_resume(parsed_resume)
    matched_section = "\n".join(f"- {term}" for term in matched_keywords) or "- None"
    missing_section = "\n".join(f"- {term}" for term in missing_keywords) or "- None"
    experience_section = "\n".join(
        json.dumps(
            {
                "heading": experience.heading,
                "bullets": _select_prompt_bullets(experience.bullets, matched_keywords, limit=5),
            },
            ensure_ascii=False,
        )
        for experience in parsed_resume.experiences
    ) or "[]"
    prompt_skills = _select_prompt_skills(parsed_resume.skills, matched_keywords, limit=35)
    job_description = _truncate_text(job["description"], 1600)
    return f"""You are helping create a manual tailored resume draft.

Hard safety rules:
- Do not fabricate anything.
- Do not invent companies, dates, roles, tools, metrics, certifications, degrees, or skills.
- Do not add skills that are not present in the master resume.
- You must not write the final resume.
- Return structured JSON only. Do not return Markdown, headings, code fences, notes, explanations, or analysis.
- You may rewrite only professional summary lines and existing experience bullets.
- You may reorder only the existing skills and existing bullets.
- Preserve truthful experience only.
- Preserve the candidate header exactly from the master resume; the app owns the header rendering.
- Use exact experience headings from the provided parsed resume.
- Do not modify company names, role titles, role locations, employment timelines, education, languages, or contact details.
- Do not auto-apply, send emails, or upload anything.

Required JSON schema:
{{
  "summary": ["one to three recruiter-ready summary lines"],
  "skills": ["existing skill exactly as provided, reordered for the job"],
  "experience": [
    {{
      "heading": "exact heading copied from parsed resume",
      "bullets": ["rewritten truthful bullets based only on that heading's existing bullets"]
    }}
  ]
}}

Do not include the missing keyword list in the resume. It is provided only so you know what not to claim.

Target job:
- Title: {job["title"]}
- Company: {job["company"] or "Unknown"}
- Location: {job["location"] or "Unknown"}
- URL: {job["url"] or "Not available"}

Job description:
{job_description}

Keywords present in the master resume:
{matched_section}

Missing job keywords that must not be added as claimed experience:
{missing_section}

Parsed master resume fields owned by the app:
Header lines, education, and languages are fixed and will be rendered by the app, not by you.

Professional summary lines:
{json.dumps(parsed_resume.summary_lines, ensure_ascii=False)}

Existing skills:
{json.dumps(prompt_skills, ensure_ascii=False)}

Experience groups. Use these exact headings:
{experience_section}
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
        "max_tokens": 2048,
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
    except TimeoutError as exc:
        raise AITailoringProviderError(
            f"AI provider timed out after {timeout_seconds} seconds."
        ) from exc

    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AITailoringProviderError("AI provider response did not include message content.") from exc


def parse_master_resume(resume_text: str) -> ParsedResume:
    sections: dict[str, list[str]] = {
        "summary": [],
        "skills": [],
        "experience": [],
        "education": [],
        "languages": [],
    }
    header_lines: list[str] = []
    current: str | None = None

    for raw_line in _normalized_master_resume_lines(resume_text):
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            section = _classify_heading(stripped)
            if section in sections:
                current = section
                continue
        bare_section = _classify_bare_section_heading(stripped)
        if bare_section in sections:
            current = bare_section
            continue
        if current is None:
            header_lines.append(stripped)
        else:
            sections[current].append(stripped)

    return ParsedResume(
        header_lines=_dedupe_lines(header_lines) or ["# Candidate"],
        summary_lines=_clean_resume_content_lines(sections["summary"]),
        skills=_parse_resume_skills(sections["skills"]),
        experiences=_parse_resume_experiences(sections["experience"]),
        education_lines=_clean_resume_content_lines(sections["education"]),
        language_lines=_clean_resume_content_lines(sections["languages"]),
    )


def _parse_ai_resume_rewrites(draft: str, parsed_resume: ParsedResume) -> dict:
    parsed_json = _extract_json_object(draft)
    if not isinstance(parsed_json, dict):
        return {}

    allowed_headings = {experience.heading for experience in parsed_resume.experiences}
    rewrites: dict[str, object] = {}

    summary = parsed_json.get("summary")
    if isinstance(summary, list):
        clean_summary = _clean_resume_content_lines([str(line) for line in summary])
        if clean_summary:
            rewrites["summary"] = clean_summary[:3]

    skills = parsed_json.get("skills")
    if isinstance(skills, list):
        rewrites["skills"] = _filter_existing_skills([str(skill) for skill in skills], parsed_resume.skills)

    experience_rewrites: dict[str, list[str]] = {}
    experience_items = parsed_json.get("experience")
    if isinstance(experience_items, list):
        for item in experience_items:
            if not isinstance(item, dict):
                continue
            heading = str(item.get("heading", "")).strip()
            bullets = item.get("bullets")
            if heading not in allowed_headings or not isinstance(bullets, list):
                continue
            clean_bullets = _clean_resume_content_lines([str(bullet) for bullet in bullets])
            if clean_bullets:
                experience_rewrites[heading] = clean_bullets
    if experience_rewrites:
        rewrites["experience"] = experience_rewrites

    return rewrites


def _render_ai_tailored_resume_v2(
    parsed_resume: ParsedResume,
    ai_rewrites: dict,
    matched_keywords: list[str] | None = None,
) -> str:
    summary_lines = ai_rewrites.get("summary") if isinstance(ai_rewrites.get("summary"), list) else None
    skill_lines = ai_rewrites.get("skills") if isinstance(ai_rewrites.get("skills"), list) else None
    experience_rewrites = ai_rewrites.get("experience") if isinstance(ai_rewrites.get("experience"), dict) else {}

    output_lines: list[str] = []
    output_lines.extend(parsed_resume.header_lines)
    output_lines.extend(["", "## Professional Summary", ""])
    output_lines.extend(_render_resume_lines(summary_lines or parsed_resume.summary_lines or ["Professional summary not specified in master resume."]))
    output_lines.extend(["", "## Core Skills & Tool Stack", ""])
    output_lines.extend(_render_resume_lines(skill_lines or parsed_resume.skills or ["Skills not specified in master resume."]))
    output_lines.extend(["", "## Professional Experience", ""])
    for experience in parsed_resume.experiences:
        output_lines.append(experience.heading)
        bullets = experience_rewrites.get(experience.heading) if isinstance(experience_rewrites, dict) else None
        rendered_bullets = _select_experience_bullets_for_render(
            experience,
            bullets if isinstance(bullets, list) else [],
            matched_keywords or [],
        )
        output_lines.extend(_render_resume_lines(rendered_bullets or ["Experience details not specified in master resume."]))
        output_lines.append("")
    output_lines.extend(["## Education", ""])
    output_lines.extend(_render_resume_lines(parsed_resume.education_lines or ["Education not specified in master resume."]))
    output_lines.extend(["", "## Languages", ""])
    output_lines.extend(_render_resume_lines(parsed_resume.language_lines or ["Languages not specified in master resume."]))

    rendered = "\n".join(output_lines)
    rendered = _strip_markdown_code_fences(rendered)
    rendered = _remove_internal_resume_sections(rendered)
    return re.sub(r"\n{3,}", "\n\n", rendered).strip() + "\n"


def _extract_json_object(text: str) -> dict | None:
    cleaned = _strip_markdown_code_fences(text).strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            value = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _parse_resume_experiences(lines: list[str]) -> list[ResumeExperience]:
    experiences: list[ResumeExperience] = []
    current_heading: str | None = None
    current_bullets: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if _is_resume_experience_heading(line):
            if current_heading:
                experiences.append(ResumeExperience(current_heading, _clean_resume_content_lines(current_bullets)))
            current_heading = line.lstrip("#").strip()
            current_bullets = []
        elif current_heading:
            current_bullets.append(line)
        else:
            current_heading = "Experience"
            current_bullets = [line]
    if current_heading:
        experiences.append(ResumeExperience(current_heading, _clean_resume_content_lines(current_bullets)))
    return experiences


def _is_resume_experience_heading(line: str) -> bool:
    stripped = line.strip().lstrip("#").strip()
    if stripped.startswith("- "):
        return False
    return _has_timeline(stripped) or _looks_like_company_heading_without_timeline(stripped)


def _classify_bare_section_heading(line: str) -> str | None:
    normalized = _normalize(line)
    exact_headings = {
        "professional summary": "summary",
        "summary": "summary",
        "core skills": "skills",
        "core skills tool stack": "skills",
        "professional experience": "experience",
        "experience": "experience",
        "education": "education",
        "languages": "languages",
    }
    return exact_headings.get(normalized)


def _parse_resume_skills(lines: list[str]) -> list[str]:
    return _dedupe_lines(_clean_resume_content_lines(lines))


def _select_experience_bullets_for_render(
    experience: ResumeExperience,
    ai_bullets: list[str],
    matched_keywords: list[str],
) -> list[str]:
    original_bullets = experience.bullets
    minimum_count, maximum_count = _experience_bullet_bounds(experience.heading, len(original_bullets))
    selected = _dedupe_lines(ai_bullets)
    if len(selected) < minimum_count:
        selected = _dedupe_lines(
            selected
            + _rank_prompt_lines(original_bullets, matched_keywords)
            + original_bullets
        )
    if maximum_count and len(selected) > maximum_count:
        return selected[:maximum_count]
    return selected


def _experience_bullet_bounds(heading: str, master_bullet_count: int) -> tuple[int, int]:
    normalized = _normalize(heading)
    if "intact financial corporation" in normalized:
        return min(master_bullet_count, 8), min(master_bullet_count, 10) or 10
    if "morgan stanley" in normalized:
        return min(master_bullet_count, 8), min(master_bullet_count, 10) or 10
    if "cognizant technology solutions" in normalized:
        return min(master_bullet_count, 5), min(master_bullet_count, 6) or 6
    if "virtusa consulting services" in normalized:
        return min(master_bullet_count, 3), min(master_bullet_count, 4) or 4
    return min(master_bullet_count, 3), min(master_bullet_count, 6) or 6


def _select_prompt_skills(skills: list[str], matched_keywords: list[str], limit: int) -> list[str]:
    ranked = _rank_prompt_lines(skills, matched_keywords)
    return ranked[:limit] or skills[:limit]


def _select_prompt_bullets(bullets: list[str], matched_keywords: list[str], limit: int) -> list[str]:
    ranked = _rank_prompt_lines(bullets, matched_keywords)
    selected = _dedupe_lines(ranked[:limit] + bullets[: max(1, limit // 2)])
    return selected[:limit]


def _rank_prompt_lines(lines: list[str], matched_keywords: list[str]) -> list[str]:
    scored: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        normalized_line = _normalize(line)
        score = sum(1 for term in matched_keywords if _contains_term(normalized_line, term))
        scored.append((score, -index, line))
    scored.sort(reverse=True)
    return [line for score, _, line in scored if score > 0]


def _truncate_text(text: str, max_chars: int) -> str:
    clean_text = re.sub(r"\s+", " ", text or "").strip()
    if len(clean_text) <= max_chars:
        return clean_text
    return clean_text[:max_chars].rsplit(" ", maxsplit=1)[0].rstrip() + "..."


def _filter_existing_skills(candidate_skills: list[str], existing_skills: list[str]) -> list[str]:
    by_key = {_normalize(skill): skill for skill in existing_skills}
    ordered = [by_key[_normalize(skill)] for skill in candidate_skills if _normalize(skill) in by_key]
    remainder = [skill for skill in existing_skills if _normalize(skill) not in {_normalize(item) for item in ordered}]
    return _dedupe_lines(ordered + remainder)


def _clean_resume_content_lines(lines: list[str]) -> list[str]:
    cleaned: list[str] = []
    for line in lines:
        clean_line = str(line).strip()
        if not clean_line:
            continue
        clean_line = clean_line.lstrip("- ").strip()
        if not clean_line or clean_line.startswith("#"):
            continue
        if any(blocked in _normalize(clean_line) for blocked in ("safety notes", "missing keywords", "truthfulness notes")):
            continue
        cleaned.append(clean_line)
    return _dedupe_lines(cleaned)


def _render_resume_lines(lines: list[str]) -> list[str]:
    return [line if line.startswith("- ") else f"- {line}" for line in lines]


def _prepare_ai_tailored_resume(draft: str, resume_text: str | None = None) -> str:
    cleaned = _strip_markdown_code_fences(draft)
    cleaned = _remove_internal_resume_sections(cleaned)
    cleaned = _remove_body_contact_details(cleaned)
    if resume_text:
        cleaned = _apply_master_resume_header(cleaned, resume_text)
        cleaned = _normalize_resume_section_headings(cleaned)
        cleaned = _repair_resume_required_sections(cleaned, resume_text)
    return cleaned.strip() + "\n"


def _validate_ai_tailored_resume(output: str, resume_text: str) -> None:
    required_values = _required_resume_values(resume_text)
    missing = [
        label
        for label, expected_value in required_values
        if expected_value and expected_value not in output
    ]
    lowered_output = output.lower()
    if "```" in output:
        missing.append("code fences removed")
    if re.search(r"^##\s+Overview\s*$", output, flags=re.IGNORECASE | re.MULTILINE):
        missing.append("Overview section removed")
    if missing:
        raise AITailoringProviderError(
            "AI resume output failed validation; missing or invalid content: "
            + ", ".join(dict.fromkeys(missing))
        )
    blocked_headings = (
        "safety notes",
        "safety notice",
        "missing keywords",
        "truthfulness notes",
        "debug",
        "keyword analysis",
        "analysis",
        "overview",
    )
    blocked_present = [
        term
        for term in blocked_headings
        if re.search(rf"^#+\s+{re.escape(term)}\s*$", lowered_output, flags=re.MULTILINE)
    ]
    if blocked_present:
        raise AITailoringProviderError(
            "AI resume output failed validation; internal section present: "
            + ", ".join(blocked_present)
        )


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
        "overview",
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


def _strip_markdown_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines)
    return text.replace("```markdown", "").replace("```", "")


def _apply_master_resume_header(draft: str, resume_text: str) -> str:
    header_lines = _extract_required_header_lines(resume_text)
    if not header_lines:
        return draft
    lines = draft.splitlines()
    first_section_index = next(
        (index for index, line in enumerate(lines) if line.strip().lower().startswith("## professional summary")),
        None,
    )
    body_lines = lines[first_section_index:] if first_section_index is not None else lines
    header = "\n".join(header_lines)
    return header + "\n\n" + "\n".join(body_lines).lstrip()


def _normalize_resume_section_headings(draft: str) -> str:
    lines: list[str] = []
    for line in draft.splitlines():
        heading = line.strip().lower()
        if heading == "## core skills":
            lines.append("## Core Skills & Tool Stack")
        elif heading == "## overview":
            continue
        else:
            lines.append(line)
    return "\n".join(lines)


def _repair_resume_required_sections(draft: str, resume_text: str) -> str:
    repaired = draft
    required_experience_lines = _extract_required_experience_lines(resume_text)
    missing_experience_lines = [line for line in required_experience_lines if line not in repaired]
    if missing_experience_lines:
        repaired = _prepend_lines_to_section(
            repaired,
            "Professional Experience",
            missing_experience_lines,
        )

    education_lines = _extract_section_body_lines(resume_text, "education")
    if education_lines:
        repaired = _replace_section(
            repaired,
            "Education",
            education_lines,
        )

    language_lines = _extract_section_body_lines(resume_text, "languages")
    if language_lines:
        repaired = _replace_section(
            repaired,
            "Languages",
            language_lines,
        )
    return repaired


def _prepend_lines_to_section(draft: str, section_name: str, lines_to_prepend: list[str]) -> str:
    lines = draft.splitlines()
    heading_index = _find_section_heading_index(lines, section_name)
    if heading_index is None:
        return draft.rstrip() + f"\n\n## {section_name}\n\n" + "\n".join(lines_to_prepend) + "\n"
    insert_index = heading_index + 1
    while insert_index < len(lines) and not lines[insert_index].strip():
        insert_index += 1
    return "\n".join(lines[:insert_index] + lines_to_prepend + [""] + lines[insert_index:])


def _replace_section(draft: str, section_name: str, replacement_lines: list[str]) -> str:
    lines = draft.splitlines()
    heading_index = _find_section_heading_index(lines, section_name)
    replacement_block = [f"## {section_name}", "", *replacement_lines]
    if heading_index is None:
        return draft.rstrip() + "\n\n" + "\n".join(replacement_block) + "\n"
    next_heading_index = next(
        (
            index
            for index in range(heading_index + 1, len(lines))
            if lines[index].strip().startswith("## ")
        ),
        len(lines),
    )
    return "\n".join(lines[:heading_index] + replacement_block + lines[next_heading_index:])


def _find_section_heading_index(lines: list[str], section_name: str) -> int | None:
    normalized_section = _normalize(section_name)
    for index, line in enumerate(lines):
        if line.strip().startswith("## ") and _normalize(line) == normalized_section:
            return index
    return None


def _extract_section_body_lines(resume_text: str, section_name: str) -> list[str]:
    target = _normalize(section_name)
    lines: list[str] = []
    in_section = False
    for raw_line in resume_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            if in_section:
                break
            in_section = _normalize(line) == target
            continue
        if in_section:
            lines.append(line.lstrip("- ").strip())
    return _dedupe_lines(lines)


def _required_resume_values(resume_text: str) -> list[tuple[str, str]]:
    required: list[tuple[str, str]] = []
    for line in _extract_required_header_lines(resume_text):
        clean_line = line.lstrip("#").strip()
        if clean_line:
            required.append((f"header line '{clean_line}'", clean_line))

    for line in _extract_required_experience_lines(resume_text):
        required.append((f"company timeline '{line}'", line))

    for education_line in _extract_section_body_lines(resume_text, "education"):
        required.append(("education", education_line))

    for languages_line in _extract_section_body_lines(resume_text, "languages"):
        required.append(("languages", languages_line))

    return required


def _extract_required_header_lines(resume_text: str) -> list[str]:
    lines = [line.rstrip() for line in resume_text.splitlines()]
    header_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if header_lines:
                continue
            continue
        normalized = _normalize(stripped)
        if stripped.startswith("##") or normalized in {
            "professional summary",
            "summary",
            "core skills",
            "core skills tool stack",
            "professional experience",
            "experience",
            "education",
            "languages",
        }:
            break
        if stripped.startswith("# "):
            header_lines.append(stripped)
            continue
        if _is_contact_line(stripped) or _looks_like_name(stripped) or _looks_like_professional_title(stripped):
            header_lines.append(stripped)
    return _dedupe_lines(header_lines)


def _extract_required_experience_lines(resume_text: str) -> list[str]:
    required_lines: list[str] = []
    for raw_line in _normalized_master_resume_lines(resume_text):
        line = raw_line.strip().lstrip("- ").strip()
        if not line:
            continue
        normalized = _normalize(line)
        if any(
            company in normalized
            for company in (
                "intact financial corporation",
                "morgan stanley",
                "cognizant technology solutions",
                "virtusa consulting services",
            )
        ) and _has_timeline(line):
            required_lines.append(line)
    return _dedupe_lines(required_lines)


def _normalized_master_resume_lines(resume_text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in resume_text.splitlines():
        line = raw_line.strip()
        if line.startswith("|") and lines and _looks_like_company_heading_without_timeline(lines[-1]):
            lines[-1] = f"{lines[-1]} {line}"
        else:
            lines.append(line)
    return lines


def _looks_like_company_heading_without_timeline(line: str) -> bool:
    normalized = _normalize(line)
    return any(
        company in normalized
        for company in (
            "intact financial corporation",
            "morgan stanley",
            "cognizant technology solutions",
            "virtusa consulting services",
        )
    ) and not _has_timeline(line)


def _has_timeline(line: str) -> bool:
    return bool(
        re.search(
            r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)\s+\d{4}\s+[–-]\s+(?:Present|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)\s+\d{4})\b",
            line,
        )
    )


def _remove_body_contact_details(draft: str) -> str:
    lines = draft.splitlines()
    body_started = False
    output_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            body_started = True
        if body_started and _is_standalone_contact_line(stripped):
            continue
        output_lines.append(line)
    return "\n".join(output_lines)


def _is_standalone_contact_line(line: str) -> bool:
    if not line:
        return False
    if re.search(r"\b[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}\b", line):
        return True
    if re.search(r"https?://|linkedin\.com|github\.com", line, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"(?:phone|mobile|tel|email|linkedin|location)?[:\s|+-]*[\d\s().-]{7,}", line, flags=re.IGNORECASE):
        return True
    normalized = line.lower().strip(" -|")
    return normalized in {"montreal", "montreal, qc", "montreal, quebec", "montreal, canada"}


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


def _looks_like_professional_title(line: str) -> bool:
    normalized = _normalize(line)
    title_terms = (
        "engineer",
        "devsecops",
        "sre",
        "site reliability",
        "platform",
        "security",
        "cloud",
        "devops",
    )
    return any(term in normalized for term in title_terms) and not _has_timeline(line)


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
