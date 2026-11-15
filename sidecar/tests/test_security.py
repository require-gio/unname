"""The token protects every endpoint—not just the ones people think of."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from presidio_sidecar.app import create_app
from presidio_sidecar.config import Settings

TOKEN = "test-token-0123456789"


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Settings(token=TOKEN)))


@pytest.mark.parametrize("path", ["/health", "/diagnostics"])
def test_without_token_rejected(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "header",
    [
        "",
        "Bearer",
        "Bearer ",
        "Basic " + TOKEN,
        "Bearer falsches-token",
        # Prefix of the correct token must not be accepted.
        "Bearer " + TOKEN[:-1],
    ],
)
def test_wrong_header_rejected(client: TestClient, header: str) -> None:
    response = client.get("/health", headers={"Authorization": header})
    assert response.status_code == 401


def test_with_token_allowed(client: TestClient) -> None:
    response = client.get("/health", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200


def test_all_routes_protected() -> None:
    """Protection against a later added unprotected route."""
    app = create_app(Settings(token=TOKEN))
    client = TestClient(app)
    paths = {
        route.path
        for route in app.routes
        if getattr(route, "methods", None) and "GET" in route.methods
    }
    assert paths, "Keine Routen gefunden — der Test wuerde nichts pruefen."
    for path in paths:
        assert client.get(path).status_code == 401, f"{path} ist ohne Token erreichbar"
