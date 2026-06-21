from datetime import datetime, timezone

import pytest

from job_auto_agent.config import Settings
from job_auto_agent.cover_letter import generator as cover_letter_module
from job_auto_agent.cover_letter.generator import (
    CoverLetterJobNotFoundError,
    generate_ai_cover_letter_for_job,
    generate_cover_letter_for_job,
)
from job_auto_agent.models import EmailMessage, JobOpportunity
from job_auto_agent.resume.tailor import (
    AITailoringDisabledError,
    MasterResumeMissingError,
    OpenAIAPIKeyMissingError,
)
from job_auto_agent.storage.database import connect, init_db
from job_auto_agent.storage.repository import save_email, save_job


def test_cover_letter_fails_when_master_resume_missing(tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    job_id = _seed_job(db_path)

    with connect(db_path) as conn:
        with pytest.raises(MasterResumeMissingError):
            generate_cover_letter_for_job(
                conn,
                job_id,
                master_resume_path=tmp_path / "missing_resume.md",
                output_dir=tmp_path / "letters",
            )


def test_cover_letter_fails_when_job_id_not_found(tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    resume_path = tmp_path / "master_resume.md"
    resume_path.write_text("Kubernetes and Terraform experience.", encoding="utf-8")

    with connect(db_path) as conn:
        with pytest.raises(CoverLetterJobNotFoundError):
            generate_cover_letter_for_job(
                conn,
                999,
                master_resume_path=resume_path,
                output_dir=tmp_path / "letters",
            )


def test_ai_cover_letter_disabled_errors_clearly(tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    job_id = _seed_job(db_path)
    resume_path = tmp_path / "master_resume.md"
    resume_path.write_text("Kubernetes and Terraform experience.", encoding="utf-8")
    settings = _settings(tmp_path, ai_tailoring_enabled=False, openai_api_key="test-key")

    with connect(db_path) as conn:
        with pytest.raises(AITailoringDisabledError, match="AI cover letter generation is disabled"):
            generate_ai_cover_letter_for_job(
                conn,
                job_id,
                settings,
                master_resume_path=resume_path,
                output_dir=tmp_path / "letters",
            )


def test_ai_cover_letter_missing_api_key_errors_clearly(tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    job_id = _seed_job(db_path)
    resume_path = tmp_path / "master_resume.md"
    resume_path.write_text("Kubernetes and Terraform experience.", encoding="utf-8")
    settings = _settings(tmp_path, ai_tailoring_enabled=True, openai_api_key=None)

    with connect(db_path) as conn:
        with pytest.raises(OpenAIAPIKeyMissingError, match="OPENAI_API_KEY is missing"):
            generate_ai_cover_letter_for_job(
                conn,
                job_id,
                settings,
                master_resume_path=resume_path,
                output_dir=tmp_path / "letters",
            )


def test_rule_based_cover_letter_generation_creates_output_file(tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    job_id = _seed_job(db_path)
    resume_path = tmp_path / "master_resume.md"
    resume_path.write_text(
        "Kubernetes, Terraform, DevSecOps, SRE, CI/CD, PKI, and reliability experience.",
        encoding="utf-8",
    )

    with connect(db_path) as conn:
        result = generate_cover_letter_for_job(
            conn,
            job_id,
            master_resume_path=resume_path,
            output_dir=tmp_path / "letters",
        )

    output = result.output_path.read_text(encoding="utf-8")
    assert result.output_path == tmp_path / "letters" / f"job_{job_id}_cover_letter.md"
    assert "## Cover Letter" in output
    assert "ExampleCo" in output
    assert "## Missing Keywords To Review" in output


def test_ai_cover_letter_generation_creates_output_file(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    job_id = _seed_job(db_path)
    resume_path = tmp_path / "master_resume.md"
    resume_path.write_text("Kubernetes and Terraform experience.", encoding="utf-8")
    settings = _settings(tmp_path, ai_tailoring_enabled=True, openai_api_key="test-key")

    def fake_provider(api_key: str, base_url: str, model: str, prompt: str) -> str:
        assert api_key == "test-key"
        assert base_url == "https://api.openai.com/v1"
        assert model == "test-model"
        assert "Do not fabricate experience" in prompt
        return """# Cover Letter Draft for Job 1

## Cover Letter

Dear Hiring Team,

I am interested in the role.

## Missing Keywords To Review

- None

## Truthfulness Notes

Uses only the master resume.
"""

    monkeypatch.setattr(cover_letter_module, "_call_openai_compatible_provider", fake_provider)

    with connect(db_path) as conn:
        result = generate_ai_cover_letter_for_job(
            conn,
            job_id,
            settings,
            master_resume_path=resume_path,
            output_dir=tmp_path / "letters",
        )

    output = result.output_path.read_text(encoding="utf-8")
    assert result.output_path.exists()
    assert "## Truthfulness Notes" in output
    assert "## Missing Keywords To Review" in output


def _seed_job(db_path) -> int:
    now = datetime.now(timezone.utc)
    with connect(db_path) as conn:
        message = EmailMessage(
            gmail_id="message-1",
            thread_id="thread-1",
            sender="Recruiter <jobs@example.com>",
            subject="Platform Security Engineer",
            snippet="Kubernetes, Terraform, DevSecOps, PKI, and reliability.",
            body_text="Kubernetes, Terraform, DevSecOps, PKI, CI/CD, cloud, and reliability.",
            received_at=now,
        )
        save_email(conn, message)
        return save_job(
            conn,
            JobOpportunity(
                source_message_id=message.gmail_id,
                company="ExampleCo",
                title="Platform Security Engineer",
                location="Remote",
                url="https://example.com/jobs/platform-security",
                description=message.body_text,
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
        openai_base_url="https://api.openai.com/v1",
        openai_model="test-model",
        ai_tailoring_enabled=ai_tailoring_enabled,
    )
