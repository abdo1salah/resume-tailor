from __future__ import annotations

import pytest
from docx import Document

from app.config import load_config
from app.importer import (
    fallback_parse,
    import_resumes,
    merge_masters,
    parse_resumes,
)
from app.models import Bullet, Project

SAMPLE_RESUME = """Jane Doe
AI Engineer
jane@example.com | +1 555 123 4567 | https://github.com/janedoe

Education
Example University | B.Sc. Computer Science | 2020 – 2024
GPA: 3.8/4

Skills
Languages: Python, SQL
ML: PyTorch, scikit-learn

Projects
Fraud Detector | Python, FastAPI
Built a model that flags suspicious transactions with a 0.91 ROC AUC.

Experience
ML Intern | Acme Corp | June 2023 – August 2023
Built a feature pipeline and evaluated model performance.

Certifications & Languages
AWS Certified Machine Learning – Specialty
Languages: English (Native), Spanish (Basic)
"""


def write_docx(path, text: str) -> None:
    document = Document()
    for line in text.splitlines():
        document.add_paragraph(line)
    document.save(str(path))


def test_fallback_parse_extracts_resume_sections():
    master = fallback_parse(SAMPLE_RESUME, "jane")

    assert master.profile.name == "Jane Doe"
    assert master.profile.headline == "AI Engineer"
    assert master.profile.email == "jane@example.com"
    assert master.profile.phone == "+1 555 123 4567"
    assert [link.label for link in master.profile.links] == ["GitHub"]

    assert len(master.education) == 1
    education = master.education[0]
    assert education.institution == "Example University"
    assert education.degree == "B.Sc. Computer Science"
    assert education.dates == "2020 – 2024"
    assert education.details == ["GPA: 3.8/4"]

    assert [(group.category, group.items) for group in master.skills] == [
        ("Languages", ["Python", "SQL"]),
        ("ML", ["PyTorch", "scikit-learn"]),
    ]

    assert len(master.projects) == 1
    assert master.projects[0].title == "Fraud Detector"
    assert master.projects[0].bullets[0].text.startswith("Built a model")

    assert len(master.experience) == 1
    assert master.experience[0].title == "ML Intern"
    assert master.experience[0].organization == "Acme Corp"
    assert master.experience[0].dates == "June 2023 – August 2023"

    assert master.certifications[0].name == "AWS Certified Machine Learning – Specialty"
    assert master.languages == ["English (Native)", "Spanish (Basic)"]


def test_merge_masters_deduplicates_and_adds_new_items():
    base = fallback_parse(SAMPLE_RESUME, "jane")
    incoming = base.model_copy(deep=True)
    incoming.projects.append(
        Project(
            id="new-project",
            title="New Project",
            bullets=[Bullet(text="Built a new project.", tags=["python"])],
        )
    )
    incoming.languages = ["English", "French (Basic)"]

    merged = merge_masters(base, incoming, "merge")

    assert [project.title for project in merged.projects] == [
        "Fraud Detector",
        "New Project",
    ]
    assert merged.education == base.education
    assert [(group.category, group.items) for group in merged.skills] == [
        (group.category, group.items) for group in base.skills
    ]
    assert len(merged.projects[0].bullets) == len(base.projects[0].bullets)
    assert merged.languages == [
        "English (Native)",
        "Spanish (Basic)",
        "French (Basic)",
    ]


def test_import_resumes_preview_then_write_creates_backup(tmp_path):
    resume_dir = tmp_path / "resumes"
    resume_dir.mkdir()
    docx_path = resume_dir / "jane.docx"
    write_docx(docx_path, SAMPLE_RESUME)
    master_path = tmp_path / "master.yaml"
    cfg = load_config()

    preview = import_resumes(
        [docx_path],
        "nvidia",
        "test-model",
        use_llm=False,
        cfg=cfg,
        master_path=master_path,
        dry_run=True,
    )
    assert preview.output_path is None
    assert preview.backup_path is None
    assert not master_path.exists()

    written = import_resumes(
        [docx_path],
        "nvidia",
        "test-model",
        use_llm=False,
        cfg=cfg,
        master_path=master_path,
        dry_run=False,
    )
    assert written.output_path == master_path
    assert written.backup_path is None
    assert master_path.exists()

    rewritten = import_resumes(
        [docx_path],
        "nvidia",
        "test-model",
        use_llm=False,
        cfg=cfg,
        master_path=master_path,
        dry_run=False,
    )
    assert rewritten.backup_path is not None
    assert rewritten.backup_path.exists()
    assert rewritten.backup_path.parent == tmp_path / "backups"


def test_parse_resumes_rejects_multiple_people(tmp_path):
    first = tmp_path / "jane.docx"
    second = tmp_path / "john.docx"
    write_docx(first, SAMPLE_RESUME)
    write_docx(second, SAMPLE_RESUME.replace("Jane Doe", "John Doe"))

    with pytest.raises(ValueError, match="different names"):
        parse_resumes([first, second], "nvidia", "test-model", use_llm=False)
