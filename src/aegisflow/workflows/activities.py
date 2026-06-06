"""Temporal activities for AegisFlow incident orchestration."""

from __future__ import annotations

import logging

from temporalio import activity

from aegisflow.agents.deps import SystemEnvironment
from aegisflow.agents.incident_agents import (
    build_mitigation_prompt,
    build_triage_prompt,
    mitigation_agent,
    triage_agent,
)
from aegisflow.models import AlertPayload, IncidentDiagnosis, MitigationPlan

logger = logging.getLogger(__name__)


@activity.defn(name="run_triage_activity")
async def run_triage_activity(payload: AlertPayload) -> IncidentDiagnosis:
    """Execute the triage agent against live dependency telemetry."""
    environment = SystemEnvironment.default_for_service(payload.affected_service)
    try:
        result = await triage_agent.run(
            build_triage_prompt(payload),
            deps=environment,
        )
    except Exception:
        logger.exception(
            "Triage activity failed for service %r.",
            payload.affected_service,
        )
        raise

    logger.info(
        "Triage completed for service %r with classification %r.",
        payload.affected_service,
        result.output.error_classification,
    )
    return result.output


@activity.defn(name="run_mitigation_activity")
async def run_mitigation_activity(
    diagnosis: IncidentDiagnosis,
    alert: AlertPayload,
) -> MitigationPlan:
    """Execute the mitigation agent using triage output and alert context."""
    environment = SystemEnvironment.default_for_service(alert.affected_service)
    try:
        result = await mitigation_agent.run(
            build_mitigation_prompt(alert, diagnosis),
            deps=environment,
        )
    except Exception:
        logger.exception(
            "Mitigation activity failed for service %r.",
            alert.affected_service,
        )
        raise

    logger.info(
        "Mitigation plan generated for service %r with risk level %r.",
        alert.affected_service,
        result.output.risk_level,
    )
    return result.output


@activity.defn(name="apply_mitigation_action_activity")
async def apply_mitigation_action_activity(plan: MitigationPlan) -> bool:
    """Simulate applying mitigation and fallback commands against infrastructure."""
    if not plan.suggested_actions:
        logger.error("Mitigation plan contains no suggested actions; aborting apply step.")
        return False

    try:
        for index, action in enumerate(plan.suggested_actions, start=1):
            logger.info("Simulating mitigation action %d: %s", index, action)

        for command in plan.fallback_commands:
            logger.info("Simulating fallback command: %s", command)
    except Exception:
        logger.exception("Failed while simulating mitigation actions.")
        return False

    logger.info("Mitigation actions simulated successfully.")
    return True
