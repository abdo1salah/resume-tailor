from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from docx import Document
from pypdf import PdfReader

from .config import BACKUPS_DIR, MASTER_PATH, RESUMES_DIR, load_config
from .jd import KNOWN_TECH
from .llm import chat_json
from .models import (
    ActivityItem,
    Bullet,
    Certification,
    EducationItem,
    ExperienceItem,
    Link,
    MasterResume,
    Profile,
    Project,
    SkillGroup,
)

SUPPORTED_SUFFIXES = {".pdf", ".docx"}

HEADINGS = {
    "education": "education",
    "academic background": "education",
    "academics": "education",
    "skills": "skills",
    "technical skills": "skills",
    "technical proficiencies": "skills",
    "technologies": "skills",
    "core competencies": "skills",
    "skills summary": "skills",
    "skills interests": "skills",
    "projects": "projects",
    "personal projects": "projects",
    "selected projects": "projects",
    "academic projects": "projects",
    "experience": "experience",
    "work experience": "experience",
    "professional experience": "experience",
    "employment": "experience",
    "employment history": "experience",
    "internships": "experience",
    "internship experience": "experience",
    "extracurricular": "extracurricular",
    "extracurricular activities": "extracurricular",
    "activities": "extracurricular",
    "leadership": "extracurricular",
    "leadership experience": "extracurricular",
    "volunteering": "extracurricular",
    "volunteer experience": "extracurricular",
    "certifications": "certifications",
    "certificates": "certifications",
    "licenses": "certifications",
    "certifications and licenses": "certifications",
    "certifications languages": "certifications",
    "certifications and languages": "certifications",
    "certifications licenses languages": "certifications",
    "licenses certifications languages": "certifications",
    "languages": "languages",
    "summary": "summary",
    "profile": "summary",
    "professional summary": "summary",
    "objective": "summary",
    "about me": "summary",
}

VERB_START = re.compile(
    r"^(architected|built|created|designed|developed|deployed|engineered|"
    r"implemented|integrated|led|managed|secured|worked|collaborated|"
    r"automated|optimized|improved|delivered|analyzed|trained|evaluated|"
    r"maintained|contributed|supported|launched|migrated|reduced|increased)\b",
    re.IGNORECASE,
)
BULLET_RE = re.compile(r"^\s*(?:[•●▪◦*-]|\d+[.)])\s+")
MONTHS = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?)"
)
DATE_RE = re.compile(
    rf"(?:{MONTHS}\s+\d{{4}}\s*(?:[-–—]|to)\s*(?:{MONTHS}\s+)?\d{{4}})"
    rf"|(?:\b(?:19|20)\d{{2}}\s*(?:[-–—]|to)\s*(?:19|20)\d{{2}}\b)"
    rf"|(?:{MONTHS}\s+\d{{4}})"
    rf"|(?:\b(?:19|20)\d{{2}}\b)",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"')\]]+", re.IGNORECASE)


@dataclass
class ImportResult:
    master: MasterResume
    files: list[Path]
    mode: str
    output_path: Path | None = None
    backup_path: Path | None = None
    warnings: list[str] = field(default_factory=list)


def list_resumes(directory: Path | None = None) -> list[Path]:
    directory = directory or RESUMES_DIR
    if not directory.exists():
        return []
    files = [
        path
        for path in directory.iterdir()
        if path.is_file()
        and path.suffix.lower() in SUPPORTED_SUFFIXES
        and not path.name.startswith("~$")
    ]
    return sorted(files, key=lambda path: path.name.lower())


def _term_in_text(term: str, text: str) -> bool:
    term = term.lower().strip()
    if not term:
        return False
    if len(term) <= 4 or any(ch in term for ch in "+#./"):
        pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
        return re.search(pattern, text) is not None
    return term in text


def _tags_for(text: str, limit: int = 12) -> list[str]:
    lowered = text.lower()
    tags: list[str] = []
    for term in sorted(KNOWN_TECH, key=lambda item: (-len(item), item)):
        if _term_in_text(term, lowered) and term not in tags:
            tags.append(term)
        if len(tags) >= limit:
            break
    return tags


def _clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\xa0", " ").replace("\t", " ").replace("\u00ad", "")
    text = re.sub(r"[ ]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        document = Document(str(path))
        parts = [paragraph.text for paragraph in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))
        for section in document.sections:
            parts.extend(paragraph.text for paragraph in section.header.paragraphs)
            parts.extend(paragraph.text for paragraph in section.footer.paragraphs)
        links: list[str] = []
        for relation in document.part.rels.values():
            if "hyperlink" in relation.reltype and relation.is_external:
                target = relation.target_ref
                if target and target not in links:
                    links.append(target)
        if links:
            parts.append("LINKS:\n" + "\n".join(links))
        return _clean_text("\n".join(parts))

    if suffix == ".pdf":
        reader = PdfReader(str(path))
        parts = []
        for page in reader.pages:
            try:
                page_text = page.extract_text(extraction_mode="layout") or ""
            except TypeError:
                page_text = page.extract_text() or ""
            parts.append(page_text)
        links: list[str] = []
        for page in reader.pages:
            for reference in page.get("/Annots") or []:
                action = reference.get_object().get("/A")
                if not action:
                    continue
                uri = action.get_object().get("/URI")
                if uri and str(uri) not in links:
                    links.append(str(uri))
        if links:
            parts.append("LINKS:\n" + "\n".join(links))
        return _clean_text("\n".join(parts))

    raise ValueError(f"Unsupported resume format: {path.suffix}. Use PDF or DOCX.")


def _urls(text: str) -> list[str]:
    found: list[str] = []
    for match in URL_RE.findall(text):
        url = match.rstrip(".,;:)]}")
        if url.lower().startswith("www."):
            url = "https://" + url
        if url not in found:
            found.append(url)
    return found


def _link_label(url: str) -> str:
    lowered = url.lower()
    if "linkedin" in lowered:
        return "LinkedIn"
    if "github" in lowered:
        return "GitHub"
    if "gitlab" in lowered:
        return "GitLab"
    host = re.sub(r"^https?://", "", lowered).split("/")[0]
    return host.replace("www.", "") or "Link"


def _links_from(urls: list[str]) -> list[Link]:
    links: list[Link] = []
    seen: set[str] = set()
    for url in urls:
        normalized = url.rstrip("/")
        if normalized in seen:
            continue
        seen.add(normalized)
        links.append(Link(label=_link_label(url), url=url))
    return links


def _heading_for(line: str) -> str | None:
    cleaned = re.sub(r"^[•●▪◦*-]\s*", "", line).strip()
    if not cleaned or len(cleaned) > 60:
        return None
    normalized = re.sub(r"[^a-z ]", " ", cleaned.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if normalized in HEADINGS:
        return HEADINGS[normalized]
    if ":" in cleaned:
        left, right = cleaned.split(":", 1)
        left_norm = re.sub(r"[^a-z ]", " ", left.lower())
        left_norm = re.sub(r"\s+", " ", left_norm).strip()
        if right.strip() and left_norm in HEADINGS:
            return HEADINGS[left_norm]
    return None


def _content_lines(text: str) -> list[str]:
    lines: list[str] = []
    in_links = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        lowered = line.lower()
        if re.fullmatch(r"(?:page\s*)?\d+(?:\s*of\s*\d+)?", lowered):
            continue
        if in_links:
            if _heading_for(line):
                in_links = False
            else:
                continue
        if lowered in {"links:", "link:", "urls:", "url:"}:
            in_links = True
            continue
        lines.append(line)
    return lines


def _split_sections(lines: list[str]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"header": []}
    current = "header"
    for line in lines:
        heading = _heading_for(line)
        if current == "skills" and heading == "languages" and ":" in line:
            heading = None
        if heading:
            current = heading
            sections.setdefault(current, [])
            if ":" in line and len(line.split(":", 1)[0].split()) <= 5:
                remainder = line.split(":", 1)[1].strip()
                if remainder:
                    sections[current].append(remainder)
            continue
        if current == "summary":
            continue
        if current == "certifications":
            language_match = re.match(r"^languages?\b\s*[:\-–—]?\s*(.*)$", line, re.I)
            if language_match:
                remainder = language_match.group(1).strip()
                if remainder:
                    sections.setdefault("languages", []).append(remainder)
                continue
        sections.setdefault(current, []).append(line)
    return sections


def _is_bullet(line: str) -> bool:
    return BULLET_RE.match(line) is not None


def _strip_bullet(line: str) -> str:
    return BULLET_RE.sub("", line, count=1).strip()


def _date_in(text: str) -> str:
    match = DATE_RE.search(text)
    return match.group(0).strip() if match else ""


def _is_date_only(text: str) -> bool:
    return bool(DATE_RE.fullmatch(text.strip()))


def _split_parts(line: str) -> list[str]:
    return [part.strip() for part in re.split(r"\s*\|\s*|\s+—\s+", line) if part.strip()]


def _split_items(line: str) -> list[str]:
    parts = re.split(r"[,;|•●▪◦]", line)
    return [part.strip(" -–—") for part in parts if part.strip(" -–—")]


def _clean_bullet_text(text: str) -> str:
    text = _strip_bullet(text)
    text = URL_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" -–—|")
    return text[:500]


def _phone_in(text: str) -> str:
    for line in text.splitlines():
        if re.search(r"\b(?:19|20)\d{2}\b", line) and not re.search(
            r"\+?\d[\d\s().-]{8,}\d", line
        ):
            continue
        match = re.search(r"\+?\d[\d\s().-]{7,}\d", line)
        if not match:
            continue
        digits = re.sub(r"\D", "", match.group(0))
        if 8 <= len(digits) <= 16:
            return match.group(0).strip()
    return ""


def _profile(
    header: list[str], full_text: str, source_name: str
) -> tuple[Profile, str]:
    email_match = EMAIL_RE.search(full_text)
    email = email_match.group(0) if email_match else ""
    phone = _phone_in("\n".join(header) or full_text)

    urls = _urls("\n".join(header))
    if not urls:
        personal = [
            url
            for url in _urls(full_text)
            if "linkedin" in url.lower()
            or re.search(r"https?://(?:www\.)?github\.com/[^/]+/?$", url, re.I)
        ]
        urls = personal
    links = _links_from(urls)

    candidates: list[str] = []
    for line in header:
        if EMAIL_RE.search(line) or URL_RE.search(line) or _heading_for(line):
            continue
        if re.search(r"\+?\d[\d\s().-]{7,}\d", line):
            continue
        if re.search(r"\b(?:19|20)\d{2}\b", line):
            continue
        if line.lower() in {"resume", "curriculum vitae", "cv"}:
            continue
        if len(line) > 90:
            continue
        candidates.append(line)

    name = ""
    headline = ""
    if candidates:
        first = candidates[0]
        parts = _split_parts(first)
        if len(parts) >= 2 and len(parts[0].split()) <= 5:
            name = parts[0]
            headline = " | ".join(parts[1:])
        else:
            name = first
        if not headline and len(candidates) > 1:
            second = candidates[1]
            if len(second.split()) <= 12 and not second.endswith("."):
                headline = second

    if not name:
        name = re.sub(r"[_-]+", " ", source_name).strip() or "Your Name"
    return (
        Profile(
            name=name,
            headline=headline,
            phone=phone,
            email=email,
            location="",
            links=links,
        ),
        name,
    )


def _group_entries(lines: list[str], *, allow_subtitle_merge: bool = False) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in lines:
        if _is_bullet(line):
            if current is None:
                current = {"title_line": "Untitled", "bullets": []}
                entries.append(current)
            current["bullets"].append(_strip_bullet(line))
            continue
        if current is None:
            current = {"title_line": line, "bullets": []}
            entries.append(current)
            continue
        if URL_RE.fullmatch(line.strip()):
            current["bullets"].append(line.strip())
            continue
        if allow_subtitle_merge and not current["bullets"]:
            if len(line) <= 90 and (
                "|" in line or "project" in line.lower() or "capstone" in line.lower()
            ):
                current["title_line"] = current["title_line"] + " | " + line
                continue
        if (
            len(line) > 100
            or line.endswith((".", ";", ":"))
            or VERB_START.match(line)
            or line[:1].islower()
        ):
            current["bullets"].append(line)
            continue
        current = {"title_line": line, "bullets": []}
        entries.append(current)
    return entries


def _unique_id(title: str, used: set[str], fallback: str = "item") -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    base = base[:48] or fallback
    candidate = base
    index = 2
    while candidate in used:
        candidate = f"{base}-{index}"
        index += 1
    used.add(candidate)
    return candidate


def _parse_skills(lines: list[str]) -> list[SkillGroup]:
    groups: list[SkillGroup] = []
    pending: str | None = None
    previous_ended_comma = False
    for line in lines:
        is_bullet = _is_bullet(line)
        cleaned = _strip_bullet(line).strip()
        if not cleaned:
            continue
        if ":" in cleaned:
            left, right = cleaned.split(":", 1)
            left = left.strip()
            if 0 < len(left.split()) <= 5 and right.strip():
                items = _split_items(right)
                if items:
                    groups.append(SkillGroup(category=left, items=items))
                pending = None
                previous_ended_comma = cleaned.endswith(",")
                continue
        items = _split_items(cleaned)
        continuation = groups and not is_bullet and (
            cleaned[:1].islower() or previous_ended_comma
        )
        if pending:
            groups.append(SkillGroup(category=pending, items=items))
            pending = None
        elif continuation:
            existing = {item.lower() for item in groups[-1].items}
            for item in items:
                if item.lower() not in existing:
                    groups[-1].items.append(item)
                    existing.add(item.lower())
        elif len(items) == 1 and len(cleaned.split()) <= 3 and not cleaned.endswith("."):
            pending = cleaned
        else:
            groups.append(SkillGroup(category="Skills", items=items))
        previous_ended_comma = cleaned.endswith(",")
    if pending:
        groups.append(SkillGroup(category=pending, items=[]))

    merged: dict[str, SkillGroup] = {}
    for group in groups:
        key = group.category.lower()
        if key not in merged:
            merged[key] = SkillGroup(category=group.category, items=[])
        existing = {item.lower() for item in merged[key].items}
        for item in group.items:
            if item.lower() not in existing:
                merged[key].items.append(item)
                existing.add(item.lower())
    return [group for group in merged.values() if group.items]


def _parse_education(lines: list[str], used: set[str]) -> list[EducationItem]:
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in lines:
        cleaned = _strip_bullet(line)
        if not cleaned:
            continue
        starts_entry = bool(
            DATE_RE.search(cleaned)
            or re.search(
                r"\b(university|college|faculty|institute|school|academy)\b",
                cleaned,
                re.I,
            )
            or "|" in cleaned
        )
        if current is None or starts_entry:
            current = {"line": cleaned, "details": []}
            entries.append(current)
        else:
            current["details"].append(cleaned)

    result: list[EducationItem] = []
    for entry in entries:
        dates = _date_in(entry["line"])
        line = DATE_RE.sub("", entry["line"]).strip(" |–—-,")
        parts = _split_parts(line)
        institution = parts[0] if parts else "Institution"
        degree = parts[1] if len(parts) > 1 else ""
        details = [_clean_bullet_text(item) for item in entry["details"]]
        details = [item for item in details if item and not _is_date_only(item)]
        if not degree:
            degree_match = re.match(
                r"^(B\.?Sc\.?|M\.?Sc\.?|Bachelor(?:'s)?|Master(?:'s)?|PhD|Doctor(?:ate)?|Diploma)\b.*",
                details[0] if details else "",
                re.I,
            )
            if degree_match:
                degree = degree_match.group(0)
                details = details[1:]
        gpa_match = re.search(r"\bGPA\b.*", degree, re.I)
        if gpa_match:
            if gpa_match.group(0).strip():
                details.insert(0, gpa_match.group(0).strip())
            degree = degree[: gpa_match.start()].strip(" |–—,-")
        result.append(
            EducationItem(
                institution=institution,
                degree=degree,
                dates=dates,
                details=details[:6],
            )
        )
    return result


def _parse_projects(lines: list[str], used: set[str]) -> list[Project]:
    result: list[Project] = []
    for entry in _group_entries(lines, allow_subtitle_merge=True):
        title_line = entry["title_line"]
        parts = _split_parts(title_line)
        title = parts[0] if parts else "Project"
        subtitle_parts = parts[1:] if len(parts) > 1 else []
        dates = _date_in(title_line)
        if dates:
            subtitle_parts = [
                part.replace(dates, "").strip(" |–—-") for part in subtitle_parts
            ]
        subtitle = " | ".join(part for part in subtitle_parts if part)
        raw_bullets = [_clean_bullet_text(item) for item in entry["bullets"]]
        bullets = [
            item
            for item in raw_bullets
            if item and not _is_date_only(item) and not URL_RE.fullmatch(item)
        ]
        combined = " ".join([title, subtitle, *bullets])
        link = next(iter(_urls(" ".join([title_line, *entry["bullets"]]))), "")
        tags = _tags_for(combined)
        tech = tags[:6]
        result.append(
            Project(
                id=_unique_id(title, used, "project"),
                title=title,
                subtitle=subtitle,
                link=link,
                tech=tech,
                tags=tags,
                bullets=[
                    Bullet(text=item, tags=_tags_for(item, 8)) for item in bullets[:8]
                ],
            )
        )
    return result


def _parse_experience(lines: list[str], used: set[str]) -> list[ExperienceItem]:
    result: list[ExperienceItem] = []
    for entry in _group_entries(lines):
        title_line = entry["title_line"]
        parts = _split_parts(title_line)
        title = parts[0] if parts else "Role"
        organization = parts[1] if len(parts) > 1 else ""
        dates = _date_in(title_line)
        if len(parts) > 2:
            organization = organization or parts[1]
        organization = organization.replace(dates, "").strip(" |–—-")
        raw_bullets = [_clean_bullet_text(item) for item in entry["bullets"]]
        bullets = [
            item
            for item in raw_bullets
            if item and not _is_date_only(item) and not URL_RE.fullmatch(item)
        ]
        combined = " ".join([title, organization, *bullets])
        tags = _tags_for(combined)
        result.append(
            ExperienceItem(
                id=_unique_id(title, used, "experience"),
                title=title,
                organization=organization,
                dates=dates,
                location="",
                tags=tags,
                bullets=[
                    Bullet(text=item, tags=_tags_for(item, 8)) for item in bullets[:8]
                ],
            )
        )
    return result


def _parse_activities(lines: list[str], used: set[str]) -> list[ActivityItem]:
    result: list[ActivityItem] = []
    for entry in _group_entries(lines, allow_subtitle_merge=True):
        title_line = entry["title_line"]
        parts = _split_parts(title_line)
        title = parts[0] if parts else "Activity"
        subtitle = " | ".join(parts[1:]) if len(parts) > 1 else ""
        raw_bullets = [_clean_bullet_text(item) for item in entry["bullets"]]
        bullets = [
            item
            for item in raw_bullets
            if item and not _is_date_only(item) and not URL_RE.fullmatch(item)
        ]
        combined = " ".join([title, subtitle, *bullets])
        result.append(
            ActivityItem(
                id=_unique_id(title, used, "activity"),
                title=title,
                subtitle=subtitle,
                tags=_tags_for(combined),
                bullets=[
                    Bullet(text=item, tags=_tags_for(item, 8)) for item in bullets[:6]
                ],
            )
        )
    return result


def _parse_certifications(lines: list[str]) -> list[Certification]:
    result: list[Certification] = []
    seen: set[str] = set()
    for line in lines:
        cleaned = _strip_bullet(line).strip()
        if not cleaned:
            continue
        parts = _split_parts(cleaned)
        name = parts[0].strip()
        issuer = parts[1].strip() if len(parts) > 1 else ""
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            result.append(Certification(name=name, issuer=issuer))
    return result


def _parse_languages(lines: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for line in lines:
        for item in _split_items(_strip_bullet(line)):
            key = item.lower()
            if item and key not in seen:
                seen.add(key)
                result.append(item)
    return result


def fallback_parse(text: str, source_name: str) -> MasterResume:
    lines = _content_lines(text)
    sections = _split_sections(lines)
    header = sections.pop("header", [])
    profile, name = _profile(header or lines[:12], text, source_name)

    project_ids: set[str] = set()
    experience_ids: set[str] = set()
    activity_ids: set[str] = set()
    return MasterResume(
        version=1,
        profile=profile,
        education=_parse_education(sections.get("education", []), set()),
        skills=_parse_skills(sections.get("skills", [])),
        projects=_parse_projects(sections.get("projects", []), project_ids),
        experience=_parse_experience(
            sections.get("experience", []), experience_ids
        ),
        extracurricular=_parse_activities(
            sections.get("extracurricular", []), activity_ids
        ),
        certifications=_parse_certifications(sections.get("certifications", [])),
        languages=_parse_languages(sections.get("languages", [])),
    )


def _prompt_payload(text: str, source_name: str) -> str:
    schema = {
        "version": 1,
        "profile": {
            "name": "string",
            "headline": "string",
            "phone": "string",
            "email": "string",
            "location": "string",
            "links": [{"label": "string", "url": "string"}],
        },
        "education": [
            {
                "institution": "string",
                "degree": "string",
                "dates": "string",
                "details": ["string"],
            }
        ],
        "skills": [{"category": "string", "items": ["string"]}],
        "projects": [
            {
                "id": "slug",
                "title": "string",
                "subtitle": "string",
                "link": "string",
                "tech": ["string"],
                "tags": ["lowercase string"],
                "bullets": [{"text": "string", "tags": ["lowercase string"]}],
            }
        ],
        "experience": [
            {
                "id": "slug",
                "title": "string",
                "organization": "string",
                "dates": "string",
                "location": "string",
                "tags": ["lowercase string"],
                "bullets": [{"text": "string", "tags": ["lowercase string"]}],
            }
        ],
        "extracurricular": [
            {
                "id": "slug",
                "title": "string",
                "subtitle": "string",
                "tags": ["lowercase string"],
                "bullets": [{"text": "string", "tags": ["lowercase string"]}],
            }
        ],
        "certifications": [{"name": "string", "issuer": "string"}],
        "languages": ["string"],
    }
    return f"""Extract the resume below into the exact JSON shape.
Rules:
1. Use only facts present in the resume. Never invent anything.
2. Keep bullet text verbatim or minimally cleaned. Remove bullet glyphs.
3. Put personal contact links (GitHub profile, LinkedIn, portfolio) in profile.links. Put project repository links on the matching project.link.
4. Use lowercase hyphenated ids. Tags are lowercase technologies or keywords found in the text.
5. Keep all sections, even if empty. Use [] for empty lists.
6. The source filename is {source_name!r}. If the name is missing, use a cleaned version of the filename.
7. Return only one valid JSON object, no markdown.

JSON SHAPE:
{json.dumps(schema, indent=2)}

RESUME TEXT:
{text[:24000]}
"""


def _normalize_items(items: Any) -> list[dict[str, Any]]:
    if items is None:
        return []
    if isinstance(items, dict):
        return [items]
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    return []


def _normalize_bullets(values: Any) -> list[dict[str, Any]]:
    if values is None:
        return []
    if isinstance(values, (str, dict)):
        values = [values]
    result: list[dict[str, Any]] = []
    for value in values:
        if isinstance(value, str):
            text = _clean_bullet_text(value)
            if text:
                result.append({"text": text, "tags": _tags_for(text, 8)})
        elif isinstance(value, dict):
            text = _clean_bullet_text(str(value.get("text") or ""))
            if text:
                tags = value.get("tags")
                if not isinstance(tags, list):
                    tags = _tags_for(text, 8)
                result.append(
                    {
                        "text": text,
                        "tags": [str(tag).lower() for tag in tags if str(tag).strip()],
                    }
                )
    return result


def _normalize_links(values: Any) -> list[dict[str, str]]:
    if values is None:
        return []
    if isinstance(values, dict):
        values = [values]
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, str):
            url = value.strip()
            label = _link_label(url)
        elif isinstance(value, dict):
            url = str(value.get("url") or "").strip()
            label = str(value.get("label") or "").strip() or _link_label(url)
        else:
            continue
        if not url or url in seen:
            continue
        seen.add(url)
        result.append({"label": label, "url": url})
    return result


def normalize_parsed(raw: dict[str, Any], source_name: str) -> MasterResume:
    profile_raw = raw.get("profile") if isinstance(raw.get("profile"), dict) else {}
    name = str(profile_raw.get("name") or "").strip()
    if not name:
        name = re.sub(r"[_-]+", " ", source_name).strip() or "Your Name"

    skills: list[dict[str, Any]] = []
    for group in _normalize_items(raw.get("skills")):
        category = str(group.get("category") or "Skills").strip() or "Skills"
        items = group.get("items")
        if isinstance(items, str):
            items = _split_items(items)
        elif not isinstance(items, list):
            items = []
        cleaned = [str(item).strip() for item in items if str(item).strip()]
        if cleaned:
            skills.append({"category": category, "items": cleaned})

    project_ids: set[str] = set()
    projects: list[dict[str, Any]] = []
    for project in _normalize_items(raw.get("projects")):
        title = str(project.get("title") or "Project").strip() or "Project"
        bullets = _normalize_bullets(project.get("bullets"))
        tags = project.get("tags")
        if not isinstance(tags, list):
            tags = _tags_for(" ".join([title, *[b["text"] for b in bullets]]))
        tech = project.get("tech")
        if isinstance(tech, str):
            tech = _split_items(tech)
        elif not isinstance(tech, list):
            tech = []
        projects.append(
            {
                "id": str(project.get("id") or "").strip()
                or _unique_id(title, project_ids, "project"),
                "title": title,
                "subtitle": str(project.get("subtitle") or "").strip(),
                "link": str(project.get("link") or "").strip(),
                "tech": [str(item).strip() for item in tech if str(item).strip()],
                "tags": [str(tag).strip().lower() for tag in tags if str(tag).strip()],
                "bullets": bullets,
            }
        )

    experience_ids: set[str] = set()
    experiences: list[dict[str, Any]] = []
    for item in _normalize_items(raw.get("experience")):
        title = str(item.get("title") or "Role").strip() or "Role"
        bullets = _normalize_bullets(item.get("bullets"))
        tags = item.get("tags")
        if not isinstance(tags, list):
            tags = _tags_for(" ".join([title, *[b["text"] for b in bullets]]))
        experiences.append(
            {
                "id": str(item.get("id") or "").strip()
                or _unique_id(title, experience_ids, "experience"),
                "title": title,
                "organization": str(item.get("organization") or "").strip(),
                "dates": str(item.get("dates") or "").strip(),
                "location": str(item.get("location") or "").strip(),
                "tags": [str(tag).strip().lower() for tag in tags if str(tag).strip()],
                "bullets": bullets,
            }
        )

    activity_ids: set[str] = set()
    activities: list[dict[str, Any]] = []
    for item in _normalize_items(raw.get("extracurricular")):
        title = str(item.get("title") or "Activity").strip() or "Activity"
        bullets = _normalize_bullets(item.get("bullets"))
        tags = item.get("tags")
        if not isinstance(tags, list):
            tags = _tags_for(" ".join([title, *[b["text"] for b in bullets]]))
        activities.append(
            {
                "id": str(item.get("id") or "").strip()
                or _unique_id(title, activity_ids, "activity"),
                "title": title,
                "subtitle": str(item.get("subtitle") or "").strip(),
                "tags": [str(tag).strip().lower() for tag in tags if str(tag).strip()],
                "bullets": bullets,
            }
        )

    certifications: list[dict[str, str]] = []
    for item in _normalize_items(raw.get("certifications")):
        name_value = str(item.get("name") or "").strip()
        if name_value:
            certifications.append(
                {
                    "name": name_value,
                    "issuer": str(item.get("issuer") or "").strip(),
                }
            )

    languages_raw = raw.get("languages")
    if isinstance(languages_raw, str):
        languages = _split_items(languages_raw)
    elif isinstance(languages_raw, list):
        languages = [str(item).strip() for item in languages_raw if str(item).strip()]
    else:
        languages = []

    return MasterResume.model_validate(
        {
            "version": 1,
            "profile": {
                "name": name,
                "headline": str(profile_raw.get("headline") or "").strip(),
                "phone": str(profile_raw.get("phone") or "").strip(),
                "email": str(profile_raw.get("email") or "").strip(),
                "location": str(profile_raw.get("location") or "").strip(),
                "links": _normalize_links(profile_raw.get("links")),
            },
            "education": _normalize_items(raw.get("education")),
            "skills": skills,
            "projects": projects,
            "experience": experiences,
            "extracurricular": activities,
            "certifications": certifications,
            "languages": languages,
        }
    )


def parse_resume(
    path: Path,
    provider: str,
    model: str,
    *,
    use_llm: bool = True,
    cfg: dict[str, Any] | None = None,
) -> tuple[MasterResume, list[str], str]:
    cfg = cfg or load_config()
    text = extract_text(path)
    if not text.strip():
        raise ValueError(f"No text could be extracted from {path.name}.")
    source_name = re.sub(r"[_-]+", " ", path.stem).strip()
    warnings: list[str] = []
    mode = "fallback"

    if use_llm:
        try:
            raw = chat_json(
                provider,
                model,
                [
                    {
                        "role": "system",
                        "content": (
                            "You extract resumes into structured JSON. Use only facts "
                            "present in the text. Return only one valid JSON object."
                        ),
                    },
                    {"role": "user", "content": _prompt_payload(text, source_name)},
                ],
                cfg=cfg,
                temperature=0.1,
                max_tokens=4096,
            )
            master = normalize_parsed(raw, source_name)
            if master.projects or master.experience or master.skills:
                return master, warnings, "llm"
            warnings.append(
                f"{path.name}: the model returned no usable sections; used the text parser."
            )
        except Exception as exc:
            warnings.append(f"{path.name}: model extraction failed ({exc}); used the text parser.")

    master = fallback_parse(text, source_name)
    return master, warnings, mode


def _merge_by_key(
    base_items: list[Any],
    incoming_items: list[Any],
    key_fn,
    merge_fn,
) -> list[Any]:
    result = list(base_items)
    index = {key_fn(item): item for item in result}
    for item in incoming_items:
        key = key_fn(item)
        if key in index:
            merge_fn(index[key], item)
        else:
            result.append(item)
            index[key] = item
    return result


def _merge_bullets(target: list[Bullet], incoming: list[Bullet]) -> None:
    existing = {bullet.text.lower() for bullet in target}
    for bullet in incoming:
        if bullet.text.lower() not in existing:
            target.append(bullet)
            existing.add(bullet.text.lower())


def _identity(text: str) -> str:
    base = re.split(r"\s+[–—-]\s+|\s*\|\s*", text.lower(), maxsplit=1)[0]
    normalized = re.sub(r"[^a-z0-9+#.]+", " ", base)
    return re.sub(r"\s+", " ", normalized).strip() or text.lower().strip()


def _language_key(language: str) -> str:
    base = re.sub(r"\s*\([^)]*\)\s*", " ", language)
    return re.sub(r"\s+", " ", base).strip().lower() or language.lower().strip()


def _merge_languages(target: list[str], incoming: list[str]) -> list[str]:
    result: list[str] = []
    index: dict[str, int] = {}
    for language in [*target, *incoming]:
        key = _language_key(language)
        if key not in index:
            index[key] = len(result)
            result.append(language)
        elif len(language) > len(result[index[key]]):
            result[index[key]] = language
    return result


def _merge_education(target: EducationItem, source: EducationItem) -> None:
    target.degree = target.degree or source.degree
    target.dates = target.dates or source.dates
    existing = {detail.lower() for detail in target.details}
    for detail in source.details:
        if detail.lower() not in existing:
            target.details.append(detail)
            existing.add(detail.lower())


def _merge_skill_group(target: SkillGroup, source: SkillGroup) -> None:
    existing = {item.lower() for item in target.items}
    for item in source.items:
        if item.lower() not in existing:
            target.items.append(item)
            existing.add(item.lower())


def _merge_project(target: Project, source: Project) -> None:
    if len(source.title) > len(target.title):
        target.title = source.title
    if not target.subtitle or len(source.subtitle) > len(target.subtitle):
        target.subtitle = source.subtitle
    target.link = target.link or source.link
    _merge_bullets(target.bullets, source.bullets)
    _merge_unique(target.tech, source.tech)
    _merge_unique(target.tags, source.tags)


def _merge_experience(target: ExperienceItem, source: ExperienceItem) -> None:
    target.organization = target.organization or source.organization
    target.dates = target.dates or source.dates
    target.location = target.location or source.location
    _merge_bullets(target.bullets, source.bullets)
    _merge_unique(target.tags, source.tags)


def _merge_activity(target: ActivityItem, source: ActivityItem) -> None:
    if len(source.title) > len(target.title):
        target.title = source.title
    if not target.subtitle or len(source.subtitle) > len(target.subtitle):
        target.subtitle = source.subtitle
    _merge_bullets(target.bullets, source.bullets)
    _merge_unique(target.tags, source.tags)


def _merge_certification(target: Certification, source: Certification) -> None:
    if len(source.name) > len(target.name):
        target.name = source.name
    target.issuer = target.issuer or source.issuer


def merge_masters(
    base: MasterResume | None,
    incoming: MasterResume,
    mode: str = "merge",
) -> MasterResume:
    if base is None or mode == "replace":
        return incoming.model_copy(deep=True)

    profile = base.profile
    incoming_profile = incoming.profile
    profile.name = profile.name or incoming_profile.name
    profile.headline = profile.headline or incoming_profile.headline
    profile.phone = profile.phone or incoming_profile.phone
    profile.email = profile.email or incoming_profile.email
    profile.location = profile.location or incoming_profile.location
    profile.links = _merge_by_key(
        list(profile.links),
        list(incoming_profile.links),
        lambda link: link.url.rstrip("/").lower(),
        lambda _target, _source: None,
    )

    education = _merge_by_key(
        list(base.education),
        list(incoming.education),
        lambda item: (item.institution.lower(), item.degree.lower()),
        _merge_education,
    )

    skills = _merge_by_key(
        list(base.skills),
        list(incoming.skills),
        lambda group: group.category.lower(),
        _merge_skill_group,
    )

    projects = _merge_by_key(
        list(base.projects),
        list(incoming.projects),
        lambda project: _identity(project.title),
        _merge_project,
    )

    experience = _merge_by_key(
        list(base.experience),
        list(incoming.experience),
        lambda item: (_identity(item.title), _identity(item.organization)),
        _merge_experience,
    )

    activities = _merge_by_key(
        list(base.extracurricular),
        list(incoming.extracurricular),
        lambda item: _identity(item.title),
        _merge_activity,
    )

    certifications = _merge_by_key(
        list(base.certifications),
        list(incoming.certifications),
        lambda item: _identity(item.name),
        _merge_certification,
    )

    return MasterResume(
        version=1,
        profile=profile,
        education=education,
        skills=skills,
        projects=projects,
        experience=experience,
        extracurricular=activities,
        certifications=certifications,
        languages=_merge_languages(list(base.languages), list(incoming.languages)),
    )


def _merge_unique(target: list[str], incoming: list[str]) -> list[str]:
    existing = {item.lower() for item in target}
    for item in incoming:
        if item.lower() not in existing:
            target.append(item)
            existing.add(item.lower())
    return target


def parse_resumes(
    paths: list[Path],
    provider: str,
    model: str,
    *,
    use_llm: bool = True,
    cfg: dict[str, Any] | None = None,
) -> tuple[MasterResume, list[str]]:
    if not paths:
        raise ValueError("No resume files were selected.")

    parsed: list[MasterResume] = []
    warnings: list[str] = []
    names: set[str] = set()
    for path in paths:
        master, file_warnings, _mode = parse_resume(
            path, provider, model, use_llm=use_llm, cfg=cfg
        )
        warnings.extend(file_warnings)
        parsed.append(master)
        if master.profile.name:
            names.add(master.profile.name.strip().lower())

    if len(names) > 1:
        raise ValueError(
            "The selected resumes contain different names "
            f"({', '.join(sorted(names))}). Import one person's resumes at a time."
        )

    combined = parsed[0]
    for master in parsed[1:]:
        combined = merge_masters(combined, master, "merge")
    return combined, warnings


def read_master(path: Path = MASTER_PATH) -> MasterResume:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return MasterResume.model_validate(data)


def save_master(
    master: MasterResume,
    path: Path = MASTER_PATH,
    *,
    backup: bool = True,
) -> tuple[Path, Path | None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path: Path | None = None
    if backup and path.exists():
        backup_dir = BACKUPS_DIR if path == MASTER_PATH else path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_path = backup_dir / f"master-{stamp}.yaml"
        backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(
        yaml.safe_dump(
            master.model_dump(),
            sort_keys=False,
            allow_unicode=True,
            width=100,
        ),
        encoding="utf-8",
    )
    return path, backup_path


def master_summary(master: MasterResume) -> dict[str, Any]:
    return {
        "profile": {
            "name": master.profile.name,
            "headline": master.profile.headline,
            "email": master.profile.email,
            "phone": master.profile.phone,
            "links": [link.model_dump() for link in master.profile.links],
        },
        "counts": {
            "education": len(master.education),
            "skills": len(master.skills),
            "projects": len(master.projects),
            "experience": len(master.experience),
            "extracurricular": len(master.extracurricular),
            "certifications": len(master.certifications),
            "languages": len(master.languages),
        },
        "projects": [project.title for project in master.projects],
        "experience": [item.title for item in master.experience],
        "skills": [group.category for group in master.skills],
    }


def import_resumes(
    paths: list[Path],
    provider: str,
    model: str,
    *,
    mode: str = "merge",
    use_llm: bool = True,
    cfg: dict[str, Any] | None = None,
    master_path: Path = MASTER_PATH,
    dry_run: bool = False,
) -> ImportResult:
    cfg = cfg or load_config()
    if mode not in {"merge", "replace"}:
        raise ValueError("Mode must be 'merge' or 'replace'.")
    incoming, warnings = parse_resumes(
        paths, provider, model, use_llm=use_llm, cfg=cfg
    )

    existing: MasterResume | None = None
    if master_path.exists():
        try:
            existing = read_master(master_path)
        except Exception as exc:
            warnings.append(f"Could not read the existing master.yaml: {exc}")

    if existing and mode == "merge":
        if existing.profile.name.lower() != incoming.profile.name.lower():
            warnings.append(
                "The imported name differs from the current master name. "
                "Choose Replace if this is a different person."
            )
        master = merge_masters(existing, incoming, "merge")
    else:
        master = incoming

    output_path = None
    backup_path = None
    if not dry_run:
        output_path, backup_path = save_master(master, master_path)
    return ImportResult(
        master=master,
        files=paths,
        mode=mode,
        output_path=output_path,
        backup_path=backup_path,
        warnings=warnings,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build or update data/master.yaml from PDF/DOCX files in resumes/."
    )
    parser.add_argument("--list", action="store_true", help="List resume files and exit.")
    parser.add_argument(
        "--file",
        action="append",
        default=[],
        help="Resume file name or path. Repeat for multiple files. Defaults to all.",
    )
    parser.add_argument("--provider", default=None, help="nvidia or ollama")
    parser.add_argument("--model", default="", help="Model name. Defaults to config.")
    parser.add_argument(
        "--mode",
        choices=["merge", "replace"],
        default="merge",
        help="Merge into the current master or replace it.",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip the model and use the built-in text parser.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the result without writing master.yaml.",
    )
    parser.add_argument(
        "--output",
        default=str(MASTER_PATH),
        help="Output YAML path (default: data/master.yaml).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    cfg = load_config()
    if args.list:
        files = list_resumes()
        if not files:
            print(f"No PDF/DOCX files found in {RESUMES_DIR}")
            return 1
        for path in files:
            print(path.name)
        return 0

    if args.file:
        paths: list[Path] = []
        for value in args.file:
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = RESUMES_DIR / candidate
            if not candidate.exists():
                print(f"Not found: {candidate}")
                return 1
            paths.append(candidate)
    else:
        paths = list_resumes()
    if not paths:
        print(f"No PDF/DOCX files found in {RESUMES_DIR}. Add a resume first.")
        return 1

    provider = args.provider or cfg.get("provider") or "nvidia"
    model = args.model or (cfg.get("models") or {}).get(provider) or ""
    result = import_resumes(
        paths,
        provider,
        model,
        mode=args.mode,
        use_llm=not args.no_llm,
        cfg=cfg,
        master_path=Path(args.output),
        dry_run=args.dry_run,
    )
    summary = master_summary(result.master)
    print(f"Files: {', '.join(path.name for path in result.files)}")
    print(f"Provider/model: {provider} / {model or '(none)'}")
    print(f"Mode: {result.mode}")
    print(f"Profile: {summary['profile']['name']} — {summary['profile']['headline']}")
    print("Counts: " + ", ".join(f"{key}={value}" for key, value in summary["counts"].items()))
    if summary["projects"]:
        print("Projects: " + ", ".join(summary["projects"]))
    if summary["experience"]:
        print("Experience: " + ", ".join(summary["experience"]))
    if summary["skills"]:
        print("Skills: " + ", ".join(summary["skills"]))
    if result.warnings:
        print("Warnings:")
        for warning in result.warnings:
            print(f"  - {warning}")
    if result.output_path:
        print(f"Wrote: {result.output_path}")
        if result.backup_path:
            print(f"Backup: {result.backup_path}")
    else:
        print("Preview only; master.yaml was not changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
