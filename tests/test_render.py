from __future__ import annotations

import shutil

import pytest
from docx import Document
from pypdf import PdfReader

from app.render_docx import build_docx
from app.render_pdf import render_with_fit

SOFFICE = shutil.which("soffice") or shutil.which("libreoffice")


def test_docx_contains_all_hyperlinks(fallback_resume, master, tmp_path):
    path = build_docx(fallback_resume, tmp_path / "resume.docx")
    document = Document(str(path))
    targets = [
        relation.target_ref
        for relation in document.part.rels.values()
        if "hyperlink" in relation.reltype
    ]
    assert len(targets) == 5
    for link in master.profile.links:
        assert link.url in targets
    for project in fallback_resume.projects:
        if project.link:
            assert project.link in targets


@pytest.mark.skipif(SOFFICE is None, reason="LibreOffice is required for PDF output")
def test_pdf_is_one_page_with_links(fallback_resume, tmp_path):
    docx_path = tmp_path / "resume.docx"
    pdf_path = tmp_path / "resume.pdf"

    rendered, pages, links = render_with_fit(fallback_resume, docx_path, pdf_path)

    assert pages == 1
    assert rendered.report.pages == 1
    assert len(links) == 5
    reader = PdfReader(str(pdf_path))
    assert len(reader.pages) == 1

    annotations: list[str] = []
    for page in reader.pages:
        for reference in page.get("/Annots") or []:
            action = reference.get_object().get("/A")
            if action:
                annotations.append(str(action.get_object().get("/URI")))
    assert len(annotations) == 5
