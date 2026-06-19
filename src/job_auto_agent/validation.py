from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from job_auto_agent.config import Settings
from job_auto_agent.gmail.auth import SCOPES


@dataclass(frozen=True)
class ValidationCheck:
    name: str
    ok: bool
    message: str


def validate_setup(settings: Settings) -> list[ValidationCheck]:
    checks = [
        _check_database_url(settings),
        _check_sqlite_directory(settings),
        _check_gmail_query(settings),
        _check_credentials_file(settings.gmail_credentials_file),
        _check_token_file(settings.gmail_token_file),
    ]
    return checks


def _check_database_url(settings: Settings) -> ValidationCheck:
    try:
        settings.sqlite_path
    except ValueError as exc:
        return ValidationCheck("DATABASE_URL", False, str(exc))
    return ValidationCheck("DATABASE_URL", True, f"SQLite database path: {settings.sqlite_path}")


def _check_sqlite_directory(settings: Settings) -> ValidationCheck:
    try:
        db_path = settings.sqlite_path
    except ValueError as exc:
        return ValidationCheck("SQLite directory", False, str(exc))

    parent = db_path.parent
    if parent.exists() and not parent.is_dir():
        return ValidationCheck("SQLite directory", False, f"{parent} exists but is not a directory.")
    if parent.exists():
        return ValidationCheck("SQLite directory", True, f"{parent} exists.")
    return ValidationCheck(
        "SQLite directory",
        True,
        f"{parent} does not exist yet; the app will create it during init-db.",
    )


def _check_gmail_query(settings: Settings) -> ValidationCheck:
    if not settings.gmail_query.strip():
        return ValidationCheck("GMAIL_QUERY", False, "GMAIL_QUERY must not be empty.")
    return ValidationCheck("GMAIL_QUERY", True, "Gmail search query is configured.")


def _check_credentials_file(credentials_file: Path) -> ValidationCheck:
    if not credentials_file.exists():
        return ValidationCheck(
            "Gmail credentials",
            False,
            f"Missing {credentials_file}. Download an OAuth desktop client JSON from Google Cloud.",
        )

    try:
        payload = json.loads(credentials_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return ValidationCheck("Gmail credentials", False, f"Invalid JSON: {exc}")

    client_config = payload.get("installed") or payload.get("web")
    if not isinstance(client_config, dict):
        return ValidationCheck(
            "Gmail credentials",
            False,
            "Expected Google OAuth client JSON with an 'installed' desktop app section.",
        )

    missing = [
        key
        for key in ("client_id", "client_secret", "auth_uri", "token_uri")
        if not client_config.get(key)
    ]
    if missing:
        return ValidationCheck(
            "Gmail credentials",
            False,
            f"OAuth client JSON is missing required field(s): {', '.join(missing)}.",
        )

    if "installed" not in payload:
        return ValidationCheck(
            "Gmail credentials",
            False,
            "Use a Desktop app OAuth client so the JSON contains an 'installed' section.",
        )

    return ValidationCheck(
        "Gmail credentials",
        True,
        f"Found desktop OAuth client JSON at {credentials_file}.",
    )


def _check_token_file(token_file: Path) -> ValidationCheck:
    if not token_file.exists():
        return ValidationCheck(
            "Gmail token",
            True,
            f"{token_file} not found yet; it will be created after the first OAuth login.",
        )

    try:
        payload = json.loads(token_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return ValidationCheck("Gmail token", False, f"Invalid token JSON: {exc}")

    scopes = set(payload.get("scopes") or [])
    missing_scopes = [scope for scope in SCOPES if scope not in scopes]
    if missing_scopes:
        return ValidationCheck(
            "Gmail token",
            False,
            f"Existing token is missing required scope(s): {', '.join(missing_scopes)}.",
        )

    return ValidationCheck("Gmail token", True, f"Existing token at {token_file} has Gmail scope.")
