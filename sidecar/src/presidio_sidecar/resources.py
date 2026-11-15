"""Resolution and validation of resources bundled with PyInstaller.

A frozen sidecar must not rely on the working directory or on global model/OCR installations.
PyInstaller extracts a one-file binary to ``sys._MEIPASS``; under that lives the build-generated,
hashed ``assets`` folder.

In development the repository paths remain. ``PRESIDIO_BUNDLE_ASSETS_DIR`` can point to a prepared
asset directory for testing. In a frozen binary this override is deliberately ignored: a release
always uses the signed bundle resources.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

ASSET_DIRECTORY_NAME = "assets"
ASSET_MANIFEST_NAME = "asset-manifest.json"
DEFAULT_GLINER_MODEL_ID = "urchade/gliner_multi_pii-v1"
DEFAULT_SPACY_MODEL_NAME = "de_core_news_lg"
#: spaCy packages per document language, preferred order for bundle and development.
#: The shell loads the small model into the language-package folder (``models/de``, ``models/en``).
SPACY_MODEL_NAMES: dict[str, tuple[str, ...]] = {
    "de": (DEFAULT_SPACY_MODEL_NAME, "de_core_news_sm"),
    "en": ("en_core_web_sm",),
}

#: The shell loads the models once (``models.rs``) and starts the sidecar with ``--models-dir``.
#: For each component a folder is created; the shell writes ``.installed.json`` only after
#: every file has been verified against the signed app’s SHA-256, then atomically renames the
#: folder.
#: A folder lacking this marker is considered absent.
MODELS_DIRECTORY_ENV = "PRESIDIO_MODELS_DIR"
#: ``1`` limits validity to model folders and the bundle, ignoring development-environment models,
#: allowing the initial setup to be tested in development.
MODELS_STRICT_ENV = "PRESIDIO_MODELS_STRICT"
INSTALL_MARKER = ".installed.json"
GLINER_ONNX_FILE = "model.int8.onnx"
#: Components the shell can load (``app/src-tauri/models.lock.json``).
MODEL_COMPONENTS = ("ner", "de", "en", "ocr", "classifier")
#: Components that represent a document language (language packages).
LANGUAGE_COMPONENTS = ("de", "en")
_COMPONENT_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")

_HASH_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
#: The bundle no longer contains models; Tesseract is a program kept in the signed package
#: (only non-executable files are loaded). On macOS Apple Vision is used instead:
#: the macOS package includes the Vision helper from this repository (internal asset
#: ``vision_ocr``).
_OPTIONAL_EXTERNAL_ASSETS = frozenset(
    {"gliner", "spacy_de_core_news_lg", "tessdata_deu", "tessdata_eng"}
)

#: OCR per platform. The choice is fixed;
#: ``PRESIDIO_OCR_ENGINE`` (``tesseract`` | ``apple-vision``) applies only in development.
OCR_ENGINE_ENV = "PRESIDIO_OCR_ENGINE"
APPLE_VISION = "apple-vision"
TESSERACT = "tesseract"
#: Development Vision helper; in a frozen sidecar only the bundle is used.
VISION_OCR_ENV = "PRESIDIO_VISION_OCR"
VISION_OCR_NAME = "unname-vision-ocr"
VISION_OCR_BUILD_COMMAND = "swift build -c release --package-path vision-ocr"


def _required_external_assets(platform: str | None = None) -> frozenset[str]:
    return frozenset() if (platform or sys.platform) == "darwin" else frozenset({"tesseract"})


def _required_internal_assets(platform: str | None = None) -> frozenset[str]:
    base = frozenset({"profiles", "stopwords"})
    return base | {"vision_ocr"} if (platform or sys.platform) == "darwin" else base


def _required_ocr_executable(platform: str | None = None) -> str:
    current = platform or sys.platform
    if current == "darwin":
        return f"vision-ocr/{VISION_OCR_NAME}"
    return "tesseract/bin/tesseract.exe" if current == "win32" else "tesseract/bin/tesseract"


def selected_ocr_engine() -> str:
    """Vision on macOS, otherwise Tesseract. ``PRESIDIO_OCR_ENGINE`` applies only in development."""

    if not is_frozen():
        configured = os.environ.get(OCR_ENGINE_ENV, "").strip().casefold()
        if configured in {APPLE_VISION, TESSERACT}:
            return configured
    return APPLE_VISION if sys.platform == "darwin" else TESSERACT


_OFFLINE_ENVIRONMENT = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
}


class BundledAssetError(RuntimeError):
    """The signed bundle is incomplete or its manifest is invalid."""


@dataclass(frozen=True, slots=True)
class TesseractBundle:
    executable: Path
    tessdata: Path


@dataclass(frozen=True, slots=True)
class ComponentLocation:
    """Location of a component. ``source``: ``download`` (model folder of the shell),
    ``bundle`` (in signed package) or ``local`` (development)."""

    path: Path | None
    source: str


_models_directory: Path | None = None


def use_models_directory(path: Path | None) -> None:
    """Model folder for this session (``--models-dir``)."""

    global _models_directory
    _models_directory = path


def models_directory() -> Path | None:
    if _models_directory is not None:
        return _models_directory
    configured = os.environ.get(MODELS_DIRECTORY_ENV)
    return Path(configured).expanduser() if configured else None


def installed_component(component: str) -> Path | None:
    """Folder of a component installed by the shell.

    Deliberately without cache: the shell installs and removes during the session,
    and the check is a ``stat`` and a small JSON.
    """

    if not _COMPONENT_PATTERN.fullmatch(component):
        return None
    root = models_directory()
    if root is None:
        return None
    folder = root / component
    try:
        marker = json.loads((folder / INSTALL_MARKER).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(marker, Mapping) or marker.get("component") != component:
        return None
    return folder


def _strict_models() -> bool:
    return os.environ.get(MODELS_STRICT_ENV) == "1"


def is_frozen() -> bool:
    """Whether the process runs from a PyInstaller bundle."""

    return bool(getattr(sys, "frozen", False))


def repository_root() -> Path:
    """Repository root for the unpackaged development process."""

    return Path(__file__).resolve().parents[3]


def frozen_runtime_root() -> Path:
    """Read-only unpack folder created by PyInstaller."""

    raw_root = getattr(sys, "_MEIPASS", None)
    if not is_frozen() or not isinstance(raw_root, str) or not raw_root:
        raise BundledAssetError("PyInstaller-Ressourcenwurzel ist nicht verfuegbar.")
    root = Path(raw_root)
    if not root.is_dir():
        raise BundledAssetError(f"PyInstaller-Ressourcenwurzel fehlt: {root}")
    return root


def bundled_assets_root() -> Path | None:
    """Asset root or ``None`` when developing without staging."""

    if is_frozen():
        return frozen_runtime_root() / ASSET_DIRECTORY_NAME

    configured = os.environ.get("PRESIDIO_BUNDLE_ASSETS_DIR")
    if configured:
        return Path(configured).expanduser()

    staged = repository_root() / "sidecar" / "bundle-assets"
    return staged if staged.is_dir() else None


def profiles_directory() -> Path:
    """Profile catalog from the bundle or the repository."""

    assets = bundled_assets_root()
    if assets is not None:
        return assets / "profiles"
    configured = os.environ.get("PRESIDIO_PROFILES_DIR")
    if configured:
        return Path(configured).expanduser()
    return repository_root() / "profiles"


def wordlists_directory() -> Path:
    """Stopword lists from the same resource state as the profiles."""

    assets = bundled_assets_root()
    if assets is not None:
        return assets / "stopwords"
    configured = os.environ.get("PRESIDIO_WORDLISTS_DIR")
    if configured:
        return Path(configured).expanduser()
    return repository_root() / "stopwords"


def gliner_location() -> ComponentLocation:
    """Name entity recognition: loaded (ONNX) before bundle or development environment."""

    installed = installed_component("ner")
    if installed is not None:
        return ComponentLocation(installed, "download")

    assets = bundled_assets_root()
    if assets is not None:
        model = assets / "models" / "gliner"
        return ComponentLocation(model if model.is_dir() else None, "bundle")
    if _strict_models():
        return ComponentLocation(None, "download")

    configured = os.environ.get("PRESIDIO_GLINER_MODEL_DIR")
    if configured:
        model = Path(configured).expanduser()
        return ComponentLocation(model if model.is_dir() else None, "local")

    # Fixed location for development builds. Without it each developer would need to set
    # PRESIDIO_GLINER_MODEL_DIR to get the preferred engine; otherwise `npm run tauri:dev`
    # would fall back to spaCy. This directory is produced by `scripts/models/gliner-onnx.py`
    # and is ignored by git, never read in a frozen build.
    development = repository_root() / "sidecar" / "models" / "gliner-onnx"
    return ComponentLocation(development if development.is_dir() else None, "local")


def gliner_model_reference() -> str:
    """Path for name-entity detection; if the local folder is missing the hub ID is used.

    If nothing is present, loading fails (NerModelUnavailableError) rather than pipeline
    construction. The sidecar should start without models on first run.
    """

    location = gliner_location()
    if location.path is not None:
        return str(location.path)
    if location.source != "local":
        root = models_directory()
        return str((root or Path(".")) / "ner")
    # The loader also forces local_files_only=True, so this ID can only hit a developer cache,
    # never the network.
    return DEFAULT_GLINER_MODEL_ID


def gliner_onnx_file(reference: str) -> str | None:
    """Filename of the ONNX model if the folder contains it."""

    path = Path(reference)
    return GLINER_ONNX_FILE if (path / GLINER_ONNX_FILE).is_file() else None


def spacy_location(language: str = "de") -> ComponentLocation:
    """spaCy language package: loaded from bundle before falling back to development."""

    names = SPACY_MODEL_NAMES.get(language)
    if names is None:
        return ComponentLocation(None, "download")
    installed = installed_component(language)
    if installed is not None:
        return ComponentLocation(installed, "download")

    assets = bundled_assets_root()
    if assets is not None:
        for name in names:
            model = assets / "models" / name
            if model.is_dir():
                return ComponentLocation(model, "bundle")
        return ComponentLocation(None, "bundle")
    if _strict_models():
        return ComponentLocation(None, "download")

    configured = os.environ.get("PRESIDIO_SPACY_MODEL_DIR") if language == "de" else None
    if configured:
        model = Path(configured).expanduser()
        return ComponentLocation(model if model.is_dir() else None, "local")
    for name in names:
        package = _installed_spacy_package(name)
        if package is not None:
            return ComponentLocation(package, "local")
    return ComponentLocation(None, "local")


@lru_cache(maxsize=8)
def _installed_spacy_package(name: str = DEFAULT_SPACY_MODEL_NAME) -> Path | None:
    """Data folder of an installed spaCy package (development), looked up once."""

    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return None
    locations = spec.submodule_search_locations if spec is not None else None
    if not locations:
        return None
    package = Path(next(iter(locations)))
    # The package contains a versioned data folder (e.g. “…-3.8.0”).
    data = sorted(package.glob(f"{name}-*"))
    return data[-1] if data and data[-1].is_dir() else package


def spacy_model_reference(language: str = "de") -> str:
    """Path of the spaCy model; during development the package name is used."""

    location = spacy_location(language)
    if location.source == "local":
        configured = os.environ.get("PRESIDIO_SPACY_MODEL_DIR") if language == "de" else None
        if configured:
            return str(Path(configured).expanduser())
        names = SPACY_MODEL_NAMES.get(language, (DEFAULT_SPACY_MODEL_NAME,))
        for name in names:
            if _installed_spacy_package(name) is not None:
                return name
        return names[0]
    if location.path is not None:
        return str(location.path)
    return str((models_directory() or Path(".")) / language)


def spacy_available(language: str) -> bool:
    """Whether a spaCy model is available for the given document language (stat only)."""

    try:
        return spacy_location(language).path is not None
    except (OSError, BundledAssetError):
        return False


def classifier_location() -> ComponentLocation:
    """Second opinion: loaded before development environment.

    The classifier is optional – without it the questions appear as before without assessment.
    It is never bundled.
    """

    installed = installed_component("classifier")
    if installed is not None:
        return ComponentLocation(installed, "download")
    if bundled_assets_root() is not None or _strict_models():
        return ComponentLocation(None, "download")
    configured = os.environ.get("PRESIDIO_CLASSIFIER_MODEL_DIR")
    if configured:
        model = Path(configured).expanduser()
        return ComponentLocation(model if model.is_dir() else None, "local")
    development = repository_root() / "sidecar" / "models" / "second-opinion"
    return ComponentLocation(development if development.is_dir() else None, "local")


def vision_ocr_executable() -> Path | None:
    """Vision helper executable or ``None`` if unavailable.

    Located in the bundle under ``assets/vision-ocr/``; in a frozen sidecar this
    is the only allowed location (no external program, spec §10). In development
    it can be overridden via ``PRESIDIO_VISION_OCR`` or the release build
    ``vision-ocr/.build/release``.
    """

    assets = bundled_assets_root()
    if assets is not None:
        bundled = assets / "vision-ocr" / VISION_OCR_NAME
        if bundled.is_file() or is_frozen():
            return bundled if bundled.is_file() else None
    configured = os.environ.get(VISION_OCR_ENV)
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_file() else None
    built = repository_root() / "vision-ocr" / ".build" / "release" / VISION_OCR_NAME
    return built if built.is_file() else None


def ocr_location() -> ComponentLocation:
    """OCR on this platform: Vision is provided by the system (``system``).

    Vision becomes available once the helper is present and its probe (``info``)
    has succeeded. The probe does not run here – ``/health`` has no child process;
    it runs at startup and before the first OCR operation.
    """

    if selected_ocr_engine() != APPLE_VISION:
        return tessdata_location()
    executable = vision_ocr_executable()
    if executable is None:
        return ComponentLocation(None, "system")
    from .extraction.ocr_vision import cached_probe_failure

    return ComponentLocation(None if cached_probe_failure() else executable, "system")


def tessdata_location() -> ComponentLocation:
    """OCR: program bundled with language data for German and English."""

    installed = installed_component("ocr")
    assets = bundled_assets_root()
    if assets is not None:
        executable = _bundled_tesseract_executable(assets)
        if not executable.is_file():
            return ComponentLocation(None, "bundle")
        if installed is not None:
            return ComponentLocation(installed, "download")
        tessdata = assets / "tesseract" / "tessdata"
        return ComponentLocation(tessdata if _has_tessdata(tessdata) else None, "bundle")
    executable = development_tesseract()
    if installed is not None and executable is not None:
        return ComponentLocation(installed, "download")
    if _strict_models():
        return ComponentLocation(None, "download")
    built = _built_tesseract_root()
    if built is not None and _has_tessdata(built / "tessdata"):
        return ComponentLocation(built / "tessdata", "local")
    # In development, system installation provides language data.
    return ComponentLocation(executable.parent if executable else None, "local")


def _built_tesseract_root() -> Path | None:
    """Built-in Tesseract binary (``scripts/tesseract-portable.py``) shipped in the repo.

    The same executable that will later be packaged; during development it
    takes precedence over any system installation. In a frozen build only the
    bundled version is used.
    """

    if is_frozen():
        return None
    root = repository_root() / ".bundle-inputs" / "tesseract"
    name = "tesseract.exe" if sys.platform == "win32" else "tesseract"
    return root if (root / "bin" / name).is_file() else None


def development_tesseract() -> Path | None:
    """Development-time Tesseract: use the built version, otherwise fall back to ``PATH``."""

    built = _built_tesseract_root()
    if built is not None:
        return built / "bin" / ("tesseract.exe" if sys.platform == "win32" else "tesseract")
    found = shutil.which("tesseract")
    return Path(found) if found else None


def ocr_missing_reason() -> str | None:
    """Reason why OCR is unavailable; ``None`` means ready.

    Scans and photos are rejected without OCR. The message explains the required
    action – in development there is no bundled program.
    """

    if selected_ocr_engine() == APPLE_VISION:
        return _vision_missing_reason()
    try:
        location = tessdata_location()
    except (OSError, BundledAssetError):
        location = ComponentLocation(None, "download")
    if location.path is not None:
        return None
    assets = bundled_assets_root()
    if assets is not None:
        if not _bundled_tesseract_executable(assets).is_file():
            return "Das mitgelieferte Tesseract fehlt im Paket."
    elif development_tesseract() is None:
        return (
            "Tesseract fehlt. Entwicklung: `scripts/tesseract-portable.py` nach "
            "`.bundle-inputs` bauen oder `brew install tesseract tesseract-lang` (macOS) "
            "bzw. `winget install UB-Mannheim.TesseractOCR` (Windows)."
        )
    return "Die Sprachdaten der Texterkennung fehlen; die App kann sie nachladen."


def _vision_missing_reason() -> str | None:
    if vision_ocr_executable() is None:
        if is_frozen():
            return "Der Vision-Helfer fehlt im Paket."
        return (
            "Der Vision-Helfer fehlt. Entwicklung: einmalig "
            f"`{VISION_OCR_BUILD_COMMAND}` (Xcode-Befehlszeilenwerkzeuge)."
        )
    from .extraction.ocr import OcrUnavailableError
    from .extraction.ocr_vision import vision_helper_info

    try:
        vision_helper_info()
    except OcrUnavailableError as error:
        return str(error)
    return None


def tesseract_bundle() -> TesseractBundle | None:
    """Tesseract from the bundle with language data; ``None`` means use system path.

    The binary is shipped in the signed package; the shell loads language data
    (component ``ocr``). In development the locally built binary from
    ``.bundle-inputs`` is used, otherwise the system binary; bundled language data
    takes precedence over any locally built data.
    """

    assets = bundled_assets_root()
    installed = installed_component("ocr")
    if assets is None:
        executable = development_tesseract()
        built = _built_tesseract_root()
        if installed is None:
            if built is None or not _has_tessdata(built / "tessdata"):
                return None
            installed = built / "tessdata"
        if executable is None:
            raise BundledAssetError("Tesseract fehlt auf diesem System.")
        return TesseractBundle(executable=executable, tessdata=installed)

    executable = _bundled_tesseract_executable(assets)
    tessdata = installed if installed is not None else assets / "tesseract" / "tessdata"
    missing = [
        path
        for path in (
            executable,
            tessdata / "deu.traineddata",
            tessdata / "eng.traineddata",
        )
        if not path.is_file()
    ]
    if missing:
        rendered = ", ".join(str(path) for path in missing)
        raise BundledAssetError(f"Tesseract-Ressourcen fehlen: {rendered}")
    return TesseractBundle(executable=executable, tessdata=tessdata)


def _bundled_tesseract_executable(assets: Path) -> Path:
    executable_name = "tesseract.exe" if sys.platform == "win32" else "tesseract"
    return assets / "tesseract" / "bin" / executable_name


def _has_tessdata(folder: Path) -> bool:
    return (folder / "deu.traineddata").is_file() and (folder / "eng.traineddata").is_file()


def model_components() -> dict[str, dict[str, str]]:
    """Inventory per component for ``/health`` – only ``stat`` calls.

    ``state`` is ``ready`` or ``missing``; only the shell knows if a component is
    currently loading. An error while checking counts as ``missing``.
    """

    report: dict[str, dict[str, str]] = {}
    for component, locate in (
        ("ner", gliner_location),
        ("de", lambda: spacy_location("de")),
        ("en", lambda: spacy_location("en")),
        ("ocr", ocr_location),
        ("classifier", classifier_location),
    ):
        try:
            location = locate()
        except (OSError, BundledAssetError):
            location = ComponentLocation(None, "download")
        report[component] = {
            "state": "ready" if location.path is not None else "missing",
            "source": location.source,
        }
    return report


#: Maximum age of the cached inventory. Any install or removal changes a
#: version marker that is seen immediately; the timeout only covers changes
#: outside the model folder (development).
_COMPONENTS_MAX_AGE_SECONDS = 30.0
_components_cache: tuple[tuple[Any, ...], float, dict[str, dict[str, str]]] | None = None


def cached_model_components() -> dict[str, dict[str, str]]:
    """Like :func:`model_components`, but cached for ``/health``.

    The live check runs every 5 s. A fresh lookup occurs only when a version
    marker, the model folder, the Vision probe, or a ``PRESIDIO_*`` variable
    changes – or when the timeout expires. The key costs one ``stat`` per
    component; the full inventory also reads each marker as JSON from the
    package.
    """

    global _components_cache

    key = _components_key()
    now = time.monotonic()
    cached = _components_cache
    if cached is not None and cached[0] == key and now - cached[1] < _COMPONENTS_MAX_AGE_SECONDS:
        return {name: dict(item) for name, item in cached[2].items()}
    report = model_components()
    # Set the cache in one atomic tuple: concurrent readers see either the old
    # or the new state, never a half-filled one.
    _components_cache = (key, now, report)
    return {name: dict(item) for name, item in report.items()}


def invalidate_model_components() -> None:
    """Discard cached components (tests or after manual change)."""

    global _components_cache
    _components_cache = None


def _components_key() -> tuple[Any, ...]:
    root = models_directory()
    markers: list[tuple[int, int, int] | None] = []
    for component in MODEL_COMPONENTS:
        if root is None:
            markers.append(None)
            continue
        try:
            info = os.stat(root / component / INSTALL_MARKER)
        except OSError:
            markers.append(None)
        else:
            markers.append((info.st_ino, info.st_mtime_ns, info.st_size))
    environment = tuple(
        sorted((name, value) for name, value in os.environ.items() if name.startswith("PRESIDIO_"))
    )
    from .extraction.ocr_vision import cached_probe_failure

    return (root, tuple(markers), environment, cached_probe_failure() is None, is_frozen())


def setup_required(components: Mapping[str, Mapping[str, str]] | None = None) -> bool:
    """Required resources are present: name detection and at least one language."""

    current = components if components is not None else model_components()
    ready = {name for name, item in current.items() if item.get("state") == "ready"}
    return "ner" not in ready or not (ready & set(LANGUAGE_COMPONENTS))


def configure_offline_runtime() -> None:
    """Hard-disable network paths for Hugging Face/Transformers.

    Values are overwritten rather than merely pre-filled. An accidental
    ``HF_HUB_OFFLINE=0`` in the launch environment must not break the offline
    guarantee of a release binary.
    """

    os.environ.update(_OFFLINE_ENVIRONMENT)


def initialize_runtime(*, verify_hashes: bool | None = None) -> None:
    """Enable offline mode and optionally verify a frozen bundle before handshake."""

    configure_offline_runtime()
    if not is_frozen():
        return
    if verify_hashes is None:
        verify_hashes = os.environ.get("PRESIDIO_VERIFY_BUNDLE_HASHES") == "1"
    validate_bundled_assets(verify_hashes=verify_hashes)


def load_asset_manifest(root: Path | None = None) -> dict[str, Any]:
    """Load manifest and validate its security-relevant structure."""

    asset_root = root or bundled_assets_root()
    if asset_root is None:
        raise BundledAssetError("Kein vorbereitetes Asset-Verzeichnis vorhanden.")
    manifest_path = asset_root / ASSET_MANIFEST_NAME
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BundledAssetError(f"Asset-Manifest nicht lesbar: {manifest_path}") from error
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise BundledAssetError("Asset-Manifest hat ein unbekanntes Schema.")
    return payload


def validate_bundled_assets(
    root: Path | None = None,
    *,
    verify_hashes: bool = False,
) -> dict[str, Any]:
    """Validate bundle content, provenance and optionally all data hashes.

    PyInstaller may rewrite and sign Mach-O/PE binaries during collection.
    Their staging hashes serve as a build proof but are not valid for runtime
    comparison. Unchanged data (models, profiles, tessdata, license texts) can be
    fully verified.
    """

    asset_root = root or bundled_assets_root()
    if asset_root is None or not asset_root.is_dir():
        raise BundledAssetError(f"Gebundelter Asset-Ordner fehlt: {asset_root}")
    manifest = load_asset_manifest(asset_root)
    assets = manifest.get("assets")
    files = manifest.get("files")
    if not isinstance(assets, Mapping) or not isinstance(files, list):
        raise BundledAssetError("Asset-Manifest enthaelt keine Assets/Dateiliste.")

    # Required assets depend on the target platform.
    platform = _platform_of_target(manifest.get("target"))
    required_external = _required_external_assets(platform)
    required = required_external | _required_internal_assets(platform)
    missing_assets = sorted(required - set(assets))
    if missing_assets:
        raise BundledAssetError("Asset-Manifest ist unvollstaendig: " + ", ".join(missing_assets))
    for name in required_external:
        _validate_provenance(name, assets[name], asset_root)
    # An older bundle with models remains valid if it declares provenance;
    # without provenance no model is used.
    for name in _OPTIONAL_EXTERNAL_ASSETS & set(assets):
        _validate_provenance(name, assets[name], asset_root)

    seen_paths: set[str] = set()
    for raw_entry in files:
        if not isinstance(raw_entry, Mapping):
            raise BundledAssetError("Ungueltiger Dateieintrag im Asset-Manifest.")
        relative = _safe_manifest_path(raw_entry.get("path"))
        if relative in seen_paths:
            raise BundledAssetError(f"Doppelter Asset-Pfad im Manifest: {relative}")
        seen_paths.add(relative)
        path = asset_root.joinpath(*PurePosixPath(relative).parts)
        if not path.is_file():
            raise BundledAssetError(f"Gebundelte Asset-Datei fehlt: {relative}")

        expected_hash = raw_entry.get("sha256")
        if not isinstance(expected_hash, str) or not _HASH_PATTERN.fullmatch(expected_hash):
            raise BundledAssetError(f"Ungueltiger SHA-256 fuer Asset: {relative}")
        kind = raw_entry.get("kind")
        if kind not in {"data", "binary"}:
            raise BundledAssetError(f"Ungueltiger Asset-Typ fuer {relative}: {kind!r}")
        if verify_hashes and kind == "data" and _sha256(path) != expected_hash:
            raise BundledAssetError(f"Hash-Pruefung fehlgeschlagen: {relative}")

    _validate_required_layout(asset_root, seen_paths, platform)
    return manifest


def _platform_of_target(target: object) -> str:
    """The ``sys.platform`` value for a target triple; the host's if the triple is unknown."""

    if isinstance(target, str):
        if "apple-darwin" in target:
            return "darwin"
        if "windows" in target:
            return "win32"
        if "linux" in target:
            return "linux"
    return sys.platform


def _validate_provenance(name: str, raw: object, asset_root: Path) -> None:
    if not isinstance(raw, Mapping):
        raise BundledAssetError(f"Provenienz fuer {name} fehlt.")
    source = raw.get("source")
    revision = raw.get("revision")
    license_id = raw.get("license")
    license_files = raw.get("license_files")
    tree_hash = raw.get("tree_sha256")
    if not isinstance(source, str) or not source.startswith("https://"):
        raise BundledAssetError(f"HTTPS-Herkunft fuer {name} fehlt.")
    if not isinstance(revision, str) or not revision.strip():
        raise BundledAssetError(f"Revision/Version fuer {name} fehlt.")
    if not isinstance(license_id, str) or not license_id.strip():
        raise BundledAssetError(f"SPDX-Lizenz fuer {name} fehlt.")
    if not isinstance(tree_hash, str) or not _HASH_PATTERN.fullmatch(tree_hash):
        raise BundledAssetError(f"Baum-Hash fuer {name} fehlt.")
    if not isinstance(license_files, list) or not license_files:
        raise BundledAssetError(f"Lizenznachweis fuer {name} fehlt.")
    for raw_path in license_files:
        relative = _safe_manifest_path(raw_path)
        if not asset_root.joinpath(*PurePosixPath(relative).parts).is_file():
            raise BundledAssetError(f"Lizenzdatei fuer {name} fehlt: {relative}")


def _validate_required_layout(root: Path, manifest_paths: set[str], platform: str) -> None:
    from .languages import packs

    required_files = {
        f"stopwords/{filename}"
        for language in packs()
        for filename in (language.business_terms_file, language.sentence_starters_file)
    }
    required_files.add(_required_ocr_executable(platform))
    missing = sorted(
        relative
        for relative in required_files
        if relative not in manifest_paths
        or not root.joinpath(*PurePosixPath(relative).parts).is_file()
    )
    if missing:
        raise BundledAssetError("Pflichtdateien fehlen im Bundle: " + ", ".join(missing))
    if not any(path.startswith("profiles/") and path.endswith(".yaml") for path in manifest_paths):
        raise BundledAssetError("Keine gebundelten YAML-Profile gefunden.")


def _safe_manifest_path(raw: object) -> str:
    if not isinstance(raw, str) or not raw:
        raise BundledAssetError("Leerer Pfad im Asset-Manifest.")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise BundledAssetError(f"Unsicherer Pfad im Asset-Manifest: {raw!r}")
    normalized = path.as_posix()
    if normalized != raw or "\\" in raw:
        raise BundledAssetError(f"Nicht kanonischer Pfad im Asset-Manifest: {raw!r}")
    return normalized


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
