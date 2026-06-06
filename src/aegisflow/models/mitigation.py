"""Mitigation agent output schema."""

from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)


class MitigationPlan(BaseModel):
    """Structured mitigation plan produced by the mitigation agent."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    suggested_actions: list[str] = Field(
        ...,
        min_length=1,
        description="Ordered remediation steps recommended for execution.",
    )
    risk_level: str = Field(
        ...,
        min_length=1,
        description="Estimated operational risk of applying the plan.",
    )
    blast_radius_assessment: str = Field(
        ...,
        min_length=1,
        description="Assessment of downstream impact if actions are applied.",
    )
    fallback_commands: list[str] = Field(
        default_factory=list,
        description="Safe rollback or verification commands if mitigation fails.",
    )

    @field_validator("suggested_actions", "fallback_commands")
    @classmethod
    def validate_non_empty_strings(cls, value: list[str]) -> list[str]:
        """Ensure list entries are non-empty after stripping whitespace."""
        cleaned = [entry.strip() for entry in value if entry.strip()]
        if value and not cleaned:
            msg = "list must contain at least one non-empty string"
            raise ValueError(msg)
        if len(cleaned) != len(value):
            logger.warning("Removed empty entries while validating mitigation list fields.")
        return cleaned
