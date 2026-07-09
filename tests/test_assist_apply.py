from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pytest

from job_auto_agent.application.assist import (
    ApplicationPackageMissingError,
    _linkedin_login_required,
    _wait_for_linkedin_login,
    assist_apply_for_job,
    detect_ats_type,
    field_value_for_identifier,
    is_final_submit_label,
)
from job_auto_agent.cli import main
from job_auto_agent.application.profile import (
    ApplicationProfileMissingError,
    load_application_profile,
    screening_answer_for_question,
    work_experience_responsibilities,
)
from job_auto_agent.application.workflow import application_paths
from job_auto_agent.models import JobOpportunity
from job_auto_agent.storage.database import connect, init_db
from job_auto_agent.storage.repository import list_jobs, save_job


def test_application_profile_loads_correctly(tmp_path) -> None:
    profile_path = _write_profile(tmp_path)

    profile = load_application_profile(profile_path)

    assert profile.personal_info["first_name"] == "Sridath"
    assert profile.education["school"] == "Concordia University"
    assert profile.work_experience[0]["company"] == "Morgan Stanley"


def test_init_application_profile_command_creates_real_profile(tmp_path, monkeypatch, capsys) -> None:
    profile_dir = tmp_path / "data" / "profile"
    profile_dir.mkdir(parents=True)
    (profile_dir / "application_profile.example.json").write_text(
        json.dumps({"personal_info": {"first_name": "Example"}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["job-auto-agent", "init-application-profile"])

    main()

    output = capsys.readouterr().out
    assert "Application profile ready" in output
    assert (profile_dir / "application_profile.json").exists()


def test_missing_profile_gives_clear_error(tmp_path) -> None:
    with pytest.raises(ApplicationProfileMissingError, match="init-application-profile"):
        load_application_profile(tmp_path / "missing.json")


def test_missing_application_package_gives_clear_error(tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    job_id = _seed_job(db_path)
    profile_path = _write_profile(tmp_path)

    with connect(db_path) as conn:
        with pytest.raises(ApplicationPackageMissingError, match="Run prepare-application first"):
            assist_apply_for_job(
                conn,
                job_id,
                profile_path=profile_path,
                output_root=tmp_path / "applications",
            )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://company.myworkdayjobs.com/job/123", "Workday"),
        ("https://boards.greenhouse.io/example/jobs/123", "Greenhouse"),
        ("https://jobs.lever.co/example/123", "Lever"),
        ("https://www.linkedin.com/jobs/view/123", "LinkedIn Easy Apply"),
        ("https://example.com/careers/123", "Generic"),
    ],
)
def test_ats_detection_for_common_urls(url: str, expected: str) -> None:
    assert detect_ats_type(url) == expected


def test_field_matching_maps_common_fields(tmp_path) -> None:
    profile = load_application_profile(_write_profile(tmp_path))

    assert field_value_for_identifier("First Name", profile) == "Sridath"
    assert field_value_for_identifier("email address", profile) == "sridath@example.com"
    assert field_value_for_identifier("LinkedIn Profile", profile) == "https://linkedin.com/in/sridath"
    assert field_value_for_identifier("School or University", profile) == "Concordia University"
    assert field_value_for_identifier("Do you require sponsorship?", profile) == "No"


def test_work_experience_responsibilities_are_available(tmp_path) -> None:
    profile = load_application_profile(_write_profile(tmp_path))

    responsibilities = work_experience_responsibilities(profile)

    assert "Operated Kubernetes platforms." in responsibilities
    assert "Supported PKI and Vault workflows." in responsibilities


def test_screening_answers_only_auto_fill_when_allowed(tmp_path) -> None:
    profile = load_application_profile(_write_profile(tmp_path))

    assert screening_answer_for_question(profile, "Are you legally authorized to work in Canada?") == "Yes"
    assert screening_answer_for_question(profile, "What is your expected salary?") is None


def test_final_submit_button_is_never_treated_as_safe() -> None:
    assert is_final_submit_label("Submit Application")
    assert is_final_submit_label("Apply Now")
    assert not is_final_submit_label("Save and continue")


def test_assist_apply_updates_statuses_and_uses_browser_driver(tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    job_id = _seed_job(db_path)
    profile_path = _write_profile(tmp_path)
    output_root = tmp_path / "applications"
    _write_application_package(job_id, output_root)
    calls: list[str] = []

    def fake_browser_driver(job, profile, paths):
        calls.append(job["url"])
        assert profile.personal_info["first_name"] == "Sridath"
        assert paths.resume_docx.exists()
        assert paths.cover_letter_docx.exists()
        return "Lever"

    with connect(db_path) as conn:
        result = assist_apply_for_job(
            conn,
            job_id,
            profile_path=profile_path,
            output_root=output_root,
            browser_driver=fake_browser_driver,
        )
        saved = list_jobs(conn)[0]

    assert calls == ["https://jobs.lever.co/example/123"]
    assert result.status == "Needs Review"
    assert result.message == "Review the application manually before submitting."
    assert saved["status"] == "Needs Review"


def test_assist_apply_passes_review_wait_to_playwright_driver(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    job_id = _seed_job(db_path)
    profile_path = _write_profile(tmp_path)
    output_root = tmp_path / "applications"
    _write_application_package(job_id, output_root)
    captured: dict[str, int | None] = {}

    def fake_playwright_driver(job, profile, paths, review_wait_seconds=None):
        captured["review_wait_seconds"] = review_wait_seconds
        return "Lever"

    monkeypatch.setattr(
        "job_auto_agent.application.assist._run_playwright_assist",
        fake_playwright_driver,
    )

    with connect(db_path) as conn:
        result = assist_apply_for_job(
            conn,
            job_id,
            profile_path=profile_path,
            output_root=output_root,
            review_wait_seconds=300,
        )

    assert captured["review_wait_seconds"] == 300
    assert result.status == "Needs Review"


def test_linkedin_login_detection_and_wait_do_not_store_tokens(tmp_path, monkeypatch) -> None:
    class FakeLocator:
        def __init__(self, page) -> None:
            self.page = page

        def count(self) -> int:
            return int("/login" in self.page.url)

    class FakePage:
        url = "https://www.linkedin.com/login"

        def __init__(self) -> None:
            self.waits: list[int] = []

        def locator(self, selector: str) -> FakeLocator:
            assert selector == 'input[type="password"]'
            return FakeLocator(self)

        def wait_for_timeout(self, milliseconds: int) -> None:
            self.waits.append(milliseconds)
            self.url = "https://www.linkedin.com/jobs/view/123"

    page = FakePage()
    monotonic_values = iter((100.0, 100.0))
    monkeypatch.setattr(
        "job_auto_agent.application.assist.time.monotonic",
        lambda: next(monotonic_values),
    )

    assert _linkedin_login_required(page)
    _wait_for_linkedin_login(page, review_deadline=101.0)

    assert page.waits == [1000]
    assert not list(tmp_path.iterdir())


def _write_profile(tmp_path: Path) -> Path:
    profile_path = tmp_path / "application_profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "personal_info": {
                    "first_name": "Sridath",
                    "last_name": "Jeelugula",
                    "email": "sridath@example.com",
                    "phone": "+1 555 123 4567",
                    "address": "123 Example Street",
                    "city": "Montreal",
                    "province_state": "QC",
                    "country": "Canada",
                    "postal_code": "H1H 1H1",
                    "linkedin_url": "https://linkedin.com/in/sridath",
                    "github_url": "https://github.com/sridath",
                    "portfolio_url": "https://sridath.example.com",
                },
                "work_authorization": {
                    "work_authorization": "Authorized to work in Canada",
                    "sponsorship_required": False,
                    "legally_authorized_to_work": True,
                    "willing_to_relocate": False,
                    "remote_preference": "Remote",
                    "notice_period": "2 weeks",
                    "salary_expectation": "Prefer to discuss",
                },
                "education": {
                    "degree": "Master of Engineering",
                    "school": "Concordia University",
                    "location": "Montreal, QC",
                    "graduation_year": "2019",
                },
                "work_experience": [
                    {
                        "company": "Morgan Stanley",
                        "title": "Site Reliability Engineer",
                        "location": "Montreal, QC",
                        "start_date": "2024-12",
                        "end_date": "",
                        "is_current": True,
                        "responsibilities": [
                            "Operated Kubernetes platforms.",
                            "Supported PKI and Vault workflows.",
                        ],
                    }
                ],
                "skills": {
                    "technical_skills": ["Python"],
                    "cloud_skills": ["Azure", "AWS"],
                    "security_skills": ["PKI", "Vault"],
                    "devops_skills": ["Terraform", "GitHub Actions"],
                },
                "screening_answers": [
                    {
                        "question": "Are you legally authorized to work in Canada?",
                        "answer": "Yes",
                        "confidence": 1.0,
                        "allow_auto_fill": True,
                    },
                    {
                        "question": "What is your expected salary?",
                        "answer": "Prefer to discuss",
                        "confidence": 0.9,
                        "allow_auto_fill": False,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return profile_path


def _write_application_package(job_id: int, output_root: Path) -> None:
    paths = application_paths(job_id, output_root)
    paths.folder.mkdir(parents=True, exist_ok=True)
    paths.resume_docx.write_bytes(b"resume")
    paths.cover_letter_docx.write_bytes(b"cover")


def _seed_job(db_path: Path) -> int:
    job = JobOpportunity(
        source_message_id="message-1",
        company="ExampleCo",
        title="Platform Engineer",
        location="Remote",
        source="Lever",
        url="https://jobs.lever.co/example/123",
        description="Kubernetes platform role.",
        received_at=datetime.now(timezone.utc),
    )
    with connect(db_path) as conn:
        return save_job(conn, job)
