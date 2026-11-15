"""Lazy services, nightly import and hardware selection."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import presidio_sidecar.services as services_module
from presidio_sidecar.analysis import PlainTextAnalyzer
from presidio_sidecar.config import Settings
from presidio_sidecar.detection import (
    DetectionPipeline,
    FallbackNerAdapter,
    FindingSource,
    GLiNERNerAdapter,
    NerModelUnavailableError,
)
from presidio_sidecar.diagnostics import DiagnosticsRecorder
from presidio_sidecar.extraction import LimitedOcrBackend
from presidio_sidecar.hardware import GIB, GpuCapability
from presidio_sidecar.profiles import ProfileRuleViolation, load_profile
from presidio_sidecar.registry import MasterDataRegistry, RegistrySources
from presidio_sidecar.registry_crypto import generate_registry_key
from presidio_sidecar.services import PhaseOneServices
from presidio_sidecar.workers import WorkerCapacity


def _capacity(memory_gib: int) -> WorkerCapacity:
    return WorkerCapacity(
        cpu_workers=1,
        ocr_workers=0,
        logical_cores=2,
        memory_bytes=memory_gib * 1024**3,
    )


class EmptyNer:
    detector_name = "test-empty"

    def detect(self, text: str):  # noqa: ANN201
        return ()


class FakeGpuBackend:
    def __init__(self, *, actual_device: str = "cuda:0") -> None:
        self.actual_device = actual_device
        self.probe_calls: list[str] = []

    def cuda_devices(self) -> tuple[GpuCapability, ...]:
        return (
            GpuCapability(
                device="cuda:0",
                name="Test NVIDIA",
                vram_bytes=8 * GIB,
            ),
        )

    def mps_available(self) -> bool:
        return False

    def test_inference(self, device: str) -> str:
        self.probe_calls.append(device)
        return self.actual_device


def test_health_status_creates_no_key_without_data(tmp_path: Path) -> None:
    services = PhaseOneServices(
        Settings(state_directory=tmp_path),
        DiagnosticsRecorder(),
        capacity=_capacity(4),
    )

    assert services.master_data_status()["total_count"] == 0
    assert not services.key_path.exists()
    assert services.nlp_status()["selected"] == "de_core_news_lg"


def test_registry_initialization_creates_private_key_file(tmp_path: Path) -> None:
    services = PhaseOneServices(
        Settings(state_directory=tmp_path),
        DiagnosticsRecorder(),
        capacity=_capacity(16),
    )

    first = services.master_data()
    second = services.master_data()

    assert first is second
    assert services.key_path.is_file()
    if os.name == "posix":
        assert services.key_path.stat().st_mode & 0o777 == 0o600
        assert tmp_path.stat().st_mode & 0o777 == 0o700
    assert services.nlp_status()["selected"] == "gliner"


def test_verified_gpu_is_cached_and_gliner_stays_on_cpu(
    tmp_path: Path,
) -> None:
    backend = FakeGpuBackend()
    services = PhaseOneServices(
        Settings(state_directory=tmp_path),
        DiagnosticsRecorder(),
        capacity=_capacity(16),
        hardware_backend=backend,
    )
    try:
        first = services.device_status()
        second = services.device_status()
        ner = services.analyzer().semantic_pipeline.ner

        assert first == second
        assert first["active"] == "cuda:0"
        assert first["probe"] == "ok"
        assert backend.probe_calls == ["cuda:0"]
        assert isinstance(ner, FallbackNerAdapter)
        assert isinstance(ner.primary, GLiNERNerAdapter)
        # E-Y1: GLiNER runs ONNX on CPU even when GPU is available.
        assert ner.primary.device == "cpu"
        assert services.nlp_status()["active"] == "gliner"
    finally:
        services.close()


def test_later_gpu_model_failure_corrects_health_and_stays_cpu_runnable(
    tmp_path: Path,
) -> None:
    backend = FakeGpuBackend()
    recorder = DiagnosticsRecorder()
    services = PhaseOneServices(
        Settings(state_directory=tmp_path),
        recorder,
        capacity=_capacity(16),
        hardware_backend=backend,
    )
    try:
        ner = services.analyzer().semantic_pipeline.ner
        assert isinstance(ner, FallbackNerAdapter)
        assert isinstance(ner.primary, GLiNERNerAdapter)

        def broken_gliner(model_name: str, device: str):  # noqa: ANN202
            raise RuntimeError(f"Lokales Modell auf {device} nicht nutzbar.")

        ner.primary._loader = broken_gliner
        ner.fallback._loader = lambda _: lambda text: SimpleNamespace(ents=())

        assert ner.detect("Anna Muster in Wien") == ()
        device = services.device_status()

        assert device["active"] == "cpu"
        assert device["probe"] == "failed"
        assert device["fallback_from"] == "cuda:0"
        assert [entry.category for entry in recorder.entries()].count("gpu_fallback") == 1
    finally:
        services.close()


def test_missing_model_snapshot_leaves_verified_gpu_untouched(
    tmp_path: Path,
) -> None:
    """A missing artifact is not a hardware finding.

    The GPU inference succeeded; only the local GLiNER snapshot is missing.
    If reported as a GPU fallback, the device line shows "CPU mode" even though the GPU
    is ready, hiding the real cause (spec §4, 5).
    """

    backend = FakeGpuBackend()
    recorder = DiagnosticsRecorder()
    services = PhaseOneServices(
        Settings(state_directory=tmp_path),
        recorder,
        capacity=_capacity(16),
        hardware_backend=backend,
    )
    try:
        ner = services.analyzer().semantic_pipeline.ner
        assert isinstance(ner, FallbackNerAdapter)
        assert isinstance(ner.primary, GLiNERNerAdapter)

        def missing_snapshot(model_name: str, device: str):  # noqa: ANN202
            raise NerModelUnavailableError(f"Kein lokaler Snapshot fuer '{model_name}'")

        ner.primary._loader = missing_snapshot
        ner.fallback._loader = lambda _: lambda text: SimpleNamespace(ents=())

        assert ner.detect("Anna Muster in Wien") == ()
        device = services.device_status()

        assert device["active"] == "cuda:0"
        assert device["probe"] == "ok"
        assert device["fallback_from"] is None
        categories = [entry.category for entry in recorder.entries()]
        assert "gpu_fallback" not in categories
        assert categories.count("model_unavailable") == 1
        # The active engine reports that it runs on the CPU.
        assert services.nlp_status()["active"] == "de_core_news_lg"
        assert services.nlp_status()["device"] == "cpu"
    finally:
        services.close()


def test_warmup_probes_ner_adapter_after_ready_and_falls_back_to_cpu(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenGpuNer:
        detector_name = "gliner"
        loaded = False

        def __init__(self) -> None:
            self.calls: list[str] = []

        def detect(self, text: str):  # noqa: ANN201
            self.calls.append(text)
            raise RuntimeError("GPU-Modellprobe fehlgeschlagen.")

    class WorkingCpuNer:
        detector_name = "de_core_news_lg"
        loaded = True

        def detect(self, text: str):  # noqa: ANN201
            return ()

    primary = BrokenGpuNer()

    def fake_factory(  # noqa: ANN202
        hardware,
        *,
        thresholds,
        on_fallback,
        **kwargs,
    ):
        assert hardware.active_device == "cpu"
        return FallbackNerAdapter(
            primary,
            WorkingCpuNer(),
            on_fallback=on_fallback,
        )

    monkeypatch.setattr(services_module, "build_ner_adapter", fake_factory)
    recorder = DiagnosticsRecorder()
    services = PhaseOneServices(
        Settings(state_directory=tmp_path),
        recorder,
        capacity=_capacity(16),
        hardware_backend=FakeGpuBackend(),
    )
    monkeypatch.setattr(services_module, "setup_required", lambda *_: False)
    try:
        services.start()

        # No model is loaded before the handshake: history and storage
        # otherwise we would wait for GLiNER.
        assert primary.calls == []
        assert services.device_status()["active"] == "cuda:0"

        services.start_warmup()
        assert services._warmup is not None
        services._warmup.join(timeout=5)
        device = services.device_status()

        assert primary.calls == ["Anna Beispiel arbeitet in Wien."]
        assert device["active"] == "cpu"
        assert device["probe"] == "failed"
        assert device["fallback_reason"] == "model_inference_failed"
        assert [entry.category for entry in recorder.entries()].count("gpu_fallback") == 1
    finally:
        services.close()


def test_worker_status_reports_cli_override_and_serial_ocr_truthfully(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PRESIDIO_OCR_ENGINE", "tesseract")
    services = PhaseOneServices(
        Settings(state_directory=tmp_path, cpu_workers=3),
        DiagnosticsRecorder(),
        capacity=_capacity(8),
        hardware_backend=FakeGpuBackend(actual_device="cpu"),
    )

    status = services.worker_status()

    assert status["cpu_workers"] == 3
    assert status["ocr_workers"] == 0
    assert status["ocr_concurrency"] == 1
    assert isinstance(services.extractor().ocr_backend, LimitedOcrBackend)
    assert services.extractor().ocr_backend.max_concurrency == 1


def test_vision_gets_two_ocr_tracks_from_eight_gb(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two parallel Vision calls (measured 1.5× throughput)."""

    monkeypatch.setenv("PRESIDIO_OCR_ENGINE", "apple-vision")
    services = PhaseOneServices(
        Settings(state_directory=tmp_path),
        DiagnosticsRecorder(),
        capacity=_capacity(8),
        hardware_backend=FakeGpuBackend(actual_device="cpu"),
    )

    assert services.worker_status()["ocr_concurrency"] == 2
    backend = services.extractor().ocr_backend
    assert isinstance(backend, LimitedOcrBackend)
    assert backend.max_concurrency == 2
    assert backend.engine_name == "apple-vision"


def test_new_system_version_appears_in_diagnosis(tmp_path: Path) -> None:
    recorder = DiagnosticsRecorder()
    services = PhaseOneServices(Settings(state_directory=tmp_path), recorder, capacity=_capacity(8))
    services._note_ocr_system("26B123", "1.0.0")
    services._note_ocr_system("26B123", "1.0.0")
    assert not [entry for entry in recorder.entries() if entry.category == "ocr"]

    services._note_ocr_system("26C5", "1.0.0")
    entries = [entry for entry in recorder.entries() if entry.category == "ocr"]
    assert [entry.message for entry in entries] == ["Texterkennung: neue Systemversion"]
    assert entries[0].details == {"previous_os_build": "26B123", "os_build": "26C5"}


def test_start_creates_vault_secret_once_and_without_mapping(tmp_path: Path) -> None:
    state_directory = tmp_path / "state"
    first = PhaseOneServices(
        Settings(state_directory=state_directory),
        DiagnosticsRecorder(),
        capacity=_capacity(8),
    )
    first.start()
    secret_path = first.vault().secret_path
    mapping_path = first.vault().mapping_path
    secret = secret_path.read_bytes()
    first.close()

    restarted = PhaseOneServices(
        Settings(state_directory=state_directory),
        DiagnosticsRecorder(),
        capacity=_capacity(8),
    )
    try:
        restarted.start()
        assert restarted.vault().secret_path.read_bytes() == secret
        assert not mapping_path.exists()
        if os.name == "posix":
            assert secret_path.stat().st_mode & 0o777 == 0o600
    finally:
        restarted.close()


def test_configured_ner_thresholds_reach_analysis_pipeline(
    tmp_path: Path,
) -> None:
    services = PhaseOneServices(
        Settings(
            state_directory=tmp_path,
            ner_thresholds=(
                ("PERSON", 0.21),
                ("ORGANIZATION", 0.82),
                ("LOCATION", 0.63),
            ),
        ),
        DiagnosticsRecorder(),
        capacity=_capacity(8),
    )
    try:
        thresholds = services.analyzer().semantic_pipeline.thresholds

        assert thresholds.for_entity("PERSON") == 0.21
        assert thresholds.for_entity("ORGANIZATION") == 0.82
        assert thresholds.for_entity("LOCATION") == 0.63
    finally:
        services.close()


def test_combined_nightly_json_is_imported_on_start(tmp_path: Path) -> None:
    source = tmp_path / "nightly.json"
    source.write_text(
        json.dumps(
            {
                "persons": ["Josef Brandstetter"],
                "organizations": ["Tischlerei Brandstetter e.U."],
            }
        ),
        encoding="utf-8",
    )
    services = PhaseOneServices(
        Settings(
            state_directory=tmp_path / "state",
            master_data_import_path=source,
            master_data_poll_seconds=0.05,
        ),
        DiagnosticsRecorder(),
        capacity=_capacity(8),
    )
    try:
        services.start()
        status = services.master_data_status()
        assert status["persons_count"] == 1
        assert status["organizations_count"] == 1
        assert status["last_reload_error"] is None
        assert status["usable"] is True
    finally:
        services.close()


def test_failed_initial_import_without_old_state_is_not_usable(
    tmp_path: Path,
) -> None:
    source = tmp_path / "kaputt.json"
    source.write_text("{kein json", encoding="utf-8")
    services = PhaseOneServices(
        Settings(
            state_directory=tmp_path / "state",
            master_data_import_path=source,
        ),
        DiagnosticsRecorder(),
        capacity=_capacity(8),
    )
    try:
        services.start()

        status = services.master_data_status()
        assert status["usable"] is False
        assert status["last_reload_error"] == "RegistryImportError"
    finally:
        services.close()


def test_failed_reload_may_use_last_valid_state(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "state"
    valid = tmp_path / "gueltig.json"
    valid.write_text(
        json.dumps(
            {
                "persons": ["Josef Brandstetter"],
                "organizations": ["Tischlerei Brandstetter e.U."],
            }
        ),
        encoding="utf-8",
    )
    first = PhaseOneServices(
        Settings(state_directory=state_directory),
        DiagnosticsRecorder(),
        capacity=_capacity(8),
    )
    first.master_data().reimport(RegistrySources(valid, valid))
    first.close()

    invalid = tmp_path / "kaputt.json"
    invalid.write_text("{kein json", encoding="utf-8")
    restarted = PhaseOneServices(
        Settings(
            state_directory=state_directory,
            master_data_import_path=invalid,
        ),
        DiagnosticsRecorder(),
        capacity=_capacity(8),
    )
    try:
        restarted.start()

        status = restarted.master_data_status()
        assert status["usable"] is True
        assert status["total_count"] == 2
        assert status["last_reload_error"] == "RegistryImportError"
    finally:
        restarted.close()


def test_profile_binds_own_master_data_and_min_length(
    tmp_path: Path,
) -> None:
    resources = tmp_path / "resources"
    master_data = resources / "master-data"
    master_data.mkdir(parents=True)
    (master_data / "personen.csv").write_text(
        "name\nProfil Person\nXY\n",
        encoding="utf-8",
    )
    (master_data / "firmen.csv").write_text(
        "name\nProfil Organisation GmbH\n",
        encoding="utf-8",
    )
    stopwords = resources / "stopwords"
    stopwords.mkdir()
    (stopwords / "de_sentence_start.txt").write_text(
        "Bitte\nAnbei\nSonder\n",
        encoding="utf-8",
    )
    global_registry = MasterDataRegistry(
        tmp_path / "global.enc",
        generate_registry_key(),
    )
    analyzer = PlainTextAnalyzer(
        global_registry,
        semantic_pipeline=DetectionPipeline(ner=EmptyNer()),
    )
    profile = load_profile(Path(__file__).resolve().parents[2] / "profiles/de-at-rechnung-v3.yaml")
    services = PhaseOneServices(
        Settings(state_directory=tmp_path / "state"),
        DiagnosticsRecorder(),
        registry=global_registry,
        analyzer=analyzer,
        profiles=(profile,),
        profile_resources_root=resources,
        capacity=_capacity(8),
    )
    try:
        runner = services.analyzer_for_profile(profile)
        result = runner.analyze(
            "Profil Person, Profil Organisation GmbH und XY",
            progress=lambda _: None,
        )

        assert {
            (finding.entity_type, finding.text, finding.source)
            for finding in result.confirmed
            if finding.source is FindingSource.MASTER_DATA
        } == {
            ("PERSON", "Profil Person", FindingSource.MASTER_DATA),
            (
                "ORGANIZATION",
                "Profil Organisation GmbH",
                FindingSource.MASTER_DATA,
            ),
        }
        assert (tmp_path / "state" / "master-data.de-at-rechnung-v3.registry.enc").is_file()
        suspicion = runner.analyze(
            "Sonder Hans Meier",
            progress=lambda _: None,
        )
        assert [finding.text for finding in suspicion.suspicious] == ["Hans Meier"]
    finally:
        services.close()


def _resources_without_master_data(tmp_path: Path) -> Path:
    """Profile resources with stopword list but without master-data lists."""

    stopwords_dir = Path(__file__).resolve().parents[2] / "stopwords"
    resources = tmp_path / "resources"
    (resources / "stopwords").mkdir(parents=True)
    for name in ("de_sentence_start.txt", "de_business.txt"):
        (resources / "stopwords" / name).write_text(
            (stopwords_dir / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    return resources


def test_profile_without_existing_master_data_sources_runs_without_level_zero(
    tmp_path: Path,
) -> None:
    """Missing master-data lists are the normal state after a fresh install.

    The upload must not be blocked: rules, NER and suspicion checks continue
    unchanged. The condition is still reported – diagnostics, profile catalog
    and review report name it (spec §6.2, 11).
    """

    # All other resources present except the master-data lists – exactly the
    # situation after a fresh install.
    resources = _resources_without_master_data(tmp_path)
    global_registry = MasterDataRegistry(tmp_path / "global.enc", generate_registry_key())
    analyzer = PlainTextAnalyzer(
        global_registry,
        semantic_pipeline=DetectionPipeline(ner=EmptyNer()),
    )
    profile = load_profile(Path(__file__).resolve().parents[2] / "profiles/de-at-rechnung-v3.yaml")
    diagnostics = DiagnosticsRecorder()
    services = PhaseOneServices(
        Settings(state_directory=tmp_path / "state"),
        diagnostics,
        registry=global_registry,
        analyzer=analyzer,
        profiles=(profile,),
        profile_resources_root=resources,
        capacity=_capacity(8),
    )
    try:
        assert services.analyzer_for_profile(profile) is not None

        state = services.profile_master_data_state(profile)
        assert state["enabled"] is True
        assert state["ready"] is False
        assert state["reason"] == "sources_missing"
        assert state["expected_sources"] == [
            str(resources / "master-data/personen.csv"),
            str(resources / "master-data/firmen.csv"),
        ]

        categories = [item["category"] for item in diagnostics.as_dicts()]
        assert "master_data_missing" in categories
    finally:
        services.close()


def test_unreadable_master_data_source_remains_fail_closed(tmp_path: Path) -> None:
    """Existing but unreadable lists are an error case.

    This differs from an empty device: here master-data files are present but
    cannot be used. Such a state must not be treated as “simply missing”
    (spec §2).
    """

    resources = _resources_without_master_data(tmp_path)
    # Paths exist but cannot be read as files. This distinguishes
    # “present but unusable” from “not present at all”.
    (resources / "master-data" / "personen.csv").mkdir(parents=True)
    (resources / "master-data" / "firmen.csv").mkdir(parents=True)

    global_registry = MasterDataRegistry(tmp_path / "global.enc", generate_registry_key())
    analyzer = PlainTextAnalyzer(
        global_registry,
        semantic_pipeline=DetectionPipeline(ner=EmptyNer()),
    )
    profile = load_profile(Path(__file__).resolve().parents[2] / "profiles/de-at-rechnung-v3.yaml")
    services = PhaseOneServices(
        Settings(state_directory=tmp_path / "state"),
        DiagnosticsRecorder(),
        registry=global_registry,
        analyzer=analyzer,
        profiles=(profile,),
        profile_resources_root=resources,
        capacity=_capacity(8),
    )
    try:
        with pytest.raises(ProfileRuleViolation) as captured:
            services.analyzer_for_profile(profile)
        assert captured.value.code == "master_data_unavailable"
    finally:
        services.close()
