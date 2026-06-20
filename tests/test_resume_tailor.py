from datetime import datetime, timezone

import pytest

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


def test_tailor_resume_creates_markdown_file(tmp_path) -> None:
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

    assert result.output_path.exists()
    assert result.output_path.name == f"job_{job_id}_tailored_resume.md"
    assert "Tailored Resume Draft" in result.output_path.read_text(encoding="utf-8")


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


def test_tailor_resume_does_not_invent_skills_in_highlights(tmp_path) -> None:
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

    output = result.output_path.read_text(encoding="utf-8")
    highlights = output.split("## Relevant Existing Resume Highlights", maxsplit=1)[1].split(
        "## Master Resume Content for Manual Editing",
        maxsplit=1,
    )[0]

    assert "kubernetes" not in highlights.lower()
    assert "prometheus" not in highlights.lower()
    assert "kubernetes" in result.missing_keywords
    assert "prometheus" in result.missing_keywords


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

    def fake_provider(api_key: str, model: str, prompt: str) -> str:
        assert api_key == "test-key"
        assert model == "test-model"
        assert "Do not fabricate anything" in prompt
        return """# AI-Tailored Resume Draft for Job 1

## Truthfulness Notes

Uses only the provided master resume.

## Missing Keywords To Review

- kubernetes
- prometheus

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

    output = result.output_path.read_text(encoding="utf-8")
    assert result.output_path == tmp_path / "generated" / f"job_{job_id}_tailored_resume.md"
    assert "## Missing Keywords To Review" in output
    assert "## Truthfulness Notes" in output
    assert "kubernetes" in result.missing_keywords


def test_real_resume_and_generated_resumes_are_ignored() -> None:
    gitignore = open(".gitignore", encoding="utf-8").read()

    assert "data/profile/master_resume.md" in gitignore
    assert "data/generated_resumes/" in gitignore


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
) -> Settings:
    return Settings(
        gmail_credentials_file=tmp_path / "credentials.json",
        gmail_token_file=tmp_path / "token.json",
        database_url=f"sqlite:///{tmp_path / 'jobs.db'}",
        gmail_query="newer_than:30d",
        match_min_score=35,
        openai_api_key=openai_api_key,
        openai_model="test-model",
        ai_tailoring_enabled=ai_tailoring_enabled,
    )
