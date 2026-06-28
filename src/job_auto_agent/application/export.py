from __future__ import annotations

import html
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path


class ExportError(Exception):
    """Raised when an application document export fails."""


@dataclass(frozen=True)
class PdfExportResult:
    output_path: Path | None
    warning: str | None = None


def export_markdown_to_docx(markdown_text: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document_xml = _build_document_xml(_markdown_to_blocks(markdown_text))
    try:
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", _content_types_xml())
            archive.writestr("_rels/.rels", _rels_xml())
            archive.writestr("word/document.xml", document_xml)
    except OSError as exc:
        raise ExportError(f"Unable to create DOCX file at {output_path}: {exc}") from exc
    return output_path


def export_docx_to_pdf_if_available(docx_path: Path, pdf_path: Path) -> PdfExportResult:
    converter = _find_pdf_converter()
    if converter is None:
        return PdfExportResult(
            output_path=None,
            warning="PDF export skipped because no local DOCX-to-PDF converter was found.",
        )
    return _convert_with_soffice(converter, docx_path, pdf_path)


def _find_pdf_converter() -> str | None:
    for executable in ("soffice", "libreoffice"):
        path = shutil.which(executable)
        if path:
            return path
    return None


def _convert_with_soffice(converter: str, docx_path: Path, pdf_path: Path) -> PdfExportResult:
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            [
                converter,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(pdf_path.parent),
                str(docx_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return PdfExportResult(output_path=None, warning=f"PDF export skipped: {exc}")

    converted_path = docx_path.with_suffix(".pdf")
    if completed.returncode != 0 or not converted_path.exists():
        detail = (completed.stderr or completed.stdout or "converter did not create a PDF").strip()
        return PdfExportResult(output_path=None, warning=f"PDF export skipped: {detail}")
    if converted_path != pdf_path:
        converted_path.replace(pdf_path)
    return PdfExportResult(output_path=pdf_path)


def _markdown_to_blocks(markdown_text: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    in_code_fence = False
    title_seen = False
    for raw_line in markdown_text.splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            in_code_fence = not in_code_fence
            continue
        if in_code_fence:
            continue
        if not line:
            blocks.append(("blank", ""))
            continue
        if _is_internal_line(line):
            continue
        if line.startswith("# "):
            text = line[2:].strip()
            blocks.append(("title", text))
            title_seen = True
        elif line.startswith("## "):
            blocks.append(("heading", line[3:].strip()))
        elif line.startswith("### "):
            blocks.append(("subheading", line[4:].strip()))
        elif line.startswith("- "):
            blocks.append(("bullet", line[2:].strip()))
        elif not title_seen:
            blocks.append(("title", line))
            title_seen = True
        else:
            blocks.append(("paragraph", line))
    return _squash_blank_blocks(blocks)


def _is_internal_line(line: str) -> bool:
    normalized = line.lower()
    internal_markers = (
        "safety notes",
        "truthfulness notes",
        "missing keywords",
        "missing information warnings",
        "debug",
        "analysis",
    )
    return any(marker in normalized for marker in internal_markers)


def _squash_blank_blocks(blocks: list[tuple[str, str]]) -> list[tuple[str, str]]:
    output: list[tuple[str, str]] = []
    previous_blank = False
    for block_type, text in blocks:
        if block_type == "blank":
            if output and not previous_blank:
                output.append((block_type, text))
            previous_blank = True
            continue
        output.append((block_type, text))
        previous_blank = False
    return output


def _build_document_xml(blocks: list[tuple[str, str]]) -> str:
    paragraphs = "\n".join(_paragraph_xml(block_type, text) for block_type, text in blocks)
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    {paragraphs}
    <w:sectPr>
      <w:pgSz w:w="12240" w:h="15840"/>
      <w:pgMar w:top="720" w:right="720" w:bottom="720" w:left="720" w:header="360" w:footer="360" w:gutter="0"/>
    </w:sectPr>
  </w:body>
</w:document>
"""


def _paragraph_xml(block_type: str, text: str) -> str:
    if block_type == "blank":
        return "<w:p/>"
    properties = {
        "title": '<w:pPr><w:jc w:val="center"/></w:pPr>',
        "heading": "",
        "subheading": "",
        "bullet": '<w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr>',
        "paragraph": "",
    }[block_type]
    run_properties = {
        "title": '<w:rPr><w:b/><w:sz w:val="32"/></w:rPr>',
        "heading": '<w:rPr><w:b/><w:sz w:val="26"/></w:rPr>',
        "subheading": '<w:rPr><w:b/><w:sz w:val="22"/></w:rPr>',
        "bullet": '<w:rPr><w:sz w:val="21"/></w:rPr>',
        "paragraph": '<w:rPr><w:sz w:val="21"/></w:rPr>',
    }[block_type]
    prefix = "• " if block_type == "bullet" else ""
    escaped_text = html.escape(prefix + _strip_markdown_symbols(text), quote=False)
    return f"<w:p>{properties}<w:r>{run_properties}<w:t>{escaped_text}</w:t></w:r></w:p>"


def _strip_markdown_symbols(text: str) -> str:
    cleaned = text.replace("**", "").replace("__", "").replace("`", "")
    return cleaned.strip()


def _content_types_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""


def _rels_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""

