"""Resume analysis endpoints.

Demo storage: analyses live in a module-level Python list. It is lost on every
restart (including each --reload) and is not shared between workers, so run a
single uvicorn worker. To be replaced by a PostgreSQL repository.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict

from app.schemas.llm_output import ResumeExtraction
from app.services.ai.base import AnalyzerConfigurationError, ResumeAnalyzer
from app.services.ai.demo_analyzer import DemoAnalyzer

router = APIRouter(prefix="/analyses", tags=["analyses"])

MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_SIZE_MB", "5")) * 1024 * 1024
ALLOWED_EXTENSIONS = frozenset({".txt", ".md", ".pdf"})
MAX_PDF_PAGES = 20


# --------------------------------------------------------------------------- #
# Response schemas
# --------------------------------------------------------------------------- #
class AnalysisRead(BaseModel):
    """Full analysis, returned by POST and GET /{id}."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    filename: str
    created_at: datetime
    llm_model: str
    prompt_version: str
    result: ResumeExtraction


class AnalysisSummary(BaseModel):
    """Lightweight row for the dashboard list."""

    id: UUID
    filename: str
    created_at: datetime
    full_name: str | None
    email: str | None
    fit_score: int
    summary: str
    top_skills: list[str]

    @classmethod
    def from_analysis(cls, analysis: AnalysisRead) -> AnalysisSummary:
        result = analysis.result
        return cls(
            id=analysis.id,
            filename=analysis.filename,
            created_at=analysis.created_at,
            full_name=result.full_name,
            email=result.email,
            fit_score=result.fit_score,
            summary=result.summary,
            top_skills=[skill.name for skill in result.skills[:5]],
        )


# --------------------------------------------------------------------------- #
# In-memory storage (demo only)
# --------------------------------------------------------------------------- #
ANALYSES: list[AnalysisRead] = []


# --------------------------------------------------------------------------- #
# Dependencies
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def _build_analyzer() -> ResumeAnalyzer:
    # Built once, on first use. A failed build is not cached, so setting the
    # key and restarting is enough.
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return DemoAnalyzer()  # offline fallback: no key, no LangChain/OpenAI install needed
    from app.services.ai.openai_analyzer import OpenAIAnalyzer  # lazy: heavy deps

    return OpenAIAnalyzer()


def get_analyzer() -> ResumeAnalyzer:
    try:
        return _build_analyzer()
    except AnalyzerConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _decode_text(content: bytes) -> str:
    try:
        text = content.decode("utf-8-sig")  # UTF-8, minus the BOM some Windows editors add
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The file must be UTF-8 encoded text.",
        ) from exc

    text = text.replace("\x00", "").strip()
    if not text:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The file contains no text.")
    return text


def _extract_pdf_text(content: bytes) -> str:
    # Lazy import: only PDF uploads pay for it.
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted:
            reader.decrypt("")  # many PDFs are "encrypted" with an empty user password
        if len(reader.pages) > MAX_PDF_PAGES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"PDF has more than {MAX_PDF_PAGES} pages.",
            )
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except (PdfReadError, ValueError, KeyError, NotImplementedError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The PDF could not be read (corrupted or password-protected).",
        ) from exc

    text = text.replace("\x00", "").strip()
    if not text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No text found in the PDF. Scanned (image-only) PDFs are not supported.",
        )
    return text


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@router.post(
    "",
    response_model=AnalysisRead,
    status_code=status.HTTP_201_CREATED,
    summary="Analyse a resume against a job description",
)
async def create_analysis(
    file: Annotated[UploadFile, File(description="Resume as a .pdf, or a UTF-8 .txt or .md file.")],
    job_description: Annotated[
        str, Form(min_length=30, max_length=20_000, description="Target job description.")
    ],
    analyzer: Annotated[ResumeAnalyzer, Depends(get_analyzer)],
) -> AnalysisRead:
    filename = Path(file.filename or "resume.txt").name  # drop any client-side path
    if Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only .pdf, .txt and .md files are accepted.",
        )

    # Read at most limit + 1 bytes: never load an oversized upload in memory.
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )

    if Path(filename).suffix.lower() == ".pdf":
        resume_text = _extract_pdf_text(content)
    else:
        resume_text = _decode_text(content)

    # ResumeAnalysisError subclasses are mapped to HTTP codes in main.py.
    extraction = await analyzer.analyze(resume_text, job_description)

    analysis = AnalysisRead(
        id=uuid4(),
        filename=filename,
        created_at=datetime.now(UTC),
        llm_model=analyzer.model_name,
        prompt_version=analyzer.prompt_version,
        result=extraction,
    )
    ANALYSES.append(analysis)
    return analysis


@router.get("", response_model=list[AnalysisSummary], summary="List all analyses, most recent first")
async def list_analyses() -> list[AnalysisSummary]:
    return [AnalysisSummary.from_analysis(a) for a in reversed(ANALYSES)]


@router.get("/{analysis_id}", response_model=AnalysisRead, summary="Get one analysis")
async def get_analysis(analysis_id: UUID) -> AnalysisRead:
    for analysis in ANALYSES:
        if analysis.id == analysis_id:
            return analysis
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found.")
