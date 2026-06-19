from job_auto_agent.matching.engine import score_job


def test_scores_devsecops_appsec_pki_roles_highly() -> None:
    result = score_job(
        1,
        "Senior Application Security Engineer",
        "DevSecOps, PKI, threat modeling, Kubernetes, and CI/CD security.",
    )

    assert result.score >= 60
    assert "application security" in result.matched_terms
    assert "pki" in result.matched_terms
