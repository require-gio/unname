"""HTTP contract for the local, confirmation-required key management."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from presidio_sidecar.app import create_app
from presidio_sidecar.config import Settings

TOKEN = "vault-api-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def test_status_shows_only_fingerprint_and_no_key(tmp_path: Path) -> None:
    settings = Settings(token=TOKEN, state_directory=tmp_path / "state")
    with TestClient(create_app(settings)) as client:
        response = client.get("/vault/status", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "ready"
    assert len(body["key_fingerprint"]) == 12
    assert body["mapping_count"] == 0
    assert body["warning"]
    assert "secret" not in {key.casefold() for key in body}


def test_key_rotation_requires_authentication_and_confirmation(
    tmp_path: Path,
) -> None:
    settings = Settings(token=TOKEN, state_directory=tmp_path / "state")
    with TestClient(create_app(settings)) as client:
        before = client.get("/vault/status", headers=AUTH).json()

        assert client.post("/vault/rotate", json={"confirm_key_change": True}).status_code == 401
        refused = client.post(
            "/vault/rotate",
            headers=AUTH,
            json={"confirm_key_change": False},
        )
        unchanged = client.get("/vault/status", headers=AUTH).json()
        rotated = client.post(
            "/vault/rotate",
            headers=AUTH,
            json={"confirm_key_change": True},
        )

    assert refused.status_code == 422
    assert unchanged["key_fingerprint"] == before["key_fingerprint"]
    assert rotated.status_code == 200
    assert rotated.json()["key_fingerprint"] != before["key_fingerprint"]
