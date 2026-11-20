from __future__ import annotations

from email import policy
from email.parser import BytesParser

from app.email import save_email_files
from app.export import sanitize_company, unique_output_dir
from app.models import EmailDraft, Profile


def test_sanitize_company():
    assert sanitize_company("Acme/Inc:*?") == "AcmeInc"
    assert sanitize_company("  ") == "Company"
    assert sanitize_company("A" * 200) == "A" * 80


def test_unique_output_dir_handles_collisions(tmp_path):
    first = unique_output_dir(tmp_path, "Acme")
    assert first.name == "Acme"
    assert first.exists()
    (first / "existing.txt").write_text("x", encoding="utf-8")

    second = unique_output_dir(tmp_path, "Acme")
    assert second.name == "Acme_2"
    assert second.exists()
    (second / "existing.txt").write_text("x", encoding="utf-8")

    third = unique_output_dir(tmp_path, "Acme")
    assert third.name == "Acme_3"
    assert third.exists()


def test_email_files_include_pdf_attachment(tmp_path):
    pdf_path = tmp_path / "resume.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    profile = Profile(name="Test User", email="test@example.com", phone="123")
    email = EmailDraft(
        subject="Application for AI Engineer",
        body="Hello, my resume is attached.",
        recruiter_email="recruiter@example.com",
    )

    paths = save_email_files(email, pdf_path, tmp_path, profile)

    assert {path.name for path in paths} == {"Email.txt", "Email.eml"}
    message = BytesParser(policy=policy.default).parsebytes(
        (tmp_path / "Email.eml").read_bytes()
    )
    assert message["To"] == "recruiter@example.com"
    assert message["Subject"] == "Application for AI Engineer"
    attachments = list(message.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "resume.pdf"
    assert attachments[0].get_content_type() == "application/pdf"
