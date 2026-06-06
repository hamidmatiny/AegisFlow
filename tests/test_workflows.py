"""Integration tests for AegisFlow Temporal incident orchestration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from aegisflow.models import (
    AlertPayload,
    AlertSource,
    IncidentDiagnosis,
    MitigationPlan,
)
from aegisflow.workflows.constants import HUMAN_APPROVAL_TIMEOUT, TASK_QUEUE
from aegisflow.workflows.incident_workflow import IncidentOrchestrationWorkflow
from aegisflow.workflows.workflow_models import (
    IncidentWorkflowResult,
    IncidentWorkflowStatus,
)


class _StubResponses:
    triage: IncidentDiagnosis | None = None
    mitigation: MitigationPlan | None = None
    apply_result: bool = True


@activity.defn(name="run_triage_activity")
async def stub_run_triage_activity(payload: AlertPayload) -> IncidentDiagnosis:
    assert _StubResponses.triage is not None
    return _StubResponses.triage


@activity.defn(name="run_mitigation_activity")
async def stub_run_mitigation_activity(
    diagnosis: IncidentDiagnosis,
    alert: AlertPayload,
) -> MitigationPlan:
    del diagnosis, alert
    assert _StubResponses.mitigation is not None
    return _StubResponses.mitigation


@activity.defn(name="apply_mitigation_action_activity")
async def stub_apply_mitigation_action_activity(plan: MitigationPlan) -> bool:
    del plan
    return _StubResponses.apply_result


@pytest.fixture
def payments_alert() -> AlertPayload:
    return AlertPayload(
        source=AlertSource.DATADOG,
        timestamp=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
        raw_payload={
            "title": "High memory utilization",
            "monitor_id": "mem-991",
            "tags": ["service:payments-api", "env:production"],
        },
        affected_service="payments-api",
    )


@pytest.fixture
def triage_diagnosis() -> IncidentDiagnosis:
    return IncidentDiagnosis(
        error_classification="resource_exhaustion",
        confidence_score=0.92,
        root_cause_analysis="Heap exhaustion confirmed by OOMKilled log fingerprints.",
        log_fingerprints=["java.lang.OutOfMemoryError", "heap usage at 98%"],
    )


@pytest.fixture
def mitigation_plan() -> MitigationPlan:
    return MitigationPlan(
        suggested_actions=[
            "Scale payments-api deployment from 4 to 6 replicas.",
            "Restart unhealthy pods after traffic drain completes.",
        ],
        risk_level="medium",
        blast_radius_assessment="Impact limited to payments-api namespace.",
        fallback_commands=["kubectl rollout undo deployment/payments-api -n production"],
    )


@pytest.fixture(autouse=True)
def configure_stub_responses(
    triage_diagnosis: IncidentDiagnosis,
    mitigation_plan: MitigationPlan,
) -> None:
    _StubResponses.triage = triage_diagnosis
    _StubResponses.mitigation = mitigation_plan
    _StubResponses.apply_result = True


def _create_worker(environment: WorkflowEnvironment) -> Worker:
    return Worker(
        environment.client,
        task_queue=TASK_QUEUE,
        workflows=[IncidentOrchestrationWorkflow],
        activities=[
            stub_run_triage_activity,
            stub_run_mitigation_activity,
            stub_apply_mitigation_action_activity,
        ],
    )


class TestIncidentOrchestrationWorkflow:
    async def test_happy_path_with_human_approval(
        self,
        payments_alert: AlertPayload,
        triage_diagnosis: IncidentDiagnosis,
        mitigation_plan: MitigationPlan,
    ) -> None:
        async with await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter,
        ) as environment:
            worker = _create_worker(environment)
            async with worker:
                handle = await environment.client.start_workflow(
                    IncidentOrchestrationWorkflow.run,
                    payments_alert,
                    id="incident-happy-path",
                    task_queue=TASK_QUEUE,
                )
                await handle.signal(IncidentOrchestrationWorkflow.approve_mitigation)
                result = await handle.result()

        assert isinstance(result, IncidentWorkflowResult)
        assert result.human_approved is True
        assert result.incident_resolved is True
        assert result.status is IncidentWorkflowStatus.RESOLVED
        assert result.diagnosis == triage_diagnosis
        assert result.mitigation_plan == mitigation_plan

    async def test_escalation_when_approval_times_out(
        self,
        payments_alert: AlertPayload,
        triage_diagnosis: IncidentDiagnosis,
        mitigation_plan: MitigationPlan,
    ) -> None:
        async with await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter,
        ) as environment:
            worker = _create_worker(environment)
            async with worker:
                handle = await environment.client.start_workflow(
                    IncidentOrchestrationWorkflow.run,
                    payments_alert,
                    id="incident-escalation-path",
                    task_queue=TASK_QUEUE,
                )
                await environment.sleep(HUMAN_APPROVAL_TIMEOUT + timedelta(minutes=1))
                result = await handle.result()

        assert isinstance(result, IncidentWorkflowResult)
        assert result.human_approved is False
        assert result.incident_resolved is False
        assert result.status is IncidentWorkflowStatus.ESCALATED
        assert result.diagnosis == triage_diagnosis
        assert result.mitigation_plan == mitigation_plan
        assert "escalated" in result.message.lower()

    async def test_approval_signal_flips_workflow_state(
        self,
        payments_alert: AlertPayload,
    ) -> None:
        async with await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter,
        ) as environment:
            worker = _create_worker(environment)
            async with worker:
                handle = await environment.client.start_workflow(
                    IncidentOrchestrationWorkflow.run,
                    payments_alert,
                    id="incident-signal-state",
                    task_queue=TASK_QUEUE,
                )
                await handle.signal(IncidentOrchestrationWorkflow.approve_mitigation)
                result = await handle.result()

        assert result.human_approved is True
