from __future__ import annotations

import re

from job_auto_agent.models import EmailMessage, JobOpportunity


URL_RE = re.compile(r"https?://[^\s>)\"']+")
TITLE_RE = re.compile(
    r"\b("
    r"devsecops|application security|appsec|product security|cloud security|"
    r"security engineer|security architect|pki|iam|identity"
    r")[^\n,.:-]*",
    re.IGNORECASE,
)


def extract_job(message: EmailMessage) -> JobOpportunity | None:
    text = _clean_text("\n".join([message.subject, message.snippet, message.body_text]))
    if not _looks_job_related(text):
        return None

    title = _extract_title(message.subject, text)
    if not title:
        return None

    return JobOpportunity(
        source_message_id=message.gmail_id,
        company=_extract_company(message.sender, text),
        title=title,
        location=_extract_location(text),
        url=_extract_url(text),
        description=text[:4000],
        received_at=message.received_at,
    )


def _looks_job_related(text: str) -> bool:
    indicators = [
        "job",
        "role",
        "opportunity",
        "recruiter",
        "interview",
        "hiring",
        "position",
        "application",
    ]
    lowered = text.lower()
    return any(indicator in lowered for indicator in indicators)


def _extract_title(subject: str, text: str) -> str | None:
    subject_match = TITLE_RE.search(subject)
    if subject_match:
        return subject_match.group(0).strip(" -:,.").title()

    text_match = TITLE_RE.search(text)
    if text_match:
        return text_match.group(0).strip(" -:,.").title()

    if "security" in text.lower():
        return "Security Role"
    return None


def _extract_company(sender: str, text: str) -> str | None:
    sender_match = re.search(r"@([A-Za-z0-9.-]+)", sender)
    if sender_match:
        domain = sender_match.group(1).split(".")[0]
        if domain not in {"gmail", "linkedin", "indeed", "greenhouse", "lever"}:
            return normalize_company_name(domain.replace("-", " ").title())

    company_match = re.search(r"\bat\s+([A-Z][A-Za-z0-9&.\- ]{2,50})", text)
    if company_match:
        return normalize_company_name(company_match.group(1))
    return None


def normalize_company_name(company: str | None) -> str | None:
    if not company:
        return None
    cleaned = re.sub(r"\s+", " ", company).strip(" -–—|,")
    cleanup_patterns = (
        r"\bPosted\s+on\b.*$",
        r"\b\d+\s+(?:days?|hours?|minutes?)\s+ago\b.*$",
        r"\b\d+\s+applicants?\b.*$",
        r"\bEasy\s+Apply\b.*$",
        r"\bApply\b.*$",
        r"\bViewed\b.*$",
        r"\bPromoted\b.*$",
    )
    for pattern in cleanup_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip(" -–—|,")
    return cleaned or None


def _extract_location(text: str) -> str | None:
    location_match = re.search(
        r"\b(Remote|Hybrid|Toronto|Canada|United States|USA|New York|San Francisco|Austin)\b",
        text,
        re.IGNORECASE,
    )
    return location_match.group(1) if location_match else None


def _extract_url(text: str) -> str | None:
    match = URL_RE.search(text)
    return match.group(0) if match else None


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
