from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_APPLICATION_PROFILE_EXAMPLE_PATH = Path("data/profile/application_profile.example.json")
DEFAULT_APPLICATION_PROFILE_PATH = Path("data/profile/application_profile.json")

REQUIRED_PERSONAL_FIELDS = ("first_name", "last_name", "email", "phone")


class ApplicationProfileError(Exception):
    """Base error for application profile handling."""


class ApplicationProfileMissingError(ApplicationProfileError):
    """Raised when application_profile.json is missing."""


class ApplicationProfileInvalidError(ApplicationProfileError):
    """Raised when application_profile.json cannot be parsed."""


@dataclass(frozen=True)
class ApplicationProfile:
    raw: dict[str, Any]

    @property
    def personal_info(self) -> dict[str, Any]:
        return _dict_value(self.raw, "personal_info")

    @property
    def work_authorization(self) -> dict[str, Any]:
        return _dict_value(self.raw, "work_authorization")

    @property
    def education(self) -> dict[str, Any]:
        return _dict_value(self.raw, "education")

    @property
    def work_experience(self) -> list[dict[str, Any]]:
        return _list_of_dicts(self.raw.get("work_experience"))

    @property
    def skills(self) -> dict[str, Any]:
        return _dict_value(self.raw, "skills")

    @property
    def screening_answers(self) -> list[dict[str, Any]]:
        return _list_of_dicts(self.raw.get("screening_answers"))


def init_application_profile(
    profile_path: Path = DEFAULT_APPLICATION_PROFILE_PATH,
    example_path: Path = DEFAULT_APPLICATION_PROFILE_EXAMPLE_PATH,
) -> Path:
    if profile_path.exists():
        return profile_path
    if not example_path.exists():
        raise ApplicationProfileMissingError(f"Missing application profile example at {example_path}.")
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(example_path, profile_path)
    return profile_path


def load_application_profile(profile_path: Path = DEFAULT_APPLICATION_PROFILE_PATH) -> ApplicationProfile:
    if not profile_path.exists():
        raise ApplicationProfileMissingError(
            f"Missing application profile at {profile_path}. Run job-auto-agent init-application-profile."
        )
    try:
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ApplicationProfileInvalidError(f"Invalid application profile JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ApplicationProfileInvalidError("Application profile must be a JSON object.")
    return ApplicationProfile(raw=payload)


def validate_application_profile(profile: ApplicationProfile) -> list[str]:
    warnings: list[str] = []
    personal = profile.personal_info
    missing_personal = [field for field in REQUIRED_PERSONAL_FIELDS if not str(personal.get(field, "")).strip()]
    if missing_personal:
        warnings.append("Application profile missing personal field(s): " + ", ".join(missing_personal))
    if not profile.work_experience:
        warnings.append("Application profile has no work experience entries.")
    if not profile.education:
        warnings.append("Application profile has no education details.")
    return warnings


def screening_answer_for_question(
    profile: ApplicationProfile,
    question: str,
    min_confidence: float = 0.8,
) -> str | None:
    normalized_question = _normalize(question)
    if not normalized_question:
        return None
    for answer in profile.screening_answers:
        if not answer.get("allow_auto_fill"):
            continue
        try:
            confidence = float(answer.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0
        if confidence < min_confidence:
            continue
        if _normalize(str(answer.get("question", ""))) == normalized_question:
            value = str(answer.get("answer", "")).strip()
            return value or None
    return None


def work_experience_responsibilities(profile: ApplicationProfile) -> list[str]:
    responsibilities: list[str] = []
    for experience in profile.work_experience:
        raw_responsibilities = experience.get("responsibilities") or []
        if isinstance(raw_responsibilities, str):
            raw_responsibilities = [raw_responsibilities]
        for responsibility in raw_responsibilities:
            text = str(responsibility).strip()
            if text:
                responsibilities.append(text)
    return responsibilities


def _dict_value(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return value if isinstance(value, dict) else {}


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())

