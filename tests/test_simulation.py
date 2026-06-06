"""Tests for live simulation control and runner helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from aegisflow.simulation.control import (
    SimulationScenario,
    clear_simulation_control_state,
    configure_scenario,
    load_simulation_control_state,
    should_force_health_check_failure,
)
from aegisflow.simulation_runner import (
    build_payments_alert,
    build_temporal_ui_url,
    build_workflow_id,
)


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "simulation_state.json"


class TestSimulationControl:
    def test_success_scenario_does_not_force_health_failure(
        self,
        state_path: Path,
    ) -> None:
        configure_scenario(
            SimulationScenario.SUCCESS,
            service_name="payments-api",
            path=state_path,
        )

        assert should_force_health_check_failure("payments-api", path=state_path) is False

    def test_chaos_scenario_forces_health_failure_for_target_service(
        self,
        state_path: Path,
    ) -> None:
        configure_scenario(
            SimulationScenario.CHAOS,
            service_name="payments-api",
            path=state_path,
        )

        assert should_force_health_check_failure("payments-api", path=state_path) is True
        assert should_force_health_check_failure("auth-gateway", path=state_path) is False

    def test_clear_simulation_control_state_removes_file(self, state_path: Path) -> None:
        configure_scenario(
            SimulationScenario.CHAOS,
            service_name="payments-api",
            path=state_path,
        )
        clear_simulation_control_state(path=state_path)

        assert not state_path.exists()
        assert load_simulation_control_state(path=state_path).force_health_check_failure is False


class TestSimulationRunnerHelpers:
    def test_build_payments_alert_targets_payments_api(self) -> None:
        alert = build_payments_alert()

        assert alert.affected_service == "payments-api"
        assert alert.source is not None

    def test_build_workflow_id_contains_scenario(self) -> None:
        workflow_id = build_workflow_id(SimulationScenario.CHAOS)

        assert workflow_id.startswith("aegisflow-sim-chaos-")

    def test_build_temporal_ui_url(self) -> None:
        url = build_temporal_ui_url("http://localhost:8080", "demo-workflow-id")

        assert "localhost:8080" in url
        assert "demo-workflow-id" in url
