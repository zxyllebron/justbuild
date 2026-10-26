"""Pluggable transport types and an explicit local simulation.

There is intentionally no undocumented cloud upload, MQTT control, credential
loader or vendor signing implementation in the open-source core.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .common import now


STATES = frozenset({"uploaded", "printer_received", "start_acknowledged", "printing",
                    "paused", "completed", "failed", "cancelled", "unknown"})


@dataclass(frozen=True)
class Observation:
    state: str
    remote_job_id: str
    evidence_id: str
    observed_at: str
    # Optional telemetry never establishes any earlier delivery milestone.
    progress_percent: float | None = None


class Transport(Protocol):
    name: str
    simulation: bool
    supported: bool

    def submit(self, artifact_path: Path, printer_id: str, idempotency_key: str) -> Observation: ...

    def poll(self, remote_job_id: str, printer_id: str) -> Observation: ...


class UnsupportedCloudTransport:
    name = "a1_cloud_unverified"
    simulation = False
    supported = False
    reason = ("A1 headless cloud upload/dispatch is unverified. Official network component "
              "or cloud-VM integration and trusted review authorization must be integrated.")


class FakeTransport:
    """In-memory test double. Never creates network connections or printer commands."""

    name = "local_simulation"
    simulation = True
    supported = True

    def __init__(self, *, timeout_on_submit: bool = False, initial_state: str = "uploaded"):
        self.timeout_on_submit = timeout_on_submit
        self.initial_state = initial_state
        self.submissions = 0
        self.next_state = initial_state

    def submit(self, artifact_path: Path, printer_id: str, idempotency_key: str) -> Observation:
        self.submissions += 1
        if self.timeout_on_submit:
            raise TimeoutError("Simulated ambiguous delivery")
        return Observation(self.initial_state, "simulation-" + idempotency_key[:20],
                           "simulation-submit", now())

    def poll(self, remote_job_id: str, printer_id: str) -> Observation:
        return Observation(self.next_state, remote_job_id, "simulation-poll", now())


def cloud_capabilities() -> dict:
    """Evidence-bounded capability report, without probing accounts/devices."""
    return {
        "schema_version": "0.1", "operation": "cloud_capabilities", "status": "unsupported",
        "job_id": "not_applicable", "artifacts": [], "issues": [],
        "target": "Bambu Lab A1", "simulation": False, "network_access_performed": False,
        "capabilities": {"account_login": "not_checked", "cloud_telemetry": "not_checked",
                         "model_upload": "not_checked", "printer_receipt": "not_checked",
                         "print_start_acknowledgement": "not_checked", "physical_completion": "not_checked"},
        "dependencies": ["A1 firmware and authorization mode", "account/printer region and binding",
                         "authorized official component or partner interface", "persistent submission reconciliation"],
    }
