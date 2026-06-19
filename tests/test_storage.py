from datetime import datetime, timezone

import pytest

from job_auto_agent.models import JobOpportunity
from job_auto_agent.storage.database import connect, init_db
from job_auto_agent.storage.repository import list_jobs, save_job, update_job_status


def test_save_job_infers_source_and_defaults_status(tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)

    job = JobOpportunity(
        source_message_id="message-1",
        company="Example",
        title="Platform Engineer",
        location="Remote",
        url="https://jobs.lever.co/example/platform-engineer",
        description="Kubernetes platform role",
        received_at=datetime.now(timezone.utc),
    )

    with connect(db_path) as conn:
        save_job(conn, job)
        saved = list_jobs(conn)[0]

    assert saved["source"] == "Lever"
    assert saved["status"] == "New"


def test_update_job_status_validates_supported_statuses(tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    init_db(db_path)

    job = JobOpportunity(
        source_message_id="message-1",
        company="Example",
        title="Platform Engineer",
        location="Remote",
        url=None,
        description="Kubernetes platform role",
        received_at=None,
    )

    with connect(db_path) as conn:
        job_id = save_job(conn, job)
        update_job_status(conn, job_id, "Interested")
        saved = list_jobs(conn)[0]

        assert saved["status"] == "Interested"

        with pytest.raises(ValueError):
            update_job_status(conn, job_id, "Archived")
