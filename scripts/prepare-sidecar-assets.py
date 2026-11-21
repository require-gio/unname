#!/usr/bin/env python3
"""Prepare all immutable sidecar resources for PyInstaller.

The script does not download anything. Sources must already be present locally
(typically from a pinned CI cache step). Each output file gets a SHA-256 entry;
external assets receive origin, revision/version and SPDX license info.

Since the initial release the package contains **no models**: GLiNER, the
spaCy model and the tessdata are loaded by the shell on first run
(``app/src-tauri/models.lock.json``). The package still includes the Tesseract
executable (its binaries are never loaded), profiles and wordlists. With
``--with-models`` the older package with all models is built, e.g. for
distribution to devices that cannot load anything at runtime.

On macOS Apple Vision is used: for ``*-apple-darwin`` the package omits
Tesseract and tessdata and includes the Vision helper ``unname-vision-ocr``
from this repository (``--vision-ocr PATH``; without the option the script builds
it itself via ``swift build``). Windows remains unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

SCHEMA_VERSION = 1
GLINER_REPOSITORY = "https://huggingface.co/urchade/gliner_multi_pii-v1"
GLINER_REVISION = "f334163a5d1dcdbacf365c4527538e3da3ecfb18"
SPACY_REPOSITORY = "https://github.com/explosion/spacy-models/releases/tag/de_core_news_lg-3.8.0"
TESSERACT_REPOSITORY = "https://github.com/tesseract-ocr/tesseract"
TESSDATA_REPOSITORY = "https://github.com/tesseract-ocr/tessdata_best"
VISION_OCR_NAME = "unname-vision-ocr"
#: Swift architecture per Rust target.
_SWIFT_ARCHITECTURES = {"aarch64-apple-darwin": "arm64", "x86_64-apple-darwin": "x86_64"}

_IGNORED_NAMES = frozenset({".DS_Store", "__pycache__"})
_IGNORED_SUFFIXES = frozenset({".pyc", ".pyo"})
_SYSTEM_MACOS_PREFIXES = ("/System/Library/", "/usr/lib/")
#: DLLs that every Windows 10+ includes. A statically built Tesseract
#: (``scripts/tesseract-portable.py``) imports only these and therefore
#: does not need bundled DLLs.
_SYSTEM_WINDOWS_DLLS = frozenset(
    {
        "advapi32.dll",
        "bcrypt.dll",
        "comdlg32.dll",
        "crypt32.dll",
        "gdi32.dll",
        "iphlpapi.dll",
        "kernel32.dll",
        "normaliz.dll",
        "ole32.dll",
        "oleaut32.dll",
        "secur32.dll",
        "shell32.dll",
        "shlwapi.dll",
        "user32.dll",
        "version.dll",
        "wldap32.dll",
        "ws2_32.dll",
        # In System32 on every supported Windows; vcpkg's libarchive (a Tesseract
        # dependency) reads XAR archives with it instead of libxml2.
        "xmllite.dll",
    }
)


class AssetPreparationError(RuntimeError):
    """A source artifact is not release-ready or is incomplete."""


@dataclass(frozen=True, slots=True)
class PreparedAsset:
    path: str
    source: str
    revision: str
    license: str
    license_files: tuple[str, ...]
    #: Additional manifest fields, e.g. Swift and SDK versions of the helper.
    details: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class AssetTrust:
    """Pinned hashes of the still-unprocessed bundle inputs."""

    lock_path: Path
    asset_id: str
    target: str
    gliner_revision: str | None
    tessdata_revision: str | None
    gliner_tree_sha256: str | None
    #: ``None`` for macOS: the macOS package contains no Tesseract.
    tesseract_tree_sha256: str | None
    tessdata_tree_sha256: str | None


@dataclass(frozen=True, slots=True)
class AssetSources:
    repository_root: Path
    destination: Path
    target: str
    #: ``None`` (standard since initial release): package without models.
    gliner_snapshot: Path | None
    gliner_revision: str | None
    spacy_model: Path | None
    #: ``None`` for macOS.
    tesseract_root: Path | None
    tessdata_directory: Path | None
    tessdata_revision: str | None
    trust: AssetTrust
    source_date_epoch: int = 0
    #: Fully built Vision helper (macOS); ``None`` builds it with ``swift build``.
    vision_ocr: Path | None = None

    @property
    def with_models(self) -> bool:
        return self.gliner_snapshot is not None


#: Fields of a trust entry: without models only the Tesseract program.
_TRUST_FIELDS_WITHOUT_MODELS = frozenset({"target", "tesseract_tree_sha256"})
_TRUST_FIELDS_WITH_MODELS = _TRUST_FIELDS_WITHOUT_MODELS | {
    "gliner_revision",
    "tessdata_revision",
    "gliner_tree_sha256",
    "tessdata_tree_sha256",
}
#: macOS: no Tesseract, no tessdata. The Vision helper is built from this repository
#: and does not need a trust entry.
_MACOS_TRUST_FIELDS_WITHOUT_MODELS = frozenset({"target"})
_MACOS_TRUST_FIELDS_WITH_MODELS = _MACOS_TRUST_FIELDS_WITHOUT_MODELS | {
    "gliner_revision",
    "gliner_tree_sha256",
}


def is_macos_target(target: str) -> bool:
    return "apple-darwin" in target


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    repository = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description=(
            "Lokale GLiNER-/spaCy-/Tesseract-Assets deterministisch fuer das "
            "Sidecar-Binary vorbereiten (ohne Downloads)."
        )
    )
    parser.add_argument("--target", required=True, help="Rust/Tauri-Ziel-Triple")
    parser.add_argument(
        "--with-models",
        action="store_true",
        default=os.environ.get("PRESIDIO_BUNDLE_MODELS") == "1",
        help="GLiNER, spaCy und tessdata ins Paket (vor Phase I der Standard)",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=repository / "sidecar" / "bundle-assets",
    )
    parser.add_argument(
        "--gliner-snapshot",
        type=Path,
        default=_optional_path(os.environ.get("PRESIDIO_GLINER_SNAPSHOT")),
        help="Lokaler Snapshot des gepinnten GLiNER-Modells",
    )
    parser.add_argument(
        "--gliner-revision",
        default=os.environ.get("PRESIDIO_GLINER_REVISION", GLINER_REVISION),
    )
    parser.add_argument(
        "--spacy-model",
        type=Path,
        default=_optional_path(os.environ.get("PRESIDIO_SPACY_MODEL_DIR")),
        help="Optionaler lokaler de_core_news_lg-Datenordner; sonst installiertes Paket",
    )
    parser.add_argument(
        "--tesseract-root",
        type=Path,
        default=_optional_path(os.environ.get("PRESIDIO_TESSERACT_ROOT")),
        help="Portabler Tesseract-Ordner oder Pfad zum Binary",
    )
    parser.add_argument(
        "--vision-ocr",
        type=Path,
        default=_optional_path(os.environ.get("PRESIDIO_VISION_OCR_BINARY")),
        help="Fertiger Vision-Helfer (macOS); sonst swift build im Repository",
    )
    parser.add_argument(
        "--tessdata-dir",
        type=Path,
        default=_optional_path(os.environ.get("PRESIDIO_TESSDATA_DIR")),
    )
    parser.add_argument(
        "--tessdata-revision",
        default=os.environ.get("PRESIDIO_TESSDATA_REVISION"),
        help="Commit/Tag der bereitgestellten deu/eng-traineddata (Pflicht)",
    )
    parser.add_argument(
        "--source-date-epoch",
        type=int,
        default=int(os.environ.get("SOURCE_DATE_EPOCH", "0")),
    )
    parser.add_argument(
        "--trust-lock",
        type=Path,
        default=_optional_path(os.environ.get("PRESIDIO_ASSET_TRUST_LOCK")),
        help="Repository-versionierter SHA-256-Lock der Bundle-Eingaben (Pflicht)",
    )
    parser.add_argument(
        "--trust-asset-id",
        default=os.environ.get("PRESIDIO_ASSET_TRUST_ID"),
        help="Plattformschluessel im Trust-Lock, z. B. windows-x64",
    )
    parser.add_argument(
        "--verify-input-trust-only",
        action="store_true",
        help="Nur Quellhashes pruefen; kein fremdes Binary ausfuehren oder kopieren",
    )
    parser.add_argument(
        "--print-input-trust",
        action="store_true",
        help="Reviewbaren Lock-Eintrag berechnen, ohne ein fremdes Binary auszufuehren",
    )
    return parser.parse_args(argv)


def sources_from_args(args: argparse.Namespace) -> AssetSources:
    repository = Path(__file__).resolve().parents[1]
    snapshot, tesseract_root, tessdata, revision, trust = _trusted_inputs_from_args(args)
    spacy_model = (args.spacy_model or _installed_spacy_model()) if args.with_models else None
    if args.source_date_epoch < 0:
        raise AssetPreparationError("SOURCE_DATE_EPOCH darf nicht negativ sein.")
    return AssetSources(
        repository_root=repository,
        destination=args.destination,
        target=args.target,
        gliner_snapshot=snapshot,
        gliner_revision=str(args.gliner_revision).strip() if args.with_models else None,
        spacy_model=spacy_model,
        tesseract_root=tesseract_root,
        tessdata_directory=tessdata,
        tessdata_revision=revision,
        trust=trust,
        source_date_epoch=args.source_date_epoch,
        vision_ocr=args.vision_ocr,
    )


def _trusted_inputs_from_args(
    args: argparse.Namespace,
) -> tuple[Path | None, Path | None, Path | None, str | None, AssetTrust]:
    snapshot, tesseract_root, tessdata, revision = _input_paths_from_args(args)
    trust = _load_asset_trust(
        args.trust_lock,
        args.trust_asset_id,
        target=args.target,
        gliner_revision=str(args.gliner_revision).strip() if args.with_models else None,
        tessdata_revision=revision,
    )
    return snapshot, tesseract_root, tessdata, revision, trust


def _input_paths_from_args(
    args: argparse.Namespace,
) -> tuple[Path | None, Path | None, Path | None, str | None]:
    if is_macos_target(args.target):
        # On macOS Vision is used; Tesseract is not bundled.
        if args.tesseract_root is not None or args.tessdata_dir is not None:
            raise AssetPreparationError(
                "Das Mac-Paket enthaelt kein Tesseract mehr (Phase U); "
                "--tesseract-root/--tessdata-dir weglassen."
            )
        if not args.with_models:
            return None, None, None, None
        snapshot = args.gliner_snapshot or _cached_gliner_snapshot(args.gliner_revision)
        if snapshot is None:
            raise AssetPreparationError(
                "GLiNER-Snapshot fehlt. PRESIDIO_GLINER_SNAPSHOT bzw. "
                "--gliner-snapshot auf den bereits lokal vorliegenden Snapshot setzen."
            )
        return snapshot, None, None, None
    if not args.with_models:
        tesseract_root = args.tesseract_root or _path_tesseract()
        if tesseract_root is None:
            raise AssetPreparationError(
                "Tesseract fehlt. PRESIDIO_TESSERACT_ROOT bzw. --tesseract-root setzen."
            )
        return None, tesseract_root, None, None
    snapshot = args.gliner_snapshot or _cached_gliner_snapshot(args.gliner_revision)
    if snapshot is None:
        raise AssetPreparationError(
            "GLiNER-Snapshot fehlt. PRESIDIO_GLINER_SNAPSHOT bzw. "
            "--gliner-snapshot auf den bereits lokal vorliegenden Snapshot setzen."
        )
    tesseract_root = args.tesseract_root or _path_tesseract()
    if tesseract_root is None:
        raise AssetPreparationError(
            "Tesseract fehlt. PRESIDIO_TESSERACT_ROOT bzw. --tesseract-root setzen."
        )
    tessdata = args.tessdata_dir or _find_tessdata(tesseract_root)
    if tessdata is None:
        raise AssetPreparationError(
            "tessdata fehlt. PRESIDIO_TESSDATA_DIR bzw. --tessdata-dir setzen."
        )
    revision = str(args.tessdata_revision or "").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise AssetPreparationError(
            "PRESIDIO_TESSDATA_REVISION bzw. --tessdata-revision muss ein "
            "vollstaendiger Commit-Hash sein."
        )
    return snapshot, tesseract_root, tessdata, revision


def prepare_assets(sources: AssetSources) -> dict[str, object]:
    """Build asset tree in a temporary folder and publish atomically."""

    _validate_target(sources.target)
    # Ensure this check runs before `_tesseract_version` and before any downloaded
    # artifact is executed. Structure/revision info alone is not proof of origin.
    _verify_source_trust(sources)
    macos = is_macos_target(sources.target)
    if sources.with_models:
        assert sources.gliner_snapshot is not None and sources.gliner_revision is not None
        assert sources.spacy_model is not None
        assert macos or sources.tessdata_directory is not None
        _validate_gliner_source(sources.gliner_snapshot, sources.gliner_revision)
        spacy_metadata = _validate_spacy_source(sources.spacy_model)
        spacy_licenses = _spacy_license_sources(sources.spacy_model)
    else:
        spacy_metadata, spacy_licenses = {}, ()
    executable: Path | None = None
    tesseract_version = ""
    if macos:
        vision_helper = sources.vision_ocr or build_vision_ocr(
            sources.repository_root, sources.target
        )
        vision_details = _vision_ocr_details(vision_helper, sources.target)
    else:
        assert sources.tesseract_root is not None
        executable = _resolve_tesseract_executable(sources.tesseract_root)
        tesseract_version = _tesseract_version(executable)
    if sources.tessdata_directory is not None:
        _validate_tessdata(sources.tessdata_directory)

    destination = sources.destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    try:
        assets: dict[str, PreparedAsset] = {}
        license_directory = temporary / "licenses"
        _copy_file(
            sources.repository_root / "sidecar" / "packaging" / "licenses" / "Apache-2.0.txt",
            license_directory / "Apache-2.0.txt",
            executable=False,
            epoch=sources.source_date_epoch,
        )
        _copy_file(
            sources.repository_root / "LICENSE",
            license_directory / "unname-MIT.txt",
            executable=False,
            epoch=sources.source_date_epoch,
        )
        for source in spacy_licenses:
            _copy_file(
                source,
                license_directory / "de_core_news_lg" / source.name,
                executable=False,
                epoch=sources.source_date_epoch,
            )

        if sources.with_models:
            _prepare_models(sources, temporary, assets, spacy_metadata)

        if macos:
            _copy_file(
                vision_helper,
                temporary / "vision-ocr" / VISION_OCR_NAME,
                executable=True,
                epoch=sources.source_date_epoch,
            )
            assets["vision_ocr"] = PreparedAsset(
                path="vision-ocr",
                source="repository://vision-ocr",
                revision=_repository_revision(sources.repository_root),
                license="MIT",
                license_files=("licenses/unname-MIT.txt",),
                details=vision_details,
            )
        else:
            assert executable is not None
            _prepare_tesseract(sources, executable, tesseract_version, temporary, assets)

        _copy_tree(
            sources.repository_root / "profiles",
            temporary / "profiles",
            epoch=sources.source_date_epoch,
        )
        assets["profiles"] = PreparedAsset(
            path="profiles",
            source="repository://profiles",
            revision="working-tree",
            license="MIT",
            license_files=("licenses/unname-MIT.txt",),
        )
        _copy_tree(
            sources.repository_root / "stopwords",
            temporary / "stopwords",
            epoch=sources.source_date_epoch,
        )
        assets["stopwords"] = PreparedAsset(
            path="stopwords",
            source="repository://stopwords",
            revision="working-tree",
            license="MIT",
            license_files=("licenses/unname-MIT.txt",),
        )

        notices = {
            "schema": SCHEMA_VERSION,
            "components": [
                {
                    "name": name,
                    "source": asset.source,
                    "revision": asset.revision,
                    "license": asset.license,
                    "license_files": list(asset.license_files),
                }
                for name, asset in sorted(assets.items())
                if asset.source.startswith("https://")
            ],
        }
        _write_json(
            temporary / "THIRD_PARTY_NOTICES.json",
            notices,
            epoch=sources.source_date_epoch,
        )

        files = _file_manifest(temporary)
        file_hashes = {entry["path"]: entry["sha256"] for entry in files}
        manifest_assets = {
            name: {
                "path": asset.path,
                "source": asset.source,
                "revision": asset.revision,
                "license": asset.license,
                "license_files": list(asset.license_files),
                "tree_sha256": _subtree_hash(asset.path, file_hashes),
                **dict(asset.details),
            }
            for name, asset in sorted(assets.items())
        }
        manifest: dict[str, object] = {
            "schema": SCHEMA_VERSION,
            "target": sources.target,
            "source_date_epoch": sources.source_date_epoch,
            "assets": manifest_assets,
            "files": files,
        }
        _write_json(
            temporary / "asset-manifest.json",
            manifest,
            epoch=sources.source_date_epoch,
        )
        _replace_directory(temporary, destination)
        return manifest
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _prepare_tesseract(
    sources: AssetSources,
    executable: Path,
    tesseract_version: str,
    temporary: Path,
    assets: dict[str, PreparedAsset],
) -> None:
    """Package Tesseract with its licenses and, with models, tessdata (non-macOS)."""

    tesseract_destination = temporary / "tesseract"
    _prepare_tesseract_runtime(
        executable,
        sources.tesseract_root,
        tesseract_destination,
        target=sources.target,
        epoch=sources.source_date_epoch,
    )
    # A statically built Tesseract includes Leptonica and the image libraries;
    # their license texts are placed by scripts/tesseract-portable.py
    # into the hashed source tree.
    tesseract_licenses = ["licenses/Apache-2.0.txt"]
    bundled_licenses = sources.tesseract_root / "licenses"
    if sources.tesseract_root.is_dir() and bundled_licenses.is_dir():
        for source in sorted(bundled_licenses.glob("*.txt"), key=lambda path: path.name):
            relative = f"licenses/tesseract/{source.name}"
            _copy_file(
                source,
                temporary / relative,
                executable=False,
                epoch=sources.source_date_epoch,
            )
            tesseract_licenses.append(relative)
    assets["tesseract"] = PreparedAsset(
        path="tesseract",
        source=TESSERACT_REPOSITORY,
        revision=tesseract_version,
        license="Apache-2.0",
        license_files=tuple(tesseract_licenses),
    )

    if sources.tessdata_directory is not None:
        tessdata_destination = tesseract_destination / "tessdata"
        for language in ("deu", "eng"):
            _copy_file(
                sources.tessdata_directory / f"{language}.traineddata",
                tessdata_destination / f"{language}.traineddata",
                executable=False,
                epoch=sources.source_date_epoch,
            )
            assets[f"tessdata_{language}"] = PreparedAsset(
                path=f"tesseract/tessdata/{language}.traineddata",
                source=TESSDATA_REPOSITORY,
                revision=str(sources.tessdata_revision),
                license="Apache-2.0",
                license_files=("licenses/Apache-2.0.txt",),
            )


def _prepare_models(
    sources: AssetSources,
    temporary: Path,
    assets: dict[str, PreparedAsset],
    spacy_metadata: Mapping[str, object],
) -> None:
    """Include GLiNER and spaCy in the package (only with ``--with-models``)."""

    assert sources.gliner_snapshot is not None and sources.spacy_model is not None
    gliner_destination = temporary / "models" / "gliner"
    _copy_tree(
        sources.gliner_snapshot,
        gliner_destination,
        epoch=sources.source_date_epoch,
        exclude=_exclude_gliner_file,
    )
    assets["gliner"] = PreparedAsset(
        path="models/gliner",
        source=GLINER_REPOSITORY,
        revision=str(sources.gliner_revision),
        license="Apache-2.0",
        license_files=("licenses/Apache-2.0.txt",),
    )

    spacy_destination = temporary / "models" / "de_core_news_lg"
    _copy_tree(sources.spacy_model, spacy_destination, epoch=sources.source_date_epoch)
    assets["spacy_de_core_news_lg"] = PreparedAsset(
        path="models/de_core_news_lg",
        source=SPACY_REPOSITORY,
        revision=str(spacy_metadata["version"]),
        license=str(spacy_metadata["license"]),
        license_files=(
            "licenses/de_core_news_lg/LICENSE",
            "licenses/de_core_news_lg/LICENSES_SOURCES",
        ),
    )


def _load_asset_trust(
    lock_path: Path | None,
    asset_id: object,
    *,
    target: str,
    gliner_revision: str | None,
    tessdata_revision: str | None,
) -> AssetTrust:
    if lock_path is None:
        raise AssetPreparationError(
            "Bundle-Trust-Lock fehlt. PRESIDIO_ASSET_TRUST_LOCK bzw. --trust-lock "
            "muss auf einen reviewten Lock im Repository zeigen."
        )
    identifier = str(asset_id or "").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", identifier):
        raise AssetPreparationError(
            "PRESIDIO_ASSET_TRUST_ID bzw. --trust-asset-id ist keine sichere Asset-ID."
        )
    path = lock_path.resolve()
    repository = Path(__file__).resolve().parents[1]
    if not path.is_relative_to(repository) or path.is_symlink() or not path.is_file():
        raise AssetPreparationError(
            "Der Bundle-Trust-Lock muss eine echte Datei innerhalb des Repositorys sein."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AssetPreparationError(f"Bundle-Trust-Lock ist unlesbar: {path}") from error
    if not isinstance(payload, dict) or set(payload) != {"schema", "assets"}:
        raise AssetPreparationError("Bundle-Trust-Lock hat unbekannte oder fehlende Felder.")
    if payload["schema"] != 1 or not isinstance(payload["assets"], dict):
        raise AssetPreparationError("Bundle-Trust-Lock hat eine ungueltige Version/Assetliste.")
    entry = payload["assets"].get(identifier)
    with_models = gliner_revision is not None
    if is_macos_target(target):
        expected_fields = (
            _MACOS_TRUST_FIELDS_WITH_MODELS if with_models else _MACOS_TRUST_FIELDS_WITHOUT_MODELS
        )
    else:
        expected_fields = _TRUST_FIELDS_WITH_MODELS if with_models else _TRUST_FIELDS_WITHOUT_MODELS
    if not isinstance(entry, dict) or set(entry) != expected_fields:
        raise AssetPreparationError(
            f"Bundle-Trust fuer {identifier!r} fehlt oder ist unvollstaendig. "
            "Produktionshashes muessen vor dem Packaging reviewed eingeschrieben werden."
        )
    values = {name: str(entry[name]).strip() for name in expected_fields}
    if values["target"] != target:
        raise AssetPreparationError(
            f"Bundle-Trust {identifier!r} gilt fuer {values['target']!r}, nicht {target!r}."
        )
    if with_models and values["gliner_revision"] != gliner_revision:
        raise AssetPreparationError("GLiNER-Revision widerspricht dem Bundle-Trust-Lock.")
    if with_models and values.get("tessdata_revision", tessdata_revision) != tessdata_revision:
        raise AssetPreparationError("tessdata-Revision widerspricht dem Bundle-Trust-Lock.")
    for name in sorted(field for field in expected_fields if field.endswith("_sha256")):
        if not re.fullmatch(r"[0-9a-f]{64}", values[name]):
            raise AssetPreparationError(f"Bundle-Trust.{identifier}.{name} ist kein SHA-256.")
    return AssetTrust(
        lock_path=path,
        asset_id=identifier,
        target=target,
        gliner_revision=gliner_revision,
        tessdata_revision=tessdata_revision,
        gliner_tree_sha256=values.get("gliner_tree_sha256"),
        tesseract_tree_sha256=values.get("tesseract_tree_sha256"),
        tessdata_tree_sha256=values.get("tessdata_tree_sha256"),
    )


def _verify_source_trust(sources: AssetSources) -> None:
    if (
        sources.trust.target != sources.target
        or sources.trust.gliner_revision != sources.gliner_revision
        or sources.trust.tessdata_revision != sources.tessdata_revision
    ):
        raise AssetPreparationError(
            "Bundle-Trust passt nicht zu Ziel oder deklarierten Quellrevisionen."
        )
    _verify_declared_source_trust(
        sources.gliner_snapshot,
        sources.tesseract_root,
        sources.tessdata_directory,
        sources.trust,
    )


def _verify_declared_source_trust(
    gliner_snapshot: Path | None,
    tesseract_root: Path | None,
    tessdata_directory: Path | None,
    trust: AssetTrust,
) -> None:
    macos = is_macos_target(trust.target)
    declarations: list[tuple[str, Path, str | None]] = []
    if macos:
        if tesseract_root is not None or tessdata_directory is not None:
            raise AssetPreparationError("Das Mac-Paket enthaelt kein Tesseract mehr (Phase U).")
    else:
        if tesseract_root is None:
            raise AssetPreparationError("Tesseract fehlt fuer dieses Ziel.")
        declarations.append(("Tesseract", tesseract_root, trust.tesseract_tree_sha256))
    if gliner_snapshot is not None or trust.gliner_tree_sha256 is not None:
        if gliner_snapshot is None or (tessdata_directory is None and not macos):
            raise AssetPreparationError("Modellquellen fehlen fuer einen Trust mit Modellen.")
        declarations.insert(0, ("GLiNER", gliner_snapshot, trust.gliner_tree_sha256))
        if tessdata_directory is not None:
            declarations.append(("tessdata", tessdata_directory, trust.tessdata_tree_sha256))
    for label, path, expected in declarations:
        actual = _source_tree_sha256(path)
        if actual != expected:
            raise AssetPreparationError(
                f"{label}-Quellhash stimmt nicht mit Bundle-Trust {trust.asset_id!r} "
                f"ueberein: erwartet {expected}, erhalten {actual}."
            )


def _source_tree_sha256(root: Path) -> str:
    """Compute SHA256 of a source tree, ignoring symlinks and platform-specific path issues."""

    if root.is_symlink():
        raise AssetPreparationError(f"Bundle-Quelle darf kein Symlink sein: {root}")
    if root.is_file():
        candidates = ((".", root),)
    elif root.is_dir():
        collected: list[tuple[str, Path]] = []
        for directory, names, filenames in os.walk(root, followlinks=False):
            directory_path = Path(directory)
            for name in (*names, *filenames):
                candidate = directory_path / name
                if candidate.is_symlink():
                    raise AssetPreparationError(
                        f"Bundle-Quelle enthaelt einen Symlink: {candidate}"
                    )
            for filename in filenames:
                candidate = directory_path / filename
                try:
                    mode = candidate.stat(follow_symlinks=False).st_mode
                except OSError as error:
                    raise AssetPreparationError(
                        f"Bundle-Quelle ist nicht lesbar: {candidate}"
                    ) from error
                if not stat.S_ISREG(mode):
                    raise AssetPreparationError(
                        f"Bundle-Quelle enthaelt eine Sonderdatei: {candidate}"
                    )
                relative = candidate.relative_to(root).as_posix()
                if (
                    relative != unicodedata.normalize("NFC", relative)
                    or "\\" in relative
                    or any(ord(character) < 32 for character in relative)
                ):
                    raise AssetPreparationError(
                        f"Bundle-Quelle enthaelt einen plattformabhaengigen Pfad: {relative!r}"
                    )
                collected.append((relative, candidate))
        candidates = tuple(sorted(collected))
    else:
        raise AssetPreparationError(f"Bundle-Quelle fehlt: {root}")

    folded: set[str] = set()
    entries: list[dict[str, object]] = []
    for relative, path in candidates:
        collision_key = relative.casefold()
        if collision_key in folded:
            raise AssetPreparationError(
                f"Bundle-Quelle enthaelt plattformabhaengig doppelte Pfade: {relative!r}"
            )
        folded.add(collision_key)
        before = path.stat(follow_symlinks=False)
        file_hash = _sha256(path)
        after = path.stat(follow_symlinks=False)
        identity_before = (before.st_size, before.st_mtime_ns, before.st_ino)
        identity_after = (after.st_size, after.st_mtime_ns, after.st_ino)
        if identity_before != identity_after:
            raise AssetPreparationError(
                f"Bundle-Quelle wurde waehrend der Trust-Pruefung veraendert: {path}"
            )
        entries.append({"path": relative, "size": after.st_size, "sha256": file_hash})
    canonical = json.dumps(
        {"schema": 1, "files": entries},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _validate_target(target: str) -> None:
    if not re.fullmatch(r"[a-zA-Z0-9_]+(?:-[a-zA-Z0-9_]+){2,3}", target):
        raise AssetPreparationError(f"Ungueltiges Ziel-Triple: {target!r}")


def _validate_gliner_source(snapshot: Path, revision: str) -> None:
    if not snapshot.is_dir():
        raise AssetPreparationError(f"GLiNER-Snapshot nicht gefunden: {snapshot}")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise AssetPreparationError(
            "GLiNER muss auf einen vollstaendigen Commit-Hash gepinnt sein."
        )
    if snapshot.name != revision and re.fullmatch(r"[0-9a-f]{40}", snapshot.name):
        raise AssetPreparationError(
            f"GLiNER-Snapshot {snapshot.name} stimmt nicht mit Revision {revision} ueberein."
        )
    required = (
        "gliner_config.json",
        "model.safetensors",
        "tokenizer_config.json",
        "spm.model",
    )
    _require_files(snapshot, required, label="GLiNER")
    try:
        config = json.loads((snapshot / "gliner_config.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AssetPreparationError("gliner_config.json ist ungueltig.") from error
    encoder_config = config.get("encoder_config") if isinstance(config, dict) else None
    if not isinstance(encoder_config, dict) or not encoder_config.get("model_type"):
        raise AssetPreparationError(
            "GLiNER-Snapshot enthaelt keine eingebettete Encoder-Konfiguration; "
            "das wuerde einen Laufzeitabruf des Basismodells erfordern."
        )
    if (snapshot / "pytorch_model.bin").is_file() and not (
        snapshot / "model.safetensors"
    ).is_file():
        raise AssetPreparationError("GLiNER wird nur im sicheren safetensors-Format gebuendelt.")


def _validate_spacy_source(model: Path) -> Mapping[str, object]:
    if not model.is_dir():
        raise AssetPreparationError(f"spaCy-Modell nicht gefunden: {model}")
    _require_files(model, ("config.cfg", "meta.json"), label="de_core_news_lg")
    try:
        metadata = json.loads((model / "meta.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AssetPreparationError("meta.json des spaCy-Modells ist ungueltig.") from error
    if not isinstance(metadata, dict) or metadata.get("lang") != "de":
        raise AssetPreparationError("Der spaCy-Modellordner ist nicht deutsch (lang=de).")
    if metadata.get("license") != "MIT" or metadata.get("version") != "3.8.0":
        raise AssetPreparationError("Lizenz oder Version von de_core_news_lg fehlt/ist unerwartet.")
    return metadata


def _spacy_license_sources(model: Path) -> tuple[Path, ...]:
    """Find original model/corpus license files and enforce fail-closed."""

    local = tuple(model / name for name in ("LICENSE", "LICENSES_SOURCES"))
    if all(path.is_file() for path in local):
        return local
    try:
        distribution = importlib.metadata.distribution("de-core-news-lg")
    except importlib.metadata.PackageNotFoundError as error:
        raise AssetPreparationError(
            "Lizenznachweise LICENSE/LICENSES_SOURCES fuer de_core_news_lg fehlen."
        ) from error
    resolved: list[Path] = []
    for name in ("LICENSE", "LICENSES_SOURCES"):
        candidate = Path(
            distribution.locate_file(
                f"{distribution.metadata['Name']}-{distribution.version}.dist-info/{name}"
            )
        )
        if not candidate.is_file():
            # locate_file may differ across installers for normalized wheel names.
            # Use the RECORD list as the canonical source.
            matches = [
                Path(distribution.locate_file(file))
                for file in distribution.files or ()
                if PurePosixPath(str(file)).name == name
            ]
            candidate = matches[0] if len(matches) == 1 else candidate
        if not candidate.is_file():
            raise AssetPreparationError(f"spaCy-Modell-Lizenznachweis fehlt: {name}")
        resolved.append(candidate)
    return tuple(resolved)


def _validate_tessdata(directory: Path) -> None:
    if not directory.is_dir():
        raise AssetPreparationError(f"tessdata-Ordner nicht gefunden: {directory}")
    _require_files(directory, ("deu.traineddata", "eng.traineddata"), label="tessdata")


def _require_files(root: Path, relatives: Iterable[str], *, label: str) -> None:
    missing = [relative for relative in relatives if not (root / relative).is_file()]
    if missing:
        raise AssetPreparationError(f"{label} unvollstaendig: {', '.join(missing)}")


def _installed_spacy_model() -> Path:
    try:
        module = importlib.import_module("de_core_news_lg")
    except ImportError as error:
        raise AssetPreparationError("Das installierte Paket de_core_news_lg fehlt.") from error
    package = Path(module.__file__).resolve().parent
    candidates = sorted(
        path for path in package.iterdir() if path.name.startswith("de_core_news_lg-")
    )
    if len(candidates) != 1:
        raise AssetPreparationError(
            f"de_core_news_lg enthaelt nicht genau einen Modellordner: {package}"
        )
    return candidates[0]


def _cached_gliner_snapshot(revision: str) -> Path | None:
    cache_root = Path(
        os.environ.get(
            "HF_HOME",
            Path.home() / ".cache" / "huggingface",
        )
    )
    candidate = cache_root / "hub" / "models--urchade--gliner_multi_pii-v1" / "snapshots" / revision
    return candidate if candidate.is_dir() else None


def _path_tesseract() -> Path | None:
    executable = shutil.which("tesseract")
    return Path(executable) if executable else None


def _find_tessdata(root: Path) -> Path | None:
    executable = _resolve_tesseract_executable(root)
    candidates = (
        root / "tessdata" if root.is_dir() else executable.parent / "tessdata",
        root / "share" / "tessdata"
        if root.is_dir()
        else executable.parent.parent / "share" / "tessdata",
        executable.parent.parent / "share" / "tessdata",
        _optional_path(os.environ.get("TESSDATA_PREFIX")),
    )
    for candidate in candidates:
        if candidate is not None and candidate.is_dir():
            return candidate
    return None


def _resolve_tesseract_executable(root: Path) -> Path:
    if root.is_file():
        return root.resolve()
    names = ("tesseract.exe",) if os.name == "nt" else ("tesseract",)
    candidates = [root / "bin" / name for name in names] + [root / name for name in names]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise AssetPreparationError(f"Tesseract-Binary nicht unter {root} gefunden.")


def _tesseract_version(executable: Path) -> str:
    try:
        result = subprocess.run(
            [str(executable), "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise AssetPreparationError(f"Tesseract ist nicht ausfuehrbar: {executable}") from error
    first_line = (result.stdout or result.stderr).splitlines()[0].strip()
    match = re.fullmatch(r"tesseract\s+(.+)", first_line, flags=re.IGNORECASE)
    if match is None:
        raise AssetPreparationError(f"Tesseract-Version nicht erkennbar: {first_line!r}")
    return match.group(1)


def build_vision_ocr(repository: Path, target: str) -> Path:
    """Build the Vision helper with ``swift build`` for the target architecture."""

    architecture = _SWIFT_ARCHITECTURES.get(target)
    if architecture is None:
        raise AssetPreparationError(f"Kein Vision-Helfer fuer das Ziel {target!r}.")
    if sys.platform != "darwin":
        raise AssetPreparationError("Der Vision-Helfer laesst sich nur auf macOS bauen.")
    command = [
        "swift",
        "build",
        "-c",
        "release",
        "--package-path",
        str(repository / "vision-ocr"),
        "--arch",
        architecture,
    ]
    try:
        subprocess.run(command, check=True, timeout=1800)
        shown = subprocess.run(
            [*command, "--show-bin-path"], check=True, capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise AssetPreparationError("Der Vision-Helfer liess sich nicht bauen.") from error
    helper = Path(shown.stdout.strip()) / VISION_OCR_NAME
    if not helper.is_file():
        raise AssetPreparationError(f"Vision-Helfer fehlt nach dem Bau: {helper}")
    return helper


def _vision_ocr_details(helper: Path, target: str) -> tuple[tuple[str, str], ...]:
    """Check architecture; collect Swift and SDK versions for the manifest."""

    if not helper.is_file():
        raise AssetPreparationError(f"Vision-Helfer nicht gefunden: {helper}")
    architecture = _SWIFT_ARCHITECTURES.get(target)
    archs = _tool_output(["lipo", "-archs", str(helper)])
    if architecture is not None and archs is not None and architecture not in archs.split():
        raise AssetPreparationError(
            f"Vision-Helfer ist fuer {archs} gebaut, nicht fuer {architecture}."
        )
    swift = _tool_output(["swift", "--version"]) or ""
    match = re.search(r"Swift version (\S+)", swift)
    sdk = _tool_output(["xcrun", "--show-sdk-version"])
    return (
        ("sdk", sdk or "unbekannt"),
        ("swift", match.group(1) if match else "unbekannt"),
    )


def _tool_output(command: Sequence[str]) -> str | None:
    try:
        completed = subprocess.run(
            list(command), check=True, capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (completed.stdout or completed.stderr).strip() or None


def _repository_revision(repository: Path) -> str:
    """Get the repository commit; use ``working-tree`` when Git is unavailable."""

    revision = _tool_output(["git", "-C", str(repository), "rev-parse", "HEAD"])
    return revision if revision and re.fullmatch(r"[0-9a-f]{40}", revision) else "working-tree"


def _prepare_tesseract_runtime(
    executable: Path,
    source_root: Path,
    destination: Path,
    *,
    target: str,
    epoch: int,
) -> None:
    executable_name = "tesseract.exe" if "windows" in target else "tesseract"
    _copy_file(executable, destination / "bin" / executable_name, executable=True, epoch=epoch)

    if "apple-darwin" in target:
        if sys.platform != "darwin":
            raise AssetPreparationError("macOS-Tesseract-Abhaengigkeiten nur auf macOS aufloesbar.")
        for dependency in _macos_dependencies(executable, source_root):
            _copy_file(
                dependency,
                destination / "lib" / dependency.name,
                executable=True,
                epoch=epoch,
            )
        return

    if "windows" in target:
        roots = {executable.parent}
        if source_root.is_dir():
            roots.update({source_root, source_root / "bin", source_root / "lib"})
        dlls = sorted(
            {path.resolve() for root in roots if root.is_dir() for path in root.glob("*.dll")},
            key=lambda path: path.name.casefold(),
        )
        if not dlls:
            foreign = [name for name in windows_imports(executable) if not is_system_dll(name)]
            if foreign:
                raise AssetPreparationError(
                    "Windows-Tesseract braucht die transitiven DLLs aus der portablen "
                    f"Distribution: {', '.join(foreign)}"
                )
            return
        for dependency in dlls:
            _copy_file(
                dependency,
                destination / "bin" / dependency.name,
                executable=True,
                epoch=epoch,
            )
        return

    # Linux is not a primary desktop target; kept for local smoke tests.
    # ldd reports the actual linked dependencies.
    for dependency in _linux_dependencies(executable):
        _copy_file(
            dependency,
            destination / "lib" / dependency.name,
            executable=True,
            epoch=epoch,
        )


def _macos_dependencies(executable: Path, source_root: Path) -> tuple[Path, ...]:
    queue: deque[Path] = deque([executable.resolve()])
    trusted_root = source_root.resolve() if source_root.is_dir() else executable.parent.resolve()
    seen: set[Path] = set()
    dependencies: dict[str, Path] = {}
    while queue:
        current = queue.popleft()
        if current in seen:
            continue
        seen.add(current)
        for reference in _otool_references(current):
            resolved = _resolve_macos_reference(reference, current, executable, source_root)
            if resolved is None or str(resolved).startswith(_SYSTEM_MACOS_PREFIXES):
                continue
            if not resolved.is_relative_to(trusted_root):
                raise AssetPreparationError(
                    f"Tesseract-dylib liegt ausserhalb des gehashten Quellbaums: {resolved}"
                )
            if not resolved.is_file():
                raise AssetPreparationError(
                    f"Nicht aufloesbare Tesseract-dylib {reference!r} aus {current}"
                )
            previous = dependencies.get(resolved.name)
            if previous is not None and _sha256(previous) != _sha256(resolved):
                raise AssetPreparationError(
                    f"Zwei verschiedene Tesseract-dylibs heissen {resolved.name}."
                )
            dependencies[resolved.name] = resolved
            queue.append(resolved)
    dependencies.pop(executable.name, None)
    return tuple(dependencies[name] for name in sorted(dependencies, key=str.casefold))


def is_system_dll(name: str) -> bool:
    folded = name.casefold()
    return folded in _SYSTEM_WINDOWS_DLLS or folded.startswith(("api-ms-win-", "ext-ms-"))


def windows_imports(binary: Path) -> tuple[str, ...]:
    """Extract DLL names from the import and delay-import tables of a PE file.

    Works without ``dumpbin`` so it runs on systems without Visual Studio installed.
    """

    data = binary.read_bytes()

    def u16(offset: int) -> int:
        return int.from_bytes(data[offset : offset + 2], "little")

    def u32(offset: int) -> int:
        return int.from_bytes(data[offset : offset + 4], "little")

    try:
        if data[:2] != b"MZ":
            raise ValueError("kein MZ-Kopf")
        header = u32(0x3C)
        if data[header : header + 4] != b"PE\0\0":
            raise ValueError("keine PE-Signatur")
        coff = header + 4
        section_count = u16(coff + 2)
        optional = coff + 20
        optional_size = u16(coff + 16)
        magic = u16(optional)
        if magic == 0x10B:
            directories, directory_count = optional + 96, u32(optional + 92)
        elif magic == 0x20B:
            directories, directory_count = optional + 112, u32(optional + 108)
        else:
            raise ValueError(f"unbekanntes Optional-Header-Format {magic:#x}")
        sections = [
            (u32(base + 12), max(u32(base + 8), u32(base + 16)), u32(base + 20))
            for base in (optional + optional_size + 40 * index for index in range(section_count))
        ]

        def offset_of(rva: int) -> int:
            for address, size, raw in sections:
                if address <= rva < address + size:
                    return raw + rva - address
            raise ValueError(f"RVA {rva:#x} liegt in keinem Abschnitt")

        def name_at(rva: int) -> str:
            start = offset_of(rva)
            return data[start : data.index(b"\0", start)].decode("ascii")

        names: list[str] = []
        # Eintrag 1: Importe (20 Byte je DLL), Eintrag 13: Delay-Importe (32 Byte).
        for directory, stride, name_field in ((1, 20, 12), (13, 32, 4)):
            if directory >= directory_count:
                continue
            rva = u32(directories + 8 * directory)
            if rva == 0:
                continue
            entry = offset_of(rva)
            while any(data[entry : entry + stride]):
                names.append(name_at(u32(entry + name_field)))
                entry += stride
    except (IndexError, ValueError, UnicodeDecodeError) as error:
        raise AssetPreparationError(f"PE-Importe von {binary} nicht lesbar: {error}") from error
    return tuple(sorted(set(names), key=str.casefold))


def _otool_references(binary: Path) -> tuple[str, ...]:
    try:
        result = subprocess.run(
            ["otool", "-L", str(binary)],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise AssetPreparationError(f"otool konnte {binary} nicht untersuchen.") from error
    references = []
    for line in result.stdout.splitlines()[1:]:
        stripped = line.strip()
        if stripped:
            references.append(stripped.split(" (compatibility version", 1)[0])
    return tuple(references)


def _resolve_macos_reference(
    reference: str,
    current: Path,
    executable: Path,
    source_root: Path,
) -> Path | None:
    if reference.startswith("@loader_path/"):
        return (current.parent / reference.removeprefix("@loader_path/")).resolve()
    if reference.startswith("@executable_path/"):
        return (executable.parent / reference.removeprefix("@executable_path/")).resolve()
    if reference.startswith("@rpath/"):
        name = reference.removeprefix("@rpath/")
        candidates = (
            current.parent / name,
            executable.parent / name,
            source_root / "lib" / name if source_root.is_dir() else executable.parent / name,
        )
        return next((path.resolve() for path in candidates if path.is_file()), None)
    if reference.startswith("/"):
        return Path(reference).resolve()
    return None


def _linux_dependencies(executable: Path) -> tuple[Path, ...]:
    try:
        result = subprocess.run(
            ["ldd", str(executable)],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise AssetPreparationError(f"ldd konnte {executable} nicht untersuchen.") from error
    dependencies: set[Path] = set()
    for line in result.stdout.splitlines():
        match = re.search(r"=>\s+(/\S+)\s+\(", line)
        if match:
            dependencies.add(Path(match.group(1)).resolve())
    return tuple(sorted(dependencies, key=lambda path: path.name.casefold()))


def _copy_tree(
    source: Path,
    destination: Path,
    *,
    epoch: int,
    exclude: Callable[[Path], bool] | None = None,
) -> None:
    if not source.is_dir():
        raise AssetPreparationError(f"Quellordner fehlt: {source}")
    for path in _source_files(source):
        relative = path.relative_to(source)
        if exclude is not None and exclude(relative):
            continue
        _copy_file(path, destination / relative, executable=False, epoch=epoch)


def _source_files(root: Path) -> Iterator[Path]:
    for directory, names, filenames in os.walk(root, followlinks=False):
        names[:] = sorted(name for name in names if name not in _IGNORED_NAMES and name != ".git")
        for filename in sorted(filenames):
            if filename in _IGNORED_NAMES or Path(filename).suffix in _IGNORED_SUFFIXES:
                continue
            path = Path(directory) / filename
            if path.is_symlink() and not path.resolve().is_file():
                raise AssetPreparationError(f"Defekter Asset-Symlink: {path}")
            if path.is_file():
                yield path


def _exclude_gliner_file(relative: Path) -> bool:
    # Die gepinnte Revision enthaelt safetensors. Das redundante Pickle-Gewicht
    # would be another 1.16 GB large and unnecessarily expands the attack surface.
    return relative.as_posix() == "pytorch_model.bin"


def _copy_file(source: Path, destination: Path, *, executable: bool, epoch: int) -> None:
    if not source.is_file():
        raise AssetPreparationError(f"Asset-Datei fehlt: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination, follow_symlinks=True)
    destination.chmod(0o755 if executable else 0o644)
    os.utime(destination, (epoch, epoch))


def _write_json(path: Path, payload: object, *, epoch: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o644)
    os.utime(path, (epoch, epoch))


def _file_manifest(root: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for path in sorted(_source_files(root), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        entries.append(
            {
                "path": relative,
                "size": path.stat().st_size,
                "sha256": _sha256(path),
                "kind": "binary" if _is_native_binary(relative, path) else "data",
            }
        )
    return entries


def _is_native_binary(relative: str, path: Path) -> bool:
    posix = PurePosixPath(relative)
    # Vision helper is signed by PyInstaller like Tesseract.
    if posix.as_posix() == f"vision-ocr/{VISION_OCR_NAME}":
        return True
    if posix.parts[:2] not in {("tesseract", "bin"), ("tesseract", "lib")}:
        return False
    suffix = path.suffix.casefold()
    if suffix in {".exe", ".dll", ".dylib", ".so"} or ".so." in path.name:
        return True
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return path.name == "tesseract" or bool(mode & stat.S_IXUSR)


def _subtree_hash(path: str, hashes: Mapping[str, str]) -> str:
    prefix = path.rstrip("/") + "/"
    selected = {
        relative: digest
        for relative, digest in hashes.items()
        if relative == path or relative.startswith(prefix)
    }
    if not selected:
        raise AssetPreparationError(f"Asset-Pfad hat keine Dateien: {path}")
    digest = hashlib.sha256()
    for relative, file_hash in sorted(selected.items()):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _replace_directory(source: Path, destination: Path) -> None:
    if destination.exists():
        if not destination.is_dir() or destination.is_symlink():
            raise AssetPreparationError(f"Asset-Ziel ist kein sicherer Ordner: {destination}")
        shutil.rmtree(destination)
    source.replace(destination)


def _optional_path(raw: object) -> Path | None:
    if raw is None:
        return None
    value = str(raw).strip()
    return Path(value).expanduser() if value else None


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        if args.print_input_trust and args.verify_input_trust_only:
            raise AssetPreparationError(
                "--print-input-trust und --verify-input-trust-only schliessen sich aus."
            )
        if args.print_input_trust:
            identifier = str(args.trust_asset_id or "").strip()
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", identifier):
                raise AssetPreparationError(
                    "--print-input-trust braucht --trust-asset-id mit einer sicheren Asset-ID."
                )
            snapshot, tesseract, tessdata, revision = _input_paths_from_args(args)
            fields: dict[str, str] = {"target": args.target}
            if tesseract is not None:
                fields["tesseract_tree_sha256"] = _source_tree_sha256(tesseract)
            if snapshot is not None and tessdata is None:
                # macOS with models: GLiNER without tessdata.
                fields |= {
                    "gliner_revision": str(args.gliner_revision).strip(),
                    "gliner_tree_sha256": _source_tree_sha256(snapshot),
                }
            if snapshot is not None and tessdata is not None:
                fields |= {
                    "gliner_revision": str(args.gliner_revision).strip(),
                    "tessdata_revision": str(revision),
                    "gliner_tree_sha256": _source_tree_sha256(snapshot),
                    "tessdata_tree_sha256": _source_tree_sha256(tessdata),
                }
            entry = {identifier: fields}
            print(json.dumps(entry, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.verify_input_trust_only:
            snapshot, tesseract, tessdata, _revision, trust = _trusted_inputs_from_args(args)
            _verify_declared_source_trust(snapshot, tesseract, tessdata, trust)
            print(
                f"Bundle inputs for {trust.asset_id!r} comply with {trust.lock_path}."
            )
            return 0
        sources = sources_from_args(args)
        manifest = prepare_assets(sources)
    except AssetPreparationError as error:
        print(f"Sidecar-Assets nicht vorbereitet: {error}", file=sys.stderr)
        return 2
    print(
        f"Sidecar-Assets fuer {manifest['target']} vorbereitet: "
        f"{sources.destination.resolve()} ({len(manifest['files'])} Dateien)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
