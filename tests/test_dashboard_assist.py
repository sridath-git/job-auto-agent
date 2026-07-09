from pathlib import Path

from job_auto_agent.application.assist import (
    ApplicationPackageMissingError,
    AssistApplyError,
    JobApplyUrlMissingError,
    PlaywrightUnavailableError,
)
from job_auto_agent.application.dashboard import (
    assist_apply_error_message,
    assist_apply_readiness,
    is_valid_application_url,
)
from job_auto_agent.application.profile import ApplicationProfileMissingError
from job_auto_agent.application.workflow import application_paths


def test_assist_apply_readiness_reports_missing_prerequisites(tmp_path: Path) -> None:
    readiness = assist_apply_readiness(
        7,
        profile_path=tmp_path / "profile.json",
        output_root=tmp_path / "applications",
    )

    assert not readiness.ready
    assert not readiness.profile_exists
    assert not readiness.package_exists


def test_assist_apply_readiness_requires_both_docx_files(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile.json"
    profile_path.write_text("{}", encoding="utf-8")
    output_root = tmp_path / "applications"
    paths = application_paths(7, output_root)
    paths.folder.mkdir(parents=True)
    paths.resume_docx.write_bytes(b"resume")

    assert not assist_apply_readiness(7, profile_path, output_root).ready

    paths.cover_letter_docx.write_bytes(b"cover letter")
    assert assist_apply_readiness(7, profile_path, output_root).ready


def test_dashboard_assist_errors_are_actionable() -> None:
    assert assist_apply_error_message(ApplicationPackageMissingError("missing")) == (
        "Prepare Application first."
    )
    assert assist_apply_error_message(ApplicationProfileMissingError("missing")) == (
        "Create application profile first."
    )
    assert "valid application URL" in assist_apply_error_message(
        JobApplyUrlMissingError("missing")
    )
    assert "playwright install chromium" in assist_apply_error_message(
        PlaywrightUnavailableError("missing")
    )
    assert "Continue manually" in assist_apply_error_message(
        AssistApplyError("Automation blocked by site")
    )


def test_dashboard_rejects_invalid_application_urls() -> None:
    assert is_valid_application_url("https://jobs.example.com/role/123")
    assert not is_valid_application_url(None)
    assert not is_valid_application_url("not-a-url")
    assert not is_valid_application_url("file:///tmp/application.html")
