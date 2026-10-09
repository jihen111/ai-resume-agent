"""Structured output contract enforced on the LLM.

This schema is sent to OpenAI as a *strict* JSON Schema (Structured Outputs), so it
follows the constraints of that mode:
- every field is required; "optional" values are nullable (`X | None`) with no default;
- every object forbids extra keys (`additionalProperties: false`);
- no string-length keywords (unsupported in strict mode): sizes are enforced by
  validators instead, which normalise rather than reject.

Field descriptions are part of the prompt: the model reads them. Keep them precise.
Field order matters too: `fit_score_rationale` comes before `fit_score` so the model
reasons before committing to a number.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_SKILLS = 40
MAX_EXPERIENCES = 15

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SkillCategory(StrEnum):
    TECHNICAL = "technical"
    TOOL = "tool"
    DOMAIN = "domain"
    SOFT = "soft"
    LANGUAGE = "language"


class _LLMSchema(BaseModel):
    """Base for every model the LLM fills in."""

    model_config = ConfigDict(
        extra="forbid",  # -> additionalProperties: false (required by strict mode)
        frozen=True,
        str_strip_whitespace=True,
    )


def _blank_to_none(value: object) -> object:
    if isinstance(value, str) and not value.strip():
        return None
    return value


class Skill(_LLMSchema):
    """A skill explicitly evidenced in the resume."""

    name: str = Field(
        description=(
            "Canonical skill name, short and normalised "
            "(e.g. 'JavaScript' not 'JS', 'PostgreSQL' not 'postgres'). Never a sentence."
        ),
    )
    category: SkillCategory = Field(
        description=(
            "technical: languages, frameworks, methods (Python, React, TDD). "
            "tool: software, platforms, cloud services (Docker, AWS, Jira). "
            "domain: business or industry knowledge (fintech, supply chain). "
            "soft: interpersonal skills (leadership, communication). "
            "language: spoken languages (English C1)."
        ),
    )


class Experience(_LLMSchema):
    """A professional experience (job, internship, freelance mission)."""

    role: str = Field(description="Job title as written in the resume.")
    company: str = Field(description="Employer or client name as written in the resume.")
    start_date: str | None = Field(
        description="Start date formatted 'YYYY-MM' or 'YYYY'. Null if not stated.",
    )
    end_date: str | None = Field(
        description="End date formatted 'YYYY-MM' or 'YYYY'. Null if current position or not stated.",
    )
    business_impact: str | None = Field(
        description=(
            "One sentence on the concrete business outcome, keeping any figures stated in "
            "the resume (revenue, users, latency, cost, team size). "
            "Null if the resume states no outcome. Never invent metrics."
        ),
    )

    _normalise_optional = field_validator(
        "start_date", "end_date", "business_impact", mode="before"
    )(_blank_to_none)


class ResumeExtraction(_LLMSchema):
    """Structured analysis of a resume against a job description."""

    full_name: str | None = Field(description="Candidate full name. Null if absent.")
    email: str | None = Field(description="Candidate email address. Null if absent.")
    skills: list[Skill] = Field(
        description=f"Key skills, most relevant to the job first, at most {MAX_SKILLS}, no duplicates.",
    )
    experiences: list[Experience] = Field(
        description=f"Major professional experiences, most recent first, at most {MAX_EXPERIENCES}.",
    )
    summary: str = Field(
        description="Neutral 2-3 sentence profile summary: seniority, core expertise, standout achievement.",
    )
    fit_score_rationale: str = Field(
        description=(
            "2-4 sentences justifying the score: which key requirements are met, "
            "which are missing. Written before the score."
        ),
    )
    fit_score: int = Field(
        ge=0,
        le=100,
        description="Job fit score from 0 to 100, following the rubric in the instructions.",
    )

    @field_validator("full_name", mode="before")
    @classmethod
    def _normalise_name(cls, value: object) -> object:
        return _blank_to_none(value)

    @field_validator("email", mode="before")
    @classmethod
    def _normalise_email(cls, value: object) -> str | None:
        # An unparseable email must not fail the whole extraction: degrade to null.
        if not isinstance(value, str):
            return None
        candidate = value.strip().lower()
        return candidate if _EMAIL_RE.fullmatch(candidate) else None

    @field_validator("skills")
    @classmethod
    def _dedupe_skills(cls, skills: list[Skill]) -> list[Skill]:
        seen: set[str] = set()
        unique: list[Skill] = []
        for skill in skills:
            key = skill.name.casefold()
            if key and key not in seen:
                seen.add(key)
                unique.append(skill)
        return unique[:MAX_SKILLS]

    @field_validator("experiences")
    @classmethod
    def _cap_experiences(cls, experiences: list[Experience]) -> list[Experience]:
        return experiences[:MAX_EXPERIENCES]

    @field_validator("fit_score", mode="before")
    @classmethod
    def _clamp_score(cls, value: object) -> object:
        # Defence in depth: strict mode already enforces 0-100, but a float or
        # out-of-range value from a non-strict fallback should not crash the pipeline.
        if isinstance(value, bool):
            return value  # let Pydantic reject it
        if isinstance(value, int | float):
            return max(0, min(100, round(value)))
        return value
