from __future__ import annotations

import json

import streamlit as st

from job_auto_agent.config import get_settings
from job_auto_agent.storage.database import connect, init_db
from job_auto_agent.storage.repository import list_jobs


settings = get_settings()
init_db(settings.sqlite_path)

st.set_page_config(page_title="Job Auto Agent", page_icon=":material/security:", layout="wide")
st.title("Job Auto Agent")
st.caption("Gmail-sourced DevSecOps, AppSec, and PKI opportunity tracker.")

with connect(settings.sqlite_path) as conn:
    jobs = list_jobs(conn)

min_score = st.sidebar.slider("Minimum score", min_value=0, max_value=100, value=settings.match_min_score)
search = st.sidebar.text_input("Search title, company, or description")

filtered = []
for job in jobs:
    score = job["score"] or 0
    haystack = f"{job['title']} {job['company'] or ''} {job['description']}".lower()
    if score < min_score:
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
        st.subheader(f"{job['title']} · {score}/100")
        st.write(
            {
                "company": job["company"],
                "location": job["location"],
                "received_at": job["received_at"],
                "url": job["url"],
            }
        )
        if job["url"]:
            st.link_button("Open posting", job["url"])
        if job["matched_terms"]:
            terms = ", ".join(json.loads(job["matched_terms"]))
            st.caption(f"Matched terms: {terms}")
        if job["notes"]:
            st.write(job["notes"])
        with st.expander("Email excerpt"):
            st.write(job["description"])
