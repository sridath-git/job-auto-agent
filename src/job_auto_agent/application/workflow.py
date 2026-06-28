from __future__ import annotations

import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from job_auto_agent.application.export import (
    ExportError,
    PdfExportResult,
    export_docx_to_pdf_if_available,
    export_markdown_to_docx,
)
from job_auto_agent.config import Settings
from job_auto_agent.cover_letter.generator import generate_ai_cover_letter_for_job
from job_auto_agent.resume.tailor import (
    DEFAULT_MASTER_RESUME_PATH,
    ResumeTailoringError,
    tailor_resume_with_ai_for_job,
)
from job_auto_agent.storage.repository import update_job_status


DEFAULT_APPLICATION_OUTPUT_DIR = Path("data/generated_applications")


class ApplicationPackageError(Exception):
    """Base error for application package workflow failures."""


class ApplicationPackageExistsError(ApplicationPackageError):
    """Raised when an application package already exists and overwrite is false."""


@dataclass(frozen=True)
class ApplicationPaths:
    job_id: int
    folder: Path
    resume_md: Path
    cover_letter_md: Path
    resume_docx: Path
    cover_letter_docx: Path
    analysis_md: Path
    resume_pdf: Path
    cover_letter_pdf: Path


@dataclass(frozen=True)
class GeneratedApplicationFiles:
    resume_md: bool
    cover_letter_md: bool
    resume_docx: bool
    cover_letter_docx: bool
    analysis_md: bool
    resume_pdf: bool
    cover_letter_pdf: bool


@dataclass(frozen=True)
class ApplicationPackageResult:
    job_id: int
    folder: Path
    files: list[Path]
    warnings: list[str]
    pdf_results: dict[str, PdfExportResult]


def application_paths(job_id: int, output_root: Path = DEFAULT_APPLICATION_OUTPUT_DIR) -> ApplicationPaths:
    folder = output_root / f"job_{job_id}"
    return ApplicationPaths(
        job_id=job_id,
        folder=folder,
        resume_md=folder / "resume.md",
        cover_letter_md=folder / "cover_letter.md",
        resume_docx=folder / "resume.docx",
        cover_letter_docx=folder / "cover_letter.docx",
        analysis_md=folder / "analysis.md",
        resume_pdf=folder / "resume.pdf",
        cover_letter_pdf=folder / "cover_letter.pdf",
    )


def detect_application_files(
    job_id: int,
    output_root: Path = DEFAULT_APPLICATION_OUTPUT_DIR,
) -> GeneratedApplicationFiles:
    paths = application_paths(job_id, output_root)
    return GeneratedApplicationFiles(
        resume_md=paths.resume_md.exists(),
        cover_letter_md=paths.cover_letter_md.exists(),
        resume_docx=paths.resume_docx.exists(),
        cover_letter_docx=paths.cover_letter_docx.exists(),
        analysis_md=paths.analysis_md.exists(),
        resume_pdf=paths.resume_pdf.exists(),
        cover_letter_pdf=paths.cover_letter_pdf.exists(),
    )


def prepare_application_package(
    conn: sqlite3.Connection,
    job_id: int,
    settings: Settings,
    master_resume_path: Path = DEFAULT_MASTER_RESUME_PATH,
    output_root: Path = DEFAULT_APPLICATION_OUTPUT_DIR,
    overwrite: bool = False,
) -> ApplicationPackageResult:
    paths = application_paths(job_id, output_root)
    _ensure_can_write_package(paths, overwrite)
    try:
        paths.folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ApplicationPackageError(
            f"Unable to create application package folder at {paths.folder}: {exc}"
        ) from exc

    try:
        with tempfile.TemporaryDirectory(prefix=f"job_auto_agent_{job_id}_") as temp_dir:
            work_dir = Path(temp_dir)
            resume_result = tailor_resume_with_ai_for_job(
                conn,
                job_id,
                settings,
                master_resume_path=master_resume_path,
                output_dir=work_dir / "resume",
                overwrite=True,
            )
            cover_result = generate_ai_cover_letter_for_job(
                conn,
                job_id,
                settings,
                master_resume_path=master_resume_path,
                output_dir=work_dir / "cover_letter",
            )
            if resume_result.output_path is None:
                raise ApplicationPackageError(
                    "AI resume generation did not create a recruiter-ready resume."
                )
            _copy_text_file(resume_result.output_path, paths.resume_md)
            _copy_text_file(cover_result.output_path, paths.cover_letter_md)
            _write_combined_analysis(
                paths.analysis_md,
                resume_result.analysis_path,
                cover_result.analysis_path,
            )
    except ResumeTailoringError:
        raise
    except ApplicationPackageError:
        raise

    try:
        export_markdown_to_docx(paths.resume_md.read_text(encoding="utf-8"), paths.resume_docx)
        export_markdown_to_docx(
            paths.cover_letter_md.read_text(encoding="utf-8"),
            paths.cover_letter_docx,
        )
    except ExportError as exc:
        raise ApplicationPackageError(
            f"DOCX export failed after Markdown files were created: {exc}"
        ) from exc
    resume_pdf = export_docx_to_pdf_if_available(paths.resume_docx, paths.resume_pdf)
    cover_pdf = export_docx_to_pdf_if_available(paths.cover_letter_docx, paths.cover_letter_pdf)
    warnings = [
        warning
        for warning in (resume_pdf.warning, cover_pdf.warning)
        if warning
    ]
    update_job_status(conn, job_id, "Ready to Apply")
    conn.commit()

    files = [
        paths.resume_md,
        paths.cover_letter_md,
        paths.resume_docx,
        paths.cover_letter_docx,
        paths.analysis_md,
    ]
    if resume_pdf.output_path:
        files.append(resume_pdf.output_path)
    if cover_pdf.output_path:
        files.append(cover_pdf.output_path)
    return ApplicationPackageResult(
        job_id=job_id,
        folder=paths.folder,
        files=files,
        warnings=warnings,
        pdf_results={"resume": resume_pdf, "cover_letter": cover_pdf},
    )


def _ensure_can_write_package(paths: ApplicationPaths, overwrite: bool) -> None:
    existing = [
        path
        for path in (
            paths.resume_md,
            paths.cover_letter_md,
            paths.resume_docx,
            paths.cover_letter_docx,
            paths.analysis_md,
            paths.resume_pdf,
            paths.cover_letter_pdf,
        )
        if path.exists()
    ]
    if existing and not overwrite:
        raise ApplicationPackageExistsError(
            f"Application package already exists at {paths.folder}. Re-run with --overwrite."
        )


def _copy_text_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def _write_combined_analysis(output_path: Path, resume_analysis_path: Path, cover_analysis_path: Path) -> None:
    sections = [
        "# Application Analysis",
        "",
        "## Resume Analysis",
        "",
        resume_analysis_path.read_text(encoding="utf-8").strip(),
        "",
        "## Cover Letter Analysis",
        "",
        cover_analysis_path.read_text(encoding="utf-8").strip(),
        "",
    ]
    output_path.write_text("\n".join(sections), encoding="utf-8")
