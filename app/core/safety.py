"""Safety policy: advisory-only operation, mode governance and advice gating.

Non-negotiable rules enforced here and verified by tests/test_safety.py:
  * This application contains NO code path that writes to a PLC/DCS/OPC UA/MQTT endpoint.
  * ALLOW_PLANT_WRITES must be false; if set true the application refuses to start.
  * SUPERVISED_MPC cannot be activated in a cloud deployment tier; even on-premises it is
    absent in this release (it would require a separately engineered, safety-assessed approval
    gateway under DCS ownership - see docs/MPC_SAFETY_CASE.md).
  * Operator acknowledgement of a recommendation changes ONLY its audit state.
  * Safety interlocks (SIS/DCS) are outside and above this application and cannot be overridden.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, List

from .errors import DomainError


class Mode(str, Enum):
    DEMO_SIMULATION = "DEMO_SIMULATION"
    OFFLINE_SCENARIO = "OFFLINE_SCENARIO"
    HISTORICAL_REPLAY = "HISTORICAL_REPLAY"
    LIVE_MONITOR_ONLY = "LIVE_MONITOR_ONLY"
    ADVISORY_SHADOW_MPC = "ADVISORY_SHADOW_MPC"
    SUPERVISED_MPC = "SUPERVISED_MPC"


MODE_INFO: Dict[str, dict] = {
    "DEMO_SIMULATION": {"data_origin": "SYNTHETIC", "feedback": "SIMULATED", "writes": "DISABLED",
                        "description": "Deterministic synthetic plant, physics twin, fully disconnected from plant interfaces."},
    "OFFLINE_SCENARIO": {"data_origin": "USER_SCENARIO", "feedback": "SIMULATED", "writes": "DISABLED",
                         "description": "What-if studies; live loop paused."},
    "HISTORICAL_REPLAY": {"data_origin": "HISTORICAL_FILE", "feedback": "RECORDED", "writes": "DISABLED",
                          "description": "Replay of uploaded/shipped historian data with timestamps, lags and quality flags."},
    "LIVE_MONITOR_ONLY": {"data_origin": "LIVE_EDGE", "feedback": "REAL", "writes": "DISABLED",
                          "description": "Read-only ingestion from an approved edge gateway; no plant writes."},
    "ADVISORY_SHADOW_MPC": {"data_origin": "SYNTHETIC_OR_LIVE", "feedback": "SIMULATED_OR_REAL", "writes": "DISABLED",
                            "description": "MPC recommendations computed and archived, never transmitted."},
    "SUPERVISED_MPC": {"data_origin": "-", "feedback": "-", "writes": "NOT_IMPLEMENTED",
                       "description": "Requires a separately engineered on-premises approval gateway; not available."},
}

ALLOWED_ROLES_FOR_MODE_CHANGE = {"PROCESS_ENGINEER", "ADMIN"}


class SafetyPolicy:
    def __init__(self, settings):
        self.s = settings
        if settings.allow_plant_writes:
            raise RuntimeError("ALLOW_PLANT_WRITES=true is not supported: this release has no plant-write path. "
                               "Set ALLOW_PLANT_WRITES=false.")

    @property
    def plant_writes_enabled(self) -> bool:
        return False

    def check_mode_transition(self, current: str, target: str, role: str, edge_configured: bool) -> None:
        if target not in MODE_INFO:
            raise DomainError("UNKNOWN_MODE", f"Unknown mode {target}", 422)
        if role not in ALLOWED_ROLES_FOR_MODE_CHANGE:
            raise DomainError("FORBIDDEN_MODE_CHANGE", "Mode change requires PROCESS_ENGINEER or ADMIN", 403)
        if target == Mode.SUPERVISED_MPC.value:
            raise DomainError("MODE_NOT_AVAILABLE",
                              "SUPERVISED_MPC is not implemented in this release and can never be enabled from a "
                              f"'{self.s.deployment_tier}' deployment. It requires an independently engineered, "
                              "safety-assessed on-premises approval gateway under DCS ownership.", 403)
        if target == Mode.LIVE_MONITOR_ONLY.value and not edge_configured:
            raise DomainError("NO_EDGE_SOURCE", "No approved read-only edge source is configured (EDGE_INGEST_TOKEN).", 409)

    @staticmethod
    def advice_gate(checks: Dict[str, bool]) -> List[str]:
        """Return the list of failed trust checks (empty list -> advice may be issued)."""
        return [k for k, ok in checks.items() if not ok]
