from __future__ import annotations

import app.tailor as tailor_module
from app.jd import analyze_jd
from app.models import EditedItem, LLMTailoring, SelectedProject, TailorOptions
from app.tailor import (
    compose,
    master_numbers,
    merge_edits,
    tailor,
    unknown_tech_terms,
    validate_bullet,
)


def test_fallback_is_grounded_in_master(fallback_resume, master):
    assert fallback_resume.report.mode == "fallback"
    assert fallback_resume.report.warnings
    source = {bullet.text for project in master.projects for bullet in project.bullets}
    source |= {bullet.text for item in master.experience for bullet in item.bullets}
    source |= {
        bullet.text for item in master.extracurricular for bullet in item.bullets
    }

    assert 1 <= len(fallback_resume.projects) <= 3
    assert 1 <= len(fallback_resume.experience) <= 2
    for project in fallback_resume.projects:
        assert 1 <= len(project.bullets) <= 3
        assert set(project.bullets) <= source
    for experience in fallback_resume.experience:
        assert set(experience.bullets) <= source


def test_fallback_orders_skills_by_relevance(fallback_resume):
    categories = [group.category for group in fallback_resume.skills]
    assert categories[0] == "Generative AI / NLP"
    assert categories == [
        "Generative AI / NLP",
        "Tools / Frameworks",
        "Backend / Data",
    ]


def test_compose_corrects_incorrect_missing_terms(master, sample_jd):
    jd = sample_jd + " Kubernetes experience is a plus."
    insight = analyze_jd(jd, master)
    selection = LLMTailoring(
        company="Acme",
        role="Junior AI Engineer",
        matched_keywords=[],
        missing_keywords=["FastAPI", "Kubernetes", "Document ingestion"],
        skills=[],
        selected_projects=[
            SelectedProject(
                id=master.projects[0].id,
                bullets=[master.projects[0].bullets[0].text],
            )
        ],
        selected_experiences=[],
        selected_extracurricular=[],
    )

    result = compose(
        master,
        selection,
        TailorOptions(),
        "nvidia",
        "test-model",
        "llm",
        insight=insight,
        jd=jd,
    )
    matched = {item.lower() for item in result.report.matched_keywords}
    missing = {item.lower() for item in result.report.missing_keywords}

    assert "fastapi" in matched
    assert "fastapi" not in missing
    assert "kubernetes" in missing
    assert "document ingestion" not in matched
    assert "document ingestion" not in missing


def test_validate_bullet_rejects_new_number_and_tech(master):
    issues = validate_bullet(
        "Built a Kubernetes platform with 99% uptime.",
        master_numbers(master),
        unknown_tech_terms(master),
    )
    joined = " ".join(issues).lower()
    assert "unsupported number" in joined
    assert "kubernetes" in joined


def test_invalid_model_ids_fall_back_to_master_items(
    master, sample_jd, monkeypatch
):
    def fake_chat(*_args, **_kwargs):
        return {
            "company": "Acme",
            "role": "Junior AI Engineer",
            "matched_keywords": [],
            "missing_keywords": [],
            "notes": "",
            "summary": None,
            "skills": [],
            "selected_projects": [{"id": "not-a-real-project", "bullets": []}],
            "selected_experiences": [{"id": "not-a-real-role", "bullets": []}],
            "selected_extracurricular": [],
        }

    monkeypatch.setattr(tailor_module, "chat_json", fake_chat)
    result = tailor(
        sample_jd,
        master,
        TailorOptions(),
        "nvidia",
        "test-model",
        company="Acme",
        role="Junior AI Engineer",
    )

    assert result.report.mode == "llm"
    assert result.projects
    assert result.experience
    assert any("invalid item ids" in warning for warning in result.report.warnings)


def test_merge_edits_respects_disabled_items(fallback_resume, master):
    first = fallback_resume.projects[0]
    merged = merge_edits(
        fallback_resume,
        master,
        [EditedItem(id=first.id, enabled=False, bullets=[])],
        [],
        [],
        ["Backend / Data"],
        None,
    )

    assert first.id not in {project.id for project in merged.projects}
    assert [group.category for group in merged.skills] == ["Backend / Data"]
