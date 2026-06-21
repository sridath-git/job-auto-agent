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

Prerequisite: Python 3.11 or newer.

1. Create and activate a virtual environment.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

If your machine has a newer Python, such as `python3.13`, use that instead.

2. Upgrade pip and install the project.

```bash
python -m pip install --upgrade pip
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

5. Validate the local setup.

```bash
job-auto-agent validate-setup
```

6. Initialize the database and sync Gmail.

```bash
job-auto-agent init-db
job-auto-agent sync-gmail
```

7. Run the dashboard.

```bash
streamlit run dashboard/app.py
```

## Gmail OAuth Setup

The app uses Google OAuth for installed desktop apps and requests read-only Gmail access. It never needs your Gmail password and it does not request send, modify, or delete permissions.

### 1. Create or Select a Google Cloud Project

1. Open the Google Cloud Console.
2. Create a new project, or select an existing project dedicated to this assistant.
3. Confirm the selected project in the top navigation before enabling APIs or creating credentials.

### 2. Enable the Gmail API

1. Go to **APIs & Services** > **Library**.
2. Search for **Gmail API**.
3. Open the Gmail API result and click **Enable**.

### 3. Configure the OAuth Consent Screen

1. Go to **APIs & Services** > **OAuth consent screen**.
2. Choose **External** unless you are using a Google Workspace internal app.
3. Fill in the required app name, user support email, and developer contact email.
4. Add yourself as a test user while the app is in testing mode.
5. Add this scope:

```text
https://www.googleapis.com/auth/gmail.readonly
```

Testing mode is fine for local personal use. If Google shows an unverified-app warning during login, continue only if the project and OAuth client are yours.

### 4. Create OAuth Credentials

1. Go to **APIs & Services** > **Credentials**.
2. Click **Create credentials** > **OAuth client ID**.
3. Select **Desktop app** as the application type.
4. Name it something like `Job Auto Agent Local`.
5. Download the JSON file.
6. Rename it to `credentials.json`.
7. Place it in the repository root, next to `README.md`.

The credentials file must look like a desktop client file and contain a top-level `installed` section. Do not use a web application client for this app.

### 5. Configure Environment Variables

Copy the example environment file:

```bash
cp .env.example .env
```

The defaults expect these local files:

```text
GMAIL_CREDENTIALS_FILE=credentials.json
GMAIL_TOKEN_FILE=token.json
```

`credentials.json` is downloaded from Google Cloud. `token.json` is generated locally after your first successful OAuth login.

### 6. Validate Before Login

Run:

```bash
job-auto-agent validate-setup
```

This command checks the database URL, Gmail query, OAuth credentials file shape, and token file status. It does not open a browser and it does not perform Gmail OAuth.

### 7. Complete First Gmail Login

Run:

```bash
job-auto-agent sync-gmail --limit 10
```

On the first run, a browser window opens for Google login and consent. After consent succeeds, the app writes `token.json` locally. Later runs reuse that token and refresh it when possible.

### Troubleshooting

- `Missing credentials.json`: confirm the downloaded OAuth client JSON is in the repository root or update `GMAIL_CREDENTIALS_FILE`.
- `Expected Google OAuth client JSON with an installed section`: recreate the OAuth client as **Desktop app**, not **Web application**.
- `Access blocked` or `user not added as a test user`: add your Gmail address under OAuth consent screen test users.
- `Token missing required scope`: delete `token.json`, confirm the readonly Gmail scope is configured, and run `sync-gmail` again.
- `DATABASE_URL` errors: use a `sqlite:///...` URL. Other database engines are not supported yet.

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
job-auto-agent validate-setup
job-auto-agent tailor-resume --job-id 123
job-auto-agent tailor-resume --job-id 123 --ai
job-auto-agent generate-cover-letter --job-id 123
job-auto-agent generate-cover-letter --job-id 123 --ai
```

## Manual Resume Tailoring

The app can generate a local Markdown resume draft for a selected job. This is a manual review tool only. It does not auto-apply, send emails, upload resumes, or claim skills that are not present in your master resume.

### Master Resume Setup

1. Review the example file:

```bash
data/profile/master_resume.example.md
```

2. Create your real master resume here:

```bash
data/profile/master_resume.md
```

The real `master_resume.md` file is ignored by Git and should never be committed.

Optional profile metadata lives here:

```bash
data/profile/profile.example.json
```

### CLI Usage

Generate local resume tailoring analysis for a saved job:

```bash
job-auto-agent tailor-resume --job-id 123
```

This command does not generate a recruiter-ready tailored resume. It saves keyword analysis only:

```text
data/generated_resumes/job_123_analysis.md
```

Final tailored resume generation requires AI:

```bash
job-auto-agent tailor-resume --job-id 123 --ai
```

The AI-generated resume is saved locally:

```text
data/generated_resumes/job_123_tailored_resume.md
```

Generated resumes and analysis files are ignored by Git. If an AI-generated resume already exists, the command will not overwrite it unless you explicitly pass:

```bash
job-auto-agent tailor-resume --job-id 123 --ai --overwrite
```

The analysis-only command:

- Extracts important job keywords.
- Compares job keywords with the master resume.
- Saves matched and missing keywords to `data/generated_resumes/job_<id>_analysis.md`.
- Prints `AI resume generation is required for recruiter-ready tailored resumes.`
- Does not write `data/generated_resumes/job_<id>_tailored_resume.md`.

### AI Tailoring

AI-assisted tailoring uses the same single local master resume file:

```text
data/profile/master_resume.md
```

It does not require or support multiple resume versions. AI tailoring sends the selected job description and the local master resume content to the configured OpenAI-compatible API only when you explicitly request AI tailoring.

Add these values to `.env`:

```bash
AI_TAILORING_ENABLED=true
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
OPENAI_API_KEY=your_api_key_here
```

Do not commit `.env` or real API keys.

Generate an AI-tailored resume draft:

```bash
job-auto-agent tailor-resume --job-id 123 --ai
```

`OPENAI_BASE_URL` defaults to OpenAI's API endpoint and can be changed for OpenAI-compatible providers.

If `AI_TAILORING_ENABLED=false`, analysis-only tailoring continues to work and AI tailoring shows a clear error. If `OPENAI_API_KEY` is missing for a cloud provider, AI tailoring shows a clear error.

### Free Local AI With Ollama

You can use AI resume tailoring without a paid OpenAI API key by running a local OpenAI-compatible Ollama endpoint.

Install Ollama, then download the model:

```bash
ollama pull qwen2.5:7b
```

Start Ollama:

```bash
ollama serve
```

Use these `.env` settings:

```bash
AI_TAILORING_ENABLED=true
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_MODEL=qwen2.5:7b
OPENAI_API_KEY=ollama
```

`OPENAI_API_KEY=ollama` is a local dummy value for Ollama. A real OpenAI API key is still required when `OPENAI_BASE_URL=https://api.openai.com/v1`.

Generate an Ollama-backed tailored resume:

```bash
job-auto-agent tailor-resume --job-id 123 --ai --overwrite
```

AI-generated resume files are also recruiter-facing only. The app removes internal safety notes, keyword analysis, missing-keyword sections, and contact details outside the top header before saving the resume file. Missing keywords are written to the separate analysis file instead of added as claimed experience.

The AI prompt explicitly instructs the provider not to fabricate companies, dates, roles, tools, metrics, certifications, skills, or experience.

### Dashboard Usage

In the Streamlit dashboard:

1. Find the job you want to tailor for.
2. Note the displayed job ID.
3. Click **Generate Resume Analysis** for local keyword analysis.
4. Click **Generate AI-Tailored Resume** only when AI tailoring is enabled and you want a recruiter-ready tailored resume from the configured AI API.
5. If a generated resume already exists, check **Overwrite existing generated resume** before regenerating.
6. Review the generated resume and the separate analysis file manually before using it.

## Cover Letter Generation

The app can generate a local Markdown cover letter draft for a selected job using the saved job description and your local master resume:

```text
data/profile/master_resume.md
```

Generated cover letters are saved here and ignored by Git:

```text
data/generated_cover_letters/job_<id>_cover_letter.md
```

The cover letter file contains only recruiter-facing content. Contact details, LinkedIn URLs, home location, safety notes, missing keyword sections, and internal warnings are excluded from the generated letter.

Internal analysis is saved separately when generated:

```text
data/generated_cover_letters/job_<id>_analysis.md
```

Rule-based generation stays fully local:

```bash
job-auto-agent generate-cover-letter --job-id 123
```

AI-assisted generation uses the same AI configuration as AI resume tailoring and only calls the configured AI API when `--ai` is explicitly used:

```bash
job-auto-agent generate-cover-letter --job-id 123 --ai
```

AI cover letter generation requires:

```bash
AI_TAILORING_ENABLED=true
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
OPENAI_API_KEY=your_api_key_here
```

For free local Ollama cover letter generation, use the Ollama `.env` settings above and run:

```bash
job-auto-agent generate-cover-letter --job-id 123 --ai
```

The cover letter generator does not auto-apply, send emails, or upload files. Drafts must be manually reviewed before use.

In the Streamlit dashboard, each job shows:

- **Generate Rule-Based Cover Letter**
- **Generate AI Cover Letter**

After generation, the dashboard shows the generated file path and any missing-information warnings.

## Project Structure

```text
dashboard/                  Streamlit UI
data/profile/               Local profile examples and ignored real master resume
data/generated_cover_letters/ Ignored local generated cover letters
src/job_auto_agent/
  cli.py                    Command-line entrypoint
  config.py                 Environment-driven settings
  cover_letter/             Manual cover letter generation
  gmail/                    Gmail OAuth and message fetchers
  extraction/               Job extraction pipeline
  matching/                 DevSecOps/AppSec/PKI scoring
  resume/                   Manual resume tailoring
  storage/                  SQLite schema and repository
tests/                      Focused unit tests
```
