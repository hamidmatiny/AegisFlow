"""Runtime dependency container for PydanticAI incident agents."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_LOGS: dict[str, list[str]] = {
    "payments-api": [
        "2026-06-05T12:01:04Z ERROR heap usage at 98% threshold exceeded",
        "2026-06-05T12:01:06Z ERROR java.lang.OutOfMemoryError: Java heap space",
        "2026-06-05T12:01:07Z WARN  pod payments-api-7f9c8 restarted by liveness probe",
    ],
    "auth-gateway": [
        "2026-06-05T11:58:12Z ERROR upstream connect error: connection timeout",
        "2026-06-05T11:58:13Z ERROR 503 Service Unavailable on /oauth/token",
    ],
}

_DEFAULT_INFRASTRUCTURE: dict[str, dict[str, Any]] = {
    "payments-api": {
        "service_name": "payments-api",
        "namespace": "production",
        "replicas_desired": 4,
        "replicas_ready": 2,
        "cpu_utilization_percent": 91.4,
        "memory_utilization_percent": 97.8,
        "recent_deployments": ["payments-api:v2.14.3 (2026-06-05T10:45:00Z)"],
        "dependencies": ["postgres-primary", "redis-cache", "fraud-scoring"],
    },
    "auth-gateway": {
        "service_name": "auth-gateway",
        "namespace": "production",
        "replicas_desired": 3,
        "replicas_ready": 3,
        "cpu_utilization_percent": 42.1,
        "memory_utilization_percent": 55.0,
        "recent_deployments": ["auth-gateway:v1.9.0 (2026-06-04T18:20:00Z)"],
        "dependencies": ["identity-provider", "redis-cache"],
    },
}


@dataclass(slots=True)
class SystemEnvironment:
    """Dependency framework exposing mock infrastructure telemetry to agents."""

    log_store: dict[str, list[str]] = field(default_factory=dict)
    infrastructure_store: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def default_for_service(cls, service_name: str) -> SystemEnvironment:
        """Build an environment preloaded with canonical mock telemetry."""
        environment = cls(
            log_store=dict(_DEFAULT_LOGS),
            infrastructure_store=dict(_DEFAULT_INFRASTRUCTURE),
        )
        if service_name not in environment.log_store:
            environment.log_store[service_name] = [
                f"2026-06-05T12:00:00Z INFO no historical incidents recorded for {service_name}",
            ]
        if service_name not in environment.infrastructure_store:
            environment.infrastructure_store[service_name] = {
                "service_name": service_name,
                "namespace": "production",
                "replicas_desired": 1,
                "replicas_ready": 1,
                "cpu_utilization_percent": 0.0,
                "memory_utilization_percent": 0.0,
                "recent_deployments": [],
                "dependencies": [],
            }
        return environment

    def fetch_recent_logs(self, service_name: str) -> list[str]:
        """Return recent log lines for the requested service."""
        normalized = service_name.strip()
        if not normalized:
            logger.error("fetch_recent_logs called with empty service_name.")
            return []

        try:
            logs = self.log_store.get(normalized)
            if logs is None:
                logger.warning(
                    "No log telemetry found for service %r; returning empty list.",
                    normalized,
                )
                return []
            return list(logs)
        except (TypeError, ValueError):
            logger.exception("Failed to fetch logs for service %r.", normalized)
            return []

    def get_infrastructure_state(self, service_name: str) -> dict[str, Any]:
        """Return infrastructure topology and health metrics for a service."""
        normalized = service_name.strip()
        if not normalized:
            logger.error("get_infrastructure_state called with empty service_name.")
            return {"service_name": "", "status": "unknown", "error": "empty service name"}

        try:
            state = self.infrastructure_store.get(normalized)
            if state is None:
                logger.warning(
                    "No infrastructure telemetry found for service %r; returning unknown state.",
                    normalized,
                )
                return {
                    "service_name": normalized,
                    "status": "unknown",
                    "replicas_desired": 0,
                    "replicas_ready": 0,
                }
            return dict(state)
        except (TypeError, ValueError):
            logger.exception("Failed to fetch infrastructure state for service %r.", normalized)
            return {
                "service_name": normalized,
                "status": "error",
                "error": "failed to read infrastructure telemetry",
            }
