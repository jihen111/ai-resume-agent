"""Offline implementation of the ResumeAnalyzer port, used when no OPENAI_API_KEY is set.

Heuristic only: keyword matching for skills, regexes for contact details and dated
experiences, and a fit score from the share of job skills found in the resume. Good
enough to exercise the full app end to end without an LLM; not a real assessment.
"""

from __future__ import annotations

import re

from app.schemas.llm_output import (
    MAX_EXPERIENCES,
    Experience,
    ResumeExtraction,
    Skill,
    SkillCategory,
)
from app.services.ai.base import InvalidAnalysisInputError

PROMPT_VERSION = "demo-heuristic/v1"

MIN_RESUME_CHARS = 100
MIN_JOB_DESCRIPTION_CHARS = 30

# Canonical name -> (category, extra aliases). The canonical name is always matched too.
_SKILLS: dict[str, tuple[SkillCategory, tuple[str, ...]]] = {
    "Python": (SkillCategory.TECHNICAL, ()),
    "JavaScript": (SkillCategory.TECHNICAL, ("js",)),
    "TypeScript": (SkillCategory.TECHNICAL, ("ts",)),
    "Java": (SkillCategory.TECHNICAL, ()),
    "C#": (SkillCategory.TECHNICAL, ()),
    "C++": (SkillCategory.TECHNICAL, ()),
    "Go": (SkillCategory.TECHNICAL, ("golang",)),
    "PHP": (SkillCategory.TECHNICAL, ()),
    "SQL": (SkillCategory.TECHNICAL, ()),
    "HTML": (SkillCategory.TECHNICAL, ()),
    "CSS": (SkillCategory.TECHNICAL, ()),
    "React": (SkillCategory.TECHNICAL, ("react.js", "reactjs")),
    "Angular": (SkillCategory.TECHNICAL, ()),
    "Vue.js": (SkillCategory.TECHNICAL, ("vue", "vuejs")),
    "Node.js": (SkillCategory.TECHNICAL, ("node", "nodejs")),
    "Django": (SkillCategory.TECHNICAL, ()),
    "Flask": (SkillCategory.TECHNICAL, ()),
    "FastAPI": (SkillCategory.TECHNICAL, ()),
    "Spring Boot": (SkillCategory.TECHNICAL, ("spring",)),
    "REST APIs": (SkillCategory.TECHNICAL, ("rest", "restful", "rest api")),
    "GraphQL": (SkillCategory.TECHNICAL, ()),
    "Machine Learning": (SkillCategory.TECHNICAL, ("ml",)),
    "Deep Learning": (SkillCategory.TECHNICAL, ()),
    "NLP": (SkillCategory.TECHNICAL, ()),
    "TensorFlow": (SkillCategory.TECHNICAL, ()),
    "PyTorch": (SkillCategory.TECHNICAL, ()),
    "pandas": (SkillCategory.TECHNICAL, ()),
    "Agile": (SkillCategory.TECHNICAL, ("scrum",)),
    "TDD": (SkillCategory.TECHNICAL, ("unit testing",)),
    "PostgreSQL": (SkillCategory.TOOL, ("postgres",)),
    "MySQL": (SkillCategory.TOOL, ()),
    "MongoDB": (SkillCategory.TOOL, ()),
    "Redis": (SkillCategory.TOOL, ()),
    "Docker": (SkillCategory.TOOL, ()),
    "Kubernetes": (SkillCategory.TOOL, ("k8s",)),
    "AWS": (SkillCategory.TOOL, ()),
    "Azure": (SkillCategory.TOOL, ()),
    "GCP": (SkillCategory.TOOL, ("google cloud",)),
    "Git": (SkillCategory.TOOL, ("github", "gitlab")),
    "CI/CD": (SkillCategory.TOOL, ()),
    "Linux": (SkillCategory.TOOL, ()),
    "Jira": (SkillCategory.TOOL, ()),
    "Figma": (SkillCategory.TOOL, ()),
    "Power BI": (SkillCategory.TOOL, ()),
    "Excel": (SkillCategory.TOOL, ()),
    "Fintech": (SkillCategory.DOMAIN, ()),
    "E-commerce": (SkillCategory.DOMAIN, ("ecommerce",)),
    "Healthcare": (SkillCategory.DOMAIN, ()),
    "Leadership": (SkillCategory.SOFT, ("led", "mentored")),
    "Communication": (SkillCategory.SOFT, ()),
    "Teamwork": (SkillCategory.SOFT, ("collaboration",)),
    "Problem Solving": (SkillCategory.SOFT, ()),
    "English": (SkillCategory.LANGUAGE, ()),
    "French": (SkillCategory.LANGUAGE, ("français",)),
    "Arabic": (SkillCategory.LANGUAGE, ()),
}


def _term_pattern(term: str) -> re.Pattern[str]:
    # Custom word boundaries: \b fails around symbols like "C#", "C++" or "CI/CD".
    return re.compile(rf"(?<![\w+#]){re.escape(term)}(?![\w+#])", re.IGNORECASE)


_SKILL_PATTERNS: list[tuple[str, SkillCategory, list[re.Pattern[str]]]] = [
    (name, category, [_term_pattern(t) for t in (name, *aliases)])
    for name, (category, aliases) in _SKILLS.items()
]

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_DATE = r"(?:\d{1,2}[/.-])?(?:19|20)\d{2}"
_RANGE_RE = re.compile(
    rf"({_DATE})\s*(?:-|–|—|to|à)\s*({_DATE}|present|current|now|today|aujourd'hui)",
    re.IGNORECASE,
)
_ROLE_COMPANY_RE = re.compile(r"^(?P<role>[^|,@]+?)\s*(?:\bat\b|@|\||,|–|—| - )\s*(?P<company>[^|,(]+)")


def _find_skills(text: str) -> list[Skill]:
    hits: list[tuple[int, Skill]] = []
    for name, category, patterns in _SKILL_PATTERNS:
        positions = [m.start() for p in patterns if (m := p.search(text))]
        if positions:
            hits.append((min(positions), Skill(name=name, category=category)))
    return [skill for _, skill in sorted(hits, key=lambda hit: hit[0])]


def _normalise_date(raw: str) -> str | None:
    match = re.fullmatch(r"(?:(\d{1,2})[/.-])?((?:19|20)\d{2})", raw.strip())
    if not match:
        return None  # "present" and friends: current position
    month, year = match.groups()
    return f"{year}-{int(month):02d}" if month else year


def _find_experiences(text: str) -> list[Experience]:
    experiences: list[Experience] = []
    lines = [line.strip(" \t-*•#") for line in text.splitlines()]
    for i, line in enumerate(lines):
        dates = _RANGE_RE.search(line)
        if not dates:
            continue
        # Role/company is either on the dated line itself or on the line just above it.
        header = _RANGE_RE.sub("", line).strip(" ()[]|,–—-")
        if not header and i > 0:
            header = lines[i - 1]
        match = _ROLE_COMPANY_RE.match(header)
        role, company = (match["role"], match["company"]) if match else (header, "")
        if not role.strip():
            continue
        experiences.append(
            Experience(
                role=role.strip(),
                company=company.strip() or "Not stated",
                start_date=_normalise_date(dates[1]),
                end_date=_normalise_date(dates[2]),
                business_impact=None,
            )
        )
    experiences.sort(key=lambda e: e.start_date or "", reverse=True)
    return experiences[:MAX_EXPERIENCES]


def _find_name(text: str) -> str | None:
    # Convention: the first short line made of 2-4 capitalised words is the name.
    for line in text.splitlines()[:5]:
        words = line.strip(" #").split()
        if 2 <= len(words) <= 4 and all(w[:1].isupper() and w.replace("-", "").isalpha() for w in words):
            return " ".join(words)
    return None


class DemoAnalyzer:
    """Keyword-based analyzer with the same contract as OpenAIAnalyzer."""

    @property
    def model_name(self) -> str:
        return "demo-heuristic (no OpenAI key)"

    @property
    def prompt_version(self) -> str:
        return PROMPT_VERSION

    async def analyze(self, resume_text: str, job_description: str) -> ResumeExtraction:
        if len(resume_text.strip()) < MIN_RESUME_CHARS:
            raise InvalidAnalysisInputError(f"Resume is too short (min {MIN_RESUME_CHARS} characters).")
        if len(job_description.strip()) < MIN_JOB_DESCRIPTION_CHARS:
            raise InvalidAnalysisInputError(
                f"Job description is too short (min {MIN_JOB_DESCRIPTION_CHARS} characters)."
            )

        resume_skills = _find_skills(resume_text)
        job_skills = {s.name for s in _find_skills(job_description)}
        matched = [s for s in resume_skills if s.name in job_skills]
        missing = sorted(job_skills - {s.name for s in resume_skills})
        # Job-relevant skills first, then the rest in order of appearance.
        skills = matched + [s for s in resume_skills if s.name not in job_skills]

        if job_skills:
            score = round(100 * len(matched) / len(job_skills))
            rationale = (
                f"Demo mode (keyword matching, no LLM): the resume mentions {len(matched)} of the "
                f"{len(job_skills)} skills detected in the job description"
                + (f" ({', '.join(s.name for s in matched)})." if matched else ".")
                + (f" Not found: {', '.join(missing)}." if missing else "")
            )
        else:
            score = 50
            rationale = "Demo mode (keyword matching, no LLM): no known skills detected in the job description; neutral score."

        experiences = _find_experiences(resume_text)
        email = _EMAIL_RE.search(resume_text)
        top = ", ".join(s.name for s in skills[:5]) or "no recognised skills"
        summary = (
            f"Profile with {len(experiences)} dated experience(s) detected; main skills: {top}. "
            "Generated offline by the demo analyzer; set OPENAI_API_KEY for a real AI assessment."
        )

        return ResumeExtraction(
            full_name=_find_name(resume_text),
            email=email[0] if email else None,
            skills=skills,
            experiences=experiences,
            summary=summary,
            fit_score_rationale=rationale,
            fit_score=score,
        )
