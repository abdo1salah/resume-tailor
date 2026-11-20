from __future__ import annotations

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import TEMPLATES_DIR
from .models import TailoredResume

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


def render_template(name: str, **context) -> str:
    return _env.get_template(name).render(**context)


def render_preview(resume: TailoredResume) -> str:
    return render_template("resume_preview.html", resume=resume)
