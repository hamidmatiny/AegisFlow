"""Shared simulation control state for live infrastructure scenarios."""

from __future__ import annotations

import json
import logging
import os
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

DEFAULT_STATE_PATH = Path(
    os.getenv("AEGISFLOW_SIMULATION_STATE_PATH", ".aegisflow/simulation_state.json"),
)


class SimulationScenario(StrEnum):
    """Supported live simulation scenarios."""

    SUCCESS = "success"
    CHAOS = "chaos"


class SimulationControlState(BaseModel):
    """Process-shared control flags consumed by worker-side activities."""

    model_config = ConfigDict(extra="forbid")

    force_health_check_failure: bool = False
    service_name: str | None = None
    scenario: SimulationScenario | None = None


def resolve_state_path(path: Path | None = None) -> Path:
    """Return the simulation state path, creating parent directories if needed."""
    resolved = path or DEFAULT_STATE_PATH
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def load_simulation_control_state(path: Path | None = None) -> SimulationControlState:
    """Load simulation control state from disk, falling back to healthy defaults."""
    resolved = resolve_state_path(path)
    if not resolved.exists():
        return SimulationControlState()

    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
        return SimulationControlState.model_validate(payload)
    except (OSError, ValueError, TypeError):
        logger.exception("Failed to load simulation control state from %s.", resolved)
        return SimulationControlState()


def save_simulation_control_state(
    state: SimulationControlState,
    path: Path | None = None,
) -> Path:
    """Persist simulation control state for cross-process coordination."""
    resolved = resolve_state_path(path)
    resolved.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    return resolved.resolve()


def configure_scenario(
    scenario: SimulationScenario,
    *,
    service_name: str,
    path: Path | None = None,
) -> Path:
    """Configure the active simulation scenario for worker-side health checks."""
    state = SimulationControlState(
        force_health_check_failure=scenario is SimulationScenario.CHAOS,
        service_name=service_name,
        scenario=scenario,
    )
    saved_path = save_simulation_control_state(state, path)
    logger.info(
        "Simulation scenario %r configured for service %r at %s.",
        scenario.value,
        service_name,
        saved_path,
    )
    return saved_path


def should_force_health_check_failure(service_name: str, path: Path | None = None) -> bool:
    """Return whether the health check should fail for the requested service."""
    state = load_simulation_control_state(path)
    if not state.force_health_check_failure:
        return False
    if state.service_name is None:
        return True
    return state.service_name == service_name.strip()


def clear_simulation_control_state(path: Path | None = None) -> None:
    """Remove persisted simulation control state."""
    resolved = resolve_state_path(path)
    if resolved.exists():
        resolved.unlink()
