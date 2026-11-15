"""HTTP contract for detection: Health, Fail-Closed and Offsets (API v2).

Earlier versions used ``POST /jobs``; now files are submitted as an entry
(``POST /entries/{id}/files``).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from presidio_sidecar.analysis import PlainTextAnalyzer
from presidio_sidecar.app import create_app
from presidio_sidecar.config import Settings
from presidio_sidecar.detection import DetectionPipeline
from presidio_sidecar.diagnostics import DiagnosticsRecorder
from presidio_sidecar.registry import MasterDataRegistry
from presidio_sidecar.registry_crypto import generate_registry_key
from presidio_sidecar.services import PhaseOneServices
from presidio_sidecar.workers import WorkerCapacity

TOKEN = "phase-one-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class EmptyNer:
    detector_name = "test-empty"
    loaded = True

    def detect(self, text: str):  # noqa: ANN201
        return ()


class LeakingFailingNer:
    detector_name = "test-failing"

    def detect(self, text: str):  # noqa: ANN201
        raise RuntimeError(f"NER-Ausfall bei vertraulichem Text: {text}")


@pytest.fixture
def api(tmp_path: Path) -> Iterator[tuple[TestClient, PhaseOneServices]]:
    settings = Settings(
        token=TOKEN,
        state_directory=tmp_path / "state",
        job_ttl_seconds=30,
        max_text_bytes=64 * 1024,
    )
    registry = MasterDataRegistry(
        tmp_path / "registry.enc",
        generate_registry_key(),
    )
    recorder = DiagnosticsRecorder()
    analyzer = PlainTextAnalyzer(
        registry,
        semantic_pipeline=DetectionPipeline(ner=EmptyNer()),
        diagnostics=recorder,
    )
    services = PhaseOneServices(
        settings,
        recorder,
        registry=registry,
        analyzer=analyzer,
        capacity=WorkerCapacity(1, 0, 2, 8 * 1024**3),
    )
    with TestClient(create_app(settings, recorder, phase_one=services)) as client:
        yield client, services


def _submit(client: TestClient, name: str, content: str) -> tuple[str, str]:
    entry = client.post("/entries", headers=AUTH, json={"kind": "documents"})
    assert entry.status_code == 201, entry.text
    entry_id = entry.json()["id"]
    response = client.post(
        f"/entries/{entry_id}/files",
        headers=AUTH,
        files=[("file", (name, content.encode(), "text/plain"))],
    )
    assert response.status_code == 202, response.text
    accepted = response.json()["accepted"]
    assert len(accepted) == 1
    return entry_id, accepted[0]["id"]


def _wait_for_file(client: TestClient, entry_id: str) -> dict:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get(f"/entries/{entry_id}/progress", headers=AUTH)
        assert response.status_code == 200
        payload = response.json()
        if payload["done"] == payload["total"]:
            return payload["files"][0]
        time.sleep(0.01)
    raise AssertionError("Erkennung wurde nicht fertig.")


def test_health_returns_registry_and_worker_data(
    api: tuple[TestClient, PhaseOneServices],
) -> None:
    client, _ = api

    health = client.get("/health", headers=AUTH).json()

    assert health["master_data"]["total_count"] == 0
    assert health["master_data"]["updated_at"] is None
    assert health["workers"]["cpu_workers"] == 1
    assert health["workers"]["performance_cores"] >= 1
    assert health["busy"] is False
    assert health["nlp_engine"]["active"] == "test-empty"


def test_old_endpoints_no_longer_exist(
    api: tuple[TestClient, PhaseOneServices],
) -> None:
    """The old endpoints (profile, master data import, jobs, export, rollback) are gone."""

    client, _ = api
    for method, path in (
        ("GET", "/profiles"),
        ("GET", "/master-data/status"),
        ("POST", "/master-data/import"),
        ("POST", "/master-data/reload"),
        ("POST", "/jobs"),
        ("GET", "/jobs/irgendein-job"),
        ("POST", "/jobs/irgendein-job/export"),
        ("POST", "/deanonymize"),
    ):
        response = client.request(method, path, headers=AUTH, json={"text": "Hans Meier"})
        assert response.status_code in {404, 405}, f"{method} {path}"


def test_api_offsets_use_utf16_for_webview(
    api: tuple[TestClient, PhaseOneServices],
) -> None:
    client, _ = api
    text = "😀 Gebrüder Wachtelhuber"
    entry_id, job_id = _submit(client, "notiz.txt", text)
    assert _wait_for_file(client, entry_id)["status"] == "review"

    body = client.get(f"/jobs/{job_id}/findings", headers=AUTH).json()
    finding = body["suspicious"][0]
    python_start = text.index("Gebrüder")
    python_end = len(text)

    assert body["offset_encoding"] == "utf-16"
    assert finding["start"] == len(text[:python_start].encode("utf-16-le")) // 2
    assert finding["end"] == len(text[:python_end].encode("utf-16-le")) // 2
    assert finding["context"]["match"] == "Gebrüder Wachtelhuber"


def test_endpoints_remain_locked_without_token(
    api: tuple[TestClient, PhaseOneServices],
) -> None:
    client, _ = api

    assert client.get("/entries").status_code == 401
    assert client.post("/entries", json={"kind": "documents"}).status_code == 401
    assert client.get("/jobs/irgendein-job/findings").status_code == 401


def test_broken_configured_initial_import_locks_health_and_submitting(
    tmp_path: Path,
) -> None:
    source = tmp_path / "kaputt.json"
    source.write_text("{kein json", encoding="utf-8")
    settings = Settings(
        token=TOKEN,
        state_directory=tmp_path / "state",
        master_data_import_path=source,
    )
    recorder = DiagnosticsRecorder()
    registry = MasterDataRegistry(
        tmp_path / "registry.enc",
        generate_registry_key(),
    )
    analyzer = PlainTextAnalyzer(
        registry,
        semantic_pipeline=DetectionPipeline(ner=EmptyNer()),
        diagnostics=recorder,
    )
    services = PhaseOneServices(
        settings,
        recorder,
        registry=registry,
        analyzer=analyzer,
        capacity=WorkerCapacity(1, 0, 2, 8 * 1024**3),
    )

    with TestClient(create_app(settings, recorder, phase_one=services)) as client:
        health = client.get("/health", headers=AUTH)
        entry = client.post("/entries", headers=AUTH, json={"kind": "documents"})
        created = client.post(
            f"/entries/{entry.json()['id']}/files",
            headers=AUTH,
            files=[("file", ("notiz.txt", "Gebrüder Wachtelhuber".encode(), "text/plain"))],
        )

    assert health.status_code == 503
    assert health.json()["master_data"]["usable"] is False
    assert created.status_code == 503


def test_ner_total_failure_returns_no_findings_and_reflects_no_text(
    tmp_path: Path,
) -> None:
    secret = "GEHEIMER NAME MAX MUSTERMANN"
    settings = Settings(
        token=TOKEN,
        state_directory=tmp_path / "state",
        job_ttl_seconds=30,
    )
    recorder = DiagnosticsRecorder()
    registry = MasterDataRegistry(
        tmp_path / "registry.enc",
        generate_registry_key(),
    )
    analyzer = PlainTextAnalyzer(
        registry,
        semantic_pipeline=DetectionPipeline(ner=LeakingFailingNer()),
        diagnostics=recorder,
    )
    services = PhaseOneServices(
        settings,
        recorder,
        registry=registry,
        analyzer=analyzer,
        capacity=WorkerCapacity(1, 0, 2, 8 * 1024**3),
    )

    with TestClient(create_app(settings, recorder, phase_one=services)) as client:
        entry_id, job_id = _submit(client, "geheim.txt", secret)
        status = _wait_for_file(client, entry_id)
        findings = client.get(f"/jobs/{job_id}/findings", headers=AUTH)
        progress = client.get(f"/entries/{entry_id}/progress", headers=AUTH)
        diagnostics = client.get("/diagnostics", headers=AUTH)

    assert status["status"] == "failed"
    assert secret not in str(status)
    assert findings.status_code == 409
    assert secret not in findings.text
    assert secret not in progress.text
    assert secret not in diagnostics.text
