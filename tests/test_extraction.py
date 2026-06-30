from datetime import datetime, timezone

from job_auto_agent.extraction.pipeline import extract_job
from job_auto_agent.models import EmailMessage


def test_extracts_security_job_from_email() -> None:
    message = EmailMessage(
        gmail_id="abc123",
        thread_id="thread123",
        sender="Recruiter <person@examplecorp.com>",
        subject="DevSecOps Engineer opportunity",
        snippet="Remote role",
        body_text="We are hiring a DevSecOps Engineer at ExampleCorp. Apply https://example.com/job",
        received_at=datetime.now(timezone.utc),
    )

    job = extract_job(message)

    assert job is not None
    assert job.title == "Devsecops Engineer Opportunity"
    assert job.company == "Examplecorp"
    assert job.location == "Remote"
    assert job.url == "https://example.com/job"


def test_extracts_company_without_linkedin_job_alert_suffix() -> None:
    message = EmailMessage(
        gmail_id="abc124",
        thread_id="thread124",
        sender="LinkedIn Job Alerts <jobs-noreply@linkedin.com>",
        subject="DevSecOps Engineer role",
        snippet="XP Venture Labs Posted on 6 days ago Easy Apply",
        body_text=(
            "We are hiring a DevSecOps Engineer at XP Venture Labs Posted on 6 days ago "
            "Easy Apply https://example.com/job"
        ),
        received_at=datetime.now(timezone.utc),
    )

    job = extract_job(message)

    assert job is not None
    assert job.company == "XP Venture Labs"
