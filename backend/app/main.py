"""FastAPI application entry point."""

from __future__ import annotations

import json
import logging
import os

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.endpoints import analyses
from app.services.ai.base import (
    AnalyzerConfigurationError,
    AnalyzerUnavailableError,
    ExtractionError,
    InvalidAnalysisInputError,
    ResumeAnalysisError,
)

API_V1_PREFIX = "/api/v1"
DEFAULT_CORS_ORIGINS = ["http://localhost:5173"]

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


def _cors_origins() -> list[str]:
    """Read CORS_ORIGINS as a JSON list or a comma-separated string."""
    raw = os.getenv("CORS_ORIGINS", "").strip()
    if not raw:
        return DEFAULT_CORS_ORIGINS
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = raw.split(",")
    if isinstance(parsed, str):
        parsed = [parsed]
    return [origin.strip() for origin in parsed if origin.strip()]


# Lookup walks the exception's MRO, so subclasses resolve before the base class.
_ANALYSIS_ERROR_STATUS: dict[type[ResumeAnalysisError], int] = {
    InvalidAnalysisInputError: status.HTTP_422_UNPROCESSABLE_ENTITY,
    AnalyzerUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
    ExtractionError: status.HTTP_502_BAD_GATEWAY,
    AnalyzerConfigurationError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    ResumeAnalysisError: status.HTTP_500_INTERNAL_SERVER_ERROR,
}


async def analysis_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ResumeAnalysisError)
    status_code = next(
        code for cls in type(exc).__mro__ if (code := _ANALYSIS_ERROR_STATUS.get(cls)) is not None
    )
    if status_code == status.HTTP_500_INTERNAL_SERVER_ERROR:
        logger.exception("analysis.error", exc_info=exc)

    # Never leak configuration details to the client on 500s.
    detail = "Internal analysis error." if status_code == 500 else str(exc)
    headers = {"Retry-After": "30"} if status_code == status.HTTP_503_SERVICE_UNAVAILABLE else None
    return JSONResponse(status_code=status_code, content={"detail": detail}, headers=headers)


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Resume Agent",
        version="0.1.0",
        description="Structured resume analysis and job-fit scoring with LLMs.",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=False,  # no cookies: keeps CORS strict and simple
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    app.add_exception_handler(ResumeAnalysisError, analysis_error_handler)
    app.include_router(analyses.router, prefix=API_V1_PREFIX)

    @app.get(f"{API_V1_PREFIX}/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
