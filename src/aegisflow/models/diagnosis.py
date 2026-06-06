"""Triage agent output schema."""

from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)


class IncidentDiagnosis(BaseModel):
    """Structured diagnosis produced by the triage agent."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    error_classification: str = Field(
        ...,
        min_length=1,
        description="High-level error category (e.g. OOM, latency, dependency failure).",
    )
    confidence_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Model confidence in the diagnosis, bounded to [0.0, 1.0].",
    )
    root_cause_analysis: str = Field(
        ...,
        min_length=1,
        description="Narrative root-cause analysis for operators.",
    )
    log_fingerprints: list[str] = Field(
        default_factory=list,
        description="Distinct log signature fingerprints supporting the diagnosis.",
    )

    @field_validator("log_fingerprints")
    @classmethod
    def validate_log_fingerprints(cls, value: list[str]) -> list[str]:
        """Drop empty fingerprints and warn when invalid entries are present."""
        cleaned = [fingerprint.strip() for fingerprint in value if fingerprint.strip()]
        if len(cleaned) != len(value):
            logger.warning(
                "Removed %d empty log fingerprint(s) from IncidentDiagnosis.",
                len(value) - len(cleaned),
            )
        return cleaned
