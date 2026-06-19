from job_auto_agent.matching.engine import score_job


def test_scores_devsecops_appsec_pki_roles_highly() -> None:
    result = score_job(
        1,
        "Senior Application Security Engineer",
        "DevSecOps, PKI, threat modeling, Kubernetes, and CI/CD security.",
    )

    assert result.score >= 60
    assert "title:application security engineer" in result.matched_terms
    assert "security:pki" in result.matched_terms


def test_scores_sre_jobs_highly() -> None:
    result = score_job(
        1,
        "Senior Site Reliability Engineer",
        "Own SLOs, incident management, observability, Kubernetes, Prometheus, and production support.",
    )

    assert result.score >= 60


def test_scores_devops_jobs_highly() -> None:
    result = score_job(
        1,
        "Senior DevOps Engineer",
        "Build CI/CD automation with Terraform, Jenkins, GitHub Actions, AWS, and Azure.",
    )

    assert result.score >= 60


def test_scores_platform_jobs_highly() -> None:
    result = score_job(
        1,
        "Cloud Platform Engineer",
        "Platform engineering role focused on infrastructure as code, automation, and reliability.",
    )

    assert result.score >= 60


def test_scores_kubernetes_jobs_highly() -> None:
    result = score_job(
        1,
        "Kubernetes Engineer",
        "Operate AKS, EKS, OpenShift, Helm, ArgoCD, Istio, Prometheus, and Grafana.",
    )

    assert result.score >= 60


def test_scores_vault_jobs_highly() -> None:
    result = score_job(
        1,
        "Vault Engineer",
        "Manage HashiCorp Vault, secret remediation, OIDC, RBAC, mTLS, and production security.",
    )

    assert result.score >= 60


def test_scores_pki_jobs_highly() -> None:
    result = score_job(
        1,
        "Senior PKI Engineer",
        "Own certificate management, intermediate CA administration, X.509 certificates, and cert-manager.",
    )

    assert result.score >= 60


def test_scores_devsecops_jobs_extremely_highly() -> None:
    result = score_job(
        1,
        "Senior DevSecOps Engineer",
        "Lead application security, SAST, DAST, SCA, container security, and supply chain security.",
    )

    assert result.score >= 80


def test_deprioritizes_java_qa_ba_and_pm_roles() -> None:
    low_priority_jobs = [
        (
            "Java Developer",
            "Build backend services with Java and Spring. Some AWS exposure preferred.",
        ),
        (
            "QA Tester",
            "Manual tester role for regression test plans, QA execution, and defect tracking.",
        ),
        (
            "Business Analyst",
            "Gather requirements, write process documentation, and support product stakeholders.",
        ),
        (
            "Project Manager",
            "Manage delivery schedules, Scrum ceremonies, budgets, and stakeholder reporting.",
        ),
    ]

    for title, description in low_priority_jobs:
        result = score_job(1, title, description)

        assert result.score <= 25
