from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


class Link(BaseModel):
    label: str
    url: str


class Profile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    headline: str = ""
    phone: str = ""
    email: str = ""
    location: str = ""
    links: list[Link] = Field(default_factory=list)


class EducationItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    institution: str
    degree: str = ""
    dates: str = ""
    details: list[str] = Field(default_factory=list)


class SkillGroup(BaseModel):
    model_config = ConfigDict(extra="ignore")

    category: str
    items: list[str] = Field(default_factory=list)


class Bullet(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str
    tags: list[str] = Field(default_factory=list)


class Project(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    subtitle: str = ""
    link: str = ""
    tech: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    bullets: list[Bullet] = Field(default_factory=list)


class ExperienceItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    organization: str = ""
    dates: str = ""
    location: str = ""
    tags: list[str] = Field(default_factory=list)
    bullets: list[Bullet] = Field(default_factory=list)


class ActivityItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    subtitle: str = ""
    tags: list[str] = Field(default_factory=list)
    bullets: list[Bullet] = Field(default_factory=list)


class Certification(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    issuer: str = ""


class MasterResume(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: int = 1
    profile: Profile
    education: list[EducationItem] = Field(default_factory=list)
    skills: list[SkillGroup] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    experience: list[ExperienceItem] = Field(default_factory=list)
    extracurricular: list[ActivityItem] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)


class SelectedProject(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    bullets: list[str] = Field(default_factory=list)
    reason: str = ""


class SelectedExperience(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    bullets: list[str] = Field(default_factory=list)
    reason: str = ""


class SelectedActivity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    bullets: list[str] = Field(default_factory=list)
    reason: str = ""


class LLMTailoring(BaseModel):
    model_config = ConfigDict(extra="ignore")

    company: str = ""
    role: str = ""
    matched_keywords: list[str] = Field(default_factory=list)
    missing_keywords: list[str] = Field(default_factory=list)
    notes: str = ""
    summary: str | None = None
    skills: list[str] = Field(default_factory=list)
    selected_projects: list[SelectedProject] = Field(default_factory=list)
    selected_experiences: list[SelectedExperience] = Field(default_factory=list)
    selected_extracurricular: list[SelectedActivity] = Field(default_factory=list)


class SelectionNote(BaseModel):
    kind: str
    title: str
    reason: str = ""


class Report(BaseModel):
    model_config = ConfigDict(extra="ignore")

    company: str = ""
    role: str = ""
    matched_keywords: list[str] = Field(default_factory=list)
    missing_keywords: list[str] = Field(default_factory=list)
    notes: str = ""
    warnings: list[str] = Field(default_factory=list)
    selections: list[SelectionNote] = Field(default_factory=list)
    provider: str = ""
    model: str = ""
    mode: str = "llm"
    trimmed: bool = False
    pages: int = 1


class RenderProject(BaseModel):
    id: str
    title: str
    subtitle: str = ""
    link: str = ""
    tech: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)
    reason: str = ""


class RenderExperience(BaseModel):
    id: str
    title: str
    organization: str = ""
    dates: str = ""
    bullets: list[str] = Field(default_factory=list)
    reason: str = ""


class RenderActivity(BaseModel):
    id: str
    title: str
    subtitle: str = ""
    bullets: list[str] = Field(default_factory=list)
    reason: str = ""


class TailoredResume(BaseModel):
    profile: Profile
    education: list[EducationItem] = Field(default_factory=list)
    summary: str | None = None
    skills: list[SkillGroup] = Field(default_factory=list)
    projects: list[RenderProject] = Field(default_factory=list)
    experience: list[RenderExperience] = Field(default_factory=list)
    extracurricular: list[RenderActivity] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    report: Report = Field(default_factory=Report)


class EmailDraft(BaseModel):
    subject: str = ""
    body: str = ""
    recruiter_email: str = ""
    company: str = ""
    role: str = ""


class TailorOptions(BaseModel):
    intensity: str = "balanced"
    max_projects: int = 3
    max_experiences: int = 2
    max_skill_groups: int = 3
    max_bullets_per_item: int = 3
    include_summary: bool = False


class TailorRequest(BaseModel):
    jd: str
    company: str = ""
    role: str = ""
    recruiter_name: str = ""
    recruiter_email: str = ""
    notes: str = ""
    provider: str = "nvidia"
    model: str = ""
    intensity: str = "balanced"
    include_summary: bool = False
    generate_email: bool = True


class ImportRequest(BaseModel):
    files: list[str] = Field(default_factory=list)
    provider: str = "nvidia"
    model: str = ""
    mode: str = "merge"
    use_llm: bool = True
    dry_run: bool = False


class EditedItem(BaseModel):
    id: str
    enabled: bool = True
    bullets: list[str] = Field(default_factory=list)


class EmailEdit(BaseModel):
    subject: str = ""
    body: str = ""
    recruiter_email: str = ""


class ExportRequest(BaseModel):
    projects: list[EditedItem] = Field(default_factory=list)
    experiences: list[EditedItem] = Field(default_factory=list)
    activities: list[EditedItem] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    summary: str | None = None
    email: EmailEdit | None = None
    include_email: bool = True


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    request: TailorRequest
    tailored: TailoredResume
    email: EmailDraft | None = None
    export_dir: str = ""
    exported_files: list[str] = Field(default_factory=list)
