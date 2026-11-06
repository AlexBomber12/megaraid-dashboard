"""Shared presentation assessment; never changes controller state or action eligibility."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from megaraid_dashboard.db.models import PhysicalDriveSnapshot
from megaraid_dashboard.storcli.states import normalized_state


@dataclass(frozen=True)
class DriveHealthAssessment:
    severity: Literal["optimal", "warning", "critical"]
    label: str
    reasons: tuple[str, ...]

    @property
    def summary(self) -> str:
        return " ".join(self.reasons) or "Drive functioning normally."


def assess_drive_health(
    drive: PhysicalDriveSnapshot, *, temperature: str = "unknown"
) -> DriveHealthAssessment:
    """Combine reported faults without treating historical counters as drive failure.

    Temperature is optional for controller-level aggregation, where it has its own
    indicators. Every drive surface supplies the same configured temperature grade.
    Raw firmware states and counters remain unchanged and visible in the UI.
    """
    state = normalized_state(drive.state)
    critical: list[str] = []
    warning: list[str] = []
    state_faults = {
        "failed": "Controller reports drive failure.",
        "offline": "Drive is offline.",
        "missing": "No drive detected.",
        "ubad": "Controller marks drive unusable (UBad).",
    }
    if state in state_faults:
        critical.append(state_faults[state])
    elif state in {"rbld", "rebld", "rebuild", "rebuilding"}:
        warning.append("Rebuild in progress.")
    elif state == "jbod":
        warning.append("JBOD drive; not a RAID member.")
    elif state not in {"onln", "online", "ugood"}:
        warning.append(f"Unrecognized drive state: {drive.state}.")

    if drive.smart_alert:
        critical.append("S.M.A.R.T. alert is active.")
    if drive.predictive_failures > 0:
        critical.append(_count(drive.predictive_failures, "predictive failure"))
    if temperature == "critical":
        critical.append("Drive temperature is critical.")
    elif temperature == "warning":
        warning.append("Drive temperature is elevated.")
    if drive.media_errors > 0:
        warning.append(_count(drive.media_errors, "historical media error"))
    if drive.other_errors > 0:
        warning.append(_count(drive.other_errors, "other error"))

    reasons = tuple(critical + warning)
    if critical:
        return DriveHealthAssessment("critical", "Critical", reasons)
    if warning:
        return DriveHealthAssessment("warning", "Attention", reasons)
    return DriveHealthAssessment("optimal", "Ready" if state == "ugood" else "Healthy", ())


def _count(value: int, description: str) -> str:
    suffix = "" if value == 1 else "s"
    return f"{value} {description}{suffix}."
