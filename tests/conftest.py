from __future__ import annotations

from pathlib import Path

import pytest

import app.tailor as tailor_module
from app.config import ROOT
from app.llm import LLMError
from app.models import TailorOptions
from app.tailor import load_master, tailor

MASTER_EXAMPLE = Path(ROOT) / "data" / "master.example.yaml"

SAMPLE_JD = """
We are hiring a Junior AI Engineer in Cairo. You will build LLM applications and
retrieval-augmented generation (RAG) pipelines using Python, LangChain or LangGraph.
You will work with embeddings, vector search, and prompt engineering, and integrate
external APIs. Experience with FastAPI, Docker, Git, and PostgreSQL is required. You
will also evaluate model outputs and work with computer vision models
(PyTorch/TensorFlow). Recent graduates with strong projects are welcome.
""".strip()


@pytest.fixture(scope="session")
def master():
    return load_master(MASTER_EXAMPLE)


@pytest.fixture(scope="session")
def sample_jd() -> str:
    return SAMPLE_JD


@pytest.fixture
def fallback_resume(master, sample_jd, monkeypatch):
    def fail(*_args, **_kwargs):
        raise LLMError("offline")

    monkeypatch.setattr(tailor_module, "chat_json", fail)
    return tailor(
        sample_jd,
        master,
        TailorOptions(),
        "nvidia",
        "test-model",
        company="Acme",
        role="Junior AI Engineer",
    )
