"""Inbound alert payload schema from observability sources."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)


class AlertSource(StrEnum):
    """Supported observability alert sources."""

    DATADOG = "Datadog"
    PROMETHEUS = "Prometheus"


class AlertPayload(BaseModel):
    """Normalized alert payload ingested from external monitoring systems."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    source: AlertSource
    timestamp: datetime
    raw_payload: dict[str, Any] = Field(
        ...,
        description="Unmodified alert payload from the upstream source.",
    )
    affected_service: str = Field(
        ...,
        min_length=1,
        description="Primary service impacted by the alert.",
    )

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp_to_utc(cls, value: datetime) -> datetime:
        """Ensure timestamps are timezone-aware and normalized to UTC."""
        if value.tzinfo is None:
            logger.warning("Alert timestamp is naive; assuming UTC.")
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @field_validator("affected_service")
    @classmethod
    def validate_affected_service(cls, value: str) -> str:
        """Reject blank service identifiers after whitespace normalization."""
        if not value:
            msg = "affected_service must not be empty"
            raise ValueError(msg)
        return value
