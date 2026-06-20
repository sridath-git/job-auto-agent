from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    gmail_credentials_file: Path
    gmail_token_file: Path
    database_url: str
    gmail_query: str
    match_min_score: int
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    ai_tailoring_enabled: bool = False

    @property
    def sqlite_path(self) -> Path:
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            raise ValueError("Only sqlite:/// DATABASE_URL values are supported.")
        return Path(self.database_url.removeprefix(prefix))


def get_settings() -> Settings:
    return Settings(
        gmail_credentials_file=Path(os.getenv("GMAIL_CREDENTIALS_FILE", "credentials.json")),
        gmail_token_file=Path(os.getenv("GMAIL_TOKEN_FILE", "token.json")),
        database_url=os.getenv("DATABASE_URL", "sqlite:///data/job_auto_agent.db"),
        gmail_query=os.getenv(
            "GMAIL_QUERY",
            "(from:(linkedin.com OR indeed.com OR greenhouse.io OR lever.co OR workday.com) "
            "OR subject:(job OR recruiter OR opportunity OR interview)) newer_than:90d",
        ),
        match_min_score=int(os.getenv("MATCH_MIN_SCORE", "35")),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        ai_tailoring_enabled=os.getenv("AI_TAILORING_ENABLED", "false").lower() == "true",
    )
