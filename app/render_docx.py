from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from .models import TailoredResume

FONT = "Times New Roman"
SANS_FONT = "Arial"
LINK_COLOR = "1155CC"
CONTENT_WIDTH = 7.1
BULLET_INDENT = 0.5
BULLET_HANGING = 0.25
SMALL_BULLET_INDENT = 0.25


def _set_run_font(
    run,
    size: float,
    *,
    font: str = FONT,
    bold: bool = False,
    italic: bool = False,
    color: str | None = None,
    underline: bool = False,
) -> None:
    run.font.name = font
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for attribute in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(attribute), font)
    run.font.size = Pt(size)
    if bold:
        run.bold = True
    if italic:
        run.italic = True
    if color:
        run.font.color.rgb = RGBColor.from_string(color)
    if underline:
        run.underline = True


def _add_text(paragraph, text: str, size: float, **kwargs):
    run = paragraph.add_run(str(text))
    _set_run_font(run, size, **kwargs)
    return run


def _add_hyperlink(paragraph, text: str, url: str, size: float, **kwargs):
    if not url:
        return _add_text(paragraph, text, size, **kwargs)
    part = paragraph.part
    relationship_id = part.relate_to(
        url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True
    )
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), relationship_id)
    run = paragraph.add_run(str(text))
    _set_run_font(run, size, **kwargs)
    hyperlink.append(run._element)
    paragraph._p.append(hyperlink)
    return run


def _add_tab(paragraph) -> None:
    run = paragraph.add_run()
    run._element.append(OxmlElement("w:tab"))


def _add_parts(paragraph, parts: list[dict], size: float) -> None:
    for part in parts:
        text = part.get("text", "")
        if not text:
            continue
        options = {
            "font": part.get("font", FONT),
            "bold": part.get("bold", False),
            "italic": part.get("italic", False),
            "color": part.get("color"),
            "underline": part.get("underline", False),
        }
        if part.get("link"):
            _add_hyperlink(paragraph, text, part["link"], size, **options)
        else:
            _add_text(paragraph, text, size, **options)


def _rich_parts(text: str, size_kwargs: dict | None = None) -> list[dict]:
    base = dict(size_kwargs or {})
    if text.lower().startswith("tech:"):
        return [
            {**base, "text": text[:5], "bold": True},
            {**base, "text": text[5:]},
        ]
    return [{**base, "text": text}]


def _add_heading(doc, text: str, density: float, gap_after: float = 0.0) -> None:
    paragraph = doc.add_paragraph()
    fmt = paragraph.paragraph_format
    fmt.space_before = Pt(6 * density)
    fmt.space_after = Pt((2 + gap_after) * density)
    fmt.line_spacing = 1.0
    fmt.keep_with_next = True
    _add_text(paragraph, text, 12 * density, bold=True)

    p_pr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "A0A0A0")
    borders.append(bottom)
    p_pr.append(borders)


def _add_bullet(
    doc,
    text: str,
    density: float,
    size: float,
    after: float = 1.0,
    indent: float = BULLET_INDENT,
    hanging: float = BULLET_HANGING,
):
    paragraph = doc.add_paragraph()
    fmt = paragraph.paragraph_format
    fmt.left_indent = Inches(indent)
    fmt.first_line_indent = Inches(-hanging)
    fmt.space_before = Pt(0)
    fmt.space_after = Pt(after * density)
    fmt.line_spacing = 1.0
    fmt.tab_stops.add_tab_stop(Inches(indent))
    _add_text(paragraph, "●\t", size, bold=False)
    _add_parts(paragraph, _rich_parts(text), size)
    return paragraph


def _add_line_with_right(
    doc,
    left_parts: list[dict],
    right_parts: list[dict] | None,
    size: float,
    density: float,
    after: float = 1.0,
) -> None:
    paragraph = doc.add_paragraph()
    fmt = paragraph.paragraph_format
    fmt.space_before = Pt(0)
    fmt.space_after = Pt(after * density)
    fmt.line_spacing = 1.0
    fmt.tab_stops.add_tab_stop(Inches(CONTENT_WIDTH), WD_TAB_ALIGNMENT.RIGHT)
    _add_parts(paragraph, left_parts, size)
    if right_parts:
        _add_tab(paragraph)
        _add_parts(paragraph, right_parts, size)


def build_docx(resume: TailoredResume, path: Path, density: float = 1.0) -> Path:
    density = max(0.86, min(1.0, density))
    doc = Document()

    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(0.5)
    section.bottom_margin = Inches(0.5)
    section.left_margin = Inches(0.7)
    section.right_margin = Inches(0.7)

    normal = doc.styles["Normal"]
    normal.font.name = FONT
    normal.font.size = Pt(10.5)
    normal_rpr = normal.element.get_or_add_rPr()
    normal_fonts = normal_rpr.find(qn("w:rFonts"))
    if normal_fonts is None:
        normal_fonts = OxmlElement("w:rFonts")
        normal_rpr.append(normal_fonts)
    for attribute in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        normal_fonts.set(qn(attribute), FONT)
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(0)
    normal.paragraph_format.line_spacing = 1.0

    doc.core_properties.title = f"{resume.profile.name} - Resume"
    doc.core_properties.author = resume.profile.name

    body_size = max(9.6, 10.5 * density)
    small_size = max(9.2, 10.0 * density)
    header_size = max(20.0, 24.0 * density)

    # Header
    paragraph = doc.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.space_after = Pt(2 * density)
    paragraph.paragraph_format.line_spacing = 1.0
    _add_text(paragraph, resume.profile.name, header_size, bold=True)

    if resume.profile.headline:
        paragraph = doc.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.paragraph_format.space_after = Pt(2 * density)
        paragraph.paragraph_format.line_spacing = 1.0
        _add_text(
            paragraph,
            resume.profile.headline,
            12 * density,
            font=SANS_FONT,
            bold=True,
        )

    contact_parts: list[dict] = []
    if resume.profile.phone:
        contact_parts.append({"text": resume.profile.phone})
    if resume.profile.email:
        if contact_parts:
            contact_parts.append({"text": " | "})
        contact_parts.append({"text": resume.profile.email})
    if resume.profile.location:
        if contact_parts:
            contact_parts.append({"text": " | "})
        contact_parts.append({"text": resume.profile.location})
    for link in resume.profile.links:
        if not link.url:
            continue
        if contact_parts:
            contact_parts.append({"text": " | "})
        contact_parts.append(
            {
                "text": link.label,
                "link": link.url,
                "color": LINK_COLOR,
                "underline": True,
            }
        )
    if contact_parts:
        paragraph = doc.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.paragraph_format.space_after = Pt(6 * density)
        paragraph.paragraph_format.line_spacing = 1.0
        _add_parts(paragraph, contact_parts, small_size)

    if resume.summary:
        _add_heading(doc, "Summary", density)
        paragraph = doc.add_paragraph()
        paragraph.paragraph_format.space_after = Pt(2 * density)
        paragraph.paragraph_format.line_spacing = 1.0
        _add_text(paragraph, resume.summary, body_size)

    if resume.education:
        _add_heading(doc, "Education", density, gap_after=10)
        for education in resume.education:
            _add_line_with_right(
                doc,
                [{"text": education.institution, "bold": True}],
                [{"text": education.dates, "bold": True}] if education.dates else None,
                body_size,
                density,
                after=1.0,
            )
            details = " | ".join(item for item in education.details if item)
            if education.degree or details:
                left: list[dict] = []
                right: list[dict] = []
                if education.degree:
                    left.append({"text": education.degree})
                if details:
                    right.append({"text": details})
                _add_line_with_right(
                    doc, left, right or None, body_size, density, after=4.0
                )

    if resume.skills:
        _add_heading(doc, "Skills", density)
        last_skill = None
        for group in resume.skills:
            paragraph = doc.add_paragraph()
            fmt = paragraph.paragraph_format
            fmt.left_indent = Inches(SMALL_BULLET_INDENT)
            fmt.first_line_indent = Inches(-SMALL_BULLET_INDENT)
            fmt.space_before = Pt(0)
            fmt.space_after = Pt(1 * density)
            fmt.line_spacing = 1.0
            fmt.tab_stops.add_tab_stop(Inches(SMALL_BULLET_INDENT))
            _add_text(paragraph, "●\t", small_size)
            _add_text(paragraph, f"{group.category}: ", small_size, bold=True)
            _add_text(paragraph, ", ".join(group.items), small_size)
            last_skill = paragraph
        if last_skill is not None:
            last_skill.paragraph_format.space_after = Pt(4 * density)

    if resume.projects:
        _add_heading(doc, "Projects", density)
        for project in resume.projects:
            left: list[dict] = []
            if project.link:
                left.append(
                    {
                        "text": project.title,
                        "link": project.link,
                        "bold": True,
                        "color": LINK_COLOR,
                        "underline": True,
                    }
                )
            else:
                left.append({"text": project.title, "bold": True})
            if project.subtitle:
                left.append({"text": f" | {project.subtitle}", "bold": True})
            if project.tech:
                left.append(
                    {
                        "text": " | " + ", ".join(project.tech),
                        "italic": not bool(project.subtitle),
                    }
                )
            _add_line_with_right(
                doc, left, None, body_size, density, after=1.0
            )
            last_bullet = None
            for bullet in project.bullets:
                last_bullet = _add_bullet(doc, bullet, density, body_size, after=1.0)
            if last_bullet is not None:
                last_bullet.paragraph_format.space_after = Pt(3 * density)

    if resume.experience:
        _add_heading(doc, "Experience", density, gap_after=10)
        for experience in resume.experience:
            left: list[dict] = [{"text": experience.title, "bold": True}]
            if experience.organization:
                left.append({"text": f" | {experience.organization}", "bold": True})
            right = (
                [{"text": f"| {experience.dates}", "bold": True}]
                if experience.dates
                else None
            )
            _add_line_with_right(doc, left, right, body_size, density, after=1.0)
            last_bullet = None
            for bullet in experience.bullets:
                last_bullet = _add_bullet(doc, bullet, density, body_size, after=1.0)
            if last_bullet is not None:
                last_bullet.paragraph_format.space_after = Pt(3 * density)

    if resume.extracurricular:
        _add_heading(doc, "Extracurricular", density, gap_after=10)
        for activity in resume.extracurricular:
            left: list[dict] = [{"text": activity.title, "bold": True}]
            if activity.subtitle:
                left.append({"text": f" | {activity.subtitle}", "bold": True})
            _add_line_with_right(doc, left, None, body_size, density, after=1.0)
            last_bullet = None
            for bullet in activity.bullets:
                last_bullet = _add_bullet(
                    doc,
                    bullet,
                    density,
                    body_size,
                    after=1.0,
                    indent=SMALL_BULLET_INDENT,
                    hanging=SMALL_BULLET_INDENT,
                )
            if last_bullet is not None:
                last_bullet.paragraph_format.space_after = Pt(3 * density)

    if resume.certifications or resume.languages:
        _add_heading(doc, "Certifications & Languages", density, gap_after=10)
        for certification in resume.certifications:
            paragraph = doc.add_paragraph()
            paragraph.paragraph_format.space_after = Pt(0.8 * density)
            paragraph.paragraph_format.line_spacing = 1.0
            _add_text(paragraph, certification.name, small_size, bold=True)
            if certification.issuer:
                _add_text(paragraph, f" – {certification.issuer}", small_size)
        if resume.languages:
            paragraph = doc.add_paragraph()
            paragraph.paragraph_format.space_after = Pt(0)
            paragraph.paragraph_format.line_spacing = 1.0
            _add_text(paragraph, "Languages: ", small_size, bold=True)
            _add_text(paragraph, " | ".join(resume.languages), small_size)

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path
