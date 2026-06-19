from __future__ import annotations

import argparse

from job_auto_agent.config import get_settings
from job_auto_agent.extraction.pipeline import extract_job
from job_auto_agent.gmail.client import GmailClient
from job_auto_agent.matching.engine import score_job
from job_auto_agent.storage.database import connect, init_db
from job_auto_agent.storage.repository import list_jobs, save_email, save_job, save_match
from job_auto_agent.validation import validate_setup


def main() -> None:
    parser = argparse.ArgumentParser(description="Gmail-based job application assistant.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Create or update SQLite tables.")

    sync_parser = subparsers.add_parser("sync-gmail", help="Fetch and extract job emails.")
    sync_parser.add_argument("--limit", type=int, default=50)

    subparsers.add_parser("score-jobs", help="Score stored job opportunities.")
    subparsers.add_parser("validate-setup", help="Validate local config before Gmail sync.")

    args = parser.parse_args()
    settings = get_settings()

    if args.command == "init-db":
        init_db(settings.sqlite_path)
        print(f"Initialized database at {settings.sqlite_path}")
        return

    if args.command == "sync-gmail":
        init_db(settings.sqlite_path)
        client = GmailClient(settings.gmail_credentials_file, settings.gmail_token_file)
        messages = client.search_messages(settings.gmail_query, limit=args.limit)
        with connect(settings.sqlite_path) as conn:
            for message in messages:
                save_email(conn, message)
                job = extract_job(message)
                if job:
                    save_job(conn, job)
        print(f"Synced {len(messages)} Gmail messages.")
        return

    if args.command == "score-jobs":
        init_db(settings.sqlite_path)
        with connect(settings.sqlite_path) as conn:
            jobs = list_jobs(conn)
            for job in jobs:
                result = score_job(job["id"], job["title"], job["description"])
                save_match(conn, result)
        print(f"Scored {len(jobs)} job opportunities.")
        return

    if args.command == "validate-setup":
        checks = validate_setup(settings)
        for check in checks:
            status = "OK" if check.ok else "FAIL"
            print(f"[{status}] {check.name}: {check.message}")
        failures = [check for check in checks if not check.ok]
        if failures:
            raise SystemExit(1)
