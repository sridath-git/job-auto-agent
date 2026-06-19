from __future__ import annotations

import re

from job_auto_agent.models import MatchResult


TARGET_JOB_TITLES = {
    "site reliability engineer": 36,
    "senior site reliability engineer": 40,
    "staff site reliability engineer": 42,
    "sre": 34,
    "devops engineer": 34,
    "senior devops engineer": 38,
    "cloud engineer": 30,
    "senior cloud engineer": 34,
    "cloud platform engineer": 36,
    "platform engineer": 34,
    "senior platform engineer": 38,
    "infrastructure engineer": 28,
    "platform reliability engineer": 38,
    "kubernetes engineer": 38,
    "devsecops engineer": 42,
    "senior devsecops engineer": 46,
    "cloud security engineer": 38,
    "application security engineer": 36,
    "appsec engineer": 36,
    "platform security engineer": 38,
    "pki engineer": 46,
    "senior pki engineer": 50,
    "vault engineer": 46,
    "identity engineer": 34,
}

BROAD_PROFILE_TERMS = {
    "devops": 12,
    "ci cd": 8,
    "platform engineering": 12,
    "cloud engineering": 12,
    "infrastructure": 8,
    "automation": 7,
    "reliability": 10,
    "sre": 12,
    "observability": 9,
    "monitoring": 7,
    "kubernetes": 12,
    "cloud security": 12,
    "devsecops": 16,
    "infrastructure as code": 10,
    "incident management": 8,
    "production support": 7,
}

TECHNOLOGY_TERMS = {
    "azure": 8,
    "aws": 8,
    "kubernetes": 14,
    "aks": 10,
    "eks": 10,
    "openshift": 10,
    "terraform": 9,
    "ansible": 7,
    "jenkins": 6,
    "github actions": 7,
    "azure devops": 8,
    "helm": 7,
    "fluxcd": 8,
    "argocd": 8,
    "istio": 8,
    "datadog": 7,
    "prometheus": 7,
    "grafana": 7,
    "pagerduty": 6,
    "elk": 6,
    "vault": 16,
    "hashicorp vault": 18,
    "pki": 18,
    "certificate management": 16,
    "cert manager": 12,
    "oidc": 10,
    "rbac": 8,
    "mtls": 10,
    "cyberark": 10,
    "veracode": 8,
    "sonarqube": 8,
    "invicti": 8,
    "sast": 8,
    "dast": 8,
    "sca": 8,
    "container security": 12,
    "vulnerability management": 10,
    "mlsecops": 8,
}

SECURITY_TERMS = {
    "devsecops": 18,
    "application security": 16,
    "appsec": 16,
    "sast": 8,
    "dast": 8,
    "sca": 8,
    "veracode": 8,
    "sonarqube": 8,
    "invicti": 8,
    "github advanced security": 10,
    "container security": 12,
    "vulnerability management": 10,
    "secret detection": 10,
    "secret remediation": 10,
    "software supply chain security": 12,
    "mlsecops": 8,
    "cloud security": 12,
    "pki": 20,
    "public key infrastructure": 20,
    "certificate management": 16,
    "intermediate ca": 14,
    "x 509": 12,
    "cert manager": 12,
    "vault": 18,
    "hashicorp vault": 20,
    "iam": 10,
    "identity": 8,
    "oidc": 10,
    "rbac": 8,
    "mtls": 10,
    "cyberark": 10,
}

DEPRIORITIZED_ROLES = {
    "java developer": -45,
    "full stack developer": -42,
    "frontend developer": -42,
    "backend developer": -38,
    "qa tester": -45,
    "qa analyst": -45,
    "manual tester": -45,
    "business analyst": -42,
    "data analyst": -38,
    "product owner": -38,
    "scrum master": -38,
    "project manager": -42,
    "salesforce developer": -42,
    "sap consultant": -42,
    "sales": -30,
    "account executive": -35,
    "unpaid": -25,
    "internship": -20,
}


def score_job(job_id: int, title: str, description: str) -> MatchResult:
    normalized_title = _normalize(title)
    normalized_text = _normalize(f"{title}\n{description}")
    score = 0
    matched_terms: list[str] = []

    for term, points in TARGET_JOB_TITLES.items():
        if _contains_term(normalized_title, term):
            score += points
            matched_terms.append(f"title:{term}")
        elif _contains_term(normalized_text, term):
            score += max(points - 12, 0)
            matched_terms.append(f"title-context:{term}")

    for category, weighted_terms in (
        ("broad", BROAD_PROFILE_TERMS),
        ("tech", TECHNOLOGY_TERMS),
        ("security", SECURITY_TERMS),
    ):
        for term, points in weighted_terms.items():
            if _contains_term(normalized_text, term):
                score += points
                matched_terms.append(f"{category}:{term}")

    for term, points in DEPRIORITIZED_ROLES.items():
        if _contains_term(normalized_title, term):
            score += points
            matched_terms.append(f"deprioritized:{term}")
        elif _contains_term(normalized_text, term):
            score += int(points * 0.6)
            matched_terms.append(f"deprioritized-context:{term}")

    score = max(0, min(score, 100))
    notes = _build_notes(score, matched_terms)
    return MatchResult(job_id=job_id, score=score, matched_terms=matched_terms, notes=notes)


def _build_notes(score: int, matched_terms: list[str]) -> str:
    if not matched_terms:
        return "No strong SRE/DevOps/Platform/Security signals found yet."
    if score >= 60:
        return "Strong profile match based on weighted title, platform, cloud, and security signals."
    if score >= 35:
        return "Potential match worth reviewing."
    return "Weak match; keep only if the company or context is interesting."


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
