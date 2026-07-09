from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from job_auto_agent.application.assist import (
    ApplicationPackageMissingError,
    AssistApplyError,
    JobApplyUrlMissingError,
    PlaywrightUnavailableError,
)
from job_auto_agent.application.profile import (
    ApplicationProfileError,
    DEFAULT_APPLICATION_PROFILE_PATH,
)
from job_auto_agent.application.workflow import (
    DEFAULT_APPLICATION_OUTPUT_DIR,
    application_paths,
)


@dataclass(frozen=True)
class AssistApplyReadiness:
    profile_exists: bool
    package_exists: bool
    profile_path: Path
    package_path: Path

    @property
    def ready(self) -> bool:
        return self.profile_exists and self.package_exists


def assist_apply_readiness(
    job_id: int,
    profile_path: Path = DEFAULT_APPLICATION_PROFILE_PATH,
    output_root: Path = DEFAULT_APPLICATION_OUTPUT_DIR,
) -> AssistApplyReadiness:
    paths = application_paths(job_id, output_root)
    return AssistApplyReadiness(
        profile_exists=profile_path.exists(),
        package_exists=paths.resume_docx.exists() and paths.cover_letter_docx.exists(),
        profile_path=profile_path,
        package_path=paths.folder,
    )


def assist_apply_error_message(error: Exception) -> str:
    if isinstance(error, PlaywrightUnavailableError):
        return (
            "Playwright or its browser is unavailable. Install project dependencies, "
            "then run `playwright install chromium`."
        )
    if isinstance(error, JobApplyUrlMissingError):
        return "This job does not have a valid application URL."
    if isinstance(error, ApplicationPackageMissingError):
        return "Prepare Application first."
    if isinstance(error, ApplicationProfileError):
        return "Create application profile first."
    if isinstance(error, AssistApplyError):
        text = str(error)
        if "blocked" in text.lower():
            return f"Application site automation was blocked. Continue manually. Details: {text}"
        return text
    return str(error)


def is_valid_application_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
