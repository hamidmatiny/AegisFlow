"""Temporal workflow orchestrating incident triage, approval, and mitigation."""

from __future__ import annotations

from temporalio import workflow
from temporalio.common import VersioningBehavior

with workflow.unsafe.imports_passed_through():
    from aegisflow.models import AlertPayload, IncidentDiagnosis, MitigationPlan
    from aegisflow.workflows.constants import (
        ACTIVITY_START_TO_CLOSE_TIMEOUT,
        HEALTH_CHECK_TIMEOUT,
        HUMAN_APPROVAL_TIMEOUT,
    )
    from aegisflow.workflows.workflow_models import (
        CompensationStep,
        IncidentWorkflowResult,
        IncidentWorkflowStatus,
    )

RUN_TRIAGE_ACTIVITY = "run_triage_activity"
RUN_MITIGATION_ACTIVITY = "run_mitigation_activity"
APPLY_MITIGATION_ACTIVITY = "apply_mitigation_action_activity"
VERIFY_SERVICE_HEALTH_ACTIVITY = "verify_service_health_activity"
ROLLBACK_MITIGATION_ACTIVITY = "rollback_mitigation_action_activity"


@workflow.defn(
    name="IncidentOrchestrationWorkflow",
    versioning_behavior=VersioningBehavior.AUTO_UPGRADE,
    sandboxed=False,
)
class IncidentOrchestrationWorkflow:
    """Durable incident response state machine with human-in-the-loop approval."""

    def __init__(self) -> None:
        self.human_approved = False
        self.incident_resolved = False

    async def _execute_compensations(
        self,
        compensation_stack: list[CompensationStep],
    ) -> bool:
        """Execute saga compensation steps in reverse order."""
        compensation_success = True

        while compensation_stack:
            step = compensation_stack.pop()
            workflow.logger.warning(
                "Executing compensation activity %r.",
                step.activity_name,
            )
            try:
                if step.activity_name == ROLLBACK_MITIGATION_ACTIVITY:
                    if step.mitigation_plan is None:
                        workflow.logger.error(
                            "Rollback compensation missing mitigation plan payload.",
                        )
                        compensation_success = False
                        continue
                    rollback_success = await workflow.execute_activity(
                        step.activity_name,
                        step.mitigation_plan,
                        start_to_close_timeout=ACTIVITY_START_TO_CLOSE_TIMEOUT,
                        result_type=bool,
                    )
                    compensation_success = compensation_success and rollback_success
                else:
                    workflow.logger.error(
                        "Unknown compensation activity %r; skipping.",
                        step.activity_name,
                    )
                    compensation_success = False
            except Exception:
                workflow.logger.exception(
                    "Compensation activity %r failed during saga rollback.",
                    step.activity_name,
                )
                compensation_success = False

        return compensation_success

    @workflow.run
    async def run(self, payload: AlertPayload) -> IncidentWorkflowResult:
        """Orchestrate triage, mitigation planning, approval, and remediation."""
        diagnosis: IncidentDiagnosis | None = None
        mitigation_plan: MitigationPlan | None = None
        compensation_stack: list[CompensationStep] = []

        try:
            diagnosis = await workflow.execute_activity(
                RUN_TRIAGE_ACTIVITY,
                payload,
                start_to_close_timeout=ACTIVITY_START_TO_CLOSE_TIMEOUT,
                result_type=IncidentDiagnosis,
            )
            mitigation_plan = await workflow.execute_activity(
                RUN_MITIGATION_ACTIVITY,
                args=[diagnosis, payload],
                start_to_close_timeout=ACTIVITY_START_TO_CLOSE_TIMEOUT,
                result_type=MitigationPlan,
            )
        except Exception as error:
            workflow.logger.exception("Incident orchestration failed during agent activities.")
            return IncidentWorkflowResult(
                human_approved=self.human_approved,
                incident_resolved=False,
                status=IncidentWorkflowStatus.FAILED,
                diagnosis=diagnosis,
                mitigation_plan=mitigation_plan,
                message=f"Workflow failed during agent execution: {error}",
            )

        try:
            await workflow.wait_condition(
                lambda: self.human_approved,
                timeout=HUMAN_APPROVAL_TIMEOUT,
                timeout_summary="human_mitigation_approval",
            )
        except TimeoutError:
            workflow.logger.warning(
                "Human approval not received within %s minutes. "
                "Escalating incident for manual operator intervention.",
                int(HUMAN_APPROVAL_TIMEOUT.total_seconds() // 60),
            )
            return IncidentWorkflowResult(
                human_approved=False,
                incident_resolved=False,
                status=IncidentWorkflowStatus.ESCALATED,
                diagnosis=diagnosis,
                mitigation_plan=mitigation_plan,
                message=(
                    "Mitigation approval window expired; incident escalated to on-call operators."
                ),
            )

        if mitigation_plan is None:
            return IncidentWorkflowResult(
                human_approved=True,
                incident_resolved=False,
                status=IncidentWorkflowStatus.FAILED,
                diagnosis=diagnosis,
                mitigation_plan=None,
                message="Mitigation plan missing after approval; aborting remediation.",
            )

        try:
            mitigation_applied = await workflow.execute_activity(
                APPLY_MITIGATION_ACTIVITY,
                mitigation_plan,
                start_to_close_timeout=ACTIVITY_START_TO_CLOSE_TIMEOUT,
                result_type=bool,
            )
        except Exception as error:
            workflow.logger.exception("Mitigation apply activity failed.")
            return IncidentWorkflowResult(
                human_approved=True,
                incident_resolved=False,
                status=IncidentWorkflowStatus.FAILED,
                diagnosis=diagnosis,
                mitigation_plan=mitigation_plan,
                message=f"Approved mitigation failed during apply step: {error}",
            )

        if not mitigation_applied:
            return IncidentWorkflowResult(
                human_approved=True,
                incident_resolved=False,
                status=IncidentWorkflowStatus.FAILED,
                diagnosis=diagnosis,
                mitigation_plan=mitigation_plan,
                message="Mitigation plan approved but apply step returned unsuccessful status.",
            )

        compensation_stack.append(
            CompensationStep(
                activity_name=ROLLBACK_MITIGATION_ACTIVITY,
                mitigation_plan=mitigation_plan,
            ),
        )

        service_healthy = await workflow.execute_activity(
            VERIFY_SERVICE_HEALTH_ACTIVITY,
            payload.affected_service,
            start_to_close_timeout=HEALTH_CHECK_TIMEOUT,
            result_type=bool,
        )

        if not service_healthy:
            workflow.logger.error(
                "Post-mitigation health check failed for service %r; starting saga rollback.",
                payload.affected_service,
            )
            compensation_executed = await self._execute_compensations(compensation_stack)
            return IncidentWorkflowResult(
                human_approved=True,
                incident_resolved=False,
                status=IncidentWorkflowStatus.FAILED_AND_ROLLED_BACK,
                diagnosis=diagnosis,
                mitigation_plan=mitigation_plan,
                compensation_executed=compensation_executed,
                message=(
                    "Mitigation did not stabilize the service; compensation rollback executed."
                ),
            )

        self.incident_resolved = True
        return IncidentWorkflowResult(
            human_approved=True,
            incident_resolved=self.incident_resolved,
            status=IncidentWorkflowStatus.RESOLVED,
            diagnosis=diagnosis,
            mitigation_plan=mitigation_plan,
            message="Mitigation actions applied successfully and service health verified.",
        )

    @workflow.signal(name="approve_mitigation")
    def approve_mitigation(self) -> None:
        """Signal handler granting operator approval to execute mitigation."""
        self.human_approved = True
        workflow.logger.info("Mitigation plan approved by human operator.")
