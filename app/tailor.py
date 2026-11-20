from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from .config import MASTER_PATH, load_config
from .jd import (
    KNOWN_TECH,
    STOPWORDS,
    JDInsight,
    _norm,
    analyze_jd,
    master_text,
    master_terms,
    unknown_tech_terms,
)
from .llm import LLMError, chat_json
from .models import (
    LLMTailoring,
    MasterResume,
    RenderActivity,
    RenderExperience,
    RenderProject,
    Report,
    SelectedActivity,
    SelectedExperience,
    SelectedProject,
    SelectionNote,
    SkillGroup,
    TailoredResume,
    TailorOptions,
)

NUMBER_RE = re.compile(r"\d+(?:[./]\d+)?%?")
EVIDENCE_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#.]*")
BULLET_MAX_CHARS = 300
SUMMARY_MAX_CHARS = 300


def _stem_token(token: str) -> str:
    token = token.lower()
    for suffix in ("ments", "ment", "ings", "ing", "ers", "er", "ed", "es", "s"):
        if not token.endswith(suffix) or len(token) <= len(suffix) + 2:
            continue
        if suffix == "s" and token.endswith(("ss", "is", "us")):
            continue
        return token[: -len(suffix)]
    return token


def load_master(path=MASTER_PATH) -> MasterResume:
    import yaml

    if not path.exists():
        raise FileNotFoundError(f"Master resume not found at {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return MasterResume.model_validate(data)


def master_numbers(master: MasterResume) -> set[str]:
    return set(NUMBER_RE.findall(master_text(master)))


def numbers_in(text: str) -> set[str]:
    return set(NUMBER_RE.findall(text))


def _term_in_text(term: str, text: str) -> bool:
    term = term.lower().strip()
    if not term:
        return False
    if len(term) <= 4 or any(ch in term for ch in "+#./"):
        pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
        return re.search(pattern, text) is not None
    return term in text


def clean_bullet(text: str, limit: int = BULLET_MAX_CHARS) -> str:
    text = re.sub(r"^\s*[•●▪◦*-]\s*", "", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def validate_bullet(
    text: str,
    master_nums: set[str],
    unknown_terms: list[str],
) -> list[str]:
    issues: list[str] = []
    if not text:
        return ["empty bullet"]
    if len(text) > BULLET_MAX_CHARS:
        issues.append(f"bullet longer than {BULLET_MAX_CHARS} characters")
    if "http://" in text.lower() or "https://" in text.lower():
        issues.append("bullet contains a URL")
    extra_numbers = numbers_in(text) - master_nums
    if extra_numbers:
        issues.append(
            "unsupported number(s): " + ", ".join(sorted(extra_numbers))
        )
    lowered = text.lower()
    for term in unknown_terms:
        if _term_in_text(term, lowered):
            issues.append(f"technology not in master resume: {term}")
    return issues


def _prompt_payload(master: MasterResume) -> dict[str, Any]:
    return {
        "profile": master.profile.model_dump(),
        "education": [item.model_dump() for item in master.education],
        "skills": [group.model_dump() for group in master.skills],
        "projects": [
            {
                "id": project.id,
                "title": project.title,
                "subtitle": project.subtitle,
                "tech": project.tech,
                "tags": project.tags,
                "bullets": [bullet.text for bullet in project.bullets],
            }
            for project in master.projects
        ],
        "experience": [
            {
                "id": experience.id,
                "title": experience.title,
                "organization": experience.organization,
                "dates": experience.dates,
                "tags": experience.tags,
                "bullets": [bullet.text for bullet in experience.bullets],
            }
            for experience in master.experience
        ],
        "extracurricular": [
            {
                "id": activity.id,
                "title": activity.title,
                "subtitle": activity.subtitle,
                "tags": activity.tags,
                "bullets": [bullet.text for bullet in activity.bullets],
            }
            for activity in master.extracurricular
        ],
        "certifications": [cert.model_dump() for cert in master.certifications],
        "languages": master.languages,
    }


def _build_messages(
    master: MasterResume,
    jd: str,
    options: TailorOptions,
    notes: str,
) -> list[dict[str, str]]:
    intensity = options.intensity.lower()
    if intensity == "light":
        rewrite_rule = (
            "LIGHT MODE: copy the source bullet text VERBATIM. Do not reword. "
            "Only choose which items and bullets to include and their order."
        )
    elif intensity == "strong":
        rewrite_rule = (
            "STRONG MODE: rewrite each selected bullet for clarity and impact, "
            "aligning wording to the job description. Every claim must still be "
            "traceable to the same source bullet. Never add tools, employers, "
            "metrics, or achievements that are not already present."
        )
    else:
        rewrite_rule = (
            "BALANCED MODE: keep bullets close to the originals. Make only small "
            "wording edits so the language matches the job description where the "
            "underlying work is genuinely the same. Preserve all proper nouns, "
            "metrics, and technical terms."
        )

    schema = {
        "company": "string",
        "role": "string",
        "matched_keywords": ["string"],
        "missing_keywords": ["string"],
        "notes": "short string",
        "summary": "string or null",
        "skills": ["exact category names, most relevant first"],
        "selected_projects": [
            {"id": "project id", "bullets": ["string"], "reason": "short string"}
        ],
        "selected_experiences": [
            {"id": "experience id", "bullets": ["string"], "reason": "short string"}
        ],
        "selected_extracurricular": [
            {"id": "activity id", "bullets": ["string"], "reason": "short string"}
        ],
    }

    system = (
        "You are an expert technical resume editor for early-career AI/ML engineers. "
        "You fine-tune an existing resume for a specific job description. You must "
        "never invent experience, tools, technologies, employers, dates, metrics, "
        "certifications, or achievements. Every bullet you return must be supported "
        "by the master resume. Return ONLY one valid JSON object with no markdown."
    )

    user = f"""MASTER RESUME (the only source of truth):
{json.dumps(_prompt_payload(master), ensure_ascii=False, indent=2)}

JOB DESCRIPTION:
{jd[:14000]}

EXTRA NOTES FROM THE CANDIDATE:
{notes.strip() or "(none)"}

RULES:
1. Select at most {options.max_projects} projects, at most {options.max_experiences} experiences, and at most 2 extracurricular activities. Order the selected items by relevance, most relevant first.
2. Use 2-3 bullets per item, but never more bullets than the source item has.
3. {rewrite_rule}
4. You may use a job-description synonym only when it describes exactly the same existing work (for example "retrieval-augmented generation" for "RAG"). Do not introduce any tool, framework, cloud, or technology absent from the master resume.
5. Preserve every number, metric, company, institution, and project name. Never copy a number from the job description into a bullet.
6. Return all skill category names in relevance order (exact names from the master). Do not invent categories. Select at most {options.max_skill_groups}.
7. {"Provide a 1-2 sentence grounded summary." if options.include_summary else "Set summary to null."}
8. matched_keywords = skills/terms required by the job that are genuinely evidenced in the master resume. missing_keywords = important job requirements not evidenced in the master resume.
9. Do not include URLs in any bullet.
10. Keep each bullet under {BULLET_MAX_CHARS} characters.
11. notes: one or two short sentences on how you tuned the resume. Do not mention items you excluded.
12. company/role: infer them from the job description when possible.

Return this exact JSON shape:
{json.dumps(schema, indent=2)}
"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _fallback_selection(
    master: MasterResume,
    insight: JDInsight,
    options: TailorOptions,
    company: str,
    role: str,
    note: str,
) -> LLMTailoring:
    def ranked(items, scores):
        indexed = list(enumerate(items))
        indexed.sort(key=lambda pair: (-scores.get(pair[1].id, 0.0), pair[0]))
        return [item for _index, item in indexed]

    projects = ranked(master.projects, insight.project_scores)[: options.max_projects]
    experiences = ranked(master.experience, insight.experience_scores)[
        : options.max_experiences
    ]
    activities = ranked(master.extracurricular, insight.activity_scores)[:2]
    indexed_skills = list(enumerate(master.skills))
    indexed_skills.sort(
        key=lambda pair: (-insight.skill_scores.get(pair[1].category, 0.0), pair[0])
    )
    skill_names = [
        group.category for _index, group in indexed_skills[: options.max_skill_groups]
    ]

    def bullets_for(item) -> list[str]:
        return [bullet.text for bullet in item.bullets][: options.max_bullets_per_item]

    return LLMTailoring(
        company=company,
        role=role,
        matched_keywords=insight.matched,
        missing_keywords=insight.missing,
        notes=note,
        summary=None,
        skills=skill_names,
        selected_projects=[
            SelectedProject(
                id=project.id,
                bullets=bullets_for(project),
                reason=f"Keyword overlap score: {insight.project_scores.get(project.id, 0):g}",
            )
            for project in projects
        ],
        selected_experiences=[
            SelectedExperience(
                id=experience.id,
                bullets=bullets_for(experience),
                reason=f"Keyword overlap score: {insight.experience_scores.get(experience.id, 0):g}",
            )
            for experience in experiences
        ],
        selected_extracurricular=[
            SelectedActivity(
                id=activity.id,
                bullets=bullets_for(activity),
                reason=f"Keyword overlap score: {insight.activity_scores.get(activity.id, 0):g}",
            )
            for activity in activities
        ],
    )


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _norm(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(value)
    return result


def _prepare_bullets(
    candidates: list[str],
    source: list[str],
    max_bullets: int,
    intensity: str,
    master_nums: set[str],
    unknown_terms: list[str],
    label: str,
    warnings: list[str],
) -> list[str]:
    if intensity == "light":
        return _dedupe(source)[:max_bullets]

    cleaned: list[str] = []
    for candidate in candidates or []:
        text = clean_bullet(candidate)
        issues = validate_bullet(text, master_nums, unknown_terms)
        if issues:
            warnings.append(f"{label}: skipped a generated bullet ({'; '.join(issues)}).")
            continue
        cleaned.append(text)
    cleaned = _dedupe(cleaned)[:max_bullets]

    minimum = min(2, len(source), max_bullets)
    if len(cleaned) < minimum:
        for original in source:
            text = clean_bullet(original)
            if _norm(text) in {_norm(item) for item in cleaned}:
                continue
            cleaned.append(text)
            if len(cleaned) >= minimum:
                break
    if not cleaned:
        cleaned = [clean_bullet(item) for item in source][:max_bullets]
        warnings.append(f"{label}: no safe generated bullets; used the original bullets.")
    return cleaned[:max_bullets]


def _skills_from_names(
    master: MasterResume,
    names: list[str],
    max_groups: int,
) -> list[SkillGroup]:
    lookup = {group.category.lower(): group for group in master.skills}
    selected: list[SkillGroup] = []
    seen: set[str] = set()
    for name in names or []:
        group = lookup.get(_norm(name))
        if not group or group.category.lower() in seen:
            continue
        seen.add(group.category.lower())
        selected.append(group)
    for group in master.skills:
        if len(selected) >= max_groups:
            break
        if group.category.lower() not in seen:
            selected.append(group)
            seen.add(group.category.lower())
    return selected[:max_groups]


def _valid_summary(
    summary: str | None,
    master_nums: set[str],
    unknown_terms: list[str],
    enabled: bool,
    warnings: list[str],
) -> str | None:
    if not enabled or not summary or not str(summary).strip():
        return None
    cleaned = re.sub(r"\s+", " ", str(summary)).strip()
    if len(cleaned) > SUMMARY_MAX_CHARS:
        warnings.append("Summary was too long and was dropped.")
        return None
    issues = validate_bullet(cleaned, master_nums, unknown_terms)
    if issues:
        warnings.append("Summary was dropped: " + "; ".join(issues))
        return None
    return cleaned


GENERIC_MATCH_TERMS = {
    "application", "applications", "engineering", "computer", "project",
    "projects", "model", "models", "pipeline", "pipelines", "generation",
    "augment", "integrate", "integration", "search", "vision", "prompt",
    "prompts", "data", "tools", "systems", "system", "text", "based",
    "using", "development", "engineer", "software", "technical", "research",
    "analysis", "design", "building", "develop", "developing", "evaluate",
    "evaluation", "outputs", "output",
}


def _clean_match(text: str) -> bool:
    normalized = _norm(text)
    if not normalized:
        return False
    if len(normalized.split()) > 1:
        return True
    return len(normalized) > 2 and normalized not in GENERIC_MATCH_TERMS


def _clean_missing(text: str) -> bool:
    normalized = _norm(text)
    if not normalized or normalized in STOPWORDS:
        return False
    if len(normalized.split()) > 1:
        return True
    return normalized in KNOWN_TECH


def compose(
    master: MasterResume,
    selection: LLMTailoring,
    options: TailorOptions,
    provider: str,
    model: str,
    mode: str,
    warnings: list[str] | None = None,
    requested_company: str = "",
    requested_role: str = "",
    insight: JDInsight | None = None,
    jd: str = "",
) -> TailoredResume:
    warnings = list(warnings or [])
    intensity = (options.intensity or "balanced").lower()
    master_nums = master_numbers(master)
    unknown_terms = unknown_tech_terms(master)

    projects_by_id = {project.id: project for project in master.projects}
    experience_by_id = {item.id: item for item in master.experience}
    activities_by_id = {item.id: item for item in master.extracurricular}

    selected_projects: list[RenderProject] = []
    seen_projects: set[str] = set()
    for choice in selection.selected_projects:
        project = projects_by_id.get(choice.id)
        if not project or choice.id in seen_projects:
            if choice.id not in projects_by_id:
                warnings.append(f"Ignored unknown project id: {choice.id}")
            continue
        seen_projects.add(choice.id)
        bullets = _prepare_bullets(
            choice.bullets,
            [bullet.text for bullet in project.bullets],
            options.max_bullets_per_item,
            intensity,
            master_nums,
            unknown_terms,
            f"Project '{project.title}'",
            warnings,
        )
        selected_projects.append(
            RenderProject(
                id=project.id,
                title=project.title,
                subtitle=project.subtitle,
                link=project.link,
                tech=project.tech,
                bullets=bullets,
                reason=choice.reason,
            )
        )
        if len(selected_projects) >= options.max_projects:
            break

    if not selected_projects and master.projects:
        insight = analyze_jd("", master)
        ranked = sorted(
            master.projects,
            key=lambda project: insight.project_scores.get(project.id, 0.0),
            reverse=True,
        )
        fallback = _fallback_selection(
            master, insight, options, requested_company, requested_role, ""
        )
        by_id = {item.id: item for item in fallback.selected_projects}
        for project in ranked[: options.max_projects]:
            item = by_id.get(project.id)
            selected_projects.append(
                RenderProject(
                    id=project.id,
                    title=project.title,
                    subtitle=project.subtitle,
                    link=project.link,
                    tech=project.tech,
                    bullets=[bullet.text for bullet in project.bullets][
                        : options.max_bullets_per_item
                    ],
                    reason=item.reason if item else "",
                )
            )
        warnings.append("No projects were selected; used the highest-scoring projects.")

    selected_experiences: list[RenderExperience] = []
    seen_experiences: set[str] = set()
    for choice in selection.selected_experiences:
        experience = experience_by_id.get(choice.id)
        if not experience or choice.id in seen_experiences:
            if choice.id not in experience_by_id:
                warnings.append(f"Ignored unknown experience id: {choice.id}")
            continue
        seen_experiences.add(choice.id)
        bullets = _prepare_bullets(
            choice.bullets,
            [bullet.text for bullet in experience.bullets],
            options.max_bullets_per_item,
            intensity,
            master_nums,
            unknown_terms,
            f"Experience '{experience.title}'",
            warnings,
        )
        selected_experiences.append(
            RenderExperience(
                id=experience.id,
                title=experience.title,
                organization=experience.organization,
                dates=experience.dates,
                bullets=bullets,
                reason=choice.reason,
            )
        )
        if len(selected_experiences) >= options.max_experiences:
            break

    selected_activities: list[RenderActivity] = []
    seen_activities: set[str] = set()
    for choice in selection.selected_extracurricular:
        activity = activities_by_id.get(choice.id)
        if not activity or choice.id in seen_activities:
            if choice.id not in activities_by_id:
                warnings.append(f"Ignored unknown activity id: {choice.id}")
            continue
        seen_activities.add(choice.id)
        bullets = _prepare_bullets(
            choice.bullets,
            [bullet.text for bullet in activity.bullets],
            options.max_bullets_per_item,
            intensity,
            master_nums,
            unknown_terms,
            f"Activity '{activity.title}'",
            warnings,
        )
        selected_activities.append(
            RenderActivity(
                id=activity.id,
                title=activity.title,
                subtitle=activity.subtitle,
                bullets=bullets,
                reason=choice.reason,
            )
        )
        if len(selected_activities) >= 2:
            break

    skills = _skills_from_names(master, selection.skills, options.max_skill_groups)
    summary = _valid_summary(
        selection.summary, master_nums, unknown_terms, options.include_summary, warnings
    )

    corpus = _norm(master_text(master))
    term_set = master_terms(master)
    master_tokens = {
        _stem_token(token)
        for token in EVIDENCE_TOKEN_RE.findall(corpus)
        if token.lower() not in STOPWORDS
    }

    def is_evidenced(phrase: str) -> bool:
        normalized = _norm(phrase)
        if not normalized:
            return True
        if normalized in term_set or normalized in corpus:
            return True
        if len(normalized) <= 4 and _term_in_text(normalized, corpus):
            return True
        tokens = [
            token.lower()
            for token in EVIDENCE_TOKEN_RE.findall(normalized)
            if token.lower() not in STOPWORDS
        ]
        if not tokens:
            return True
        return all(_stem_token(token) in master_tokens for token in tokens)

    jd_lower = _norm(jd)

    def appears_in_jd(text: str) -> bool:
        return not jd_lower or _term_in_text(_norm(text), jd_lower)

    matched_values = list(insight.matched) if insight else []
    missing_values = list(insight.missing) if insight else []
    for item in selection.matched_keywords:
        text = str(item).strip()
        if text and is_evidenced(text) and appears_in_jd(text):
            matched_values.append(text)
    for item in selection.missing_keywords:
        text = str(item).strip()
        if not text:
            continue
        if is_evidenced(text):
            if appears_in_jd(text):
                matched_values.append(text)
        elif _clean_missing(text) and appears_in_jd(text):
            missing_values.append(text)

    matched = [item for item in _dedupe(matched_values) if _clean_match(item)][:40]
    missing = _dedupe(missing_values)[:25]

    selections: list[SelectionNote] = []
    for project in selected_projects:
        selections.append(
            SelectionNote(kind="project", title=project.title, reason=project.reason)
        )
    for experience in selected_experiences:
        selections.append(
            SelectionNote(
                kind="experience", title=experience.title, reason=experience.reason
            )
        )
    for activity in selected_activities:
        selections.append(
            SelectionNote(kind="activity", title=activity.title, reason=activity.reason)
        )

    report = Report(
        company=(requested_company or selection.company or "").strip(),
        role=(requested_role or selection.role or "").strip(),
        matched_keywords=matched,
        missing_keywords=missing,
        notes=(selection.notes or "").strip(),
        warnings=_dedupe(warnings),
        selections=selections,
        provider=provider,
        model=model,
        mode=mode,
    )

    return TailoredResume(
        profile=master.profile,
        education=master.education,
        summary=summary,
        skills=skills,
        projects=selected_projects,
        experience=selected_experiences,
        extracurricular=selected_activities,
        certifications=master.certifications,
        languages=master.languages,
        report=report,
    )


def tailor(
    jd: str,
    master: MasterResume,
    options: TailorOptions,
    provider: str,
    model: str,
    cfg: dict[str, Any] | None = None,
    company: str = "",
    role: str = "",
    notes: str = "",
) -> TailoredResume:
    cfg = cfg or load_config()
    insight = analyze_jd(jd, master)
    warnings: list[str] = []
    mode = "llm"

    try:
        raw = chat_json(
            provider,
            model,
            _build_messages(master, jd, options, notes),
            cfg=cfg,
            temperature=0.15,
            max_tokens=4096,
        )
        selection = LLMTailoring.model_validate(raw)
    except (LLMError, ValidationError) as exc:
        mode = "fallback"
        message = str(exc)
        warnings.append(
            "LLM tailoring was unavailable, so the app used deterministic keyword "
            f"matching with your original bullet text. Reason: {message}"
        )
        selection = _fallback_selection(
            master,
            insight,
            options,
            company,
            role,
            "Deterministic keyword matching. Original bullets were preserved.",
        )

    if mode == "llm":
        valid_projects = {project.id for project in master.projects}
        valid_experiences = {item.id for item in master.experience}
        valid_activities = {item.id for item in master.extracurricular}
        missing_project = not any(
            item.id in valid_projects for item in selection.selected_projects
        )
        missing_experience = not any(
            item.id in valid_experiences for item in selection.selected_experiences
        )
        missing_activity = not any(
            item.id in valid_activities for item in selection.selected_extracurricular
        )
        if missing_project or missing_experience or missing_activity:
            fallback = _fallback_selection(
                master, insight, options, company, role, "Keyword-based selection."
            )
            if missing_project:
                selection.selected_projects = fallback.selected_projects
            if missing_experience:
                selection.selected_experiences = fallback.selected_experiences
            if missing_activity:
                selection.selected_extracurricular = (
                    fallback.selected_extracurricular
                )
            if not selection.skills:
                selection.skills = fallback.skills
            if not selection.matched_keywords:
                selection.matched_keywords = insight.matched
            warnings.append(
                "The model returned missing or invalid item ids; the app filled "
                "those sections with keyword-matched items."
            )

    return compose(
        master=master,
        selection=selection,
        options=options,
        provider=provider,
        model=model,
        mode=mode,
        warnings=warnings,
        requested_company=company,
        requested_role=role,
        insight=insight,
        jd=jd,
    )


def merge_edits(
    tailored: TailoredResume,
    master: MasterResume,
    edited_projects,
    edited_experiences,
    edited_activities,
    skill_names: list[str],
    summary: str | None,
) -> TailoredResume:
    warnings: list[str] = list(tailored.report.warnings)
    master_nums = master_numbers(master)
    unknown_terms = unknown_tech_terms(master)

    projects_by_id = {project.id: project for project in master.projects}
    experience_by_id = {item.id: item for item in master.experience}
    activities_by_id = {item.id: item for item in master.extracurricular}
    current_projects = {item.id: item for item in tailored.projects}
    current_experiences = {item.id: item for item in tailored.experience}
    current_activities = {item.id: item for item in tailored.extracurricular}

    def resolve_bullets(edited, current, fallback: list[str], label: str) -> list[str]:
        cleaned = [clean_bullet(item, 400) for item in (edited.bullets or []) if str(item).strip()]
        cleaned = _dedupe(cleaned)
        if not cleaned:
            cleaned = list(current.bullets) if current else list(fallback)
        for text in cleaned:
            issues = validate_bullet(text, master_nums, unknown_terms)
            if issues:
                warnings.append(f"{label}: edited bullet warning ({'; '.join(issues)}).")
        return cleaned

    new_projects: list[RenderProject] = []
    if edited_projects:
        for edited in edited_projects:
            if not edited.enabled:
                continue
            project = projects_by_id.get(edited.id)
            current = current_projects.get(edited.id)
            if not project:
                continue
            bullets = resolve_bullets(
                edited,
                current,
                [bullet.text for bullet in project.bullets],
                f"Project '{project.title}'",
            )
            new_projects.append(
                RenderProject(
                    id=project.id,
                    title=project.title,
                    subtitle=project.subtitle,
                    link=project.link,
                    tech=project.tech,
                    bullets=bullets,
                    reason=current.reason if current else "",
                )
            )
    else:
        new_projects = list(tailored.projects)

    new_experiences: list[RenderExperience] = []
    if edited_experiences:
        for edited in edited_experiences:
            if not edited.enabled:
                continue
            experience = experience_by_id.get(edited.id)
            current = current_experiences.get(edited.id)
            if not experience:
                continue
            bullets = resolve_bullets(
                edited,
                current,
                [bullet.text for bullet in experience.bullets],
                f"Experience '{experience.title}'",
            )
            new_experiences.append(
                RenderExperience(
                    id=experience.id,
                    title=experience.title,
                    organization=experience.organization,
                    dates=experience.dates,
                    bullets=bullets,
                    reason=current.reason if current else "",
                )
            )
    else:
        new_experiences = list(tailored.experience)

    new_activities: list[RenderActivity] = []
    if edited_activities:
        for edited in edited_activities:
            if not edited.enabled:
                continue
            activity = activities_by_id.get(edited.id)
            current = current_activities.get(edited.id)
            if not activity:
                continue
            bullets = resolve_bullets(
                edited,
                current,
                [bullet.text for bullet in activity.bullets],
                f"Activity '{activity.title}'",
            )
            new_activities.append(
                RenderActivity(
                    id=activity.id,
                    title=activity.title,
                    subtitle=activity.subtitle,
                    bullets=bullets,
                    reason=current.reason if current else "",
                )
            )
    else:
        new_activities = list(tailored.extracurricular)

    skills = _skills_from_names(
        master,
        skill_names,
        max(1, len(skill_names)) if skill_names else len(tailored.skills) or 3,
    )
    if not skill_names:
        skills = list(tailored.skills)

    new_summary = tailored.summary
    if summary is not None:
        cleaned_summary = re.sub(r"\s+", " ", str(summary)).strip()
        if cleaned_summary:
            issues = validate_bullet(cleaned_summary, master_nums, unknown_terms)
            if issues:
                warnings.append(
                    "Edited summary warning: " + "; ".join(issues)
                )
            new_summary = cleaned_summary[:SUMMARY_MAX_CHARS]
        else:
            new_summary = None

    report = tailored.report.model_copy(
        update={"warnings": _dedupe(warnings), "trimmed": False, "pages": 1}
    )
    return tailored.model_copy(
        update={
            "projects": new_projects,
            "experience": new_experiences,
            "extracurricular": new_activities,
            "skills": skills,
            "summary": new_summary,
            "report": report,
        }
    )


def related_terms(master: MasterResume, limit: int = 20) -> list[str]:
    terms = sorted(master_terms(master), key=lambda item: (-len(item), item))
    return terms[:limit]
