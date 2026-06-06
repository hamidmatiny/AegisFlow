"""Temporal workflow orchestrating incident triage, approval, and mitigation."""

from __future__ import annotations

from temporalio import workflow
from temporalio.common import VersioningBehavior

with workflow.unsafe.imports_passed_through():
    from aegisflow.models import AlertPayload, IncidentDiagnosis, MitigationPlan
    from aegisflow.workflows.constants import (
        ACTIVITY_START_TO_CLOSE_TIMEOUT,
        HUMAN_APPROVAL_TIMEOUT,
    )
    from aegisflow.workflows.workflow_models import (
        IncidentWorkflowResult,
        IncidentWorkflowStatus,
    )

RUN_TRIAGE_ACTIVITY = "run_triage_activity"
RUN_MITIGATION_ACTIVITY = "run_mitigation_activity"
APPLY_MITIGATION_ACTIVITY = "apply_mitigation_action_activity"


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

    @workflow.run
    async def run(self, payload: AlertPayload) -> IncidentWorkflowResult:
        """Orchestrate triage, mitigation planning, approval, and remediation."""
        diagnosis: IncidentDiagnosis | None = None
        mitigation_plan: MitigationPlan | None = None

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

        self.incident_resolved = mitigation_applied
        status = (
            IncidentWorkflowStatus.RESOLVED
            if mitigation_applied
            else IncidentWorkflowStatus.FAILED
        )
        message = (
            "Mitigation actions applied successfully."
            if mitigation_applied
            else "Mitigation plan approved but apply step returned unsuccessful status."
        )

        return IncidentWorkflowResult(
            human_approved=True,
            incident_resolved=self.incident_resolved,
            status=status,
            diagnosis=diagnosis,
            mitigation_plan=mitigation_plan,
            message=message,
        )

    @workflow.signal(name="approve_mitigation")
    def approve_mitigation(self) -> None:
        """Signal handler granting operator approval to execute mitigation."""
        self.human_approved = True
        workflow.logger.info("Mitigation plan approved by human operator.")
