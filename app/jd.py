from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import MasterResume, Project, ExperienceItem, ActivityItem

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can",
    "could", "did", "do", "does", "for", "from", "had", "has", "have", "he",
    "her", "here", "him", "his", "how", "i", "if", "in", "into", "is", "it",
    "its", "job", "just", "may", "me", "might", "more", "most", "must", "my",
    "no", "not", "of", "on", "or", "our", "out", "over", "own", "per", "role",
    "should", "so", "some", "such", "than", "that", "the", "their", "them",
    "then", "there", "these", "they", "this", "those", "through", "to", "too",
    "under", "up", "us", "very", "was", "we", "were", "what", "when", "where",
    "which", "while", "who", "will", "with", "would", "you", "your", "years",
    "year", "work", "working", "experience", "experienced", "strong", "good",
    "knowledge", "understanding", "ability", "skills", "skill", "plus",
    "preferred", "required", "requirements", "responsibilities", "team",
    "teams", "company", "candidate", "candidates", "including", "using",
    "build", "building", "develop", "developing", "design", "designing",
    "new", "well", "also", "across", "within", "about", "apply", "join",
}

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#./-]*")

KNOWN_TECH = {
    "python", "java", "javascript", "typescript", "c++", "c#", "rust",
    "scala", "matlab", "sql", "nosql", "postgresql", "mysql", "sqlite",
    "mongodb", "redis", "kafka", "pyspark", "hadoop", "airflow",
    "docker", "kubernetes", "terraform", "ansible", "aws", "azure", "gcp",
    "google cloud", "linux", "git", "github", "gitlab", "jenkins", "ci/cd",
    "fastapi", "flask", "django", "nodejs", "next.js",
    "vue", "angular", "tensorflow", "pytorch", "keras", "scikit-learn",
    "pandas", "numpy", "opencv", "hugging face", "langchain", "langgraph",
    "llama", "openai", "gemini", "ollama", "chromadb", "pgvector", "pinecone",
    "weaviate", "qdrant", "faiss", "rag", "llm", "llms", "nlp", "computer vision",
    "ros", "ros 2", "cuda", "tensorrt", "onnx", "mlflow", "kubeflow",
    "celery", "websockets", "websocket", "rest", "graphql", "grpc", "jwt",
    "oauth", "owasp", "selenium", "pytest", "unittest", "tableau", "jira",
    "confluence", "power bi", "streamlit", "gradio", "pinecone",
    "sentence transformers", "huggingface", "transformers", "llamaindex",
    "autogen", "crewai", "n8n", "zapier", "supabase", "firebase", "dynamodb",
    "bigquery", "snowflake", "databricks", "dbt", "elasticsearch", "rabbitmq",
    "nginx", "grafana", "prometheus", "opentelemetry", "playwright", "typescript",
    "microservices", "serverless", "devops", "mlops", "data pipelines", "etl",
    "elt", "nlp", "speech recognition", "recommender systems", "reinforcement learning",
}


@dataclass
class JDInsight:
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    project_scores: dict[str, float] = field(default_factory=dict)
    experience_scores: dict[str, float] = field(default_factory=dict)
    activity_scores: dict[str, float] = field(default_factory=dict)
    skill_scores: dict[str, float] = field(default_factory=dict)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def master_terms(master: MasterResume) -> set[str]:
    terms: set[str] = set()

    def add(value: str) -> None:
        value = _norm(value)
        if value:
            terms.add(value)
            for token in TOKEN_RE.findall(value):
                if len(token) > 1 and token not in STOPWORDS:
                    terms.add(token.lower())

    for group in master.skills:
        add(group.category)
        for item in group.items:
            add(item)
    for project in master.projects:
        add(project.title)
        add(project.subtitle)
        for tag in project.tags:
            add(tag)
        for tech in project.tech:
            add(tech)
        for bullet in project.bullets:
            add(bullet.text)
            for tag in bullet.tags:
                add(tag)
    for experience in master.experience:
        add(experience.title)
        add(experience.organization)
        for tag in experience.tags:
            add(tag)
        for bullet in experience.bullets:
            add(bullet.text)
            for tag in bullet.tags:
                add(tag)
    for activity in master.extracurricular:
        add(activity.title)
        add(activity.subtitle)
        for tag in activity.tags:
            add(tag)
        for bullet in activity.bullets:
            add(bullet.text)
            for tag in bullet.tags:
                add(tag)
    for cert in master.certifications:
        add(cert.name)
        add(cert.issuer)
    for lang in master.languages:
        add(lang)
    return terms


def master_display_terms(master: MasterResume) -> dict[str, str]:
    display: dict[str, str] = {}

    def add(value: str) -> None:
        original = (value or "").strip()
        normalized = _norm(original)
        if len(normalized) < 2 or normalized in STOPWORDS:
            return
        display.setdefault(normalized, original)

    for group in master.skills:
        add(group.category)
        for item in group.items:
            add(item)
    for project in master.projects:
        for tag in project.tags:
            add(tag)
        for tech in project.tech:
            add(tech)
    for experience in master.experience:
        for tag in experience.tags:
            add(tag)
    for activity in master.extracurricular:
        for tag in activity.tags:
            add(tag)
    for cert in master.certifications:
        add(cert.name)
    for language in master.languages:
        add(language)
    return display


def master_text(master: MasterResume) -> str:
    parts: list[str] = [
        master.profile.name,
        master.profile.headline,
        master.profile.phone,
        master.profile.email,
    ]
    parts.extend(link.url for link in master.profile.links)
    for education in master.education:
        parts.extend([education.institution, education.degree, education.dates, *education.details])
    for group in master.skills:
        parts.append(group.category)
        parts.extend(group.items)
    for project in master.projects:
        parts.extend([project.title, project.subtitle, *project.tech, *project.tags])
        parts.extend(bullet.text for bullet in project.bullets)
    for experience in master.experience:
        parts.extend([experience.title, experience.organization, experience.dates, *experience.tags])
        parts.extend(bullet.text for bullet in experience.bullets)
    for activity in master.extracurricular:
        parts.extend([activity.title, activity.subtitle, *activity.tags])
        parts.extend(bullet.text for bullet in activity.bullets)
    for cert in master.certifications:
        parts.extend([cert.name, cert.issuer])
    parts.extend(master.languages)
    return "\n".join(parts)


def _term_in_text(term: str, text: str) -> bool:
    term = term.lower().strip()
    if not term:
        return False
    if len(term) <= 4 or any(ch in term for ch in "+#./"):
        pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
        return re.search(pattern, text) is not None
    return term in text


def _item_terms(project: Project | ExperienceItem | ActivityItem) -> list[str]:
    terms: list[str] = []
    if isinstance(project, Project):
        terms.extend([project.title, project.subtitle, *project.tech, *project.tags])
    else:
        terms.extend([project.title, *project.tags])
        if isinstance(project, ExperienceItem):
            terms.extend([project.organization, project.dates])
        else:
            terms.append(project.subtitle)
    for bullet in project.bullets:
        terms.extend(bullet.tags)
        terms.extend(TOKEN_RE.findall(bullet.text))
    return [term for term in terms if term]


def _score(terms: list[str], jd_lower: str) -> float:
    score = 0.0
    seen: set[str] = set()
    for term in terms:
        term_lower = _norm(term)
        if len(term_lower) < 2 or term_lower in seen:
            continue
        seen.add(term_lower)
        if term_lower in STOPWORDS:
            continue
        weight = 1.0
        if len(term_lower.split()) > 1:
            weight = 2.0
        if _term_in_text(term_lower, jd_lower):
            score += weight
    return round(score, 3)


def analyze_jd(jd: str, master: MasterResume) -> JDInsight:
    jd_lower = _norm(jd)
    display_terms = master_display_terms(master)
    matched_display = [
        display_terms[term]
        for term in sorted(
            display_terms,
            key=lambda item: (-len(item.split()), -len(item), item),
        )
        if _term_in_text(term, jd_lower)
    ][:30]

    master_lower = _norm(master_text(master))

    def in_master(term: str) -> bool:
        if _term_in_text(term, master_lower):
            return True
        if term.endswith("s") and _term_in_text(term[:-1], master_lower):
            return True
        if not term.endswith("s") and _term_in_text(term + "s", master_lower):
            return True
        return False

    known_in_jd = sorted(
        {term for term in KNOWN_TECH if _term_in_text(term, jd_lower)},
        key=lambda item: (-len(item.split()), -len(item), item),
    )
    missing = [term for term in known_in_jd if not in_master(term)][:15]

    insight = JDInsight(matched=matched_display, missing=missing)
    for group in master.skills:
        insight.skill_scores[group.category] = _score(
            [group.category, *group.items], jd_lower
        )
    for project in master.projects:
        insight.project_scores[project.id] = _score(_item_terms(project), jd_lower)
    for experience in master.experience:
        insight.experience_scores[experience.id] = _score(_item_terms(experience), jd_lower)
    for activity in master.extracurricular:
        insight.activity_scores[activity.id] = _score(_item_terms(activity), jd_lower)
    return insight


def unknown_tech_terms(master: MasterResume) -> list[str]:
    corpus = _norm(master_text(master))
    unknown: list[str] = []
    for term in sorted(KNOWN_TECH):
        if _term_in_text(term, corpus):
            continue
        unknown.append(term)
    return unknown
