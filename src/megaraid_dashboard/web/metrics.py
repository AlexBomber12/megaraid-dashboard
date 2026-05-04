from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route


def build_registry() -> CollectorRegistry:
    registry = CollectorRegistry(auto_describe=True)
    up = Gauge(
        "megaraid_exporter_up",
        "1 when the megaraid-dashboard exporter is running.",
        registry=registry,
    )
    up.set(1)
    return registry


async def metrics_endpoint(request: Request) -> Response:
    registry: CollectorRegistry = request.app.state.registry
    body = generate_latest(registry)
    return Response(body, media_type=CONTENT_TYPE_LATEST)


def create_metrics_app() -> Starlette:
    app = Starlette(routes=[Route("/metrics", endpoint=metrics_endpoint)])
    app.state.registry = build_registry()
    return app
