from __future__ import annotations

import json
import platform
import subprocess
from pathlib import Path

import streamlit as st

from job_auto_agent.application.workflow import (
    DEFAULT_APPLICATION_OUTPUT_DIR,
    ApplicationPackageError,
    application_paths,
    detect_application_files,
    prepare_application_package,
)
from job_auto_agent.config import get_settings
from job_auto_agent.cover_letter.generator import (
    CoverLetterGenerationError,
    DEFAULT_COVER_LETTER_OUTPUT_DIR,
    generate_ai_cover_letter_for_job,
    generate_cover_letter_for_job,
)
from job_auto_agent.resume.tailor import (
    DEFAULT_OUTPUT_DIR,
    ResumeTailoringError,
    tailor_resume_for_job,
    tailor_resume_with_ai_for_job,
)
from job_auto_agent.storage.database import connect, init_db
from job_auto_agent.storage.repository import JOB_STATUSES, list_jobs, update_job_status


def _read_markdown(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _download_file(label: str, path: Path, mime: str, key: str) -> None:
    if path.exists():
        st.download_button(
            label,
            data=path.read_bytes(),
            file_name=path.name,
            mime=mime,
            key=key,
        )
    else:
        st.caption(f"{label}: generate the application package first.")


def _safe_open_folder(path: Path) -> None:
    if platform.system() == "Darwin":
        try:
            subprocess.Popen(["open", str(path)])
            st.success("Opened application folder in Finder.")
        except OSError as exc:
            st.warning(f"Could not open Finder automatically: {exc}")
    else:
        st.info("Automatic folder opening is available only on macOS. Use the path below.")


def _missing_keywords_for_job(job_id: int) -> list[str]:
    candidate_paths = [
        DEFAULT_OUTPUT_DIR / f"job_{job_id}_analysis.md",
        DEFAULT_COVER_LETTER_OUTPUT_DIR / f"job_{job_id}_analysis.md",
        application_paths(job_id).analysis_md,
    ]
    for path in candidate_paths:
        if not path.exists():
            continue
        missing = _parse_missing_keywords(path.read_text(encoding="utf-8"))
        if missing:
            return missing
    return []


def _parse_missing_keywords(text: str) -> list[str]:
    marker = "## Missing Job Keywords"
    if marker not in text:
        return []
    section = text.split(marker, maxsplit=1)[1]
    if "\n## " in section:
        section = section.split("\n## ", maxsplit=1)[0]
    return [
        line.strip().lstrip("- ").strip()
        for line in section.splitlines()
        if line.strip().startswith("- ") and line.strip().lower() != "- none"
    ]


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
        missing_keywords = _missing_keywords_for_job(job["id"])
        st.markdown(
            "**Missing keywords:** "
            + (", ".join(missing_keywords) if missing_keywords else "No generated analysis yet")
        )
        if job["notes"]:
            st.markdown(f"**Notes:** {job['notes']}")

        resume_path = DEFAULT_OUTPUT_DIR / f"job_{job['id']}_tailored_resume.md"
        cover_letter_path = DEFAULT_COVER_LETTER_OUTPUT_DIR / f"job_{job['id']}_cover_letter.md"
        app_paths = application_paths(job["id"])
        app_files = detect_application_files(job["id"])
        status_summary = {
            "generated_resume": resume_path.exists() or app_files.resume_md,
            "generated_cover_letter": cover_letter_path.exists() or app_files.cover_letter_md,
            "application_package": app_files.resume_docx and app_files.cover_letter_docx,
        }
        st.write(status_summary)

        overwrite_key = f"overwrite-resume-{job['id']}"
        overwrite = False
        if resume_path.exists():
            st.info(f"Tailored resume already exists: `{resume_path}`")
            overwrite = st.checkbox(
                "Overwrite existing generated resume",
                key=overwrite_key,
            )
        package_overwrite = st.checkbox(
            "Overwrite existing application package",
            key=f"overwrite-package-{job['id']}",
        )

        action_cols = st.columns(4)
        if action_cols[0].button("Generate Resume", key=f"generate-resume-{job['id']}"):
            try:
                with connect(settings.sqlite_path) as conn:
                    result = tailor_resume_with_ai_for_job(
                        conn,
                        job["id"],
                        settings,
                        overwrite=overwrite,
                    )
                st.success(f"Generated resume: `{result.output_path}`")
                st.info(f"Saved tailoring analysis: `{result.analysis_path}`")
                if result.missing_keywords:
                    st.warning(
                        "Missing keywords to review manually: "
                        + ", ".join(result.missing_keywords)
                    )
            except ResumeTailoringError as exc:
                st.error(str(exc))

        if action_cols[1].button("View Resume", key=f"view-resume-{job['id']}"):
            resume_text = _read_markdown(app_paths.resume_md) or _read_markdown(resume_path)
            if resume_text:
                st.markdown(resume_text)
            else:
                st.info("Generate resume first")

        if action_cols[2].button("Generate Cover Letter", key=f"generate-cover-letter-{job['id']}"):
            try:
                with connect(settings.sqlite_path) as conn:
                    result = generate_ai_cover_letter_for_job(conn, job["id"], settings)
                st.success(f"Generated cover letter: `{result.output_path}`")
                if result.warnings:
                    st.warning("Warnings: " + " ".join(result.warnings))
            except (CoverLetterGenerationError, ResumeTailoringError) as exc:
                st.error(str(exc))

        if action_cols[3].button("View Cover Letter", key=f"view-cover-letter-{job['id']}"):
            cover_text = _read_markdown(app_paths.cover_letter_md) or _read_markdown(cover_letter_path)
            if cover_text:
                st.markdown(cover_text)
            else:
                st.info("Generate cover letter first")

        package_cols = st.columns(4)
        if package_cols[0].button("Prepare Application", key=f"prepare-application-{job['id']}"):
            try:
                with connect(settings.sqlite_path) as conn:
                    result = prepare_application_package(
                        conn,
                        job["id"],
                        settings,
                        overwrite=package_overwrite,
                    )
                st.success(f"Prepared application package: `{result.folder}`")
                st.write([str(path) for path in result.files])
                for warning in result.warnings:
                    st.warning(warning)
            except (ApplicationPackageError, CoverLetterGenerationError, ResumeTailoringError) as exc:
                st.error(str(exc))

        if package_cols[1].button("Open Application Folder", key=f"open-folder-{job['id']}"):
            st.code(str(app_paths.folder))
            if app_paths.folder.exists():
                _safe_open_folder(app_paths.folder)
            else:
                st.info("Prepare application first")

        status_button_cols = st.columns(4)
        status_buttons = (
            ("Mark Interested", "Interested"),
            ("Mark Ready to Apply", "Ready to Apply"),
            ("Mark Applied", "Applied"),
            ("Mark Not Interested", "Not Interested"),
        )
        for column, (label, status) in zip(status_button_cols, status_buttons):
            if column.button(label, key=f"{status}-{job['id']}"):
                with connect(settings.sqlite_path) as conn:
                    update_job_status(conn, job["id"], status)
                st.toast(f"Updated status to {status}")
                st.rerun()

        with st.expander("Downloads"):
            download_cols = st.columns(2)
            with download_cols[0]:
                _download_file(
                    "Download Resume DOCX",
                    app_paths.resume_docx,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    f"download-resume-docx-{job['id']}",
                )
                _download_file(
                    "Download Resume PDF",
                    app_paths.resume_pdf,
                    "application/pdf",
                    f"download-resume-pdf-{job['id']}",
                )
            with download_cols[1]:
                _download_file(
                    "Download Cover Letter DOCX",
                    app_paths.cover_letter_docx,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    f"download-cover-docx-{job['id']}",
                )
                _download_file(
                    "Download Cover Letter PDF",
                    app_paths.cover_letter_pdf,
                    "application/pdf",
                    f"download-cover-pdf-{job['id']}",
                )

        with st.expander("Rule-Based Utilities"):
            rule_col, cover_rule_col = st.columns(2)
            if rule_col.button("Generate Resume Analysis", key=f"tailor-rule-resume-{job['id']}"):
                try:
                    with connect(settings.sqlite_path) as conn:
                        result = tailor_resume_for_job(conn, job["id"], overwrite=overwrite)
                    st.success(f"Generated resume analysis: `{result.analysis_path}`")
                    st.info("AI resume generation is required for recruiter-ready tailored resumes.")
                    if result.missing_keywords:
                        st.warning(
                            "Missing keywords to review manually: "
                            + ", ".join(result.missing_keywords)
                        )
                except ResumeTailoringError as exc:
                    st.error(str(exc))
            if cover_rule_col.button(
                "Generate Rule-Based Cover Letter",
                key=f"cover-letter-rule-{job['id']}",
            ):
                try:
                    with connect(settings.sqlite_path) as conn:
                        result = generate_cover_letter_for_job(conn, job["id"])
                    st.success(f"Generated cover letter: `{result.output_path}`")
                    if result.warnings:
                        st.warning("Warnings: " + " ".join(result.warnings))
                except (CoverLetterGenerationError, ResumeTailoringError) as exc:
                    st.error(str(exc))

        with st.expander("Email excerpt"):
            st.write(job["description"])
