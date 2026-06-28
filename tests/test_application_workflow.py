from __future__ import annotations

from datetime import datetime, timezone
import sys
import zipfile

from job_auto_agent.application import workflow as workflow_module
from job_auto_agent.application.export import PdfExportResult
from job_auto_agent.application.workflow import (
    application_paths,
    detect_application_files,
    prepare_application_package,
)
from job_auto_agent.cli import main
from job_auto_agent.config import Settings
from job_auto_agent.cover_letter.generator import CoverLetterResult
from job_auto_agent.models import JobOpportunity
from job_auto_agent.resume.tailor import TailoredResumeResult
from job_auto_agent.storage.database import connect, init_db
from job_auto_agent.storage.repository import JOB_STATUSES, list_jobs, save_job, update_job_status


def test_prepare_application_creates_markdown_docx_analysis_and_updates_status(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    job_id = _seed_job(db_path)
    resume_path = tmp_path / "master_resume.md"
    resume_path.write_text("Sridath Jeelugula\nKubernetes and Vault experience.", encoding="utf-8")
    output_root = tmp_path / "applications"
    _stub_generators(monkeypatch, tmp_path)

    with connect(db_path) as conn:
        result = prepare_application_package(
            conn,
            job_id,
            _settings(tmp_path),
            master_resume_path=resume_path,
            output_root=output_root,
            overwrite=True,
        )
        saved = list_jobs(conn)[0]

    paths = application_paths(job_id, output_root)
    assert result.folder == output_root / f"job_{job_id}"
    assert paths.resume_md.exists()
    assert paths.cover_letter_md.exists()
    assert paths.resume_docx.exists()
    assert paths.cover_letter_docx.exists()
    assert paths.analysis_md.exists()
    assert paths.resume_docx.stat().st_size > 0
    assert paths.cover_letter_docx.stat().st_size > 0
    assert saved["status"] == "Ready to Apply"


def test_prepare_application_pdf_failure_does_not_fail_workflow(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    job_id = _seed_job(db_path)
    resume_path = tmp_path / "master_resume.md"
    resume_path.write_text("Sridath Jeelugula\nKubernetes and Vault experience.", encoding="utf-8")
    output_root = tmp_path / "applications"
    _stub_generators(monkeypatch, tmp_path)

    def fake_pdf_export(docx_path, pdf_path):
        return PdfExportResult(output_path=None, warning=f"Skipped {pdf_path.name}")

    monkeypatch.setattr(workflow_module, "export_docx_to_pdf_if_available", fake_pdf_export)

    with connect(db_path) as conn:
        result = prepare_application_package(
            conn,
            job_id,
            _settings(tmp_path),
            master_resume_path=resume_path,
            output_root=output_root,
            overwrite=True,
        )

    paths = application_paths(job_id, output_root)
    assert paths.resume_md.exists()
    assert paths.cover_letter_md.exists()
    assert paths.resume_docx.exists()
    assert paths.cover_letter_docx.exists()
    assert not paths.resume_pdf.exists()
    assert not paths.cover_letter_pdf.exists()
    assert result.warnings == ["Skipped resume.pdf", "Skipped cover_letter.pdf"]


def test_generated_application_file_availability_detection(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    job_id = _seed_job(db_path)
    resume_path = tmp_path / "master_resume.md"
    resume_path.write_text("Sridath Jeelugula\nKubernetes and Vault experience.", encoding="utf-8")
    output_root = tmp_path / "applications"
    _stub_generators(monkeypatch, tmp_path)

    assert not detect_application_files(job_id, output_root).resume_md
    with connect(db_path) as conn:
        prepare_application_package(
            conn,
            job_id,
            _settings(tmp_path),
            master_resume_path=resume_path,
            output_root=output_root,
            overwrite=True,
        )

    files = detect_application_files(job_id, output_root)
    assert files.resume_md
    assert files.cover_letter_md
    assert files.resume_docx
    assert files.cover_letter_docx
    assert files.analysis_md


def test_status_update_supports_application_lifecycle(tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    job_id = _seed_job(db_path)

    assert "Ready to Apply" in JOB_STATUSES
    assert "Not Interested" in JOB_STATUSES
    assert "Interview" in JOB_STATUSES
    assert "Offer" in JOB_STATUSES
    with connect(db_path) as conn:
        update_job_status(conn, job_id, "Ready to Apply")
        assert list_jobs(conn)[0]["status"] == "Ready to Apply"


def test_docx_export_contains_clean_document_text(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    job_id = _seed_job(db_path)
    resume_path = tmp_path / "master_resume.md"
    resume_path.write_text("Sridath Jeelugula\nKubernetes and Vault experience.", encoding="utf-8")
    output_root = tmp_path / "applications"
    _stub_generators(monkeypatch, tmp_path)

    with connect(db_path) as conn:
        prepare_application_package(
            conn,
            job_id,
            _settings(tmp_path),
            master_resume_path=resume_path,
            output_root=output_root,
            overwrite=True,
        )

    with zipfile.ZipFile(application_paths(job_id, output_root).resume_docx) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert "Sridath Jeelugula" in document_xml
    assert "Professional Summary" in document_xml
    assert "##" not in document_xml
    assert "```" not in document_xml


def test_prepare_application_cli_creates_package(tmp_path, monkeypatch, capsys) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    job_id = _seed_job(db_path)
    profile_dir = tmp_path / "data" / "profile"
    profile_dir.mkdir(parents=True)
    (profile_dir / "master_resume.md").write_text(
        "Sridath Jeelugula\nKubernetes and Vault experience.",
        encoding="utf-8",
    )
    _stub_generators(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AI_TAILORING_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        sys,
        "argv",
        ["job-auto-agent", "prepare-application", "--job-id", str(job_id), "--ai", "--overwrite"],
    )

    main()

    captured = capsys.readouterr()
    assert "Prepared application package" in captured.out
    assert (tmp_path / "data" / "generated_applications" / f"job_{job_id}" / "resume.md").exists()
    assert (
        tmp_path / "data" / "generated_applications" / f"job_{job_id}" / "cover_letter.md"
    ).exists()


def _stub_generators(monkeypatch, tmp_path) -> None:
    def fake_resume(conn, job_id, settings, master_resume_path, output_dir, overwrite=False):
        output_dir.mkdir(parents=True, exist_ok=True)
        resume_path = output_dir / f"job_{job_id}_tailored_resume.md"
        analysis_path = output_dir / f"job_{job_id}_analysis.md"
        resume_path.write_text(
            """Sridath Jeelugula

## Professional Summary

- 7+ years of enterprise cloud engineering experience.

## Professional Experience

- Built Kubernetes, Vault, and PKI automation.
""",
            encoding="utf-8",
        )
        analysis_path.write_text(
            "## Relevant Existing Keywords\n\n- kubernetes\n\n## Missing Job Keywords\n\n- prometheus\n",
            encoding="utf-8",
        )
        return TailoredResumeResult(
            job_id=job_id,
            output_path=resume_path,
            analysis_path=analysis_path,
            matched_keywords=["kubernetes"],
            missing_keywords=["prometheus"],
        )

    def fake_cover(conn, job_id, settings, master_resume_path, output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
        cover_path = output_dir / f"job_{job_id}_cover_letter.md"
        analysis_path = output_dir / f"job_{job_id}_analysis.md"
        cover_path.write_text(
            """Dear Hiring Manager,

I am interested in the role.

This experience is relevant to secure platform delivery.

I would welcome a conversation.

Sincerely,

Sridath Jeelugula
""",
            encoding="utf-8",
        )
        analysis_path.write_text("## Missing Job Keywords\n\n- prometheus\n", encoding="utf-8")
        return CoverLetterResult(
            job_id=job_id,
            output_path=cover_path,
            analysis_path=analysis_path,
            missing_keywords=["prometheus"],
            warnings=[],
        )

    monkeypatch.setattr(workflow_module, "tailor_resume_with_ai_for_job", fake_resume)
    monkeypatch.setattr(workflow_module, "generate_ai_cover_letter_for_job", fake_cover)
    monkeypatch.setattr(
        workflow_module,
        "export_docx_to_pdf_if_available",
        lambda docx_path, pdf_path: PdfExportResult(output_path=None, warning="PDF export skipped"),
    )


def _seed_job(db_path) -> int:
    job = JobOpportunity(
        source_message_id="message-1",
        company="ExampleCo",
        title="DevSecOps Platform Engineer",
        location="Remote",
        source="Gmail",
        url="https://example.com/job",
        description="Kubernetes, Vault, PKI, Terraform, and DevSecOps role.",
        received_at=datetime.now(timezone.utc),
    )
    with connect(db_path) as conn:
        return save_job(conn, job)


def _settings(tmp_path) -> Settings:
    return Settings(
        gmail_credentials_file=tmp_path / "credentials.json",
        gmail_token_file=tmp_path / "token.json",
        database_url=f"sqlite:///{tmp_path / 'jobs.db'}",
        gmail_query="jobs",
        match_min_score=40,
        ai_tailoring_enabled=True,
        openai_api_key="test-key",
        openai_model="test-model",
        openai_base_url="https://api.openai.com/v1",
        ai_provider_timeout_seconds=60,
    )
