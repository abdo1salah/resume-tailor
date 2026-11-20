from __future__ import annotations

import mimetypes
import re
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .config import (
    MASTER_PATH,
    PROVIDERS,
    RESUMES_DIR,
    STATE_DIR,
    ensure_dirs,
    load_config,
    output_root,
    provider_key_status,
    save_config,
)
from .email import generate_email
from .export import write_application
from .importer import import_resumes, list_resumes, master_summary
from .llm import LLMError, list_models
from .models import (
    EmailDraft,
    ExportRequest,
    ImportRequest,
    MasterResume,
    RunRecord,
    TailoredResume,
    TailorOptions,
    TailorRequest,
)
from .render_html import render_preview, render_template
from .tailor import load_master, merge_edits, tailor

RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")

app = FastAPI(title="Resume Tailor", version="0.1.0")
ensure_dirs()


def _run_path(run_id: str) -> Path:
    if not RUN_ID_RE.fullmatch(run_id):
        raise HTTPException(status_code=404, detail="Run not found.")
    return STATE_DIR / f"{run_id}.json"


def _save_run(run: RunRecord) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _run_path(run.id).write_text(run.model_dump_json(indent=2), encoding="utf-8")


def _load_run(run_id: str) -> RunRecord:
    path = _run_path(run_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Run not found.")
    try:
        return RunRecord.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read run: {exc}") from exc


def _new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def _tailor_options(cfg: dict, request: TailorRequest) -> TailorOptions:
    tailoring = cfg.get("tailoring") or {}
    intensity = request.intensity if request.intensity in {"light", "balanced", "strong"} else "balanced"
    return TailorOptions(
        intensity=intensity,
        max_projects=int(tailoring.get("max_projects", 3)),
        max_experiences=int(tailoring.get("max_experiences", 2)),
        max_skill_groups=int(tailoring.get("max_skill_groups", 3)),
        max_bullets_per_item=int(tailoring.get("max_bullets_per_item", 3)),
        include_summary=bool(request.include_summary),
    )


def _edit_context(master: MasterResume, tailored: TailoredResume) -> dict:
    tailored_projects = {item.id: item for item in tailored.projects}
    tailored_experiences = {item.id: item for item in tailored.experience}
    tailored_activities = {item.id: item for item in tailored.extracurricular}

    projects = []
    for project in tailored.projects:
        projects.append(
            {
                "id": project.id,
                "title": project.title,
                "subtitle": project.subtitle,
                "link": project.link,
                "tech": project.tech,
                "meta": "",
                "enabled": True,
                "bullets": project.bullets,
                "reason": project.reason,
            }
        )
    for project in master.projects:
        if project.id in tailored_projects:
            continue
        projects.append(
            {
                "id": project.id,
                "title": project.title,
                "subtitle": project.subtitle,
                "link": project.link,
                "tech": project.tech,
                "meta": "",
                "enabled": False,
                "bullets": [bullet.text for bullet in project.bullets],
                "reason": "Not in the draft. Check to add it on export.",
            }
        )

    experiences = []
    for item in tailored.experience:
        experiences.append(
            {
                "id": item.id,
                "title": item.title,
                "subtitle": item.organization,
                "link": "",
                "tech": [],
                "meta": item.dates,
                "enabled": True,
                "bullets": item.bullets,
                "reason": item.reason,
            }
        )
    for item in master.experience:
        if item.id in tailored_experiences:
            continue
        experiences.append(
            {
                "id": item.id,
                "title": item.title,
                "subtitle": item.organization,
                "link": "",
                "tech": [],
                "meta": item.dates,
                "enabled": False,
                "bullets": [bullet.text for bullet in item.bullets],
                "reason": "Not in the draft. Check to add it on export.",
            }
        )

    activities = []
    for item in tailored.extracurricular:
        activities.append(
            {
                "id": item.id,
                "title": item.title,
                "subtitle": item.subtitle,
                "link": "",
                "tech": [],
                "meta": "",
                "enabled": True,
                "bullets": item.bullets,
                "reason": item.reason,
            }
        )
    for item in master.extracurricular:
        if item.id in tailored_activities:
            continue
        activities.append(
            {
                "id": item.id,
                "title": item.title,
                "subtitle": item.subtitle,
                "link": "",
                "tech": [],
                "meta": "",
                "enabled": False,
                "bullets": [bullet.text for bullet in item.bullets],
                "reason": "Not in the draft. Check to add it on export.",
            }
        )

    selected_skills = [group.category for group in tailored.skills]
    skill_options = []
    for group in tailored.skills:
        skill_options.append({"category": group.category, "enabled": True})
    for group in master.skills:
        if group.category in selected_skills:
            continue
        skill_options.append({"category": group.category, "enabled": False})

    return {
        "edit_projects": projects,
        "edit_experiences": experiences,
        "edit_activities": activities,
        "skill_options": skill_options,
    }


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    cfg = load_config()
    master = load_master()
    providers = [
        {
            "name": name,
            "label": definition["label"],
            "configured": provider_key_status(name),
        }
        for name, definition in PROVIDERS.items()
    ]
    html = render_template(
        "index.html",
        config=cfg,
        master=master,
        providers=providers,
        default_models=cfg.get("models", {}),
        provider_configured={name: provider_key_status(name) for name in PROVIDERS},
        output_dir=str(output_root(cfg)),
        nvidia_ready=provider_key_status("nvidia"),
    )
    return HTMLResponse(html)


@app.get("/import", response_class=HTMLResponse)
def import_page() -> HTMLResponse:
    cfg = load_config()
    files = []
    for path in list_resumes(RESUMES_DIR):
        stat = path.stat()
        files.append(
            {
                "name": path.name,
                "suffix": path.suffix.lower().lstrip("."),
                "size_kb": max(1, round(stat.st_size / 1024)),
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime(
                    "%Y-%m-%d %H:%M"
                ),
            }
        )
    master = load_master()
    providers = [
        {
            "name": name,
            "label": definition["label"],
            "configured": provider_key_status(name),
        }
        for name, definition in PROVIDERS.items()
    ]
    html = render_template(
        "import.html",
        config=cfg,
        files=files,
        master=master,
        summary=master_summary(master),
        providers=providers,
        default_models=cfg.get("models", {}),
        provider_configured={name: provider_key_status(name) for name in PROVIDERS},
        resumes_dir=str(RESUMES_DIR),
        master_path=str(MASTER_PATH),
        nvidia_ready=provider_key_status("nvidia"),
    )
    return HTMLResponse(html)


@app.post("/api/import")
def api_import(request: ImportRequest):
    if not request.files:
        raise HTTPException(status_code=400, detail="Select at least one resume file.")
    if request.mode not in {"merge", "replace"}:
        raise HTTPException(status_code=400, detail="Mode must be 'merge' or 'replace'.")
    if request.provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail="Unknown provider.")

    available = {path.name: path for path in list_resumes(RESUMES_DIR)}
    selected: list[Path] = []
    seen: set[str] = set()
    for name in request.files:
        if Path(name).name != name:
            raise HTTPException(status_code=400, detail="Resume file not found.")
        path = available.get(name)
        if path is None:
            raise HTTPException(
                status_code=400,
                detail=f"Resume file not found in {RESUMES_DIR.name}/: {name}",
            )
        if name not in seen:
            selected.append(path)
            seen.add(name)

    cfg = load_config()
    model = (request.model or "").strip() or (cfg.get("models") or {}).get(
        request.provider, ""
    )
    if request.use_llm and not model:
        raise HTTPException(status_code=400, detail="Select a model first.")

    try:
        result = import_resumes(
            selected,
            request.provider,
            model,
            mode=request.mode,
            use_llm=request.use_llm,
            cfg=cfg,
            master_path=MASTER_PATH,
            dry_run=request.dry_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Import failed: {exc}") from exc

    if not request.dry_run:
        cfg["provider"] = request.provider
        cfg.setdefault("models", {})[request.provider] = model
        save_config(cfg)

    summary = master_summary(result.master)
    return {
        "ok": True,
        "dry_run": request.dry_run,
        "mode": result.mode,
        "files": [path.name for path in result.files],
        "provider": request.provider,
        "model": model,
        "output_path": str(result.output_path) if result.output_path else "",
        "backup_path": str(result.backup_path) if result.backup_path else "",
        "warnings": result.warnings,
        "summary": summary,
    }


@app.get("/api/models")
def api_models(provider: str):
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail="Unknown provider.")
    cfg = load_config()
    try:
        models = list_models(provider, cfg)
    except LLMError as exc:
        return JSONResponse(
            status_code=502,
            content={"detail": str(exc), "default": cfg.get("models", {}).get(provider, "")},
        )
    return {"models": models, "default": cfg.get("models", {}).get(provider, "")}


@app.post("/api/tailor")
def api_tailor(request: TailorRequest):
    jd = (request.jd or "").strip()
    if len(jd) < 90:
        raise HTTPException(
            status_code=400,
            detail="Please paste a fuller job description (at least ~90 characters).",
        )
    if request.provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail="Unknown provider.")

    cfg = load_config()
    model = (request.model or "").strip() or (cfg.get("models") or {}).get(
        request.provider, ""
    )
    if not model:
        raise HTTPException(status_code=400, detail="Select a model first.")

    cfg["provider"] = request.provider
    cfg.setdefault("models", {})[request.provider] = model
    save_config(cfg)

    master = load_master()
    options = _tailor_options(cfg, request)

    try:
        tailored = tailor(
            jd=jd,
            master=master,
            options=options,
            provider=request.provider,
            model=model,
            cfg=cfg,
            company=request.company.strip(),
            role=request.role.strip(),
            notes=request.notes,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Tailoring failed: {exc}") from exc

    company = request.company.strip() or tailored.report.company.strip()
    role = request.role.strip() or tailored.report.role.strip()
    if not company:
        company = "Company"
    tailored.report.company = company
    tailored.report.role = role

    email = None
    if request.generate_email:
        try:
            email = generate_email(
                master=master,
                tailored=tailored,
                jd=jd,
                company=company,
                role=role,
                recruiter_name=request.recruiter_name,
                recruiter_email=request.recruiter_email,
                notes=request.notes,
                provider=request.provider,
                model=model,
                cfg=cfg,
            )
        except Exception as exc:
            tailored.report.warnings.append(f"Email generation failed: {exc}")

    run_request = request.model_copy(
        update={
            "company": company,
            "role": role,
            "jd": jd,
            "provider": request.provider,
            "model": model,
        }
    )
    run = RunRecord(
        id=_new_run_id(),
        request=run_request,
        tailored=tailored,
        email=email,
    )
    _save_run(run)
    return {"run_id": run.id, "review_url": f"/review/{run.id}"}


@app.get("/review/{run_id}", response_class=HTMLResponse)
def review(run_id: str) -> HTMLResponse:
    run = _load_run(run_id)
    master = load_master()
    context = _edit_context(master, run.tailored)
    html = render_template(
        "review.html",
        run=run,
        preview=render_preview(run.tailored),
        include_email_default=bool(run.request.generate_email or run.email),
        **context,
    )
    return HTMLResponse(html)


@app.post("/api/email/{run_id}")
def api_email(run_id: str, payload: ExportRequest | None = None):
    run = _load_run(run_id)
    master = load_master()
    resume = run.tailored
    if payload is not None:
        resume = merge_edits(
            tailored=run.tailored,
            master=master,
            edited_projects=payload.projects,
            edited_experiences=payload.experiences,
            edited_activities=payload.activities,
            skill_names=payload.skills,
            summary=payload.summary,
        )
    try:
        email = generate_email(
            master=master,
            tailored=resume,
            jd=run.request.jd,
            company=run.tailored.report.company or run.request.company,
            role=run.tailored.report.role or run.request.role,
            recruiter_name=run.request.recruiter_name,
            recruiter_email=run.request.recruiter_email,
            notes=run.request.notes,
            provider=run.request.provider,
            model=run.request.model,
            cfg=load_config(),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Email generation failed: {exc}") from exc
    run.email = email
    _save_run(run)
    return {"email": email.model_dump()}


@app.post("/api/export/{run_id}")
def api_export(run_id: str, payload: ExportRequest):
    run = _load_run(run_id)
    master = load_master()

    resume = merge_edits(
        tailored=run.tailored,
        master=master,
        edited_projects=payload.projects,
        edited_experiences=payload.experiences,
        edited_activities=payload.activities,
        skill_names=payload.skills,
        summary=payload.summary,
    )

    email = run.email
    if payload.email and (payload.email.subject.strip() or payload.email.body.strip()):
        email = EmailDraft(
            subject=payload.email.subject.strip(),
            body=payload.email.body.strip(),
            recruiter_email=payload.email.recruiter_email.strip(),
            company=run.tailored.report.company or run.request.company,
            role=run.tailored.report.role or run.request.role,
        )

    if email and not email.subject.strip():
        email.subject = f"Application – {master.profile.name}"

    if payload.include_email and (not email or not email.body.strip()):
        try:
            email = generate_email(
                master=master,
                tailored=resume,
                jd=run.request.jd,
                company=run.tailored.report.company or run.request.company,
                role=run.tailored.report.role or run.request.role,
                recruiter_name=run.request.recruiter_name,
                recruiter_email=payload.email.recruiter_email.strip()
                if payload.email
                else run.request.recruiter_email,
                notes=run.request.notes,
                provider=run.request.provider,
                model=run.request.model,
                cfg=load_config(),
            )
        except Exception as exc:
            resume.report.warnings.append(f"Email generation failed: {exc}")
            email = None

    try:
        result = write_application(
            run=run,
            master=master,
            resume=resume,
            email=email if payload.include_email else None,
            include_email=payload.include_email,
            output_root=output_root(),
            existing_dir=Path(run.export_dir) if run.export_dir else None,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Export failed: {exc}") from exc

    run.tailored = result["resume"]
    run.email = email
    run.export_dir = result["folder"]
    run.exported_files = result["files"]
    _save_run(run)

    files = [
        {"name": name, "url": f"/files/{run.id}/{name}"}
        for name in result["files"]
    ]
    return {
        "folder": result["folder"],
        "files": files,
        "pages": result["pages"],
        "trimmed": result["trimmed"],
        "warnings": result["warnings"],
        "email_saved": result["email_saved"],
    }


@app.get("/files/{run_id}/{filename}")
def download(run_id: str, filename: str):
    run = _load_run(run_id)
    if not run.export_dir:
        raise HTTPException(status_code=404, detail="Nothing has been exported for this run.")
    if Path(filename).name != filename:
        raise HTTPException(status_code=404, detail="File not found.")

    base = Path(run.export_dir).resolve()
    target = (base / filename).resolve()
    if target != base and base not in target.parents:
        raise HTTPException(status_code=404, detail="File not found.")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found.")

    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    if target.suffix.lower() == ".pdf":
        return FileResponse(
            target,
            media_type=media_type,
            headers={"Content-Disposition": f'inline; filename="{target.name}"'},
        )
    return FileResponse(target, media_type=media_type, filename=target.name)


@app.get("/healthz")
def healthz():
    return {"ok": True}


def run() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    run()
