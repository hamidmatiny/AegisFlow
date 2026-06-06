"""Shared domain enumerations."""

from enum import StrEnum


class IncidentSeverity(StrEnum):
    """Severity classification for incidents and alerts."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
