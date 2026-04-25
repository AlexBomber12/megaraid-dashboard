from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.responses import HTMLResponse

from megaraid_dashboard import __version__

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html><head><title>MegaRAID Dashboard</title></head>"
        "<body><h1>MegaRAID Dashboard</h1></body></html>"
    )


def create_app() -> FastAPI:
    app = FastAPI(title="MegaRAID Dashboard")
    app.include_router(router)
    return app
