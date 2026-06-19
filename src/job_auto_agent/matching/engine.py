from __future__ import annotations

from job_auto_agent.models import MatchResult


ROLE_KEYWORDS = {
    "devsecops": 18,
    "application security": 18,
    "appsec": 18,
    "product security": 14,
    "cloud security": 12,
    "security engineer": 10,
    "security architect": 10,
    "pki": 18,
    "public key infrastructure": 18,
    "iam": 12,
    "identity": 8,
    "threat modeling": 10,
    "sast": 8,
    "dast": 8,
    "container security": 8,
    "kubernetes": 7,
    "terraform": 6,
    "ci/cd": 6,
    "aws": 5,
    "azure": 5,
    "gcp": 5,
}

NEGATIVE_KEYWORDS = {
    "sales": -15,
    "account executive": -20,
    "unpaid": -15,
    "internship": -10,
    "senior vice president": -10,
}


def score_job(job_id: int, title: str, description: str) -> MatchResult:
    haystack = f"{title}\n{description}".lower()
    score = 0
    matched_terms: list[str] = []

    for term, points in ROLE_KEYWORDS.items():
        if term in haystack:
            score += points
            matched_terms.append(term)

    for term, points in NEGATIVE_KEYWORDS.items():
        if term in haystack:
            score += points
            matched_terms.append(term)

    score = max(0, min(score, 100))
    notes = _build_notes(score, matched_terms)
    return MatchResult(job_id=job_id, score=score, matched_terms=matched_terms, notes=notes)


def _build_notes(score: int, matched_terms: list[str]) -> str:
    if not matched_terms:
        return "No strong DevSecOps/AppSec/PKI signals found yet."
    if score >= 60:
        return "Strong security role match based on weighted keywords."
    if score >= 35:
        return "Potential match worth reviewing."
    return "Weak match; keep only if the company or context is interesting."
