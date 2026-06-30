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


def test_validate_setup_warns_when_application_profile_missing(tmp_path, monkeypatch) -> None:
    credentials_file = tmp_path / "credentials.json"
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
        gmail_token_file=tmp_path / "token.json",
        database_url=f"sqlite:///{tmp_path / 'job_auto_agent.db'}",
        gmail_query="newer_than:30d",
        match_min_score=35,
    )
    monkeypatch.chdir(tmp_path)

    checks = validate_setup(settings)
    profile_check = next(check for check in checks if check.name == "Application profile")

    assert profile_check.ok
    assert "Warning:" in profile_check.message


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


def test_validate_setup_allows_localhost_ai_provider_with_dummy_key(tmp_path) -> None:
    settings = Settings(
        gmail_credentials_file=tmp_path / "missing.json",
        gmail_token_file=tmp_path / "token.json",
        database_url=f"sqlite:///{tmp_path / 'job_auto_agent.db'}",
        gmail_query="newer_than:30d",
        match_min_score=35,
        openai_api_key="ollama",
        openai_base_url="http://localhost:11434/v1",
        openai_model="qwen2.5:7b",
        ai_tailoring_enabled=True,
    )

    checks = validate_setup(settings)
    ai_check = next(check for check in checks if check.name == "AI provider")

    assert ai_check.ok
    assert "Local OpenAI-compatible provider" in ai_check.message


def test_validate_setup_requires_api_key_for_openai_cloud(tmp_path) -> None:
    settings = Settings(
        gmail_credentials_file=tmp_path / "missing.json",
        gmail_token_file=tmp_path / "token.json",
        database_url=f"sqlite:///{tmp_path / 'job_auto_agent.db'}",
        gmail_query="newer_than:30d",
        match_min_score=35,
        openai_api_key=None,
        openai_base_url="https://api.openai.com/v1",
        ai_tailoring_enabled=True,
    )

    checks = validate_setup(settings)
    ai_check = next(check for check in checks if check.name == "AI provider")

    assert not ai_check.ok
    assert "OPENAI_API_KEY is required" in ai_check.message


def test_validate_setup_rejects_ollama_dummy_key_for_openai_cloud(tmp_path) -> None:
    settings = Settings(
        gmail_credentials_file=tmp_path / "missing.json",
        gmail_token_file=tmp_path / "token.json",
        database_url=f"sqlite:///{tmp_path / 'job_auto_agent.db'}",
        gmail_query="newer_than:30d",
        match_min_score=35,
        openai_api_key="ollama",
        openai_base_url="https://api.openai.com/v1",
        ai_tailoring_enabled=True,
    )

    checks = validate_setup(settings)
    ai_check = next(check for check in checks if check.name == "AI provider")

    assert not ai_check.ok
    assert "real OpenAI API key" in ai_check.message
