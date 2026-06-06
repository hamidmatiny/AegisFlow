"""Live end-to-end simulation runner for AegisFlow."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from datetime import UTC, datetime

from temporalio.client import Client, WorkflowFailureError
from temporalio.contrib.pydantic import pydantic_data_converter

from aegisflow.models import AlertPayload, AlertSource
from aegisflow.simulation.control import SimulationScenario, configure_scenario
from aegisflow.workflows.constants import TASK_QUEUE
from aegisflow.workflows.incident_workflow import IncidentOrchestrationWorkflow
from aegisflow.workflows.workflow_models import IncidentWorkflowResult, IncidentWorkflowStatus

logger = logging.getLogger(__name__)

DEFAULT_TEMPORAL_ADDRESS = "localhost:7233"
DEFAULT_TEMPORAL_UI_BASE = "http://localhost:8080"
DEFAULT_AGENT_WAIT_SECONDS = 5.0
DEFAULT_RESULT_TIMEOUT_SECONDS = 600.0
TARGET_SERVICE = "payments-api"

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[36m"
BOLD = "\033[1m"
RESET = "\033[0m"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a live AegisFlow incident simulation against Temporal.",
    )
    parser.add_argument(
        "--scenario",
        choices=[scenario.value for scenario in SimulationScenario],
        required=True,
        help="Simulation scenario to execute.",
    )
    parser.add_argument(
        "--temporal-address",
        default=DEFAULT_TEMPORAL_ADDRESS,
        help="Temporal gRPC endpoint.",
    )
    parser.add_argument(
        "--temporal-ui-base",
        default=DEFAULT_TEMPORAL_UI_BASE,
        help="Temporal Web UI base URL.",
    )
    parser.add_argument(
        "--agent-wait-seconds",
        type=float,
        default=DEFAULT_AGENT_WAIT_SECONDS,
        help="Delay before sending human approval signal.",
    )
    parser.add_argument(
        "--result-timeout-seconds",
        type=float,
        default=DEFAULT_RESULT_TIMEOUT_SECONDS,
        help="Maximum time to wait for workflow completion.",
    )
    return parser


def build_payments_alert() -> AlertPayload:
    return AlertPayload(
        source=AlertSource.DATADOG,
        timestamp=datetime.now(tz=UTC),
        raw_payload={
            "title": "High memory utilization",
            "monitor_id": "mem-sim-001",
            "tags": [f"service:{TARGET_SERVICE}", "env:production", "simulation:true"],
            "message": "Heap usage exceeded 95% for more than 5 minutes.",
        },
        affected_service=TARGET_SERVICE,
    )


def build_workflow_id(scenario: SimulationScenario) -> str:
    return f"aegisflow-sim-{scenario.value}-{uuid.uuid4().hex[:10]}"


def build_temporal_ui_url(base_url: str, workflow_id: str) -> str:
    namespace = "default"
    return (
        f"{base_url.rstrip('/')}/namespaces/{namespace}/workflows/"
        f"{workflow_id}/history"
    )


def _status_color(status: IncidentWorkflowStatus) -> str:
    if status is IncidentWorkflowStatus.RESOLVED:
        return GREEN
    if status in {IncidentWorkflowStatus.FAILED, IncidentWorkflowStatus.FAILED_AND_ROLLED_BACK}:
        return RED
    if status is IncidentWorkflowStatus.ESCALATED:
        return YELLOW
    return CYAN


def render_result_summary(
    *,
    scenario: SimulationScenario,
    workflow_id: str,
    ui_url: str,
    result: IncidentWorkflowResult,
) -> str:
    status_color = _status_color(result.status)
    compensation_text = (
        f"{GREEN}Yes{RESET}" if result.compensation_executed else f"{YELLOW}No{RESET}"
    )
    resolved_text = (
        f"{GREEN}Yes{RESET}" if result.incident_resolved else f"{RED}No{RESET}"
    )
    approved_text = (
        f"{GREEN}Yes{RESET}" if result.human_approved else f"{YELLOW}No{RESET}"
    )

    diagnosis_line = (
        f"- **Diagnosis:** `{result.diagnosis.error_classification}` "
        f"(confidence `{result.diagnosis.confidence_score:.2f}`)"
        if result.diagnosis is not None
        else "- **Diagnosis:** _unavailable_"
    )
    mitigation_line = (
        f"- **Mitigation actions:** `{len(result.mitigation_plan.suggested_actions)}` planned"
        if result.mitigation_plan is not None
        else "- **Mitigation plan:** _unavailable_"
    )

    return f"""
{BOLD}{'=' * 72}{RESET}
{BOLD}AegisFlow Live Simulation Summary{RESET}
{BOLD}{'=' * 72}{RESET}

## Scenario
- **Mode:** `{scenario.value}`
- **Target service:** `{TARGET_SERVICE}`
- **Workflow ID:** `{workflow_id}`
- **Temporal UI:** {ui_url}

## Final Outcome
- **Status:** {status_color}`{result.status.value}`{RESET}
- **Human approved:** {approved_text}
- **Incident resolved:** {resolved_text}
- **Compensation executed:** {compensation_text}
- **Message:** {result.message}

## Agent Outputs
{diagnosis_line}
{mitigation_line}

## OpenTelemetry
Console trace spans are emitted by the background worker process. Look for blocks prefixed
with span names such as `activity.run_triage_activity`, `pydantic_ai.incident.triage`,
`activity.verify_service_health_activity`, and `activity.rollback_mitigation_action_activity`.

{BOLD}{'=' * 72}{RESET}
""".strip()


async def connect_client(temporal_address: str) -> Client:
    try:
        return await Client.connect(
            temporal_address,
            data_converter=pydantic_data_converter,
        )
    except Exception as error:
        msg = (
            f"Failed to connect to Temporal at {temporal_address!r}. "
            "Ensure `docker compose up -d` is running."
        )
        raise RuntimeError(msg) from error


async def run_simulation(args: argparse.Namespace) -> IncidentWorkflowResult:
    scenario = SimulationScenario(args.scenario)
    alert = build_payments_alert()
    workflow_id = build_workflow_id(scenario)
    ui_url = build_temporal_ui_url(args.temporal_ui_base, workflow_id)

    print(f"{CYAN}Step 1:{RESET} Connecting to Temporal at {args.temporal_address!r}...")
    client = await connect_client(args.temporal_address)

    print(f"{CYAN}Step 1:{RESET} Starting `IncidentOrchestrationWorkflow`...")
    handle = await client.start_workflow(
        IncidentOrchestrationWorkflow.run,
        alert,
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    print(f"{GREEN}Workflow started.{RESET}")
    print(f"- Workflow ID: `{workflow_id}`")
    print(f"- Temporal UI: {ui_url}")

    print(
        f"\n{CYAN}Step 2:{RESET} Waiting {args.agent_wait_seconds:.1f}s for "
        "triage and mitigation agents to complete...",
    )
    await asyncio.sleep(args.agent_wait_seconds)

    print(f"\n{CYAN}Step 3:{RESET} Configuring `{scenario.value}` scenario controls...")
    state_path = configure_scenario(scenario, service_name=TARGET_SERVICE)
    print(f"- Simulation control file: `{state_path}`")
    if scenario is SimulationScenario.CHAOS:
        print(
            f"{YELLOW}Chaos mode enabled.{RESET} Health check will fail and trigger saga rollback.",
        )
    else:
        print(f"{GREEN}Success mode enabled.{RESET} Health check will report a stable service.")

    print(f"\n{CYAN}Step 2:{RESET} Sending `approve_mitigation` signal...")
    await handle.signal(IncidentOrchestrationWorkflow.approve_mitigation)
    print(f"{GREEN}Approval signal delivered.{RESET}")

    print(f"\n{CYAN}Step 4:{RESET} Awaiting final workflow result...")
    try:
        result = await asyncio.wait_for(
            handle.result(),
            timeout=args.result_timeout_seconds,
        )
    except TimeoutError as error:
        msg = (
            f"Workflow `{workflow_id}` did not complete within "
            f"{args.result_timeout_seconds:.0f}s."
        )
        raise RuntimeError(msg) from error
    except WorkflowFailureError as error:
        msg = f"Workflow `{workflow_id}` failed: {error}"
        raise RuntimeError(msg) from error

    if not isinstance(result, IncidentWorkflowResult):
        msg = f"Unexpected workflow result type: {type(result)!r}"
        raise TypeError(msg)

    print(render_result_summary(
        scenario=scenario,
        workflow_id=workflow_id,
        ui_url=ui_url,
        result=result,
    ))
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = build_parser()
    args = parser.parse_args()

    try:
        asyncio.run(run_simulation(args))
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Simulation interrupted by user.{RESET}")
        sys.exit(130)
    except Exception as error:
        logger.exception("Simulation failed.")
        print(f"{RED}Simulation failed:{RESET} {error}")
        sys.exit(1)


if __name__ == "__main__":
    main()
