"""Shared controller component evidence, without polling or changing hardware."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from megaraid_dashboard.config import Settings
from megaraid_dashboard.db.models import ControllerSnapshot
from megaraid_dashboard.storcli.states import normalized_state


@dataclass(frozen=True)
class ControllerComponentHealth:
    severity: Literal["optimal", "warning", "critical"]
    reasons: tuple[str, ...]


def assess_controller_components(
    snapshot: ControllerSnapshot, *, settings: Settings | None = None
) -> ControllerComponentHealth:
    """Threshold-free callers still assess explicit CacheVault state/presence.

    Live pages and the production exporter supply their configured thresholds.
    Incomplete legacy evidence is unknown, never an invented absence or recovery.
    """
    critical: list[str] = []
    warning: list[str] = []
    cv = snapshot.cachevault
    if cv is None:
        if snapshot.cv_present:
            warning.append("CacheVault status is unverified.")
        else:
            critical.append("CacheVault is not detected.")
    else:
        if not snapshot.cv_present:
            warning.append("CacheVault presence evidence conflicts.")
        if normalized_state(cv.state) != "optimal":
            critical.append(f"CacheVault reports {cv.state}.")
        if cv.replacement_required:
            critical.append("CacheVault replacement is required.")
        if (
            settings is not None
            and cv.capacitance_percent is not None
            and cv.capacitance_percent < settings.cv_capacitance_warning_percent
        ):
            warning.append("CacheVault capacitance is below the warning threshold.")
    temperature = snapshot.roc_temperature_celsius
    if settings is not None and temperature is not None:
        if temperature >= settings.roc_temp_critical_celsius:
            critical.append("Controller temperature is critical.")
        elif temperature >= settings.roc_temp_warning_celsius:
            warning.append("Controller temperature is elevated.")
    reasons = tuple(critical + warning)
    severity: Literal["optimal", "warning", "critical"] = (
        "critical" if critical else "warning" if warning else "optimal"
    )
    return ControllerComponentHealth(severity, reasons)
