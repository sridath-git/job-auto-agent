# Job Auto Agent

Gmail-based job application assistant for DevSecOps, AppSec, and PKI roles.

This starter project reads job-related email from Gmail, extracts opportunity details, stores them in SQLite, scores them against security-focused role preferences, and shows the results in a Streamlit dashboard.

Auto-apply is intentionally not implemented yet.

## Features

- Gmail OAuth integration using Google’s installed-app flow.
- Gmail message search and parsing pipeline.
- Job opportunity extraction from email subject/body text.
- SQLite persistence for messages, jobs, and match scores.
- Matching engine tuned for DevSecOps, Application Security, Cloud Security, PKI, IAM, and related roles.
- Streamlit dashboard for reviewing saved opportunities and scores.

## Setup

1. Create and activate a virtual environment.

```bash
python -m venv .venv
source .venv/bin/activate
```

2. Install the project.

```bash
pip install -e ".[dev]"
```

3. Create environment config.

```bash
cp .env.example .env
```

4. Create a Google OAuth desktop client in Google Cloud Console, download it as `credentials.json`, and place it in the repository root.

Required OAuth scope:

```text
https://www.googleapis.com/auth/gmail.readonly
```

5. Initialize the database and sync Gmail.

```bash
job-auto-agent init-db
job-auto-agent sync-gmail
```

6. Run the dashboard.

```bash
streamlit run dashboard/app.py
```

## Configuration

Environment variables are documented in `.env.example`.

- `GMAIL_CREDENTIALS_FILE`: OAuth client JSON downloaded from Google Cloud.
- `GMAIL_TOKEN_FILE`: Local OAuth token cache generated after login.
- `DATABASE_URL`: SQLite database URL. Defaults to `sqlite:///data/job_auto_agent.db`.
- `GMAIL_QUERY`: Gmail search query used during sync.
- `MATCH_MIN_SCORE`: Minimum score shown as a strong match in the dashboard.

## Commands

```bash
job-auto-agent init-db
job-auto-agent sync-gmail --limit 25
job-auto-agent score-jobs
```

## Project Structure

```text
dashboard/                  Streamlit UI
src/job_auto_agent/
  cli.py                    Command-line entrypoint
  config.py                 Environment-driven settings
  gmail/                    Gmail OAuth and message fetchers
  extraction/               Job extraction pipeline
  matching/                 DevSecOps/AppSec/PKI scoring
  storage/                  SQLite schema and repository
tests/                      Focused unit tests
```
