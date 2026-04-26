from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from megaraid_dashboard.db.models import ControllerSnapshot, PhysicalDriveSnapshot
from megaraid_dashboard.storcli import CacheVault, PhysicalDrive, StorcliSnapshot, VirtualDrive

DriveKey = tuple[int, int, str]
SlotKey = tuple[int, int]

TEMP_STATE_OK = "ok"
TEMP_STATE_WARNING = "warning"
TEMP_STATE_CRITICAL = "critical"


@dataclass(frozen=True)
class DetectedEvent:
    severity: str
    category: str
    subject: str
    summary: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None


@dataclass(frozen=True)
class TempStateUpdate:
    enclosure_id: int
    slot_id: int
    serial_number: str
    state: str


@dataclass(frozen=True)
class TempStateClear:
    enclosure_id: int
    slot_id: int


class EventDetector:
    def __init__(
        self,
        *,
        temp_warning: int,
        temp_critical: int,
        temp_hysteresis: int,
        cv_capacitance_warning_percent: int = 70,
    ) -> None:
        self.temp_warning = temp_warning
        self.temp_critical = temp_critical
        self.temp_hysteresis = temp_hysteresis
        self.cv_capacitance_warning_percent = cv_capacitance_warning_percent
        self._temperature_states: dict[DriveKey, str] = {}
        self._temperature_updates: dict[DriveKey, str] = {}
        self._temperature_clears: set[SlotKey] = set()

    def set_temperature_states(self, states: dict[DriveKey, str]) -> None:
        self._temperature_states = dict(states)

    @property
    def temperature_updates(self) -> list[TempStateUpdate]:
        return [
            TempStateUpdate(
                enclosure_id=enclosure_id,
                slot_id=slot_id,
                serial_number=serial_number,
                state=state,
            )
            for (enclosure_id, slot_id, serial_number), state in sorted(
                self._temperature_updates.items()
            )
        ]

    @property
    def temperature_clears(self) -> list[TempStateClear]:
        return [
            TempStateClear(enclosure_id=enclosure_id, slot_id=slot_id)
            for enclosure_id, slot_id in sorted(self._temperature_clears)
        ]

    def detect(
        self,
        previous: ControllerSnapshot | None,
        current: StorcliSnapshot,
    ) -> list[DetectedEvent]:
        self._temperature_updates = {}
        self._temperature_clears = set()
        events: list[DetectedEvent] = []
        replaced_slots: set[SlotKey] = set()

        if previous is not None:
            events.extend(self._detect_controller(previous, current))
            events.extend(self._detect_virtual_drives(previous, current))
            replacement_events, replaced_slots = self._detect_drive_replacements(
                previous,
                current,
            )
            events.extend(replacement_events)
            events.extend(self._detect_physical_drives(previous, current, replaced_slots))
            events.extend(self._detect_cachevault(previous, current))
        else:
            events.extend(self._detect_baseline(current))

        events.extend(self._detect_temperatures(current, replaced_slots))
        return events

    def _detect_baseline(self, current: StorcliSnapshot) -> list[DetectedEvent]:
        events: list[DetectedEvent] = []
        if current.controller.alarm_state != "Off":
            events.append(
                DetectedEvent(
                    severity="info",
                    category="controller",
                    subject="Controller",
                    summary=f"Alarm state is {current.controller.alarm_state}",
                    before=None,
                    after={"alarm_state": current.controller.alarm_state},
                )
            )
        for virtual_drive in current.virtual_drives:
            new_drive_event = _new_virtual_drive_event(virtual_drive)
            if new_drive_event is not None:
                events.append(new_drive_event)
        for physical_drive in current.physical_drives:
            events.extend(_new_physical_drive_events(physical_drive))
        if current.cachevault is not None:
            events.extend(self._new_cachevault_events(current.cachevault))
        return events

    def _detect_controller(
        self,
        previous: ControllerSnapshot,
        current: StorcliSnapshot,
    ) -> list[DetectedEvent]:
        current_alarm = current.controller.alarm_state
        if previous.alarm_state == current_alarm:
            return []
        return [
            DetectedEvent(
                severity="info",
                category="controller",
                subject="Controller",
                summary=f"Alarm state changed from {previous.alarm_state} to {current_alarm}",
                before={"alarm_state": previous.alarm_state},
                after={"alarm_state": current_alarm},
            )
        ]

    def _detect_virtual_drives(
        self,
        previous: ControllerSnapshot,
        current: StorcliSnapshot,
    ) -> list[DetectedEvent]:
        current_by_id = {
            virtual_drive.vd_id: virtual_drive for virtual_drive in current.virtual_drives
        }
        previous_ids = {virtual_drive.vd_id for virtual_drive in previous.virtual_drives}
        events: list[DetectedEvent] = []
        for previous_drive in previous.virtual_drives:
            current_drive = current_by_id.get(previous_drive.vd_id)
            if current_drive is None or previous_drive.state == current_drive.state:
                continue
            events.append(
                DetectedEvent(
                    severity=_virtual_drive_state_severity(current_drive.state),
                    category="vd_state",
                    subject=f"VD {previous_drive.vd_id}",
                    summary=(
                        f"VD {previous_drive.vd_id} state changed from "
                        f"{previous_drive.state} to {current_drive.state}"
                    ),
                    before={"state": previous_drive.state},
                    after={"state": current_drive.state},
                )
            )
        for current_drive in current.virtual_drives:
            if current_drive.vd_id in previous_ids:
                continue
            new_drive_event = _new_virtual_drive_event(current_drive)
            if new_drive_event is not None:
                events.append(new_drive_event)
        return events

    def _detect_drive_replacements(
        self,
        previous: ControllerSnapshot,
        current: StorcliSnapshot,
    ) -> tuple[list[DetectedEvent], set[SlotKey]]:
        previous_by_slot = {
            (drive.enclosure_id, drive.slot_id): drive for drive in previous.physical_drives
        }
        current_slots = {(drive.enclosure_id, drive.slot_id) for drive in current.physical_drives}
        events: list[DetectedEvent] = []
        replaced_slots: set[SlotKey] = set()
        for slot_key in previous_by_slot:
            if slot_key not in current_slots:
                self._temperature_clears.add(slot_key)

        for current_drive in current.physical_drives:
            slot_key = (current_drive.enclosure_id, current_drive.slot_id)
            previous_drive = previous_by_slot.get(slot_key)
            if previous_drive is None:
                self._temperature_clears.add(slot_key)
                replaced_slots.add(slot_key)
                continue
            if previous_drive.serial_number == current_drive.serial_number:
                continue
            replaced_slots.add(slot_key)
            self._temperature_clears.add(slot_key)
            events.append(
                DetectedEvent(
                    severity="info",
                    category="pd_state",
                    subject=_physical_drive_subject(current_drive),
                    summary=(
                        "Drive replaced: "
                        f"{previous_drive.serial_number} -> {current_drive.serial_number}"
                    ),
                    before={"serial_number": previous_drive.serial_number},
                    after={"serial_number": current_drive.serial_number},
                )
            )
        return events, replaced_slots

    def _detect_physical_drives(
        self,
        previous: ControllerSnapshot,
        current: StorcliSnapshot,
        replaced_slots: set[SlotKey],
    ) -> list[DetectedEvent]:
        previous_by_slot = {
            (drive.enclosure_id, drive.slot_id): drive for drive in previous.physical_drives
        }
        events: list[DetectedEvent] = []
        for current_drive in current.physical_drives:
            slot_key = (current_drive.enclosure_id, current_drive.slot_id)
            previous_drive = previous_by_slot.get(slot_key)
            if previous_drive is None:
                events.extend(_new_physical_drive_events(current_drive))
                continue
            if slot_key in replaced_slots:
                if previous_drive.state != current_drive.state:
                    events.append(_physical_drive_state_event(previous_drive, current_drive))
                    events.extend(_new_counter_events(current_drive))
                    if current_drive.smart_alert:
                        events.append(_smart_alert_event(None, current_drive))
                else:
                    events.extend(_new_physical_drive_events(current_drive))
                continue
            if previous_drive.state != current_drive.state:
                events.append(_physical_drive_state_event(previous_drive, current_drive))
            events.extend(_counter_events(previous_drive, current_drive))
            if current_drive.smart_alert and not previous_drive.smart_alert:
                events.append(_smart_alert_event(previous_drive.smart_alert, current_drive))
        return events

    def _detect_temperatures(
        self,
        current: StorcliSnapshot,
        replaced_slots: set[SlotKey],
    ) -> list[DetectedEvent]:
        events: list[DetectedEvent] = []
        for drive in current.physical_drives:
            if drive.temperature_celsius is None:
                continue
            key = _drive_key(drive)
            slot_key = (drive.enclosure_id, drive.slot_id)
            initial_state = (
                TEMP_STATE_OK
                if slot_key in replaced_slots
                else self._temperature_states.get(key, TEMP_STATE_OK)
            )
            next_events, next_state = self._temperature_transitions(drive, initial_state)
            events.extend(next_events)
            self._temperature_updates[key] = next_state
        return events

    def _temperature_transitions(
        self,
        drive: PhysicalDrive,
        initial_state: str,
    ) -> tuple[list[DetectedEvent], str]:
        state = initial_state
        events: list[DetectedEvent] = []
        temperature = drive.temperature_celsius
        if temperature is None:
            return events, state

        while True:
            if state == TEMP_STATE_OK and temperature >= self.temp_warning:
                next_state = TEMP_STATE_WARNING
                events.append(
                    _temperature_event(
                        drive,
                        severity="warning",
                        summary=f"Temperature reached warning threshold: {temperature} C",
                        before_state=state,
                        after_state=next_state,
                    )
                )
                state = next_state
                continue
            if state == TEMP_STATE_WARNING and temperature >= self.temp_critical:
                next_state = TEMP_STATE_CRITICAL
                events.append(
                    _temperature_event(
                        drive,
                        severity="critical",
                        summary=f"Temperature reached critical threshold: {temperature} C",
                        before_state=state,
                        after_state=next_state,
                    )
                )
                state = next_state
                continue
            if state == TEMP_STATE_CRITICAL and temperature < (
                self.temp_critical - self.temp_hysteresis
            ):
                next_state = TEMP_STATE_WARNING
                events.append(
                    _temperature_event(
                        drive,
                        severity="info",
                        summary=f"Temperature back below critical: {temperature} C",
                        before_state=state,
                        after_state=next_state,
                    )
                )
                state = next_state
                continue
            if state == TEMP_STATE_WARNING and temperature < (
                self.temp_warning - self.temp_hysteresis
            ):
                next_state = TEMP_STATE_OK
                events.append(
                    _temperature_event(
                        drive,
                        severity="info",
                        summary=f"Temperature back to normal: {temperature} C",
                        before_state=state,
                        after_state=next_state,
                    )
                )
                state = next_state
                continue
            return events, state

    def _detect_cachevault(
        self,
        previous: ControllerSnapshot,
        current: StorcliSnapshot,
    ) -> list[DetectedEvent]:
        if previous.cachevault is None or current.cachevault is None:
            return []

        events: list[DetectedEvent] = []
        if previous.cachevault.state != current.cachevault.state:
            events.append(
                DetectedEvent(
                    severity=_cachevault_state_severity(current.cachevault.state),
                    category="cv_state",
                    subject="CacheVault",
                    summary=(
                        "CacheVault state changed from "
                        f"{previous.cachevault.state} to {current.cachevault.state}"
                    ),
                    before={"state": previous.cachevault.state},
                    after={"state": current.cachevault.state},
                )
            )
        if current.cachevault.replacement_required and not previous.cachevault.replacement_required:
            events.append(
                DetectedEvent(
                    severity="critical",
                    category="cv_state",
                    subject="CacheVault",
                    summary="CacheVault replacement required",
                    before={"replacement_required": previous.cachevault.replacement_required},
                    after={"replacement_required": current.cachevault.replacement_required},
                )
            )
        previous_capacitance = previous.cachevault.capacitance_percent
        current_capacitance = current.cachevault.capacitance_percent
        if (
            previous_capacitance is not None
            and current_capacitance is not None
            and previous_capacitance >= self.cv_capacitance_warning_percent
            and current_capacitance < self.cv_capacitance_warning_percent
        ):
            events.append(
                DetectedEvent(
                    severity="warning",
                    category="cv_state",
                    subject="CacheVault",
                    summary=(
                        "CacheVault capacitance dropped below "
                        f"{self.cv_capacitance_warning_percent}%: "
                        f"{previous_capacitance}% -> {current_capacitance}%"
                    ),
                    before={"capacitance_percent": previous_capacitance},
                    after={"capacitance_percent": current_capacitance},
                )
            )
        return events

    def _new_cachevault_events(self, cachevault: CacheVault) -> list[DetectedEvent]:
        events: list[DetectedEvent] = []
        state_severity = _cachevault_state_severity(cachevault.state)
        if state_severity != "info":
            events.append(
                DetectedEvent(
                    severity=state_severity,
                    category="cv_state",
                    subject="CacheVault",
                    summary=f"CacheVault state is {cachevault.state}",
                    before=None,
                    after={"state": cachevault.state},
                )
            )
        if cachevault.replacement_required:
            events.append(
                DetectedEvent(
                    severity="critical",
                    category="cv_state",
                    subject="CacheVault",
                    summary="CacheVault replacement required",
                    before=None,
                    after={"replacement_required": cachevault.replacement_required},
                )
            )
        if (
            cachevault.capacitance_percent is not None
            and cachevault.capacitance_percent < self.cv_capacitance_warning_percent
        ):
            events.append(
                DetectedEvent(
                    severity="warning",
                    category="cv_state",
                    subject="CacheVault",
                    summary=(
                        "CacheVault capacitance below "
                        f"{self.cv_capacitance_warning_percent}%: "
                        f"{cachevault.capacitance_percent}%"
                    ),
                    before=None,
                    after={"capacitance_percent": cachevault.capacitance_percent},
                )
            )
        return events


def _virtual_drive_state_severity(state: str) -> str:
    if state in {"Optl", "Optimal"}:
        return "info"
    if state in {"Failed", "Offline", "Offln", "Partially Degraded", "Pdgd"}:
        return "critical"
    return "warning"


def _new_virtual_drive_event(virtual_drive: VirtualDrive) -> DetectedEvent | None:
    severity = _virtual_drive_state_severity(virtual_drive.state)
    if severity == "info":
        return None
    return DetectedEvent(
        severity=severity,
        category="vd_state",
        subject=f"VD {virtual_drive.vd_id}",
        summary=f"VD {virtual_drive.vd_id} state is {virtual_drive.state}",
        before=None,
        after={"state": virtual_drive.state},
    )


def _physical_drive_state_event(
    previous: PhysicalDriveSnapshot,
    current: PhysicalDrive,
) -> DetectedEvent:
    return DetectedEvent(
        severity=_physical_drive_state_severity(previous.state, current.state),
        category="pd_state",
        subject=_physical_drive_subject(current),
        summary=(
            f"{_physical_drive_subject(current)} state changed from "
            f"{previous.state} to {current.state}"
        ),
        before={"state": previous.state},
        after={"state": current.state},
    )


def _new_physical_drive_events(current: PhysicalDrive) -> list[DetectedEvent]:
    events: list[DetectedEvent] = []
    state_severity = _physical_drive_state_severity(current.state, current.state)
    if state_severity != "info":
        events.append(
            DetectedEvent(
                severity=state_severity,
                category="pd_state",
                subject=_physical_drive_subject(current),
                summary=f"{_physical_drive_subject(current)} state is {current.state}",
                before=None,
                after={"state": current.state},
            )
        )
    events.extend(_new_counter_events(current))
    if current.smart_alert:
        events.append(_smart_alert_event(None, current))
    return events


def _new_counter_events(current: PhysicalDrive) -> list[DetectedEvent]:
    events: list[DetectedEvent] = []
    for field_name, severity, category, label in (
        ("media_errors", "critical", "media_errors", "Media error count"),
        ("other_errors", "warning", "other_errors", "Other error count"),
        (
            "predictive_failures",
            "critical",
            "predictive_failures",
            "Predictive failure count",
        ),
    ):
        after_value = getattr(current, field_name)
        if after_value <= 0:
            continue
        events.append(
            DetectedEvent(
                severity=severity,
                category=category,
                subject=_physical_drive_subject(current),
                summary=f"{label} is {after_value}",
                before=None,
                after={field_name: after_value},
            )
        )
    return events


def _smart_alert_event(
    previous_smart_alert: bool | None,
    current: PhysicalDrive,
) -> DetectedEvent:
    before = None if previous_smart_alert is None else {"smart_alert": previous_smart_alert}
    return DetectedEvent(
        severity="critical",
        category="smart_alert",
        subject=_physical_drive_subject(current),
        summary="SMART alert flagged by drive",
        before=before,
        after={"smart_alert": current.smart_alert},
    )


def _physical_drive_state_severity(previous_state: str, current_state: str) -> str:
    if current_state in {"Failed", "Missing", "Msng", "Offline", "Offln"}:
        return "critical"
    if current_state in {"JBOD", "UGood", "UBad"}:
        return "warning"
    if previous_state == "Onln" or current_state == "Onln":
        return "info"
    return "info"


def _cachevault_state_severity(state: str) -> str:
    if state in {"Optimal", "Optl"}:
        return "info"
    return "critical"


def _counter_events(
    previous: PhysicalDriveSnapshot,
    current: PhysicalDrive,
) -> list[DetectedEvent]:
    events: list[DetectedEvent] = []
    for field_name, severity, category, label in (
        ("media_errors", "critical", "media_errors", "Media error count"),
        ("other_errors", "warning", "other_errors", "Other error count"),
        (
            "predictive_failures",
            "critical",
            "predictive_failures",
            "Predictive failure count",
        ),
    ):
        before_value = getattr(previous, field_name)
        after_value = getattr(current, field_name)
        if before_value >= after_value:
            continue
        events.append(
            DetectedEvent(
                severity=severity,
                category=category,
                subject=_physical_drive_subject(current),
                summary=f"{label} increased from {before_value} to {after_value}",
                before={field_name: before_value},
                after={field_name: after_value},
            )
        )
    return events


def _temperature_event(
    drive: PhysicalDrive,
    *,
    severity: str,
    summary: str,
    before_state: str,
    after_state: str,
) -> DetectedEvent:
    return DetectedEvent(
        severity=severity,
        category="temperature",
        subject=_physical_drive_subject(drive),
        summary=summary,
        before={
            "state": before_state,
            "temperature_celsius": drive.temperature_celsius,
        },
        after={
            "state": after_state,
            "temperature_celsius": drive.temperature_celsius,
        },
    )


def _physical_drive_subject(drive: PhysicalDrive | PhysicalDriveSnapshot) -> str:
    return f"PD e{drive.enclosure_id}:s{drive.slot_id}"


def _drive_key(drive: PhysicalDrive) -> DriveKey:
    return drive.enclosure_id, drive.slot_id, drive.serial_number
