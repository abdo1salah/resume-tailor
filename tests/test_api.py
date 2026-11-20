from __future__ import annotations

import shutil

import pytest
from docx import Document
from fastapi.testclient import TestClient

import app.main as main

SOFFICE = shutil.which("soffice") or shutil.which("libreoffice")


@pytest.fixture
def client(monkeypatch, tmp_path, master):
    cfg = {
        "provider": "nvidia",
        "models": {
            "nvidia": "meta/llama-3.3-70b-instruct",
            "ollama": "qwen3:4b-instruct-32k",
        },
        "tailoring": {
            "max_projects": 3,
            "max_experiences": 2,
            "max_skill_groups": 3,
            "max_bullets_per_item": 3,
        },
        "output_dir": str(tmp_path / "output"),
    }
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setattr(main, "load_config", lambda: cfg)
    monkeypatch.setattr(main, "save_config", lambda _cfg: None)
    monkeypatch.setattr(main, "load_master", lambda *_args, **_kwargs: master)
    monkeypatch.setattr(main, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(main, "output_root", lambda _cfg=None: tmp_path / "output")
    return TestClient(main.app)


def test_health(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_index_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Resume" in response.text
    assert 'href="/import"' in response.text


def test_import_page_and_api(client, tmp_path, monkeypatch):
    resume_dir = tmp_path / "resumes"
    resume_dir.mkdir()
    resume_path = resume_dir / "jane.docx"
    document = Document()
    for line in [
        "Jane Doe",
        "AI Engineer",
        "jane@example.com | https://github.com/janedoe",
        "Education",
        "Example University | B.Sc. Computer Science | 2020 – 2024",
        "Skills",
        "ML: Python, PyTorch",
        "Projects",
        "Fraud Detector | Python",
        "Built a model that flags suspicious transactions.",
    ]:
        document.add_paragraph(line)
    document.save(str(resume_path))

    master_path = tmp_path / "master.yaml"
    monkeypatch.setattr(main, "RESUMES_DIR", resume_dir)
    monkeypatch.setattr(main, "MASTER_PATH", master_path)

    page = client.get("/import")
    assert page.status_code == 200
    assert "jane.docx" in page.text

    preview = client.post(
        "/api/import",
        json={
            "files": ["jane.docx"],
            "provider": "nvidia",
            "model": "test-model",
            "use_llm": False,
            "dry_run": True,
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["summary"]["profile"]["name"] == "Jane Doe"
    assert not master_path.exists()

    written = client.post(
        "/api/import",
        json={
            "files": ["jane.docx"],
            "provider": "nvidia",
            "model": "test-model",
            "use_llm": False,
            "dry_run": False,
        },
    )
    assert written.status_code == 200, written.text
    assert written.json()["output_path"] == str(master_path)
    assert master_path.exists()

    traversal = client.post(
        "/api/import",
        json={"files": ["../jane.docx"], "use_llm": False, "dry_run": True},
    )
    assert traversal.status_code == 400

    unknown = client.post(
        "/api/import",
        json={"files": ["missing.docx"], "use_llm": False, "dry_run": True},
    )
    assert unknown.status_code == 400


@pytest.mark.skipif(SOFFICE is None, reason="LibreOffice is required for PDF output")
def test_tailor_review_export_flow(client, sample_jd):
    response = client.post(
        "/api/tailor",
        json={
            "jd": sample_jd,
            "company": "API Test Co",
            "role": "Junior AI Engineer",
            "provider": "nvidia",
            "model": "test-model",
            "generate_email": False,
        },
    )
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]

    review = client.get(f"/review/{run_id}")
    assert review.status_code == 200
    assert "Export" in review.text

    master = main.load_master()
    edited_projects = [
        {
            "id": project.id,
            "enabled": project.id != master.projects[0].id,
            "bullets": [bullet.text for bullet in project.bullets],
        }
        for project in master.projects
    ]
    email_response = client.post(
        f"/api/email/{run_id}",
        json={"projects": edited_projects, "include_email": False},
    )
    assert email_response.status_code == 200, email_response.text
    assert master.projects[0].title not in email_response.json()["email"]["body"]

    export = client.post(f"/api/export/{run_id}", json={"include_email": False})
    assert export.status_code == 200, export.text
    data = export.json()
    assert data["pages"] == 1
    names = {item["name"] for item in data["files"]}
    stem = master.profile.name.replace(" ", "_")
    assert f"{stem}_Resume.pdf" in names
    assert f"{stem}_Resume.docx" in names
    assert "tailoring_report.md" in names
    for item in data["files"]:
        assert client.get(item["url"]).status_code == 200
