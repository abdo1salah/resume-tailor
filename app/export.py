from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .email import save_email_files
from .models import EmailDraft, MasterResume, RunRecord, TailoredResume
from .render_pdf import render_with_fit


def sanitize_company(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".")
    return cleaned[:80] or "Company"


def unique_output_dir(root: Path, company: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    base_name = sanitize_company(company)
    base = root / base_name
    if not base.exists() or (base.is_dir() and not any(base.iterdir())):
        base.mkdir(parents=True, exist_ok=True)
        return base
    for index in range(2, 200):
        candidate = root / f"{base_name}_{index}"
        if not candidate.exists():
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
    raise RuntimeError("Could not find an available output folder name.")


def _resume_stem(profile) -> str:
    stem = re.sub(r"[^A-Za-z0-9]+", "_", profile.name or "Resume").strip("_")
    return (stem or "Resume") + "_Resume"


def _write_report(
    path: Path,
    run: RunRecord,
    resume: TailoredResume,
    files: list[str],
    include_email: bool,
) -> None:
    report = resume.report
    lines = [
        f"# Application: {report.company or run.request.company}"
        + (f" — {report.role or run.request.role}" if (report.role or run.request.role) else ""),
        "",
        f"- Generated: {run.created_at}",
        f"- Provider: {report.provider}",
        f"- Model: {report.model}",
        f"- Mode: {report.mode}",
        f"- Resume pages: {report.pages}",
        f"- Email files: {'yes' if include_email else 'no'}",
        "",
        "## Keyword match",
        "",
        "Matched: " + (", ".join(report.matched_keywords) or "none"),
        "",
        "Missing / not evidenced: " + (", ".join(report.missing_keywords) or "none"),
        "",
        "## Selection",
        "",
    ]
    for selection in report.selections:
        lines.append(
            f"- {selection.kind.title()}: **{selection.title}**"
            + (f" — {selection.reason}" if selection.reason else "")
        )
    if report.notes:
        lines.extend(["", "## Notes", "", report.notes])
    if report.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in report.warnings)
    lines.extend(
        [
            "",
            "## Files",
            "",
            *[f"- {name}" for name in files],
            "",
            "The master resume was not modified. Edit data/master.yaml to change the source of truth.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_application(
    run: RunRecord,
    master: MasterResume,
    resume: TailoredResume,
    email: EmailDraft | None,
    include_email: bool,
    output_root: Path,
    existing_dir: Path | None = None,
) -> dict[str, Any]:
    if existing_dir and existing_dir.exists() and existing_dir.is_dir():
        out_dir = existing_dir
    else:
        out_dir = unique_output_dir(
            output_root, resume.report.company or run.request.company
        )
    stem = _resume_stem(resume.profile)
    docx_path = out_dir / f"{stem}.docx"
    pdf_path = out_dir / f"{stem}.pdf"

    rendered, pages, _links = render_with_fit(resume, docx_path, pdf_path)

    files: list[str] = [pdf_path.name, docx_path.name]
    email_saved = False
    if include_email and email and email.subject and email.body:
        email_paths = save_email_files(email, pdf_path, out_dir, resume.profile)
        files.extend(path.name for path in email_paths)
        email_saved = True

    report_path = out_dir / "tailoring_report.md"
    files.append(report_path.name)
    _write_report(report_path, run, rendered, files, email_saved)

    return {
        "folder": str(out_dir),
        "files": files,
        "pages": pages,
        "trimmed": rendered.report.trimmed,
        "warnings": rendered.report.warnings,
        "email_saved": email_saved,
        "resume": rendered,
    }
