"""Unit tests for AegisFlow PydanticAI incident agents."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic_ai import capture_run_messages, models
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from aegisflow.agents.deps import SystemEnvironment
from aegisflow.agents.incident_agents import (
    ANTHROPIC_DEFAULT_MODEL,
    OPENAI_DEFAULT_MODEL,
    UNCONFIGURED_MODEL,
    XAI_DEFAULT_MODEL,
    build_mitigation_prompt,
    build_triage_prompt,
    get_default_model,
    mitigation_agent,
    triage_agent,
)
from aegisflow.models import (
    AlertPayload,
    AlertSource,
    IncidentDiagnosis,
    MitigationPlan,
)

models.ALLOW_MODEL_REQUESTS = False


class TestModelSelection:
    def test_get_default_model_prefers_anthropic(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
        monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
        monkeypatch.setenv("XAI_API_KEY", "test-xai-key")

        assert get_default_model() == ANTHROPIC_DEFAULT_MODEL

    def test_get_default_model_falls_back_to_openai(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
        monkeypatch.delenv("XAI_API_KEY", raising=False)

        assert get_default_model() == OPENAI_DEFAULT_MODEL

    def test_get_default_model_falls_back_to_xai(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("XAI_API_KEY", "test-xai-key")

        assert get_default_model() == XAI_DEFAULT_MODEL

    def test_get_default_model_uses_test_placeholder_without_keys(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("XAI_API_KEY", raising=False)

        assert get_default_model() == UNCONFIGURED_MODEL


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
def payments_diagnosis() -> IncidentDiagnosis:
    return IncidentDiagnosis(
        error_classification="resource_exhaustion",
        confidence_score=0.93,
        root_cause_analysis=(
            "Heap exhaustion in payments-api confirmed by OOMKilled log fingerprints."
        ),
        log_fingerprints=["java.lang.OutOfMemoryError", "heap usage at 98%"],
    )


@pytest.fixture
def system_environment() -> SystemEnvironment:
    return SystemEnvironment.default_for_service("payments-api")


def _extract_tool_names(messages: list[ModelMessage]) -> list[str]:
    tool_names: list[str] = []
    for message in messages:
        if not hasattr(message, "parts"):
            continue
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                tool_names.append(part.tool_name)
    return tool_names


def _extract_tool_return_contents(messages: list[ModelMessage]) -> list[Any]:
    contents: list[Any] = []
    for message in messages:
        if not hasattr(message, "parts"):
            continue
        for part in message.parts:
            if isinstance(part, ToolReturnPart):
                contents.append(part.content)
    return contents


class TestSystemEnvironment:
    def test_fetch_recent_logs_returns_telemetry_for_known_service(
        self,
        system_environment: SystemEnvironment,
    ) -> None:
        logs = system_environment.fetch_recent_logs("payments-api")

        assert len(logs) >= 2
        assert any("OutOfMemoryError" in line for line in logs)

    def test_fetch_recent_logs_unknown_service_returns_empty_list(self) -> None:
        environment = SystemEnvironment(log_store={}, infrastructure_store={})

        logs = environment.fetch_recent_logs("unknown-service")

        assert logs == []

    def test_fetch_recent_logs_empty_service_name_is_defensive(self) -> None:
        environment = SystemEnvironment.default_for_service("payments-api")

        assert environment.fetch_recent_logs("   ") == []

    def test_get_infrastructure_state_returns_topology(
        self,
        system_environment: SystemEnvironment,
    ) -> None:
        state = system_environment.get_infrastructure_state("payments-api")

        assert state["service_name"] == "payments-api"
        assert state["replicas_ready"] < state["replicas_desired"]
        assert "dependencies" in state

    def test_get_infrastructure_state_unknown_service_returns_unknown_status(self) -> None:
        environment = SystemEnvironment(log_store={}, infrastructure_store={})

        state = environment.get_infrastructure_state("missing-service")

        assert state["service_name"] == "missing-service"
        assert state["status"] == "unknown"


class TestPromptBuilders:
    def test_build_triage_prompt_includes_alert_context(
        self,
        payments_alert: AlertPayload,
    ) -> None:
        prompt = build_triage_prompt(payments_alert)

        assert "payments-api" in prompt
        assert "Datadog" in prompt
        assert "fetch_service_logs" in prompt

    def test_build_mitigation_prompt_includes_diagnosis(
        self,
        payments_alert: AlertPayload,
        payments_diagnosis: IncidentDiagnosis,
    ) -> None:
        prompt = build_mitigation_prompt(payments_alert, payments_diagnosis)

        assert "resource_exhaustion" in prompt
        assert "inspect_infrastructure_state" in prompt


class TestTriageAgent:
    async def test_alert_payload_triggers_log_tool_and_valid_diagnosis(
        self,
        payments_alert: AlertPayload,
        system_environment: SystemEnvironment,
    ) -> None:
        expected = IncidentDiagnosis(
            error_classification="resource_exhaustion",
            confidence_score=0.91,
            root_cause_analysis=(
                "OOMKilled events and heap saturation in payments-api logs "
                "indicate memory exhaustion."
            ),
            log_fingerprints=["OutOfMemoryError", "heap usage at 98%"],
        )
        test_model = TestModel(custom_output_args=expected.model_dump())

        with capture_run_messages() as messages:
            with triage_agent.override(model=test_model):
                result = await triage_agent.run(
                    build_triage_prompt(payments_alert),
                    deps=system_environment,
                )

        assert isinstance(result.output, IncidentDiagnosis)
        assert result.output.error_classification == expected.error_classification
        assert 0.0 <= result.output.confidence_score <= 1.0
        assert result.output.root_cause_analysis
        assert "fetch_service_logs" in _extract_tool_names(messages)

        log_returns = _extract_tool_return_contents(messages)
        assert any(isinstance(content, list) for content in log_returns)

    async def test_triage_agent_function_model_passes_service_name_to_log_tool(
        self,
        payments_alert: AlertPayload,
        system_environment: SystemEnvironment,
    ) -> None:
        captured_service_name: dict[str, str | None] = {"value": None}

        def triage_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            if len(messages) == 1:
                last_part = messages[0].parts[-1]
                prompt_text = (
                    str(last_part.content)
                    if isinstance(last_part, UserPromptPart)
                    else build_triage_prompt(payments_alert)
                )
                match = re.search(r"<affected_service>([^<]+)</affected_service>", prompt_text)
                service_name = match.group(1).strip() if match else payments_alert.affected_service
                captured_service_name["value"] = service_name
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="fetch_service_logs",
                            args={"service_name": service_name},
                        ),
                    ],
                )

            last_part = messages[-1].parts[0]
            assert isinstance(last_part, ToolReturnPart)
            diagnosis = IncidentDiagnosis(
                error_classification="resource_exhaustion",
                confidence_score=0.88,
                root_cause_analysis="Diagnosis grounded in returned log telemetry.",
                log_fingerprints=["OutOfMemoryError"],
            )
            return ModelResponse(
                parts=[
                    TextPart(content=diagnosis.model_dump_json()),
                ],
            )

        with triage_agent.override(model=FunctionModel(triage_model)):
            result = await triage_agent.run(
                build_triage_prompt(payments_alert),
                deps=system_environment,
            )

        assert captured_service_name["value"] == "payments-api"
        assert isinstance(result.output, IncidentDiagnosis)
        assert result.output.confidence_score == pytest.approx(0.88)


class TestMitigationAgent:
    async def test_mitigation_agent_returns_validated_plan(
        self,
        payments_alert: AlertPayload,
        payments_diagnosis: IncidentDiagnosis,
        system_environment: SystemEnvironment,
    ) -> None:
        expected = MitigationPlan(
            suggested_actions=[
                "Scale payments-api deployment from 4 to 6 replicas.",
                "Restart unhealthy pods after confirming traffic drain.",
            ],
            risk_level="medium",
            blast_radius_assessment=(
                "Impact limited to payments-api; downstream billing may see elevated latency."
            ),
            fallback_commands=[
                "kubectl rollout undo deployment/payments-api -n production",
            ],
        )
        test_model = TestModel(custom_output_args=expected.model_dump())

        with capture_run_messages() as messages:
            with mitigation_agent.override(model=test_model):
                result = await mitigation_agent.run(
                    build_mitigation_prompt(payments_alert, payments_diagnosis),
                    deps=system_environment,
                )

        assert isinstance(result.output, MitigationPlan)
        assert len(result.output.suggested_actions) >= 1
        assert result.output.risk_level
        assert result.output.blast_radius_assessment
        assert "inspect_infrastructure_state" in _extract_tool_names(messages)

    async def test_mitigation_agent_uses_infrastructure_telemetry_in_tool_returns(
        self,
        payments_alert: AlertPayload,
        payments_diagnosis: IncidentDiagnosis,
        system_environment: SystemEnvironment,
    ) -> None:
        def mitigation_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            if len(messages) == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="inspect_infrastructure_state",
                            args={"service_name": payments_alert.affected_service},
                        ),
                    ],
                )

            last_part = messages[-1].parts[0]
            assert isinstance(last_part, ToolReturnPart)
            plan = MitigationPlan(
                suggested_actions=["Scale payments-api replicas to absorb memory pressure."],
                risk_level="medium",
                blast_radius_assessment=(
                    "Scaling is limited to payments-api based on infrastructure telemetry."
                ),
                fallback_commands=["kubectl rollout undo deployment/payments-api -n production"],
            )
            return ModelResponse(parts=[TextPart(content=plan.model_dump_json())])

        with capture_run_messages() as messages:
            with mitigation_agent.override(model=FunctionModel(mitigation_model)):
                result = await mitigation_agent.run(
                    build_mitigation_prompt(payments_alert, payments_diagnosis),
                    deps=system_environment,
                )

        infra_returns = [
            content
            for content in _extract_tool_return_contents(messages)
            if isinstance(content, dict) and "replicas_ready" in content
        ]
        assert infra_returns
        assert infra_returns[0]["service_name"] == "payments-api"
        assert isinstance(result.output, MitigationPlan)
