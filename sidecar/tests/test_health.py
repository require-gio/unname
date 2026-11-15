"""Health and diagnostic endpoint."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from presidio_sidecar import PROTOCOL_VERSION, __version__
from presidio_sidecar.app import create_app
from presidio_sidecar.config import Settings
from presidio_sidecar.diagnostics import DiagnosticsRecorder
from presidio_sidecar.services import PhaseOneServices
from presidio_sidecar.workers import WorkerCapacity

TOKEN = "test-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class NoGpuBackend:
    def cuda_devices(self) -> tuple[()]:
        return ()

    def mps_available(self) -> bool:
        return False

    def test_inference(self, device: str) -> str:
        raise AssertionError(f"Unerwartete Probe auf {device}")


@pytest.fixture
def recorder() -> DiagnosticsRecorder:
    return DiagnosticsRecorder()


@pytest.fixture
def client(recorder: DiagnosticsRecorder, tmp_path: Path) -> TestClient:
    settings = Settings(token=TOKEN, state_directory=tmp_path)
    services = PhaseOneServices(
        settings,
        recorder,
        hardware_backend=NoGpuBackend(),
    )
    return TestClient(create_app(settings, recorder, phase_one=services))


def test_health_reports_version_and_protocol(client: TestClient) -> None:
    body = client.get("/health", headers=AUTH).json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["protocol"] == PROTOCOL_VERSION
    assert body["components"]["presidio-analyzer"] is not None


def test_health_does_not_claim_a_loaded_model(client: TestClient) -> None:
    """Phase 0 runs no detection; the report must reflect that."""
    body = client.get("/health", headers=AUTH).json()
    assert body["nlp_engine"]["loaded"] is False


def test_health_claims_no_gpu_without_test_inference(client: TestClient) -> None:
    """torch.cuda.is_available() is not sufficient proof (spec §4)."""
    device = client.get("/health", headers=AUTH).json()["device"]
    assert device["active"] == "cpu"
    assert device["probe"] == "not_run"


def test_presidio_device_reported_from_cached_start_state(
    tmp_path: Path,
) -> None:
    settings = Settings(
        token=TOKEN,
        state_directory=tmp_path,
        presidio_device="mps",
    )
    recorder = DiagnosticsRecorder()
    services = PhaseOneServices(
        settings,
        recorder,
        hardware_backend=NoGpuBackend(),
    )
    # The device probe runs at startup (lifespan) rather than on a health request.
    # The endpoint reports the already-checked finding instead of probing again.
    with TestClient(create_app(settings, recorder, phase_one=services)) as custom_client:
        response = custom_client.get("/health", headers=AUTH)
    device = response.json()["device"]

    assert response.status_code == 200
    assert device["requested"] == "mps"
    assert device["active"] == "cpu"
    assert device["probe"] == "failed"
    assert device["fallback_from"] == "mps"


def test_health_reports_effective_workers_not_only_auto(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Tesseract uses a serial processing path; Vision uses its own path.
    monkeypatch.setenv("PRESIDIO_OCR_ENGINE", "tesseract")
    settings = Settings(
        token=TOKEN,
        state_directory=tmp_path,
        cpu_workers=3,
    )
    recorder = DiagnosticsRecorder()
    services = PhaseOneServices(
        settings,
        recorder,
        capacity=WorkerCapacity(
            cpu_workers=1,
            ocr_workers=0,
            logical_cores=2,
            memory_bytes=8 * 1024**3,
        ),
        hardware_backend=NoGpuBackend(),
    )
    custom_client = TestClient(create_app(settings, recorder, phase_one=services))

    workers = custom_client.get("/health", headers=AUTH).json()["workers"]

    assert workers["cpu_workers"] == 3
    assert workers["ocr_workers"] == 0
    assert workers["ocr_concurrency"] == 1


def test_diagnostics_returns_recorded_entries(
    client: TestClient, recorder: DiagnosticsRecorder
) -> None:
    recorder.record("mps_fallback", "MPS-Kurztest fehlgeschlagen.", severity="warning", to="cpu")
    body = client.get("/diagnostics", headers=AUTH).json()
    assert body["count"] == 1
    assert body["fallbacks"] == 1
    entry = body["entries"][0]
    assert entry["category"] == "mps_fallback"
    assert entry["severity"] == "warning"
    assert entry["details"] == {"to": "cpu"}
    assert entry["source"] == "sidecar"


def test_diagnostics_is_initially_empty(client: TestClient) -> None:
    assert client.get("/diagnostics", headers=AUTH).json() == {
        "entries": [],
        "count": 0,
        "fallbacks": 0,
    }


def test_health_responds_while_a_service_is_being_built(
    recorder: DiagnosticsRecorder,
    tmp_path: Path,
) -> None:
    """A busy service is not a broken service.

    Previously every health check shared the same lock used for registry and model
    loading. The first request hit ``/health`` while the sidecar was still starting,
    causing the shell to mark the sidecar as dead and abort the in-flight job. On the
    second attempt the startup had already completed, so the check succeeded.
    """

    settings = Settings(token=TOKEN, state_directory=tmp_path)
    services = PhaseOneServices(settings, recorder, hardware_backend=NoGpuBackend())
    client = TestClient(create_app(settings, recorder, phase_one=services))
    # After startup the device selection is set; the rest of the initialization is in progress.
    # The live health check arrives exactly at this point.
    services.device_selection()

    with services._lock:
        response = client.get("/health", headers=AUTH)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_reports_warmup_as_busy_not_as_failure(
    recorder: DiagnosticsRecorder,
    tmp_path: Path,
) -> None:
    settings = Settings(token=TOKEN, state_directory=tmp_path)
    services = PhaseOneServices(settings, recorder, hardware_backend=NoGpuBackend())
    client = TestClient(create_app(settings, recorder, phase_one=services))

    payload = client.get("/health", headers=AUTH).json()

    assert payload["warming_up"] is False
    assert payload["status"] == "ok"


def test_health_reports_ocr(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """``ocr`` reports engine and state without spawning a child process."""

    monkeypatch.setenv("PRESIDIO_OCR_ENGINE", "apple-vision")
    ocr = client.get("/health", headers=AUTH).json()["ocr"]

    assert ocr["engine"] == "apple-vision"
    assert ocr["state"] in {"ready", "warming", "missing"}
    assert ocr["request"] == "text"
    assert ocr["revision"] == 3
    assert ocr["languages"] == ["de-DE", "en-US"]

    monkeypatch.setenv("PRESIDIO_OCR_ENGINE", "tesseract")
    ocr = client.get("/health", headers=AUTH).json()["ocr"]
    assert ocr["engine"] == "tesseract"
    assert ocr["languages"] == ["deu", "eng"]


def test_idle_connections_survive_the_shell_heartbeat() -> None:
    """uvicorn closed after 5 s; the shell pings every 5 s and drops idle connections after 2 s
    (``app/src-tauri/src/sidecar.rs``)."""

    from presidio_sidecar.__main__ import KEEP_ALIVE_SECONDS

    hull_health_interval_s = 5
    hull_pool_idle_timeout_s = 2
    assert hull_health_interval_s < KEEP_ALIVE_SECONDS
    assert hull_pool_idle_timeout_s < KEEP_ALIVE_SECONDS
