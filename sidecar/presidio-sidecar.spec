# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Tauri sidecar.

The spec is executed by ``scripts/build-sidecar.mjs`` on the exact target platform.
Native Tesseract files and the Vision helper (macOS) are intentionally included via
``binaries``: only then does PyInstaller normalize macOS install names/rpaths and
sign the entire Mach-O dependency chain (with ``SidecarEntitlements.plist``).

``PRESIDIO_SIDECAR_LAYOUT`` chooses the shape (``scripts/sidecar-artifact.mjs``):
``onedir`` on macOS, because a onefile bootloader cannot start inside the App
Sandbox, and ``onefile`` on Windows, where Tauri expects a single ``externalBin``.
"""

import json
import os
from pathlib import Path, PurePosixPath

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata


SIDE_CAR_ROOT = Path(SPECPATH).resolve()
ASSET_ROOT = Path(
    os.environ.get("PRESIDIO_SIDECAR_ASSETS", SIDE_CAR_ROOT / "bundle-assets")
).resolve()
MANIFEST_PATH = ASSET_ROOT / "asset-manifest.json"
TARGET = os.environ.get("PRESIDIO_SIDECAR_TARGET", "").strip()
BINARY_NAME = os.environ.get("PRESIDIO_SIDECAR_BINARY_NAME", "").strip()
CODESIGN_IDENTITY = os.environ.get("PRESIDIO_CODESIGN_IDENTITY") or None
ENTITLEMENTS_FILE = os.environ.get("PRESIDIO_ENTITLEMENTS_FILE") or None
LAYOUT = os.environ.get("PRESIDIO_SIDECAR_LAYOUT", "").strip()
#: In a onedir build: the executable inside the folder named ``BINARY_NAME``.
EXECUTABLE_NAME = os.environ.get("PRESIDIO_SIDECAR_EXECUTABLE_NAME", "").strip()

if not TARGET or not BINARY_NAME:
    raise SystemExit(
        "PRESIDIO_SIDECAR_TARGET und PRESIDIO_SIDECAR_BINARY_NAME muessen gesetzt sein."
    )
if LAYOUT not in ("onefile", "onedir"):
    raise SystemExit("PRESIDIO_SIDECAR_LAYOUT muss onefile oder onedir sein.")
if LAYOUT == "onedir" and not EXECUTABLE_NAME:
    raise SystemExit("Ein onedir-Build braucht PRESIDIO_SIDECAR_EXECUTABLE_NAME.")
if not MANIFEST_PATH.is_file():
    raise SystemExit(
        f"Vorbereitete Assets fehlen: {MANIFEST_PATH}. Erst prepare-sidecar-assets.py ausfuehren."
    )

manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
if manifest.get("schema") != 1 or manifest.get("target") != TARGET:
    raise SystemExit("Asset-Manifest passt nicht zum angeforderten Zielsystem.")

datas = []
binaries = []
hiddenimports = []
for entry in manifest.get("files", []):
    relative = PurePosixPath(entry["path"])
    source = ASSET_ROOT.joinpath(*relative.parts)
    destination = str(PurePosixPath("assets", *relative.parts[:-1]))
    item = (str(source), destination)
    if entry.get("kind") == "binary":
        binaries.append(item)
    elif entry.get("kind") == "data":
        datas.append(item)
    else:
        raise SystemExit(f"Unbekannter Asset-Typ: {entry!r}")
datas.append((str(MANIFEST_PATH), "assets"))

# FastAPI/Presidio/spaCy use registries and dynamic imports.
# ``collect_all`` includes data, native extensions, and submodules;
# package metadata remains available for health reports and plugin detection.
COLLECTED_PACKAGES = (
    "faker",
    "presidio_analyzer",
    "presidio_anonymizer",
    "presidio_image_redactor",
    "presidio_structured",
    "pycountry",
    "spacy",
    "tldextract",
)
for package in COLLECTED_PACKAGES:
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas.extend(package_datas)
    binaries.extend(package_binaries)
    hiddenimports.extend(package_hidden)

for package in (
    "uvicorn",
    "presidio_analyzer",
    "presidio_anonymizer",
    "presidio_image_redactor",
    "presidio_structured",
    "spacy.lang.de",
):
    hiddenimports.extend(collect_submodules(package))

for distribution in (
    "fastapi",
    "onnxruntime",
    "presidio-analyzer",
    "presidio-anonymizer",
    "presidio-image-redactor",
    "presidio-structured",
    "spacy",
    "tokenizers",
    "uvicorn",
):
    try:
        datas.extend(copy_metadata(distribution, recursive=True))
    except Exception as error:
        raise SystemExit(f"Paketmetadaten fehlen fuer {distribution}: {error}") from error

target_arch = None
if "apple-darwin" in TARGET:
    target_arch = "arm64" if TARGET.startswith("aarch64-") else "x86_64"
if ENTITLEMENTS_FILE and not Path(ENTITLEMENTS_FILE).is_file():
    raise SystemExit(f"Sidecar-Entitlements fehlen: {ENTITLEMENTS_FILE}")

analysis = Analysis(
    [str(SIDE_CAR_ROOT / "pyinstaller_entry.py")],
    pathex=[str(SIDE_CAR_ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Name detection uses ONNX without gliner, PyTorch
    # and transformers. Explicitly exclude them so they do not
    # end up in the package even if the build environment has the ``modellbau`` group.
    excludes=["tkinter", "pytest", "gliner", "torch", "transformers"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)

executable_options = dict(
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=target_arch,
    codesign_identity=CODESIGN_IDENTITY,
    entitlements_file=ENTITLEMENTS_FILE,
)

if LAYOUT == "onefile":
    executable = EXE(
        pyz,
        analysis.scripts,
        analysis.binaries,
        analysis.datas,
        [],
        name=BINARY_NAME,
        **executable_options,
    )
else:
    # COLLECT signs every collected binary with the executable's identity and
    # entitlements, so the whole folder carries one signature chain.
    executable = EXE(
        pyz,
        analysis.scripts,
        [],
        exclude_binaries=True,
        name=EXECUTABLE_NAME,
        **executable_options,
    )
    COLLECT(
        executable,
        analysis.binaries,
        analysis.datas,
        strip=False,
        upx=False,
        name=BINARY_NAME,
    )
