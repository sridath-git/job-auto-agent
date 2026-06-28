from datetime import datetime, timezone
import sys

import pytest

from job_auto_agent.cli import main
from job_auto_agent.models import EmailMessage, JobOpportunity
from job_auto_agent.config import Settings
from job_auto_agent.resume import tailor as tailor_module
from job_auto_agent.resume.tailor import (
    AITailoringDisabledError,
    JobNotFoundError,
    MasterResumeMissingError,
    OpenAIAPIKeyMissingError,
    tailor_resume_for_job,
    tailor_resume_with_ai_for_job,
)
from job_auto_agent.storage.database import connect, init_db
from job_auto_agent.storage.repository import save_email, save_job


def test_tailor_resume_fails_when_master_resume_missing(tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    job_id = _seed_job(db_path)

    with connect(db_path) as conn:
        with pytest.raises(MasterResumeMissingError):
            tailor_resume_for_job(
                conn,
                job_id,
                master_resume_path=tmp_path / "missing_resume.md",
                output_dir=tmp_path / "generated",
            )


def test_tailor_resume_fails_when_job_id_not_found(tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    resume_path = tmp_path / "master_resume.md"
    resume_path.write_text("Terraform and AWS platform engineering experience.", encoding="utf-8")

    with connect(db_path) as conn:
        with pytest.raises(JobNotFoundError):
            tailor_resume_for_job(
                conn,
                999,
                master_resume_path=resume_path,
                output_dir=tmp_path / "generated",
            )


def test_tailor_resume_creates_analysis_only(tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    job_id = _seed_job(db_path)
    resume_path = tmp_path / "master_resume.md"
    resume_path.write_text(
        "Built Terraform automation for AWS platform engineering teams.",
        encoding="utf-8",
    )

    with connect(db_path) as conn:
        result = tailor_resume_for_job(
            conn,
            job_id,
            master_resume_path=resume_path,
            output_dir=tmp_path / "generated",
        )

    expected_resume_path = tmp_path / "generated" / f"job_{job_id}_tailored_resume.md"
    assert result.output_path is None
    assert not expected_resume_path.exists()
    assert result.analysis_path.exists()
    assert result.analysis_path.name == f"job_{job_id}_analysis.md"
    output = result.analysis_path.read_text(encoding="utf-8")
    assert "## Relevant Existing Keywords" in output
    assert "## Missing Job Keywords" in output


def test_tailor_resume_suggests_missing_keywords(tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    job_id = _seed_job(
        db_path,
        title="Kubernetes Platform Engineer",
        description="Operate Kubernetes, Terraform, and Prometheus platforms.",
    )
    resume_path = tmp_path / "master_resume.md"
    resume_path.write_text("Terraform automation for platform engineering.", encoding="utf-8")

    with connect(db_path) as conn:
        result = tailor_resume_for_job(
            conn,
            job_id,
            master_resume_path=resume_path,
            output_dir=tmp_path / "generated",
        )

    assert "kubernetes" in result.missing_keywords
    assert "prometheus" in result.missing_keywords
    assert "## Missing Job Keywords" in result.analysis_path.read_text(encoding="utf-8")


def test_tailor_resume_analysis_does_not_create_final_resume(tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    job_id = _seed_job(
        db_path,
        title="Kubernetes Platform Engineer",
        description="Kubernetes and Prometheus role for platform reliability.",
    )
    resume_path = tmp_path / "master_resume.md"
    resume_path.write_text(
        "Platform reliability experience with incident response and Terraform.",
        encoding="utf-8",
    )

    with connect(db_path) as conn:
        result = tailor_resume_for_job(
            conn,
            job_id,
            master_resume_path=resume_path,
            output_dir=tmp_path / "generated",
        )

    output_path = tmp_path / "generated" / f"job_{job_id}_tailored_resume.md"

    assert result.output_path is None
    assert not output_path.exists()
    assert "kubernetes" in result.missing_keywords
    assert "prometheus" in result.missing_keywords


def test_tailor_resume_analysis_does_not_leak_contact_details_into_final_resume(tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    job_id = _seed_job(
        db_path,
        title="DevSecOps Platform Engineer",
        description="DevSecOps, Kubernetes, Vault, PKI, AWS, Azure, Terraform, CI/CD, and SRE role.",
    )
    resume_path = tmp_path / "master_resume.md"
    resume_path.write_text(
        """# Sridath Jeelugula

Phone: +1 555 123 4567
Email: sridath@example.com
LinkedIn: https://linkedin.com/in/sridath
Montreal, Canada

## Professional Summary

DevOps and DevSecOps engineer with Kubernetes, Azure, AWS, Vault, PKI, Terraform, CI/CD, SRE, and Platform Engineering experience.

## Professional Experience

- Intact: Built Kubernetes platform automation with Terraform, Vault, PKI, and CI/CD.
- Morgan Stanley: Supported AWS and Azure platform reliability.
- Cognizant: Delivered DevOps automation.

## Education

- Example University

## Languages

- English
""",
        encoding="utf-8",
    )

    with connect(db_path) as conn:
        result = tailor_resume_for_job(
            conn,
            job_id,
            master_resume_path=resume_path,
            output_dir=tmp_path / "generated",
        )

    output_path = tmp_path / "generated" / f"job_{job_id}_tailored_resume.md"

    assert result.output_path is None
    assert result.analysis_path.exists()
    assert not output_path.exists()


def test_cli_non_ai_resume_tailoring_generates_analysis_only_message(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    job_id = _seed_job(db_path)
    profile_dir = tmp_path / "data" / "profile"
    profile_dir.mkdir(parents=True)
    (profile_dir / "master_resume.md").write_text(
        "Terraform and AWS platform engineering experience.",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setattr(sys, "argv", ["job-auto-agent", "tailor-resume", "--job-id", str(job_id)])

    main()

    captured = capsys.readouterr()
    assert "AI resume generation is required for recruiter-ready tailored resumes." in captured.out
    assert f"Saved tailoring analysis: data/generated_resumes/job_{job_id}_analysis.md" in captured.out
    assert (tmp_path / "data" / "generated_resumes" / f"job_{job_id}_analysis.md").exists()
    assert not (tmp_path / "data" / "generated_resumes" / f"job_{job_id}_tailored_resume.md").exists()


def test_ai_tailoring_disabled_errors_clearly(tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    job_id = _seed_job(db_path)
    resume_path = tmp_path / "master_resume.md"
    resume_path.write_text("Terraform and AWS platform engineering.", encoding="utf-8")
    settings = _settings(tmp_path, ai_tailoring_enabled=False, openai_api_key="test-key")

    with connect(db_path) as conn:
        with pytest.raises(AITailoringDisabledError, match="AI tailoring is disabled"):
            tailor_resume_with_ai_for_job(
                conn,
                job_id,
                settings,
                master_resume_path=resume_path,
                output_dir=tmp_path / "generated",
            )


def test_ai_tailoring_missing_openai_api_key_errors_clearly(tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    job_id = _seed_job(db_path)
    resume_path = tmp_path / "master_resume.md"
    resume_path.write_text("Terraform and AWS platform engineering.", encoding="utf-8")
    settings = _settings(tmp_path, ai_tailoring_enabled=True, openai_api_key=None)

    with connect(db_path) as conn:
        with pytest.raises(OpenAIAPIKeyMissingError, match="OPENAI_API_KEY is missing"):
            tailor_resume_with_ai_for_job(
                conn,
                job_id,
                settings,
                master_resume_path=resume_path,
                output_dir=tmp_path / "generated",
            )


def test_ai_tailoring_creates_output_with_required_sections(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    job_id = _seed_job(
        db_path,
        title="Kubernetes Platform Engineer",
        description="Terraform, Kubernetes, and Prometheus platform role.",
    )
    resume_path = tmp_path / "master_resume.md"
    resume_path.write_text("Terraform platform engineering experience.", encoding="utf-8")
    settings = _settings(tmp_path, ai_tailoring_enabled=True, openai_api_key="test-key")

    def fake_provider(
        api_key: str,
        base_url: str,
        model: str,
        prompt: str,
        timeout_seconds: int = 60,
    ) -> str:
        assert api_key == "test-key"
        assert base_url == "https://api.openai.com/v1"
        assert model == "test-model"
        assert timeout_seconds == 60
        assert "Do not fabricate anything" in prompt
        return """# Sridath Jeelugula

sridath@example.com | https://linkedin.com/in/sridath

## Truthfulness Notes

Uses only the provided master resume.

## Missing Keywords To Review

- kubernetes
- prometheus

## Tailored Resume Draft

## Professional Summary

- Terraform platform engineering experience.

## Core Skills

- Terraform

## Professional Experience

- Terraform platform engineering experience with sridath@example.com.

## Education

- Not specified in master resume.

## Languages

- Not specified in master resume.
"""

    monkeypatch.setattr(tailor_module, "_call_openai_compatible_provider", fake_provider)

    with connect(db_path) as conn:
        result = tailor_resume_with_ai_for_job(
            conn,
            job_id,
            settings,
            master_resume_path=resume_path,
            output_dir=tmp_path / "generated",
        )

    output = result.output_path.read_text(encoding="utf-8")
    assert result.output_path == tmp_path / "generated" / f"job_{job_id}_tailored_resume.md"
    assert result.analysis_path == tmp_path / "generated" / f"job_{job_id}_analysis.md"
    assert "## Missing Keywords To Review" not in output
    assert "## Truthfulness Notes" not in output
    assert "sridath@example.com" not in _body_after_header(output)
    assert "kubernetes" in result.missing_keywords


def test_ai_tailoring_uses_custom_openai_base_url(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    job_id = _seed_job(db_path)
    resume_path = tmp_path / "master_resume.md"
    resume_path.write_text("Terraform platform engineering experience.", encoding="utf-8")
    settings = _settings(
        tmp_path,
        ai_tailoring_enabled=True,
        openai_api_key="test-key",
        openai_base_url="https://llm.example.com/v1",
    )

    def fake_provider(
        api_key: str,
        base_url: str,
        model: str,
        prompt: str,
        timeout_seconds: int = 60,
    ) -> str:
        assert api_key == "test-key"
        assert base_url == "https://llm.example.com/v1"
        assert model == "test-model"
        assert timeout_seconds == 60
        return """# AI-Tailored Resume Draft for Job 1

## Truthfulness Notes

Uses only the provided master resume.

## Missing Keywords To Review

- None

## Tailored Resume Draft

Terraform platform engineering experience.
"""

    monkeypatch.setattr(tailor_module, "_call_openai_compatible_provider", fake_provider)

    with connect(db_path) as conn:
        result = tailor_resume_with_ai_for_job(
            conn,
            job_id,
            settings,
            master_resume_path=resume_path,
            output_dir=tmp_path / "generated",
        )

    assert result.output_path.exists()


def test_ai_tailoring_uses_local_ollama_without_real_api_key(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    job_id = _seed_job(db_path)
    resume_path = tmp_path / "master_resume.md"
    resume_path.write_text("Terraform platform engineering experience.", encoding="utf-8")
    settings = _settings(
        tmp_path,
        ai_tailoring_enabled=True,
        openai_api_key=None,
        openai_base_url="http://localhost:11434/v1",
        openai_model="qwen2.5:7b",
    )

    def fake_provider(
        api_key: str,
        base_url: str,
        model: str,
        prompt: str,
        timeout_seconds: int = 60,
    ) -> str:
        assert api_key == "ollama"
        assert base_url == "http://localhost:11434/v1"
        assert model == "qwen2.5:7b"
        assert timeout_seconds == 300
        return """# Candidate

## Professional Summary

- Terraform platform engineering experience.

## Core Skills

- Terraform

## Professional Experience

- Terraform platform engineering experience.

## Education

- Not specified in master resume.

## Languages

- Not specified in master resume.
"""

    monkeypatch.setattr(tailor_module, "_call_openai_compatible_provider", fake_provider)

    with connect(db_path) as conn:
        result = tailor_resume_with_ai_for_job(
            conn,
            job_id,
            settings,
            master_resume_path=resume_path,
            output_dir=tmp_path / "generated",
        )

    assert result.output_path.exists()


def test_ai_tailoring_preserves_header_timelines_education_and_languages(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    job_id = _seed_job(
        db_path,
        title="Senior DevSecOps Engineer",
        description="DevSecOps, Kubernetes, PKI, Vault, Terraform, and SRE role.",
    )
    resume_path = tmp_path / "master_resume.md"
    resume_path.write_text(_full_master_resume(), encoding="utf-8")
    settings = _settings(tmp_path, ai_tailoring_enabled=True, openai_api_key="test-key")

    def fake_provider(
        api_key: str,
        base_url: str,
        model: str,
        prompt: str,
        timeout_seconds: int = 60,
    ) -> str:
        assert "Preserve the candidate header exactly" in prompt
        return """```markdown
## Overview

Remove this section.

## Professional Summary

- Tailored DevSecOps and SRE summary based on existing resume content.

## Core Skills

- Kubernetes, Vault, PKI, Terraform, AWS, Azure, CI/CD

## Professional Experience

Intact Financial Corporation — Senior DevSecOps Security Engineer | Jan 2026 – Present
- Rewritten truthful bullet.
Morgan Stanley — Site Reliability Engineer | Montreal, QC | Dec 2024 – Present
- Rewritten truthful bullet.
Cognizant Technology Solutions — Site Reliability Engineer / DevSecOps Engineer | Montreal, QC | Nov 2020 – Sept 2024
- Rewritten truthful bullet.
Virtusa Consulting Services — Build & Release Engineer | Hyderabad, India | Jan 2015 – Apr 2017
- Rewritten truthful bullet.

## Education

Master of Engineering (Quality Systems Engineering), Concordia University, Montreal | 2019

## Languages

English | Telugu | Hindi
```"""

    monkeypatch.setattr(tailor_module, "_call_openai_compatible_provider", fake_provider)

    with connect(db_path) as conn:
        result = tailor_resume_with_ai_for_job(
            conn,
            job_id,
            settings,
            master_resume_path=resume_path,
            output_dir=tmp_path / "generated",
        )

    output = result.output_path.read_text(encoding="utf-8")
    assert "Sridath Jeelugula" in output
    assert "Senior DevSecOps Security Engineer" in output
    assert "Montreal, QC" in output
    assert "+1 555 123 4567" in output
    assert "sridath@example.com" in output
    assert "https://linkedin.com/in/sridath" in output
    assert "Intact Financial Corporation — Senior DevSecOps Security Engineer | Jan 2026 – Present" in output
    assert "Morgan Stanley — Site Reliability Engineer | Montreal, QC | Dec 2024 – Present" in output
    assert (
        "Cognizant Technology Solutions — Site Reliability Engineer / DevSecOps Engineer | "
        "Montreal, QC | Nov 2020 – Sept 2024"
    ) in output
    assert "Virtusa Consulting Services — Build & Release Engineer | Hyderabad, India | Jan 2015 – Apr 2017" in output
    assert "Master of Engineering (Quality Systems Engineering), Concordia University, Montreal | 2019" in output
    assert "Master of Engineering (Quality Systems Engineering), Concordia University, Montreal | 2017" not in output
    assert "English | Telugu | Hindi" in output
    assert "```" not in output
    assert "## Overview" not in output


def test_ai_tailoring_repairs_missing_timelines_from_master_resume(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    job_id = _seed_job(db_path)
    resume_path = tmp_path / "master_resume.md"
    resume_path.write_text(_full_master_resume(), encoding="utf-8")
    settings = _settings(tmp_path, ai_tailoring_enabled=True, openai_api_key="test-key")

    def fake_provider(
        api_key: str,
        base_url: str,
        model: str,
        prompt: str,
        timeout_seconds: int = 60,
    ) -> str:
        return """## Professional Summary

- Summary.

## Core Skills & Tool Stack

- Terraform

## Professional Experience

Morgan Stanley — Site Reliability Engineer | Montreal, QC | Dec 2024 – Present

## Education

Master of Engineering (Quality Systems Engineering), Concordia University, Montreal | 2017

## Languages

English | Telugu | Hindi
"""

    monkeypatch.setattr(tailor_module, "_call_openai_compatible_provider", fake_provider)

    with connect(db_path) as conn:
        result = tailor_resume_with_ai_for_job(
            conn,
            job_id,
            settings,
            master_resume_path=resume_path,
            output_dir=tmp_path / "generated",
        )

    output = result.output_path.read_text(encoding="utf-8")
    assert "Intact Financial Corporation — Senior DevSecOps Security Engineer | Jan 2026 – Present" in output
    assert "Cognizant Technology Solutions — Site Reliability Engineer / DevSecOps Engineer | Montreal, QC | Nov 2020 – Sept 2024" in output
    assert "Virtusa Consulting Services — Build & Release Engineer | Hyderabad, India | Jan 2015 – Apr 2017" in output


def test_ai_tailoring_repairs_missing_education_from_master_resume(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    job_id = _seed_job(db_path)
    resume_path = tmp_path / "master_resume.md"
    resume_path.write_text(_full_master_resume(), encoding="utf-8")
    settings = _settings(tmp_path, ai_tailoring_enabled=True, openai_api_key="test-key")

    def fake_provider(
        api_key: str,
        base_url: str,
        model: str,
        prompt: str,
        timeout_seconds: int = 60,
    ) -> str:
        return """```markdown
## Overview

Bad section.

## Professional Summary

- Summary.

## Core Skills & Tool Stack

- Terraform

## Professional Experience

Intact Financial Corporation — Senior DevSecOps Security Engineer | Jan 2026 – Present
Morgan Stanley — Site Reliability Engineer | Montreal, QC | Dec 2024 – Present
Cognizant Technology Solutions — Site Reliability Engineer / DevSecOps Engineer | Montreal, QC | Nov 2020 – Sept 2024
Virtusa Consulting Services — Build & Release Engineer | Hyderabad, India | Jan 2015 – Apr 2017

## Languages

English | Telugu | Hindi
```"""

    monkeypatch.setattr(tailor_module, "_call_openai_compatible_provider", fake_provider)

    with connect(db_path) as conn:
        result = tailor_resume_with_ai_for_job(
            conn,
            job_id,
            settings,
            master_resume_path=resume_path,
            output_dir=tmp_path / "generated",
        )

    output = result.output_path.read_text(encoding="utf-8")
    assert "Master of Engineering (Quality Systems Engineering), Concordia University, Montreal | 2019" in output
    assert "Master of Engineering (Quality Systems Engineering), Concordia University, Montreal | 2017" not in output
    assert "```" not in output
    assert "## Overview" not in output


def test_openai_provider_builds_default_chat_completions_url(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"ok"}}]}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(tailor_module.urllib.request, "urlopen", fake_urlopen)

    output = tailor_module._call_openai_compatible_provider(
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        model="test-model",
        prompt="prompt",
    )

    assert output == "ok"
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["timeout"] == 60


def test_openai_provider_builds_custom_chat_completions_url(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"ok"}}]}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(tailor_module.urllib.request, "urlopen", fake_urlopen)

    tailor_module._call_openai_compatible_provider(
        api_key="test-key",
        base_url="https://llm.example.com/v1/",
        model="test-model",
        prompt="prompt",
    )

    assert captured["url"] == "https://llm.example.com/v1/chat/completions"


def test_openai_provider_builds_ollama_chat_completions_url(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"ok"}}]}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(tailor_module.urllib.request, "urlopen", fake_urlopen)

    tailor_module._call_openai_compatible_provider(
        api_key="ollama",
        base_url="http://localhost:11434/v1",
        model="qwen2.5:7b",
        prompt="prompt",
        timeout_seconds=300,
    )

    assert captured["url"] == "http://localhost:11434/v1/chat/completions"
    assert captured["timeout"] == 300


def test_real_resume_and_generated_resumes_are_ignored() -> None:
    gitignore = open(".gitignore", encoding="utf-8").read()

    assert "data/profile/master_resume.md" in gitignore
    assert "data/generated_resumes/" in gitignore


def _body_after_header(markdown: str) -> str:
    if "## Professional Summary" not in markdown:
        return markdown
    return markdown.split("## Professional Summary", maxsplit=1)[1]


def _seed_job(
    db_path,
    title: str = "Cloud Platform Engineer",
    description: str = "Terraform, AWS, Kubernetes, and Prometheus platform role.",
) -> int:
    now = datetime.now(timezone.utc)
    with connect(db_path) as conn:
        message = EmailMessage(
            gmail_id=f"message-{title}",
            thread_id=f"thread-{title}",
            sender="Recruiter <jobs@example.com>",
            subject=title,
            snippet=description,
            body_text=description,
            received_at=now,
        )
        save_email(conn, message)
        return save_job(
            conn,
            JobOpportunity(
                source_message_id=message.gmail_id,
                company="Example",
                title=title,
                location="Remote",
                url="https://example.com/jobs/1",
                description=description,
                received_at=now,
            ),
        )


def _settings(
    tmp_path,
    ai_tailoring_enabled: bool,
    openai_api_key: str | None,
    openai_base_url: str = "https://api.openai.com/v1",
    openai_model: str = "test-model",
    ai_provider_timeout_seconds: int | None = None,
) -> Settings:
    if ai_provider_timeout_seconds is None:
        ai_provider_timeout_seconds = 300 if "localhost" in openai_base_url else 60
    return Settings(
        gmail_credentials_file=tmp_path / "credentials.json",
        gmail_token_file=tmp_path / "token.json",
        database_url=f"sqlite:///{tmp_path / 'jobs.db'}",
        gmail_query="newer_than:30d",
        match_min_score=35,
        openai_api_key=openai_api_key,
        openai_base_url=openai_base_url,
        openai_model=openai_model,
        ai_tailoring_enabled=ai_tailoring_enabled,
        ai_provider_timeout_seconds=ai_provider_timeout_seconds,
    )


def _full_master_resume() -> str:
    return """# Sridath Jeelugula
Senior DevSecOps Security Engineer
Montreal, QC
+1 555 123 4567
sridath@example.com
https://linkedin.com/in/sridath

## Professional Summary

DevSecOps and SRE experience.

## Professional Experience

Intact Financial Corporation — Senior DevSecOps Security Engineer | Jan 2026 – Present
- Kubernetes, Vault, PKI, and Terraform security automation.
Morgan Stanley — Site Reliability Engineer | Montreal, QC | Dec 2024 – Present
- Reliability engineering for cloud platforms.
Cognizant Technology Solutions — Site Reliability Engineer / DevSecOps Engineer | Montreal, QC
| Nov 2020 – Sept 2024
- DevSecOps automation and platform support.
Virtusa Consulting Services — Build & Release Engineer | Hyderabad, India | Jan 2015 – Apr 2017
- Build and release automation.

## Education

Master of Engineering (Quality Systems Engineering), Concordia University, Montreal | 2019

## Languages

English | Telugu | Hindi
"""
