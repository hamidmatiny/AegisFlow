"""Temporal activities for AegisFlow incident orchestration."""

from __future__ import annotations

import asyncio
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
from aegisflow.simulation.control import should_force_health_check_failure
from aegisflow.telemetry import activity_span, run_traced_agent

logger = logging.getLogger(__name__)

POST_MITIGATION_HEALTH_DELAY_SECONDS = 5


@activity.defn(name="run_triage_activity")
async def run_triage_activity(payload: AlertPayload) -> IncidentDiagnosis:
    """Execute the triage agent against live dependency telemetry."""
    with activity_span(
        "run_triage_activity",
        attributes={"aegisflow.service.name": payload.affected_service},
    ):
        environment = SystemEnvironment.default_for_service(payload.affected_service)
        try:
            agent_result = await run_traced_agent(
                "incident.triage",
                agent_name="triage_agent",
                model_name=str(triage_agent.model),
                runner=lambda: triage_agent.run(
                    build_triage_prompt(payload),
                    deps=environment,
                ),
                usage_provider=lambda result: result.usage,
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
            agent_result.output.error_classification,
        )
        return agent_result.output


@activity.defn(name="run_mitigation_activity")
async def run_mitigation_activity(
    diagnosis: IncidentDiagnosis,
    alert: AlertPayload,
) -> MitigationPlan:
    """Execute the mitigation agent using triage output and alert context."""
    with activity_span(
        "run_mitigation_activity",
        attributes={"aegisflow.service.name": alert.affected_service},
    ):
        environment = SystemEnvironment.default_for_service(alert.affected_service)
        try:
            agent_result = await run_traced_agent(
                "incident.mitigation",
                agent_name="mitigation_agent",
                model_name=str(mitigation_agent.model),
                runner=lambda: mitigation_agent.run(
                    build_mitigation_prompt(alert, diagnosis),
                    deps=environment,
                ),
                usage_provider=lambda result: result.usage,
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
            agent_result.output.risk_level,
        )
        return agent_result.output


@activity.defn(name="apply_mitigation_action_activity")
async def apply_mitigation_action_activity(plan: MitigationPlan) -> bool:
    """Simulate applying mitigation and fallback commands against infrastructure."""
    with activity_span("apply_mitigation_action_activity"):
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


@activity.defn(name="verify_service_health_activity")
async def verify_service_health_activity(service_name: str) -> bool:
    """Simulate a post-mitigation health check after a stabilization window."""
    with activity_span(
        "verify_service_health_activity",
        attributes={"aegisflow.service.name": service_name},
    ):
        normalized = service_name.strip()
        if not normalized:
            logger.error("verify_service_health_activity received empty service_name.")
            return False

        logger.info(
            "Waiting %s seconds before evaluating health for service %r.",
            POST_MITIGATION_HEALTH_DELAY_SECONDS,
            normalized,
        )
        await asyncio.sleep(POST_MITIGATION_HEALTH_DELAY_SECONDS)

        if should_force_health_check_failure(normalized):
            logger.warning(
                "Simulation control forcing post-mitigation health failure for service %r.",
                normalized,
            )
            return False

        environment = SystemEnvironment.default_for_service(normalized)
        state = environment.get_infrastructure_state(normalized)
        if state.get("status") == "unknown":
            logger.warning("Service %r remains unknown after mitigation window.", normalized)
            return False

        logger.info(
            "Post-mitigation health check passed for service %r after stabilization window.",
            normalized,
        )
        return True


@activity.defn(name="rollback_mitigation_action_activity")
async def rollback_mitigation_action_activity(plan: MitigationPlan) -> bool:
    """Execute fallback commands to compensate for a failed mitigation."""
    with activity_span("rollback_mitigation_action_activity"):
        if not plan.fallback_commands:
            logger.warning("Rollback requested but mitigation plan has no fallback commands.")
            return False

        try:
            for index, command in enumerate(plan.fallback_commands, start=1):
                logger.info("Simulating rollback command %d: %s", index, command)
        except Exception:
            logger.exception("Failed while executing rollback fallback commands.")
            return False

        logger.info("Rollback fallback commands simulated successfully.")
        return True
