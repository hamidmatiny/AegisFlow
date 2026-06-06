"""Integration tests for saga compensation and telemetry helpers."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
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
from aegisflow.telemetry import activity_span, configure_telemetry, reset_telemetry_for_tests
from aegisflow.workflows.constants import TASK_QUEUE
from aegisflow.workflows.incident_workflow import IncidentOrchestrationWorkflow
from aegisflow.workflows.workflow_models import (
    IncidentWorkflowResult,
    IncidentWorkflowStatus,
)


class _SagaStubState:
    triage: IncidentDiagnosis | None = None
    mitigation: MitigationPlan | None = None
    health_check_result: bool = True
    rollback_called: bool = False


@activity.defn(name="run_triage_activity")
async def saga_stub_run_triage_activity(payload: AlertPayload) -> IncidentDiagnosis:
    del payload
    assert _SagaStubState.triage is not None
    return _SagaStubState.triage


@activity.defn(name="run_mitigation_activity")
async def saga_stub_run_mitigation_activity(
    diagnosis: IncidentDiagnosis,
    alert: AlertPayload,
) -> MitigationPlan:
    del diagnosis, alert
    assert _SagaStubState.mitigation is not None
    return _SagaStubState.mitigation


@activity.defn(name="apply_mitigation_action_activity")
async def saga_stub_apply_mitigation_action_activity(plan: MitigationPlan) -> bool:
    del plan
    return True


@activity.defn(name="verify_service_health_activity")
async def saga_stub_verify_service_health_activity(service_name: str) -> bool:
    del service_name
    return _SagaStubState.health_check_result


@activity.defn(name="rollback_mitigation_action_activity")
async def saga_stub_rollback_mitigation_action_activity(plan: MitigationPlan) -> bool:
    del plan
    _SagaStubState.rollback_called = True
    return True


@pytest.fixture
def payments_alert() -> AlertPayload:
    return AlertPayload(
        source=AlertSource.DATADOG,
        timestamp=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
        raw_payload={"title": "High memory utilization"},
        affected_service="payments-api",
    )


@pytest.fixture
def triage_diagnosis() -> IncidentDiagnosis:
    return IncidentDiagnosis(
        error_classification="resource_exhaustion",
        confidence_score=0.9,
        root_cause_analysis="Heap exhaustion detected in payments-api.",
        log_fingerprints=["OutOfMemoryError"],
    )


@pytest.fixture
def mitigation_plan() -> MitigationPlan:
    return MitigationPlan(
        suggested_actions=["Scale payments-api replicas to 6."],
        risk_level="medium",
        blast_radius_assessment="Limited to payments-api.",
        fallback_commands=["kubectl rollout undo deployment/payments-api -n production"],
    )


@pytest.fixture(autouse=True)
def reset_saga_stub_state(
    triage_diagnosis: IncidentDiagnosis,
    mitigation_plan: MitigationPlan,
) -> None:
    _SagaStubState.triage = triage_diagnosis
    _SagaStubState.mitigation = mitigation_plan
    _SagaStubState.health_check_result = True
    _SagaStubState.rollback_called = False


def _create_saga_worker(environment: WorkflowEnvironment) -> Worker:
    return Worker(
        environment.client,
        task_queue=TASK_QUEUE,
        workflows=[IncidentOrchestrationWorkflow],
        activities=[
            saga_stub_run_triage_activity,
            saga_stub_run_mitigation_activity,
            saga_stub_apply_mitigation_action_activity,
            saga_stub_verify_service_health_activity,
            saga_stub_rollback_mitigation_action_activity,
        ],
    )


class TestSagaCompensation:
    async def test_health_check_failure_triggers_rollback_compensation(
        self,
        payments_alert: AlertPayload,
    ) -> None:
        _SagaStubState.health_check_result = False

        async with await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter,
        ) as environment:
            worker = _create_saga_worker(environment)
            async with worker:
                handle = await environment.client.start_workflow(
                    IncidentOrchestrationWorkflow.run,
                    payments_alert,
                    id="incident-saga-rollback",
                    task_queue=TASK_QUEUE,
                )
                await handle.signal(IncidentOrchestrationWorkflow.approve_mitigation)
                result = await handle.result()

        assert isinstance(result, IncidentWorkflowResult)
        assert result.status is IncidentWorkflowStatus.FAILED_AND_ROLLED_BACK
        assert result.incident_resolved is False
        assert result.compensation_executed is True
        assert _SagaStubState.rollback_called is True
        assert "rollback" in result.message.lower()


class TestTelemetry:
    def test_configure_telemetry_records_activity_span(self) -> None:
        reset_telemetry_for_tests()
        exporter = InMemorySpanExporter()
        provider = configure_telemetry(service_name="aegisflow-test")
        provider.add_span_processor(SimpleSpanProcessor(exporter))

        with activity_span(
            "verify_service_health_activity",
            attributes={"aegisflow.service.name": "payments-api"},
        ):
            pass

        spans = exporter.get_finished_spans()
        assert spans
        assert spans[-1].name == "activity.verify_service_health_activity"
        assert spans[-1].attributes is not None
        assert spans[-1].attributes["aegisflow.activity.name"] == "verify_service_health_activity"
        assert spans[-1].attributes["aegisflow.service.name"] == "payments-api"
        assert "aegisflow.latency_ms" in spans[-1].attributes

        reset_telemetry_for_tests()
