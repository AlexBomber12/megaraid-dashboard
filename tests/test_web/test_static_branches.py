from __future__ import annotations

from pathlib import Path

import pytest
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from megaraid_dashboard.web import static
from megaraid_dashboard.web.static import CacheControlStaticFiles


async def test_cache_control_skips_header_for_non_200_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_response(_self: StaticFiles, _path: str, _scope: Scope) -> Response:
        return Response(status_code=304)

    monkeypatch.setattr(static.StaticFiles, "get_response", fake_get_response)

    static_files = CacheControlStaticFiles(directory=tmp_path, check_dir=False)
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/missing.css",
        "headers": [],
    }

    response = await static_files.get_response("missing.css", scope)

    assert response.status_code == 304
    assert "cache-control" not in {key.lower() for key in response.headers}
