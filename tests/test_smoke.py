from __future__ import annotations

from fastapi.testclient import TestClient

from megaraid_dashboard import __version__
from megaraid_dashboard.app import create_app


def test_health_returns_ok() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_returns_package_version() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.json()["version"] == __version__


def test_index_contains_dashboard_title() -> None:
    client = TestClient(create_app())

    response = client.get("/")

    assert response.status_code == 200
    assert "MegaRAID Dashboard" in response.text
