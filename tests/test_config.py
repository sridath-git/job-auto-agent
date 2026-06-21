from job_auto_agent.config import default_ai_provider_timeout_seconds, get_settings


def test_default_ai_provider_timeout_is_60_seconds_for_cloud_openai() -> None:
    assert default_ai_provider_timeout_seconds("https://api.openai.com/v1") == 60


def test_default_ai_provider_timeout_is_300_seconds_for_localhost_ollama() -> None:
    assert default_ai_provider_timeout_seconds("http://localhost:11434/v1") == 300
    assert default_ai_provider_timeout_seconds("http://127.0.0.1:11434/v1") == 300


def test_get_settings_uses_ai_provider_timeout_override(monkeypatch) -> None:
    monkeypatch.setenv("AI_PROVIDER_TIMEOUT_SECONDS", "120")

    settings = get_settings()

    assert settings.ai_provider_timeout_seconds == 120


def test_get_settings_uses_localhost_timeout_default(monkeypatch) -> None:
    monkeypatch.delenv("AI_PROVIDER_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")

    settings = get_settings()

    assert settings.ai_provider_timeout_seconds == 300


def test_get_settings_treats_blank_timeout_as_default(monkeypatch) -> None:
    monkeypatch.setenv("AI_PROVIDER_TIMEOUT_SECONDS", "")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    settings = get_settings()

    assert settings.ai_provider_timeout_seconds == 60
