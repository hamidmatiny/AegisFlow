"""Workflow-specific Pydantic models kept free of agent dependencies."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from aegisflow.models import IncidentDiagnosis, MitigationPlan


class IncidentWorkflowStatus(StrEnum):
    """Terminal status for the incident orchestration workflow."""

    RESOLVED = "resolved"
    ESCALATED = "escalated"
    FAILED = "failed"


class IncidentWorkflowResult(BaseModel):
    """Structured workflow result returned to callers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    human_approved: bool
    incident_resolved: bool
    status: IncidentWorkflowStatus
    diagnosis: IncidentDiagnosis | None = None
    mitigation_plan: MitigationPlan | None = None
    message: str = Field(min_length=1)
