from __future__ import annotations

import base64
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from bs4 import BeautifulSoup
from googleapiclient.discovery import build

from job_auto_agent.gmail.auth import get_credentials
from job_auto_agent.models import EmailMessage


class GmailClient:
    def __init__(self, credentials_file, token_file) -> None:
        creds = get_credentials(credentials_file, token_file)
        self.service = build("gmail", "v1", credentials=creds)

    def search_messages(self, query: str, limit: int = 50) -> list[EmailMessage]:
        response = (
            self.service.users()
            .messages()
            .list(userId="me", q=query, maxResults=limit)
            .execute()
        )
        messages = response.get("messages", [])
        return [self.get_message(item["id"]) for item in messages]

    def get_message(self, message_id: str) -> EmailMessage:
        raw = (
            self.service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
        headers = {
            header["name"].lower(): header["value"]
            for header in raw.get("payload", {}).get("headers", [])
        }
        return EmailMessage(
            gmail_id=raw["id"],
            thread_id=raw.get("threadId", ""),
            sender=headers.get("from", ""),
            subject=headers.get("subject", ""),
            snippet=raw.get("snippet", ""),
            body_text=_extract_body_text(raw.get("payload", {})),
            received_at=_parse_received_at(headers.get("date")),
        )


def _parse_received_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _extract_body_text(payload: dict[str, Any]) -> str:
    chunks: list[str] = []
    _walk_parts(payload, chunks)
    return "\n".join(chunk.strip() for chunk in chunks if chunk.strip())


def _walk_parts(part: dict[str, Any], chunks: list[str]) -> None:
    mime_type = part.get("mimeType", "")
    body_data = part.get("body", {}).get("data")

    if body_data and mime_type in {"text/plain", "text/html"}:
        decoded = base64.urlsafe_b64decode(body_data.encode("utf-8")).decode(
            "utf-8",
            errors="replace",
        )
        if mime_type == "text/html":
            decoded = BeautifulSoup(decoded, "html.parser").get_text(" ", strip=True)
        chunks.append(decoded)

    for child in part.get("parts", []):
        _walk_parts(child, chunks)
