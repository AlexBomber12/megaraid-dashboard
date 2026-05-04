from __future__ import annotations

from collections.abc import Iterable

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest
from prometheus_client.core import GaugeMetricFamily
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from megaraid_dashboard.db import ControllerSnapshot, PhysicalDriveSnapshot, VirtualDriveSnapshot
from megaraid_dashboard.services.overview import derive_controller_health


class MegaraidCollector:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._cache: tuple[int | None, list[GaugeMetricFamily]] = (None, [])

    def collect(self) -> Iterable[GaugeMetricFamily]:
        with self._session_factory() as session:
            latest_id = session.execute(
                select(ControllerSnapshot.id)
                .order_by(ControllerSnapshot.captured_at.desc())
                .limit(1)
            ).scalar_one_or_none()
        if latest_id is not None and latest_id == self._cache[0]:
            yield from self._cache[1]
            return

        families = self._build_families(latest_id)
        self._cache = (latest_id, families)
        yield from families

    def _build_families(self, latest_id: int | None) -> list[GaugeMetricFamily]:
        if latest_id is None:
            return []

        with self._session_factory() as session:
            snap = session.get(ControllerSnapshot, latest_id)
            if snap is None:
                return []
            physical_drives = list(snap.physical_drives)
            virtual_drives = list(snap.virtual_drives)
            cachevault = snap.cachevault

        controller_health = GaugeMetricFamily(
            "megaraid_controller_health",
            "Controller health (0=optimal, 1=warning, 2=critical).",
            labels=["model", "serial"],
        )
        roc_temperature = GaugeMetricFamily(
            "megaraid_controller_roc_temperature_celsius",
            "Controller RoC silicon temperature in Celsius.",
            labels=["model", "serial"],
        )
        cv_capacitance = GaugeMetricFamily(
            "megaraid_cv_capacitance_percent",
            "Cache vault capacitance percent.",
            labels=["model", "serial"],
        )
        temperature = GaugeMetricFamily(
            "megaraid_drive_temperature_celsius",
            "Per-physical-drive temperature in Celsius from latest snapshot.",
            labels=["enclosure", "slot", "model", "serial"],
        )
        physical_drive_state = GaugeMetricFamily(
            "megaraid_physical_drive_state",
            "Per-physical-drive state (0=optimal, 1=warning, 2=critical).",
            labels=["enclosure", "slot", "model", "serial"],
        )
        virtual_drive_state = GaugeMetricFamily(
            "megaraid_virtual_drive_state",
            "Per-virtual-drive state (0=optimal, 1=warning, 2=critical).",
            labels=["vd_id", "name", "raid_level"],
        )

        controller_labels = [snap.model_name, snap.serial_number]
        controller_health.add_metric(
            controller_labels,
            float(_encode_controller_health(snap, physical_drives, virtual_drives)),
        )
        controller_families = [controller_health]
        if snap.roc_temperature_celsius is not None:
            roc_temperature.add_metric(controller_labels, float(snap.roc_temperature_celsius))
            controller_families.append(roc_temperature)
        if cachevault is not None and cachevault.capacitance_percent is not None:
            cv_capacitance.add_metric(controller_labels, float(cachevault.capacitance_percent))
            controller_families.append(cv_capacitance)

        for physical_drive in physical_drives:
            labels = [
                str(physical_drive.enclosure_id),
                str(physical_drive.slot_id),
                physical_drive.model,
                physical_drive.serial_number,
            ]
            if physical_drive.temperature_celsius is not None:
                temperature.add_metric(labels, float(physical_drive.temperature_celsius))
            physical_drive_state.add_metric(labels, float(_encode_pd_state(physical_drive.state)))

        for virtual_drive in virtual_drives:
            labels = [
                str(virtual_drive.vd_id),
                virtual_drive.name,
                virtual_drive.raid_level,
            ]
            virtual_drive_state.add_metric(labels, float(_encode_vd_state(virtual_drive.state)))

        return [
            *controller_families,
            temperature,
            physical_drive_state,
            virtual_drive_state,
        ]


def _encode_controller_health(
    snap: ControllerSnapshot,
    pds: list[PhysicalDriveSnapshot],
    vds: list[VirtualDriveSnapshot],
) -> int:
    health = derive_controller_health(snap, pds, vds)
    return {
        "optimal": 0,
        "warning": 1,
        "critical": 2,
    }[health]


def _encode_pd_state(state: str) -> int:
    normalized = state.lower()
    if normalized in {"onln", "ugood", "optl"}:
        return 0
    if normalized in {"rbld", "ureb", "missing"}:
        return 1
    return 2


def _encode_vd_state(state: str) -> int:
    normalized = state.lower()
    if normalized in {"optl", "optimal"}:
        return 0
    if normalized in {"dgrd", "degraded", "pdgd", "partially-degraded"}:
        return 1
    return 2


def build_registry(session_factory: sessionmaker[Session] | None = None) -> CollectorRegistry:
    registry = CollectorRegistry(auto_describe=True)
    up = Gauge(
        "megaraid_exporter_up",
        "1 when the megaraid-dashboard exporter is running.",
        registry=registry,
    )
    up.set(1)
    if session_factory is not None:
        registry.register(MegaraidCollector(session_factory))
    return registry


async def metrics_endpoint(request: Request) -> Response:
    registry: CollectorRegistry = request.app.state.registry
    body = generate_latest(registry)
    return Response(body, headers={"Content-Type": CONTENT_TYPE_LATEST})


def create_metrics_app(session_factory: sessionmaker[Session] | None = None) -> Starlette:
    app = Starlette(routes=[Route("/metrics", endpoint=metrics_endpoint)])
    app.state.registry = build_registry(session_factory)
    return app
