from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from pypdf import PdfReader

from .models import TailoredResume
from .render_docx import build_docx


def _soffice_binary() -> str:
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError(
        "LibreOffice is required to create the PDF. Install it or make "
        "'soffice' available on PATH."
    )


def convert_docx_to_pdf(docx_path: Path, pdf_path: Path) -> Path:
    soffice = _soffice_binary()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    produced = pdf_path.parent / f"{docx_path.stem}.pdf"
    for candidate in (pdf_path, produced):
        if candidate.exists():
            candidate.unlink()

    profile_dir = tempfile.mkdtemp(prefix="resume-helper-lo-")
    command = [
        soffice,
        "--headless",
        "--nologo",
        "--norestore",
        f"-env:UserInstallation=file://{profile_dir}",
        "--convert-to",
        "pdf:writer_pdf_Export",
        "--outdir",
        str(pdf_path.parent),
        str(docx_path),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)

    if result.returncode != 0 or not produced.exists():
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            "LibreOffice could not convert the resume to PDF."
            + (f" Details: {detail[:400]}" if detail else "")
        )
    if produced != pdf_path:
        produced.replace(pdf_path)
    return pdf_path


def pdf_stats(pdf_path: Path) -> tuple[int, list[str]]:
    reader = PdfReader(str(pdf_path))
    links: list[str] = []
    for page in reader.pages:
        annotations = page.get("/Annots")
        if not annotations:
            continue
        for reference in annotations:
            try:
                annotation = reference.get_object()
            except Exception:
                continue
            action = annotation.get("/A")
            if not action:
                continue
            try:
                action = action.get_object()
            except Exception:
                pass
            uri = action.get("/URI")
            if uri:
                links.append(str(uri))
    return len(reader.pages), links


def _trim_one_bullet(resume: TailoredResume) -> str | None:
    for collection_name in ("extracurricular", "experience", "projects"):
        collection = getattr(resume, collection_name)
        for item in reversed(collection):
            if len(item.bullets) > 1:
                item.bullets.pop()
                return f"Removed the last bullet from '{item.title}' to fit one page."

    if resume.extracurricular:
        removed = resume.extracurricular.pop()
        return f"Removed the extracurricular item '{removed.title}' to fit one page."
    if len(resume.experience) > 1:
        removed = resume.experience.pop()
        return f"Removed the experience '{removed.title}' to fit one page."
    if len(resume.projects) > 1:
        removed = resume.projects.pop()
        return f"Removed the project '{removed.title}' to fit one page."
    return None


def render_with_fit(
    resume: TailoredResume,
    docx_path: Path,
    pdf_path: Path,
    max_trims: int = 10,
) -> tuple[TailoredResume, int, list[str]]:
    working = resume.model_copy(deep=True)
    expected_links = len([link for link in working.profile.links if link.url]) + len(
        [project for project in working.projects if project.link]
    )

    pages = 99
    links: list[str] = []
    trimmed = False

    for density in (1.0, 0.97, 0.94):
        build_docx(working, docx_path, density=density)
        convert_docx_to_pdf(docx_path, pdf_path)
        pages, links = pdf_stats(pdf_path)
        if pages <= 1:
            break

    attempts = 0
    while pages > 1 and attempts < max_trims:
        note = _trim_one_bullet(working)
        if not note:
            break
        trimmed = True
        attempts += 1
        working.report.warnings.append(note)
        build_docx(working, docx_path, density=0.94)
        convert_docx_to_pdf(docx_path, pdf_path)
        pages, links = pdf_stats(pdf_path)

    if pages > 1:
        working.report.warnings.append(
            f"The resume is {pages} pages. Remove an item or shorten bullets to "
            "bring it back to one page."
        )

    if len(links) < expected_links:
        working.report.warnings.append(
            f"Only {len(links)} of {expected_links} hyperlinks were found in the "
            "generated PDF. Open the DOCX to confirm the links."
        )

    working.report.pages = pages
    working.report.trimmed = trimmed
    return working, pages, links
