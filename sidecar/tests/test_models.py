"""Models from the shell's model directory, stored in ``/health``, ONNX."""

# The ``api`` fixture comes from ``test_api_v2``; as a parameter it hides the import.
# ruff: noqa: F811

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from presidio_sidecar import resources, workers
from presidio_sidecar.config import parse_args
from presidio_sidecar.detection import ner
from presidio_sidecar.detection.ner import (
    GLINER_ONNX_MIN_MEMORY_BYTES,
    NerModelUnavailableError,
    build_ner_adapter,
    select_ner_hardware,
)
from test_api_v2 import AUTH, Harness, api  # noqa: F401


@pytest.fixture
def models_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """An empty models directory, without the development environment models."""

    root = tmp_path / "models"
    root.mkdir()
    monkeypatch.setenv(resources.MODELS_STRICT_ENV, "1")
    monkeypatch.delenv("PRESIDIO_BUNDLE_ASSETS_DIR", raising=False)
    monkeypatch.setattr(resources, "bundled_assets_root", lambda: None)
    # A custom-built Tesseract in the real repository does not count here.
    monkeypatch.setattr(resources, "repository_root", lambda: tmp_path / "repo")
    resources.use_models_directory(root)
    yield root
    resources.use_models_directory(None)


def install(root: Path, component: str, files: dict[str, bytes]) -> Path:
    """Like the shell: files, then the marker."""

    folder = root / component
    for name, content in files.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(content)
    (folder / resources.INSTALL_MARKER).write_text(
        json.dumps({"component": component, "revision": "test", "installed_at": "x"}),
        encoding="utf-8",
    )
    return folder


def test_only_folders_with_marker_count_as_installed(models_dir: Path) -> None:
    (models_dir / "ner").mkdir()
    (models_dir / "ner" / resources.GLINER_ONNX_FILE).write_bytes(b"halb")
    assert resources.installed_component("ner") is None, "ohne Marke nicht installiert"

    (models_dir / "ner" / resources.INSTALL_MARKER).write_text('{"component": "de"}')
    assert resources.installed_component("ner") is None, "fremde Marke"
    (models_dir / "ner" / resources.INSTALL_MARKER).write_text("kein json")
    assert resources.installed_component("ner") is None

    install(models_dir, "ner", {resources.GLINER_ONNX_FILE: b"onnx"})
    assert resources.installed_component("ner") == models_dir / "ner"
    assert resources.installed_component("../ner") is None


def test_without_models_setup_is_required(
    models_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(resources.OCR_ENGINE_ENV, "tesseract")
    assert resources.model_components() == {
        "ner": {"state": "missing", "source": "download"},
        "de": {"state": "missing", "source": "download"},
        "en": {"state": "missing", "source": "download"},
        "ocr": {"state": "missing", "source": "download"},
        "classifier": {"state": "missing", "source": "download"},
    }
    assert resources.setup_required()

    install(models_dir, "ner", {resources.GLINER_ONNX_FILE: b"onnx"})
    assert resources.setup_required(), "Namenserkennung allein genuegt nicht"
    install(models_dir, "de", {"config.cfg": b"[nlp]"})
    assert not resources.setup_required()
    assert resources.model_components()["ner"] == {"state": "ready", "source": "download"}
    # Without a model the reference points to the model directory – loading fails
    # only when processing the first document, not during initialization.
    assert resources.spacy_model_reference() == str(models_dir / "de")
    assert resources.spacy_model_reference("en") == str(models_dir / "en")


def test_loaded_models_take_precedence_over_development_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(resources, "bundled_assets_root", lambda: None)
    development = tmp_path / "dev-gliner"
    development.mkdir()
    monkeypatch.setenv("PRESIDIO_GLINER_MODEL_DIR", str(development))
    resources.use_models_directory(tmp_path / "models")
    try:
        assert resources.gliner_location() == resources.ComponentLocation(development, "local")
        installed = install(tmp_path / "models", "ner", {resources.GLINER_ONNX_FILE: b"x"})
        assert resources.gliner_location() == resources.ComponentLocation(installed, "download")
        assert resources.gliner_onnx_file(resources.gliner_model_reference()) == "model.int8.onnx"
        monkeypatch.setenv(resources.MODELS_STRICT_ENV, "1")
        (installed / resources.INSTALL_MARKER).unlink()
        assert resources.gliner_location().path is None, "streng: keine Entwicklungsmodelle"
    finally:
        resources.use_models_directory(None)


def test_ocr_uses_loaded_language_data_and_program_from_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(resources.OCR_ENGINE_ENV, "tesseract")
    bundle = tmp_path / "assets"
    program = (
        bundle / "tesseract" / "bin" / ("tesseract.exe" if sys.platform == "win32" else "tesseract")
    )
    program.parent.mkdir(parents=True)
    program.write_bytes(b"#!")
    monkeypatch.setattr(resources, "bundled_assets_root", lambda: bundle)
    resources.use_models_directory(tmp_path / "models")
    try:
        assert resources.tessdata_location().path is None
        with pytest.raises(resources.BundledAssetError):
            resources.tesseract_bundle()

        ocr_files = install(
            tmp_path / "models", "ocr", {"deu.traineddata": b"d", "eng.traineddata": b"e"}
        )
        assert resources.tessdata_location() == resources.ComponentLocation(ocr_files, "download")
        assert resources.tesseract_bundle() == resources.TesseractBundle(program, ocr_files)
    finally:
        resources.use_models_directory(None)


def test_missing_ocr_states_reason_in_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(resources.OCR_ENGINE_ENV, "tesseract")
    bundle = tmp_path / "assets"
    monkeypatch.setattr(resources, "bundled_assets_root", lambda: bundle)
    resources.use_models_directory(tmp_path / "models")
    try:
        assert "Tesseract fehlt im Paket" in (resources.ocr_missing_reason() or "")

        program = (
            bundle
            / "tesseract"
            / "bin"
            / ("tesseract.exe" if sys.platform == "win32" else "tesseract")
        )
        program.parent.mkdir(parents=True)
        program.write_bytes(b"#!")
        assert "Sprachdaten" in (resources.ocr_missing_reason() or "")

        install(tmp_path / "models", "ocr", {"deu.traineddata": b"d", "eng.traineddata": b"e"})
        assert resources.ocr_missing_reason() is None
    finally:
        resources.use_models_directory(None)


def test_missing_ocr_states_reason_in_development(
    models_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(resources.OCR_ENGINE_ENV, "tesseract")
    monkeypatch.setattr(resources.shutil, "which", lambda _name: None)
    assert "brew install tesseract" in (resources.ocr_missing_reason() or "")

    monkeypatch.setattr(resources.shutil, "which", lambda _name: "/usr/local/bin/tesseract")
    assert "Sprachdaten" in (resources.ocr_missing_reason() or ""), (
        "streng: Systemdaten zaehlen nicht"
    )

    install(models_dir, "ocr", {"deu.traineddata": b"d", "eng.traineddata": b"e"})
    assert resources.ocr_missing_reason() is None


def test_bundle_without_models_is_valid() -> None:
    # Since phase I only OCR, profiles and word lists are required; since phase U the Vision helper
    # is provided by the Mac.
    assert frozenset({"tesseract"}) == resources._required_external_assets("win32")
    assert frozenset() == resources._required_external_assets("darwin")
    assert "vision_ocr" in resources._required_internal_assets("darwin")
    assert "gliner" in resources._OPTIONAL_EXTERNAL_ASSETS


def test_vision_states_reason_when_helper_is_missing(
    models_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On macOS the OCR is part of the system."""

    from presidio_sidecar.extraction import ocr_vision

    # Frozen builds choose OCR by platform and ignore the development override.
    monkeypatch.setattr(resources.sys, "platform", "darwin")
    monkeypatch.setenv(resources.OCR_ENGINE_ENV, "apple-vision")
    monkeypatch.delenv(resources.VISION_OCR_ENV, raising=False)
    ocr_vision.reset_probe()
    assert resources.model_components()["ocr"] == {"state": "missing", "source": "system"}
    assert resources.VISION_OCR_BUILD_COMMAND in (resources.ocr_missing_reason() or "")

    monkeypatch.setattr(resources.sys, "frozen", True, raising=False)
    try:
        assert resources.ocr_missing_reason() == "Der Vision-Helfer fehlt im Paket."
    finally:
        monkeypatch.delattr(resources.sys, "frozen", raising=False)


def test_vision_is_ready_when_helper_exists_and_probe_passes(
    models_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from presidio_sidecar.extraction import ocr_vision

    helper = tmp_path / "unname-vision-ocr"
    helper.write_bytes(b"#!")
    monkeypatch.setenv(resources.OCR_ENGINE_ENV, "apple-vision")
    monkeypatch.setenv(resources.VISION_OCR_ENV, str(helper))
    ocr_vision.reset_probe()
    try:
        # Without a probe: ready as soon as the helper is present (``/health`` does not start
        # anything).
        assert resources.model_components()["ocr"] == {"state": "ready", "source": "system"}

        monkeypatch.setattr(
            ocr_vision,
            "_ask_info",
            lambda _executable: (_ for _ in ()).throw(
                ocr_vision.OcrUnavailableError(
                    "Apple Vision ist auf diesem macOS nicht verfuegbar."
                )
            ),
        )
        assert "nicht verfuegbar" in (resources.ocr_missing_reason() or "")
        assert resources.model_components()["ocr"] == {"state": "missing", "source": "system"}
    finally:
        ocr_vision.reset_probe()


def test_onnx_model_is_loaded_on_cpu(models_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = install(models_dir, "ner", {resources.GLINER_ONNX_FILE: b"onnx"})
    calls: list[dict[str, object]] = []

    class FakeRunner:
        def __init__(self, path: Path, **kwargs: object) -> None:
            calls.append({"path": path, **kwargs})

    from presidio_sidecar.detection import gliner_onnx

    monkeypatch.setattr(gliner_onnx, "GlinerOnnxModel", FakeRunner)
    # neither gliner nor PyTorch are needed.
    monkeypatch.setitem(sys.modules, "gliner", None)
    monkeypatch.setitem(sys.modules, "torch", None)
    ner._load_local_gliner(str(folder), "mps")
    options = calls[0].pop("session_options")
    assert calls == [{"path": folder, "onnx_file": "model.int8.onnx"}]
    # as many threads as performance cores, no parallelism between operators.
    assert options.intra_op_num_threads == workers.detect_performance_cores()  # type: ignore[attr-defined]
    assert options.inter_op_num_threads == 1  # type: ignore[attr-defined]

    with pytest.raises(NerModelUnavailableError):
        ner._load_local_gliner(str(models_dir / "fehlt"), "cpu")


def test_pytorch_snapshot_and_broken_onnx_count_as_not_loaded(tmp_path: Path) -> None:
    # E-Y1: A folder without ONNX (PyTorch snapshot) is no longer loaded.
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "model.safetensors").write_bytes(b"x")
    with pytest.raises(NerModelUnavailableError, match="kein ONNX"):
        ner._load_local_gliner(str(snapshot), "cpu")

    broken = tmp_path / "kaputt"
    broken.mkdir()
    (broken / resources.GLINER_ONNX_FILE).write_bytes(b"kein onnx")
    (broken / "gliner_config.json").write_text('{"max_width": 12}', encoding="utf-8")
    with pytest.raises(NerModelUnavailableError):
        ner._load_local_gliner(str(broken), "cpu")


def test_onnx_bevorzugt_gliner_schon_ab_8_gb(models_dir: Path) -> None:
    eight_gib = select_ner_hardware(8 * 1024**3, active_device="mps")
    assert (eight_gib.prefer_gliner, eight_gib.active_device) == (True, "cpu")
    four_gib = select_ner_hardware(4 * 1024**3)
    assert not four_gib.prefer_gliner
    assert GLINER_ONNX_MIN_MEMORY_BYTES < 8 * 1024**3

    install(models_dir, "ner", {resources.GLINER_ONNX_FILE: b"onnx"})
    adapter = build_ner_adapter(select_ner_hardware(8 * 1024**3))
    assert adapter.status().as_dict()["device"] == "cpu"


def test_health_reports_models_and_setup(api: Harness, models_dir: Path) -> None:
    body = api.client.get("/health", headers=AUTH).json()
    assert body["setup_required"] is True
    assert body["models"]["ner"] == {"state": "missing", "source": "download"}

    install(models_dir, "ner", {resources.GLINER_ONNX_FILE: b"onnx"})
    install(models_dir, "de", {"config.cfg": b"[nlp]"})
    body = api.client.get("/health", headers=AUTH).json()
    assert body["setup_required"] is False

    models = api.client.get("/models", headers=AUTH).json()
    assert models["setup_required"] is False
    ner_entry = next(item for item in models["components"] if item["id"] == "ner")
    assert ner_entry == {
        "id": "ner",
        "state": "ready",
        "source": "download",
        "size_bytes": 4 + (models_dir / "ner" / resources.INSTALL_MARKER).stat().st_size,
        "languages": [],
    }


def test_nothing_is_warmed_up_before_setup(api: Harness, models_dir: Path) -> None:
    api.services._warmup = None
    api.services.start_warmup()
    assert api.services._warmup is None


def test_model_folder_comes_from_the_shell(tmp_path: Path) -> None:
    settings = parse_args(["--models-dir", str(tmp_path / "models")])
    assert settings.models_directory == tmp_path / "models"
    assert parse_args([]).models_directory is None


def test_loaded_language_data_without_program_reports_missing_ocr(
    models_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Development: language data from the app but Tesseract not in PATH.
    # This is "OCR missing", not "file unreadable".
    from PIL import Image

    from presidio_sidecar.extraction import OcrUnavailableError, TesseractOcrBackend

    install(models_dir, "ocr", {"deu.traineddata": b"d", "eng.traineddata": b"e"})
    monkeypatch.setattr(resources.shutil, "which", lambda _name: None)

    with pytest.raises(OcrUnavailableError):
        TesseractOcrBackend().recognize(Image.new("RGB", (10, 10)), languages=("deu", "eng"))


def test_development_prefers_self_built_tesseract_over_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(resources.OCR_ENGINE_ENV, "tesseract")
    monkeypatch.setattr(resources, "bundled_assets_root", lambda: None)
    monkeypatch.setattr(resources, "repository_root", lambda: tmp_path)
    monkeypatch.setattr(resources.shutil, "which", lambda _name: "/opt/homebrew/bin/tesseract")
    monkeypatch.delenv(resources.MODELS_STRICT_ENV, raising=False)
    resources.use_models_directory(tmp_path / "models")
    try:
        assert resources.development_tesseract() == Path("/opt/homebrew/bin/tesseract")
        assert resources.tesseract_bundle() is None, "ohne eigenen Bau: Systemprogramm"

        built = tmp_path / ".bundle-inputs" / "tesseract"
        program = built / "bin" / ("tesseract.exe" if sys.platform == "win32" else "tesseract")
        program.parent.mkdir(parents=True)
        program.write_bytes(b"#!")
        (built / "tessdata").mkdir()
        for language in ("deu", "eng"):
            (built / "tessdata" / f"{language}.traineddata").write_bytes(b"x")

        assert resources.development_tesseract() == program
        assert resources.tesseract_bundle() == resources.TesseractBundle(
            program, built / "tessdata"
        )
        assert resources.tessdata_location() == resources.ComponentLocation(
            built / "tessdata", "local"
        )
        assert resources.ocr_missing_reason() is None

        # Language data loaded from the app takes precedence.
        ocr_files = install(
            tmp_path / "models", "ocr", {"deu.traineddata": b"d", "eng.traineddata": b"e"}
        )
        assert resources.tesseract_bundle() == resources.TesseractBundle(program, ocr_files)
    finally:
        resources.use_models_directory(None)


def test_health_inventory_is_cached_and_rescanned_on_new_marker(
    models_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(resources.OCR_ENGINE_ENV, "tesseract")
    calls = 0
    original = resources.model_components

    def counting() -> dict[str, dict[str, str]]:
        nonlocal calls
        calls += 1
        return original()

    monkeypatch.setattr(resources, "model_components", counting)

    assert resources.cached_model_components()["ner"]["state"] == "missing"
    assert resources.cached_model_components()["ner"]["state"] == "missing"
    assert calls == 1, "ohne Aenderung kein zweites Nachsehen"

    install(models_dir, "ner", {resources.GLINER_ONNX_FILE: b"onnx"})
    assert resources.cached_model_components()["ner"]["state"] == "ready"
    assert calls == 2, "neue Marke: sofort neu gesucht"

    # Like "Remove" in the shell: remove the marker first.
    (models_dir / "ner" / resources.INSTALL_MARKER).unlink()
    assert resources.cached_model_components()["ner"]["state"] == "missing"
    assert calls == 3


def test_health_inventory_goes_stale_after_ttl(
    models_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(resources.OCR_ENGINE_ENV, "tesseract")
    now = 1000.0
    monkeypatch.setattr(resources.time, "monotonic", lambda: now)
    calls = 0
    original = resources.model_components

    def counting() -> dict[str, dict[str, str]]:
        nonlocal calls
        calls += 1
        return original()

    monkeypatch.setattr(resources, "model_components", counting)

    resources.cached_model_components()
    now += resources._COMPONENTS_MAX_AGE_SECONDS - 1
    resources.cached_model_components()
    assert calls == 1
    now += 2
    resources.cached_model_components()
    assert calls == 2


def test_cached_inventory_cannot_be_modified_from_outside(models_dir: Path) -> None:
    resources.cached_model_components()["ner"]["state"] = "ready"
    assert resources.cached_model_components()["ner"]["state"] == "missing"
