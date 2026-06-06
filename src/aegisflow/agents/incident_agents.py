"""PydanticAI agents for incident triage and mitigation planning."""

from __future__ import annotations

import logging
from typing import Any

from pydantic_ai import Agent, RunContext, format_as_xml

from aegisflow.agents.deps import SystemEnvironment
from aegisflow.models import AlertPayload, IncidentDiagnosis, MitigationPlan

logger = logging.getLogger(__name__)

TRIAGE_SYSTEM_PROMPT = """\
You are AegisFlow's incident triage specialist.

Rules:
- Never guess, speculate, or invent root causes.
- Base every conclusion strictly on structured tool telemetry and the provided alert payload.
- If telemetry is insufficient, state that explicitly and lower confidence_score accordingly.
- Extract log_fingerprints only from log lines returned by tools; do not fabricate signatures.
- Return a single structured IncidentDiagnosis object as your final answer.
"""

MITIGATION_SYSTEM_PROMPT = """\
You are AegisFlow's incident mitigation planner.

Rules:
- Never guess remediation steps or risk without consulting infrastructure telemetry tools.
- suggested_actions must be concrete, ordered, and justified by observed signals.
- risk_level and blast_radius_assessment must reflect infrastructure state from tools.
- fallback_commands must be safe rollback or verification commands, not destructive guesses.
- If telemetry is incomplete, recommend observability-first steps before invasive changes.
- Return a single structured MitigationPlan object as your final answer.
"""

triage_agent: Agent[SystemEnvironment, IncidentDiagnosis] = Agent(
    "anthropic:claude-3-5-sonnet",
    deps_type=SystemEnvironment,
    output_type=IncidentDiagnosis,
    system_prompt=TRIAGE_SYSTEM_PROMPT,
    defer_model_check=True,
)

mitigation_agent: Agent[SystemEnvironment, MitigationPlan] = Agent(
    "anthropic:claude-3-5-sonnet",
    deps_type=SystemEnvironment,
    output_type=MitigationPlan,
    system_prompt=MITIGATION_SYSTEM_PROMPT,
    defer_model_check=True,
)


def build_triage_prompt(alert: AlertPayload) -> str:
    """Serialize an alert payload into a triage user prompt."""
    return format_as_xml(
        {
            "task": "Perform incident triage for the alert below.",
            "alert": {
                "source": alert.source.value,
                "timestamp": alert.timestamp.isoformat(),
                "affected_service": alert.affected_service,
                "raw_payload": alert.raw_payload,
            },
            "instructions": (
                "Call fetch_service_logs for the affected service before producing a diagnosis."
            ),
        },
        root_tag="triage_request",
    )


def build_mitigation_prompt(
    alert: AlertPayload,
    diagnosis: IncidentDiagnosis,
) -> str:
    """Serialize alert and diagnosis context into a mitigation user prompt."""
    return format_as_xml(
        {
            "task": "Produce a mitigation plan for the diagnosed incident.",
            "alert": {
                "source": alert.source.value,
                "timestamp": alert.timestamp.isoformat(),
                "affected_service": alert.affected_service,
                "raw_payload": alert.raw_payload,
            },
            "diagnosis": diagnosis.model_dump(),
            "instructions": (
                "Call inspect_infrastructure_state for the affected service "
                "before proposing actions."
            ),
        },
        root_tag="mitigation_request",
    )


@triage_agent.tool
async def fetch_service_logs(
    ctx: RunContext[SystemEnvironment],
    service_name: str,
) -> list[str]:
    """Retrieve recent log lines for an affected service from infrastructure telemetry."""
    logger.info("Triage agent fetching logs for service %r.", service_name)
    return ctx.deps.fetch_recent_logs(service_name)


@mitigation_agent.tool
async def inspect_infrastructure_state(
    ctx: RunContext[SystemEnvironment],
    service_name: str,
) -> dict[str, Any]:
    """Retrieve live infrastructure topology and health metrics for a service."""
    logger.info("Mitigation agent inspecting infrastructure for service %r.", service_name)
    return ctx.deps.get_infrastructure_state(service_name)
