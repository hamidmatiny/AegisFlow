"""Background Temporal worker for AegisFlow incident orchestration."""

from __future__ import annotations

import asyncio
import logging
import os

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from aegisflow.telemetry import build_temporal_otel_interceptor, configure_telemetry
from aegisflow.workflows.activities import (
    apply_mitigation_action_activity,
    rollback_mitigation_action_activity,
    run_mitigation_activity,
    run_triage_activity,
    verify_service_health_activity,
)
from aegisflow.workflows.constants import TASK_QUEUE
from aegisflow.workflows.incident_workflow import IncidentOrchestrationWorkflow

logger = logging.getLogger(__name__)

DEFAULT_TEMPORAL_ADDRESS = "localhost:7233"


async def run_worker(*, temporal_address: str = DEFAULT_TEMPORAL_ADDRESS) -> None:
    """Connect to Temporal and run the AegisFlow worker until interrupted."""
    logging.basicConfig(level=logging.INFO)
    configure_telemetry()

    client = await Client.connect(
        temporal_address,
        data_converter=pydantic_data_converter,
    )
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[IncidentOrchestrationWorkflow],
        activities=[
            run_triage_activity,
            run_mitigation_activity,
            apply_mitigation_action_activity,
            verify_service_health_activity,
            rollback_mitigation_action_activity,
        ],
        interceptors=[build_temporal_otel_interceptor()],
    )

    logger.info(
        "AegisFlow worker listening on task queue %r at %s.",
        TASK_QUEUE,
        temporal_address,
    )
    await worker.run()


def main() -> None:
    """Entry point for `python -m aegisflow.worker`."""
    temporal_address = os.getenv("TEMPORAL_ADDRESS", DEFAULT_TEMPORAL_ADDRESS)
    asyncio.run(run_worker(temporal_address=temporal_address))


if __name__ == "__main__":
    main()
