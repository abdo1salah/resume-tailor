from __future__ import annotations

from app.jd import KNOWN_TECH, analyze_jd, master_display_terms, unknown_tech_terms


def test_analyze_jd_finds_relevant_terms(master, sample_jd):
    insight = analyze_jd(sample_jd, master)
    matched = {item.lower() for item in insight.matched}
    for term in [
        "rag",
        "langchain",
        "langgraph",
        "fastapi",
        "postgresql",
        "pytorch",
        "tensorflow",
        "docker",
        "git",
        "python",
    ]:
        assert term in matched
    assert "fastapi" not in {item.lower() for item in insight.missing}
    assert all(term in KNOWN_TECH for term in insight.missing)


def test_analyze_jd_reports_true_missing_tech(master):
    insight = analyze_jd(
        "We need hands-on Kubernetes, Terraform, and AWS experience.", master
    )
    missing = {item.lower() for item in insight.missing}
    assert {"kubernetes", "terraform", "aws"} <= missing


def test_unknown_tech_terms_excludes_master_stack(master):
    unknown = set(unknown_tech_terms(master))
    assert "kubernetes" in unknown
    assert "terraform" in unknown
    assert "python" not in unknown
    assert "pytorch" not in unknown


def test_display_terms_are_structured(master):
    display = master_display_terms(master)
    assert display["rag"] == "RAG"
    assert display["langchain"] == "LangChain"
    assert "langgraph" in display
    assert "bullet" not in display
