from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import cast

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session, sessionmaker

from megaraid_dashboard import __version__
from megaraid_dashboard.services.overview import OverviewViewModel, load_overview_view_model
from megaraid_dashboard.web.templates import create_templates

LOGGER = structlog.get_logger(__name__)
TEMPLATES = create_templates(Path(__file__).resolve().parents[1] / "templates")

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/", name="overview")
def overview(request: Request) -> Response:
    started_at = perf_counter()
    view_model = _load_overview(request)
    response = TEMPLATES.TemplateResponse(
        request=request,
        name="pages/overview.html",
        context={
            "active_nav": "overview",
            "current_utc_label": _current_utc_label(),
            "view_model": view_model,
        },
    )
    _log_overview_rendered(view_model=view_model, elapsed_ms=_elapsed_ms(started_at), partial=False)
    return response


@router.get("/partials/overview", name="overview_partial")
def overview_partial(request: Request) -> Response:
    started_at = perf_counter()
    view_model = _load_overview(request)
    response = TEMPLATES.TemplateResponse(
        request=request,
        name="partials/overview_data.html",
        context={"view_model": view_model},
    )
    _log_overview_rendered(view_model=view_model, elapsed_ms=_elapsed_ms(started_at), partial=True)
    return response


@router.get("/drives", name="drives")
def drives(request: Request) -> RedirectResponse:
    return RedirectResponse(str(request.url_for("overview").path), status_code=303)


@router.get("/events", name="events")
def events(request: Request) -> Response:
    return TEMPLATES.TemplateResponse(
        request=request,
        name="pages/events.html",
        context={
            "active_nav": "events",
            "current_utc_label": _current_utc_label(),
        },
    )


def _load_overview(request: Request) -> OverviewViewModel:
    session_factory = cast(sessionmaker[Session], request.app.state.session_factory)
    scheduler = getattr(request.app.state, "scheduler", None)
    with session_factory() as session:
        return load_overview_view_model(session, scheduler=scheduler)


def _current_utc_label() -> str:
    return datetime.now(UTC).strftime("UTC %H:%M:%S")


def _elapsed_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1000, 3)


def _log_overview_rendered(
    *,
    view_model: OverviewViewModel,
    elapsed_ms: float,
    partial: bool,
) -> None:
    captured_at = view_model.captured_at.isoformat() if view_model.captured_at is not None else None
    LOGGER.info(
        "ui_overview_rendered",
        captured_at=captured_at,
        elapsed_ms=elapsed_ms,
        partial=partial,
    )
