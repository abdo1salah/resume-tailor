from __future__ import annotations

import json
import re
from datetime import datetime
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from typing import Any

from .config import load_config
from .llm import chat_json
from .models import EmailDraft, MasterResume, TailoredResume
from .tailor import master_numbers, numbers_in, unknown_tech_terms, _term_in_text


def _fallback_email(
    master: MasterResume,
    tailored: TailoredResume,
    company: str,
    role: str,
    recruiter_name: str,
    recruiter_email: str,
) -> EmailDraft:
    profile = master.profile
    greeting = f"Dear {recruiter_name}," if recruiter_name else "Dear Hiring Team,"
    subject = (
        f"Application for {role} – {profile.name}"
        if role
        else f"Application – {profile.name}"
    )

    headline = profile.headline or "AI/ML Engineer"
    article = "an" if headline[:1].lower() in "aeiou" else "a"

    matched = [item for item in tailored.report.matched_keywords if item][:3]
    if matched:
        skill_text = ", ".join(matched)
    else:
        skill_items: list[str] = []
        for group in tailored.skills[:2]:
            skill_items.extend(group.items[:2])
        skill_text = (
            ", ".join(skill_items[:3]) if skill_items else "AI and machine learning"
        )

    project_titles = [project.title for project in tailored.projects[:2]]
    if project_titles:
        projects_sentence = (
            "Recent projects include " + " and ".join(project_titles) + ". "
        )
    else:
        projects_sentence = ""

    company_text = f" at {company}" if company else ""
    role_text = f"the {role} role" if role else "an AI/ML role"
    body = (
        f"{greeting}\n\n"
        f"I am writing to apply for {role_text}{company_text}. I am {article} "
        f"{headline} with hands-on experience in {skill_text}. "
        f"{projects_sentence}"
        "I have attached my resume with the details of these projects and my training. "
        "I would welcome the opportunity to discuss how I can contribute to your team.\n\n"
        "Best regards,\n"
        f"{profile.name}\n"
        + (f"{profile.phone} | " if profile.phone else "")
        + (profile.email or "")
        + (
            "\n" + " | ".join(link.url for link in profile.links if link.url)
            if profile.links
            else ""
        )
    )
    return EmailDraft(
        subject=subject,
        body=body.strip(),
        recruiter_email=recruiter_email,
        company=company,
        role=role,
    )


def generate_email(
    master: MasterResume,
    tailored: TailoredResume,
    jd: str,
    company: str,
    role: str,
    recruiter_name: str,
    recruiter_email: str,
    notes: str,
    provider: str,
    model: str,
    cfg: dict[str, Any] | None = None,
) -> EmailDraft:
    cfg = cfg or load_config()
    profile = master.profile
    evidence = {
        "projects": [
            {
                "title": project.title,
                "bullets": project.bullets,
                "tech": project.tech,
            }
            for project in tailored.projects
        ],
        "experience": [
            {
                "title": experience.title,
                "organization": experience.organization,
                "bullets": experience.bullets,
            }
            for experience in tailored.experience
        ],
        "skills": [
            group.category + ": " + ", ".join(group.items) for group in tailored.skills
        ],
        "matched_keywords": tailored.report.matched_keywords,
        "missing_keywords": tailored.report.missing_keywords,
    }

    system = (
        "You write concise, specific application emails for early-career AI/ML "
        "engineers. You may only use facts present in the supplied resume evidence. "
        "Never invent experience, tools, metrics, employers, or achievements. "
        "Return only one valid JSON object."
    )
    schema = {"subject": "string", "body": "string"}
    user = f"""CANDIDATE
Name: {profile.name}
Headline: {profile.headline}
Phone: {profile.phone}
Email: {profile.email}
Links: {json.dumps([link.model_dump() for link in profile.links])}

APPLICATION
Company: {company or tailored.report.company}
Role: {role or tailored.report.role}
Recruiter name: {recruiter_name or "(unknown, use 'Hiring Team')"}
Recruiter email: {recruiter_email or "(unknown)"}

TAILORED RESUME EVIDENCE
{json.dumps(evidence, ensure_ascii=False, indent=2)}

JOB DESCRIPTION
{jd[:8000]}

CANDIDATE NOTES
{notes.strip() or "(none)"}

RULES
1. 110-170 words in the body, 3-5 short paragraphs, plain text only.
2. Greet the recruiter by name if provided; otherwise use "Dear Hiring Team,".
3. Mention 1-2 of the selected projects and connect them to the job requirements.
4. Do not claim years of experience, seniority, or skills that are not in the evidence.
5. Do not copy numbers from the job description. Do not invent metrics.
6. Say that the resume is attached. Do not use markdown, placeholders, or brackets.
7. Sign off with the candidate's name, phone, email, and links.
8. Subject: "Application for <role> – <name>" (or "Application – <name>" if the role is unknown).
9. Return this JSON shape: {json.dumps(schema)}
"""
    try:
        raw = chat_json(
            provider,
            model,
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            cfg=cfg,
            temperature=0.3,
            max_tokens=1200,
        )
        draft = EmailDraft.model_validate(
            {
                **raw,
                "recruiter_email": recruiter_email,
                "company": company,
                "role": role,
            }
        )
    except Exception:
        return _fallback_email(
            master, tailored, company, role, recruiter_name, recruiter_email
        )

    subject = re.sub(r"\s+", " ", draft.subject or "").strip()
    body = (draft.body or "").strip()
    if not subject:
        subject = f"Application – {profile.name}"
    if not body:
        return _fallback_email(
            master, tailored, company, role, recruiter_name, recruiter_email
        )
    if len(body) < 40 or len(body) > 3500:
        return _fallback_email(
            master, tailored, company, role, recruiter_name, recruiter_email
        )

    master_nums = master_numbers(master)
    extra_numbers = numbers_in(body) - master_nums
    if extra_numbers:
        return _fallback_email(
            master, tailored, company, role, recruiter_name, recruiter_email
        )
    for term in unknown_tech_terms(master):
        if _term_in_text(term, body.lower()):
            return _fallback_email(
                master, tailored, company, role, recruiter_name, recruiter_email
            )

    return EmailDraft(
        subject=subject,
        body=body,
        recruiter_email=recruiter_email,
        company=company,
        role=role,
    )


def save_email_files(
    email: EmailDraft,
    pdf_path: Path,
    out_dir: Path,
    profile,
) -> list[Path]:
    txt_path = out_dir / "Email.txt"
    to_line = email.recruiter_email or ""
    txt_path.write_text(
        f"To: {to_line}\nSubject: {email.subject}\n\n{email.body}\n",
        encoding="utf-8",
    )

    eml_path = out_dir / "Email.eml"
    message = EmailMessage()
    if profile.email:
        message["From"] = profile.email
    if email.recruiter_email and "@" in email.recruiter_email:
        message["To"] = email.recruiter_email
    message["Subject"] = email.subject
    message["Date"] = format_datetime(datetime.now().astimezone())
    message.set_content(email.body)

    if pdf_path.exists():
        pdf_bytes = pdf_path.read_bytes()
        message.add_attachment(
            pdf_bytes,
            maintype="application",
            subtype="pdf",
            filename=pdf_path.name,
        )
    eml_path.write_bytes(message.as_bytes())
    return [txt_path, eml_path]
