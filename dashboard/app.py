from __future__ import annotations

import json

import streamlit as st

from job_auto_agent.config import get_settings
from job_auto_agent.resume.tailor import (
    DEFAULT_OUTPUT_DIR,
    ResumeTailoringError,
    tailor_resume_for_job,
    tailor_resume_with_ai_for_job,
)
from job_auto_agent.storage.database import connect, init_db
from job_auto_agent.storage.repository import JOB_STATUSES, list_jobs, update_job_status


settings = get_settings()
init_db(settings.sqlite_path)

st.set_page_config(page_title="Job Auto Agent", page_icon=":material/security:", layout="wide")
st.title("Job Auto Agent")
st.caption("Gmail-sourced DevSecOps, AppSec, and PKI opportunity tracker.")

with connect(settings.sqlite_path) as conn:
    jobs = list_jobs(conn)

min_score = st.sidebar.slider("Minimum score", min_value=0, max_value=100, value=settings.match_min_score)
sources = sorted({job["source"] for job in jobs if job["source"]})
selected_sources = st.sidebar.multiselect("Source", sources, default=sources)
locations = sorted({job["location"] for job in jobs if job["location"]})
selected_locations = st.sidebar.multiselect("Location", locations, default=locations)
selected_statuses = st.sidebar.multiselect("Status", JOB_STATUSES, default=list(JOB_STATUSES))
search = st.sidebar.text_input("Search title, company, or description")

filtered = []
for job in jobs:
    score = job["score"] or 0
    haystack = f"{job['title']} {job['company'] or ''} {job['description']}".lower()
    if score < min_score:
        continue
    if selected_sources and job["source"] not in selected_sources:
        continue
    if selected_locations and job["location"] not in selected_locations:
        continue
    if selected_statuses and job["status"] not in selected_statuses:
        continue
    if search and search.lower() not in haystack:
        continue
    filtered.append(job)

left, middle, right = st.columns(3)
left.metric("Saved jobs", len(jobs))
middle.metric("Visible matches", len(filtered))
right.metric("Strong matches", sum(1 for job in jobs if (job["score"] or 0) >= 60))

st.divider()

if not filtered:
    st.info("No matching jobs yet. Run `job-auto-agent sync-gmail` and `job-auto-agent score-jobs`.")

for job in filtered:
    with st.container(border=True):
        score = job["score"] or 0
        title_col, score_col, status_col = st.columns([4, 1, 2])
        title_col.subheader(job["title"])
        score_col.metric("Match", f"{score}/100")
        current_status = job["status"] or "New"
        selected_status = status_col.selectbox(
            "Status",
            JOB_STATUSES,
            index=JOB_STATUSES.index(current_status) if current_status in JOB_STATUSES else 0,
            key=f"status-{job['id']}",
        )
        if selected_status != current_status:
            with connect(settings.sqlite_path) as conn:
                update_job_status(conn, job["id"], selected_status)
            st.toast(f"Updated status to {selected_status}")
            st.rerun()

        st.progress(score / 100)
        st.write(
            {
                "job_id": job["id"],
                "company": job["company"],
                "source": job["source"],
                "location": job["location"],
                "received_at": job["received_at"],
                "url": job["url"],
            }
        )
        if job["url"]:
            st.link_button("Open posting", job["url"])
        if job["matched_terms"]:
            terms = ", ".join(json.loads(job["matched_terms"]))
            st.markdown(f"**Matched terms:** {terms}")
        if job["notes"]:
            st.markdown(f"**Notes:** {job['notes']}")
        output_path = DEFAULT_OUTPUT_DIR / f"job_{job['id']}_tailored_resume.md"
        overwrite_key = f"overwrite-resume-{job['id']}"
        overwrite = False
        if output_path.exists():
            st.info(f"Tailored resume already exists: `{output_path}`")
            overwrite = st.checkbox(
                "Overwrite existing generated resume",
                key=overwrite_key,
            )
        rule_col, ai_col = st.columns(2)
        if rule_col.button("Generate Rule-Based Resume", key=f"tailor-rule-resume-{job['id']}"):
            try:
                with connect(settings.sqlite_path) as conn:
                    result = tailor_resume_for_job(conn, job["id"], overwrite=overwrite)
                st.success(f"Generated tailored resume: `{result.output_path}`")
                if result.missing_keywords:
                    st.warning(
                        "Missing keywords to review manually: "
                        + ", ".join(result.missing_keywords)
                    )
            except ResumeTailoringError as exc:
                st.error(str(exc))
        if ai_col.button("Generate AI-Tailored Resume", key=f"tailor-ai-resume-{job['id']}"):
            try:
                with connect(settings.sqlite_path) as conn:
                    result = tailor_resume_with_ai_for_job(
                        conn,
                        job["id"],
                        settings,
                        overwrite=overwrite,
                    )
                st.success(f"Generated AI-tailored resume: `{result.output_path}`")
                if result.missing_keywords:
                    st.warning(
                        "Missing keywords to review manually: "
                        + ", ".join(result.missing_keywords)
                    )
            except ResumeTailoringError as exc:
                st.error(str(exc))
        with st.expander("Email excerpt"):
            st.write(job["description"])
