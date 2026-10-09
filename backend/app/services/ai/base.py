"""Resume analysis port.

The application layer depends on this Protocol, never on a concrete provider.
Any object with a matching shape satisfies it (structural typing), which makes
swapping providers or injecting a fake in tests trivial.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.schemas.llm_output import ResumeExtraction


class ResumeAnalysisError(Exception):
    """Base class for every analysis failure."""


class InvalidAnalysisInputError(ResumeAnalysisError):
    """Input is unusable (empty or too short). Client error, not retryable."""


class AnalyzerUnavailableError(ResumeAnalysisError):
    """Provider is temporarily unreachable (timeout, rate limit, 5xx). Retryable."""


class AnalyzerConfigurationError(ResumeAnalysisError):
    """Invalid credentials, unknown model or rejected request. Needs an operator."""


class ExtractionError(ResumeAnalysisError):
    """Provider answered, but the output is unusable (refusal, truncation, invalid schema)."""


@runtime_checkable
class ResumeAnalyzer(Protocol):
    """Extracts structured data from a resume and scores it against a job."""

    @property
    def model_name(self) -> str:
        """Identifier of the underlying model, persisted for traceability."""
        ...

    @property
    def prompt_version(self) -> str:
        """Version of the prompt in use, persisted to compare results over time."""
        ...

    async def analyze(self, resume_text: str, job_description: str) -> ResumeExtraction:
        """Analyse a resume against a job description.

        Raises:
            InvalidAnalysisInputError: input is empty or too short.
            AnalyzerUnavailableError: transient provider failure.
            AnalyzerConfigurationError: misconfiguration (key, model).
            ExtractionError: output refused, truncated or non-conforming.
        """
        ...
