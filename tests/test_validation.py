import json

from job_auto_agent.config import Settings
from job_auto_agent.validation import validate_setup


def test_validate_setup_passes_with_desktop_credentials(tmp_path) -> None:
    credentials_file = tmp_path / "credentials.json"
    token_file = tmp_path / "token.json"
    credentials_file.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "client-id",
                    "client_secret": "client-secret",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            }
        ),
        encoding="utf-8",
    )

    settings = Settings(
        gmail_credentials_file=credentials_file,
        gmail_token_file=token_file,
        database_url=f"sqlite:///{tmp_path / 'job_auto_agent.db'}",
        gmail_query="newer_than:30d",
        match_min_score=35,
    )

    checks = validate_setup(settings)

    assert all(check.ok for check in checks)


def test_validate_setup_fails_without_credentials(tmp_path) -> None:
    settings = Settings(
        gmail_credentials_file=tmp_path / "missing.json",
        gmail_token_file=tmp_path / "token.json",
        database_url=f"sqlite:///{tmp_path / 'job_auto_agent.db'}",
        gmail_query="newer_than:30d",
        match_min_score=35,
    )

    checks = validate_setup(settings)

    assert any(check.name == "Gmail credentials" and not check.ok for check in checks)


def test_validate_setup_rejects_web_oauth_client(tmp_path) -> None:
    credentials_file = tmp_path / "credentials.json"
    credentials_file.write_text(
        json.dumps(
            {
                "web": {
                    "client_id": "client-id",
                    "client_secret": "client-secret",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            }
        ),
        encoding="utf-8",
    )

    settings = Settings(
        gmail_credentials_file=credentials_file,
        gmail_token_file=tmp_path / "token.json",
        database_url=f"sqlite:///{tmp_path / 'job_auto_agent.db'}",
        gmail_query="newer_than:30d",
        match_min_score=35,
    )

    checks = validate_setup(settings)

    assert any(
        check.name == "Gmail credentials"
        and not check.ok
        and "Desktop app" in check.message
        for check in checks
    )
