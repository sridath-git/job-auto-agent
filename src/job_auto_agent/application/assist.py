from __future__ import annotations

import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from job_auto_agent.application.profile import (
    ApplicationProfile,
    ApplicationProfileMissingError,
    DEFAULT_APPLICATION_PROFILE_PATH,
    load_application_profile,
    screening_answer_for_question,
    work_experience_responsibilities,
)
from job_auto_agent.application.workflow import (
    DEFAULT_APPLICATION_OUTPUT_DIR,
    ApplicationPaths,
    application_paths,
)
from job_auto_agent.storage.repository import update_job_status


class AssistApplyError(Exception):
    """Base error for browser-assisted apply failures."""


class ApplicationPackageMissingError(AssistApplyError):
    """Raised when the prepared application package is missing."""


class JobApplyUrlMissingError(AssistApplyError):
    """Raised when a saved job has no application URL."""


class PlaywrightUnavailableError(AssistApplyError):
    """Raised when Playwright is not installed or browsers are unavailable."""


@dataclass(frozen=True)
class AssistApplyResult:
    job_id: int
    ats_type: str
    status: str
    message: str


BrowserDriver = Callable[[sqlite3.Row, ApplicationProfile, ApplicationPaths], str]
DEFAULT_REVIEW_WAIT_SECONDS = 300


def assist_apply_for_job(
    conn: sqlite3.Connection,
    job_id: int,
    profile_path: Path = DEFAULT_APPLICATION_PROFILE_PATH,
    output_root: Path = DEFAULT_APPLICATION_OUTPUT_DIR,
    browser_driver: BrowserDriver | None = None,
    review_wait_seconds: int | None = None,
) -> AssistApplyResult:
    job = _get_job(conn, job_id)
    if job is None:
        raise AssistApplyError(f"Job ID {job_id} was not found.")
    if not job["url"]:
        raise JobApplyUrlMissingError("Saved job has no URL to open.")

    paths = application_paths(job_id, output_root)
    _ensure_application_package(paths)
    try:
        profile = load_application_profile(profile_path)
    except ApplicationProfileMissingError as exc:
        raise ApplicationProfileMissingError(
            f"{exc} Create application_profile.json first."
        ) from exc

    update_job_status(conn, job_id, "Application Started")
    conn.commit()

    if browser_driver is None:
        ats_type = _run_playwright_assist(
            job,
            profile,
            paths,
            review_wait_seconds=review_wait_seconds,
        )
    else:
        ats_type = browser_driver(job, profile, paths)

    update_job_status(conn, job_id, "Needs Review")
    conn.commit()
    return AssistApplyResult(
        job_id=job_id,
        ats_type=ats_type,
        status="Needs Review",
        message="Review the application manually before submitting.",
    )


def detect_ats_type(url: str | None) -> str:
    if not url:
        return "Generic"
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if "workday" in host or "myworkdayjobs" in host:
        return "Workday"
    if "greenhouse" in host or "greenhouse.io" in host:
        return "Greenhouse"
    if "lever" in host or "lever.co" in host:
        return "Lever"
    if "linkedin" in host and ("easyapply" in path or "jobs" in path):
        return "LinkedIn Easy Apply"
    return "Generic"


def field_value_for_identifier(identifier: str, profile: ApplicationProfile) -> str | None:
    normalized = _normalize_identifier(identifier)
    personal = profile.personal_info
    authorization = profile.work_authorization
    education = profile.education
    mapping = (
        (("first", "name"), personal.get("first_name")),
        (("last", "name"), personal.get("last_name")),
        (("email",), personal.get("email")),
        (("phone",), personal.get("phone")),
        (("address",), personal.get("address")),
        (("city",), personal.get("city")),
        (("province",), personal.get("province_state")),
        (("state",), personal.get("province_state")),
        (("country",), personal.get("country")),
        (("postal",), personal.get("postal_code")),
        (("zip",), personal.get("postal_code")),
        (("linkedin",), personal.get("linkedin_url")),
        (("github",), personal.get("github_url")),
        (("portfolio",), personal.get("portfolio_url")),
        (("work", "authorization"), authorization.get("work_authorization")),
        (("sponsor",), _bool_text(authorization.get("sponsorship_required"))),
        (("legally", "authorized"), _bool_text(authorization.get("legally_authorized_to_work"))),
        (("relocate",), _bool_text(authorization.get("willing_to_relocate"))),
        (("remote",), authorization.get("remote_preference")),
        (("notice",), authorization.get("notice_period")),
        (("salary",), authorization.get("salary_expectation")),
        (("degree",), education.get("degree")),
        (("school",), education.get("school")),
        (("university",), education.get("school")),
        (("graduation",), education.get("graduation_year")),
    )
    for terms, value in mapping:
        if all(term in normalized for term in terms):
            text = str(value).strip() if value is not None else ""
            return text or None
    return None


def is_final_submit_label(label: str) -> bool:
    normalized = _normalize_identifier(label)
    final_terms = (
        "submit application",
        "submit",
        "final submit",
        "send application",
        "apply now",
        "complete application",
    )
    return any(term in normalized for term in final_terms)


def fill_application_page(page: Any, profile: ApplicationProfile, paths: ApplicationPaths) -> None:
    _fill_common_fields(page, profile)
    _fill_responsibilities(page, profile)
    _fill_screening_answers(page, profile)
    _upload_documents(page, paths)


def _run_playwright_assist(
    job: sqlite3.Row,
    profile: ApplicationProfile,
    paths: ApplicationPaths,
    review_wait_seconds: int | None = None,
) -> str:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PlaywrightUnavailableError(
            "Playwright is not installed. Install project dependencies and run `playwright install`."
        ) from exc

    ats_type = detect_ats_type(job["url"])
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()
            page.goto(job["url"], wait_until="domcontentloaded")
            wait_seconds = (
                review_wait_seconds
                if review_wait_seconds is not None
                else _review_wait_seconds()
            )
            inspector_enabled = _debug_inspector_enabled()
            review_deadline = None if inspector_enabled else time.monotonic() + wait_seconds
            if ats_type == "LinkedIn Easy Apply" and _linkedin_login_required(page):
                print("Please login manually and continue in the browser.", flush=True)
                _wait_for_linkedin_login(
                    page,
                    review_deadline,
                    inspector_enabled=inspector_enabled,
                )
            linkedin_login_pending = (
                ats_type == "LinkedIn Easy Apply" and _linkedin_login_required(page)
            )
            if not linkedin_login_pending:
                fill_application_page(page, profile, paths)
            else:
                print("Please login manually and continue in the browser.", flush=True)
            _warn_if_final_buttons_present(page)
            print(
                "Browser is open. Review the application manually and submit "
                "if everything looks correct.",
                flush=True,
            )
            _wait_for_manual_review(
                page,
                review_deadline,
                inspector_enabled=inspector_enabled,
            )
            browser.close()
    except PlaywrightError as exc:
        raise AssistApplyError(
            f"Browser-assisted apply stopped because the site blocked or failed automation: {exc}"
        ) from exc
    return ats_type


def _fill_common_fields(page: Any, profile: ApplicationProfile) -> None:
    labels = (
        "First Name",
        "Last Name",
        "Email",
        "Phone",
        "Address",
        "City",
        "State",
        "Province",
        "Country",
        "Postal Code",
        "ZIP",
        "LinkedIn",
        "GitHub",
        "Portfolio",
        "School",
        "University",
        "Degree",
        "Graduation Year",
        "Work Authorization",
        "Sponsorship",
        "Notice Period",
        "Salary",
    )
    for label in labels:
        value = field_value_for_identifier(label, profile)
        if value:
            _try_fill_by_label(page, label, value)


def _fill_responsibilities(page: Any, profile: ApplicationProfile) -> None:
    responsibilities = work_experience_responsibilities(profile)
    if not responsibilities:
        return
    value = "\n".join(f"- {responsibility}" for responsibility in responsibilities)
    for label in ("Responsibilities", "Work Experience", "Experience Summary"):
        _try_fill_by_label(page, label, value)


def _fill_screening_answers(page: Any, profile: ApplicationProfile) -> None:
    for answer in profile.screening_answers:
        question = str(answer.get("question", "")).strip()
        value = screening_answer_for_question(profile, question)
        if question and value:
            _try_fill_by_label(page, question, value)


def _upload_documents(page: Any, paths: ApplicationPaths) -> None:
    upload_targets = (
        ("resume", paths.resume_docx),
        ("cover", paths.cover_letter_docx),
        ("cover letter", paths.cover_letter_docx),
    )
    for label, path in upload_targets:
        if not path.exists():
            continue
        for selector in (
            f'input[type="file"][name*="{label}" i]',
            f'input[type="file"][aria-label*="{label}" i]',
            f'input[type="file"][id*="{label}" i]',
        ):
            try:
                locator = page.locator(selector)
                if locator.count():
                    locator.first.set_input_files(str(path))
                    break
            except Exception:
                continue


def _warn_if_final_buttons_present(page: Any) -> None:
    try:
        buttons = page.locator("button, input[type=submit], a")
        for index in range(min(buttons.count(), 100)):
            button = buttons.nth(index)
            label = " ".join(
                value
                for value in (
                    button.inner_text(timeout=250) if hasattr(button, "inner_text") else "",
                    button.get_attribute("value") or "",
                    button.get_attribute("aria-label") or "",
                )
                if value
            )
            if is_final_submit_label(label):
                print("Final Submit/Apply button detected. Stopping for manual review.")
                return
    except Exception:
        return


def _try_fill_by_label(page: Any, label: str, value: str) -> bool:
    try:
        locator = page.get_by_label(label)
        if locator.count():
            locator.first.fill(value)
            return True
    except Exception:
        return False
    return False


def _ensure_application_package(paths: ApplicationPaths) -> None:
    missing = [
        path.name
        for path in (paths.resume_docx, paths.cover_letter_docx)
        if not path.exists()
    ]
    if missing:
        raise ApplicationPackageMissingError(
            f"Run prepare-application first. Missing file(s): {', '.join(missing)}."
        )


def _get_job(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM job_opportunities WHERE id = ?", (job_id,)).fetchone()


def _normalize_identifier(identifier: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", identifier.lower()).strip()


def _bool_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


def _review_wait_seconds() -> int:
    raw_value = os.getenv("JOB_AUTO_AGENT_ASSIST_REVIEW_SECONDS")
    if raw_value is None:
        return DEFAULT_REVIEW_WAIT_SECONDS
    try:
        return max(0, int(raw_value))
    except ValueError:
        return DEFAULT_REVIEW_WAIT_SECONDS


def _debug_inspector_enabled() -> bool:
    return any(_truthy_env_value(os.getenv(name)) for name in ("DEBUG", "PWDEBUG"))


def _truthy_env_value(value: str | None) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower()
    return bool(normalized) and normalized not in {"0", "false", "no", "off"}


def _linkedin_login_required(page: Any) -> bool:
    try:
        parsed = urlparse(page.url)
        login_path = parsed.path.lower()
        if any(marker in login_path for marker in ("/login", "/checkpoint", "/uas/")):
            return True
        return bool(page.locator('input[type="password"]').count())
    except Exception:
        return False


def _wait_for_linkedin_login(
    page: Any,
    review_deadline: float | None,
    inspector_enabled: bool = False,
) -> None:
    if inspector_enabled:
        page.pause()
        return
    if review_deadline is None:
        return
    while _linkedin_login_required(page):
        remaining_seconds = review_deadline - time.monotonic()
        if remaining_seconds <= 0:
            return
        page.wait_for_timeout(round(min(1.0, remaining_seconds) * 1000))


def _wait_for_manual_review(
    page: Any,
    review_deadline: float | None,
    inspector_enabled: bool = False,
) -> None:
    if inspector_enabled:
        page.pause()
        return
    if review_deadline is None:
        return
    remaining_seconds = max(0, review_deadline - time.monotonic())
    page.wait_for_timeout(round(remaining_seconds * 1000))
