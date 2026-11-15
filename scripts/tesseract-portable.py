#!/usr/bin/env python3
"""Build a portable Tesseract for the release bundle.

Users install nothing themselves: the program is in the signed package,
the app downloads the language data. This script creates the source tree
expected by ``scripts/prepare-sidecar-assets.py`` as ``--tesseract-root``::

    <out>/tesseract/bin/tesseract[.exe]
    <out>/tesseract/tessdata/{deu,eng}.traineddata
    <out>/tesseract/licenses/<port>.txt
    <out>/tesseract/BUILD-INFO.json

Built from source with vcpkg at a fixed commit, statically linked
against Leptonica and all image libraries. After that the program only
needs what the OS provides; the script checks this before reporting
success, and finally lets Tesseract read a fabricated invoice text.
The language data come from the same ``tessdata_best`` commit and
with the same SHA-256 as in the app's model manifest.

Usage (from repo root, Pillow for the test image)::

    uv run --no-project --with pillow==12.3.0 python scripts/tesseract-portable.py \\
        --target aarch64-apple-darwin --out .bundle-inputs

An existing vcpkg checkout can be supplied via ``--vcpkg-root``; it must
be at ``VCPKG_COMMIT``.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

REPOSITORY = Path(__file__).resolve().parents[1]
VCPKG_REPOSITORY = "https://github.com/microsoft/vcpkg"
#: Release 2026.07.29 – Tesseract 5.5.2, Leptonica 1.87.0.
VCPKG_COMMIT = "9e593bb18ea69cc5095e012465dcd675a822ed0d"
MODELS_LOCK = REPOSITORY / "app" / "src-tauri" / "models.lock.json"
#: Fabricated; only so the test can verify Tesseract actually reads.
TEST_TEXT = "Rechnung Brettschneider"
#: Words that must be reliably recognized in the test.
TEST_WORDS = ("Rechnung", "Brettschneider")


@dataclass(frozen=True, slots=True)
class Target:
    triplet: str
    executable: str
    #: Content of the overlay triplet file: all static, release only.
    triplet_cmake: str


TARGETS: dict[str, Target] = {
    "aarch64-apple-darwin": Target(
        triplet="arm64-osx-unname",
        executable="tesseract",
        triplet_cmake="\n".join(
            (
                "set(VCPKG_TARGET_ARCHITECTURE arm64)",
                "set(VCPKG_CRT_LINKAGE dynamic)",
                "set(VCPKG_LIBRARY_LINKAGE static)",
                "set(VCPKG_CMAKE_SYSTEM_NAME Darwin)",
                "set(VCPKG_OSX_ARCHITECTURES arm64)",
                "set(VCPKG_OSX_DEPLOYMENT_TARGET 11.0)",
                "set(VCPKG_BUILD_TYPE release)",
                "",
            )
        ),
    ),
    "x86_64-pc-windows-msvc": Target(
        triplet="x64-windows-static-unname",
        executable="tesseract.exe",
        # Static CRT: no Visual C++ redistributable needed on target.
        triplet_cmake="\n".join(
            (
                "set(VCPKG_TARGET_ARCHITECTURE x64)",
                "set(VCPKG_CRT_LINKAGE static)",
                "set(VCPKG_LIBRARY_LINKAGE static)",
                "set(VCPKG_BUILD_TYPE release)",
                "",
            )
        ),
    ),
}


class BuildError(RuntimeError):
    """The build or a verification step failed."""


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", required=True, choices=sorted(TARGETS))
    parser.add_argument("--out", required=True, type=Path, help="Ziel; darin entsteht tesseract/")
    parser.add_argument(
        "--work",
        type=Path,
        default=None,
        help="Arbeitsordner fuer vcpkg (Standard: temporaer)",
    )
    parser.add_argument("--vcpkg-root", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    target_cfg = TARGETS[args.target]
    try:
        _check_host(args.target)
        with tempfile.TemporaryDirectory(prefix="tesseract-portable-") as temporary:
            work = (args.work or Path(temporary)).resolve()
            work.mkdir(parents=True, exist_ok=True)
            vcpkg = _vcpkg_checkout(args.vcpkg_root, work)
            installed = _vcpkg_install(vcpkg, target_cfg, work)
            root = _assemble(installed, target_cfg, args.out.resolve() / "tesseract")
            _verify(root, target_cfg, args.target, work)
    except BuildError as error:
        print(f"tesseract-portable: {error}", file=sys.stderr)
        return 1
    print(f"Portables Tesseract liegt unter {args.out.resolve() / 'tesseract'}")
    return 0


def _check_host(target: str) -> None:
    host = {"darwin": "apple-darwin", "win32": "windows"}.get(sys.platform, sys.platform)
    if host not in target:
        raise BuildError(f"{target} laesst sich nur auf dem Zielsystem bauen und pruefen.")


def _run(command: Sequence[str | Path], **kwargs: object) -> subprocess.CompletedProcess[str]:
    printable = " ".join(str(part) for part in command)
    print(f"+ {printable}", flush=True)
    try:
        return subprocess.run([str(part) for part in command], check=True, text=True, **kwargs)
    except (OSError, subprocess.CalledProcessError) as error:
        raise BuildError(f"Befehl gescheitert: {printable}") from error


def _vcpkg_checkout(given: Path | None, work: Path) -> Path:
    root = given.resolve() if given else work / "vcpkg"
    if given is None and not (root / ".git").is_dir():
        root.mkdir(parents=True, exist_ok=True)
        _run(["git", "init", "--quiet", root])
        _run(
            [
                "git",
                "-C",
                root,
                "fetch",
                "--quiet",
                "--depth",
                "1",
                VCPKG_REPOSITORY,
                VCPKG_COMMIT,
            ]
        )
        _run(["git", "-C", root, "checkout", "--quiet", "FETCH_HEAD"])
    head = _run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True).stdout.strip()
    if head != VCPKG_COMMIT:
        raise BuildError(f"vcpkg steht auf {head}, erwartet {VCPKG_COMMIT}.")
    executable = root / ("vcpkg.exe" if os.name == "nt" else "vcpkg")
    if not executable.is_file():
        bootstrap = "bootstrap-vcpkg.bat" if os.name == "nt" else "bootstrap-vcpkg.sh"
        _run([root / bootstrap, "-disableMetrics"], cwd=root)
    return root


def _vcpkg_install(vcpkg: Path, target_cfg: Target, work: Path) -> Path:
    triplets = work / "triplets"
    triplets.mkdir(parents=True, exist_ok=True)
    (triplets / f"{target_cfg.triplet}.cmake").write_text(target_cfg.triplet_cmake, encoding="utf-8")
    install_root = work / "installed"
    executable = vcpkg / ("vcpkg.exe" if os.name == "nt" else "vcpkg")
    environment = {**os.environ, "VCPKG_ROOT": str(vcpkg), "VCPKG_DISABLE_METRICS": "1"}
    _run(
        [
            executable,
            "install",
            "tesseract",
            f"--triplet={target_cfg.triplet}",
            f"--host-triplet={target_cfg.triplet}",
            f"--overlay-triplets={triplets}",
            f"--x-install-root={install_root}",
            "--clean-after-build",
        ],
        cwd=vcpkg,
        env=environment,
    )
    return install_root


def _assemble(install_root: Path, target_cfg: Target, root: Path) -> Path:
    installed = install_root / target_cfg.triplet
    program = installed / "tools" / "tesseract" / target_cfg.executable
    if not program.is_file():
        raise BuildError(f"vcpkg hat kein Programm gebaut: {program}")
    if root.exists():
        shutil.rmtree(root)
    (root / "bin").mkdir(parents=True)
    shutil.copy2(program, root / "bin" / target_cfg.executable)

    _download_tessdata(root / "tessdata")

    licenses = root / "licenses"
    licenses.mkdir()
    for copyright_file in sorted((installed / "share").glob("*/copyright")):
        port = copyright_file.parent.name
        if port.startswith("vcpkg-"):
            continue  # Build tools, not part of the program
        shutil.copy2(copyright_file, licenses / f"{port}.txt")

    info = {
        "vcpkg_repository": VCPKG_REPOSITORY,
        "vcpkg_commit": VCPKG_COMMIT,
        "triplet": target_cfg.triplet,
        "triplet_cmake": target_cfg.triplet_cmake.splitlines(),
        "ports": _installed_ports(install_root, target_cfg.triplet),
        "tessdata_origin": _ocr_component()["origin"],
    }
    (root / "BUILD-INFO.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return root


def _installed_ports(install_root: Path, triplet: str) -> dict[str, str]:
    """Ports with version from vcpkg's own bookkeeping (``<port>_<version>_<triplet>.list``)."""

    ports: dict[str, str] = {}
    for listing in sorted((install_root / "vcpkg" / "info").glob(f"*_{triplet}.list")):
        port, _, version = listing.name.removesuffix(f"_{triplet}.list").partition("_")
        if not port.startswith("vcpkg-"):
            ports[port] = version
    if "tesseract" not in ports:
        raise BuildError("vcpkg fuehrt tesseract nicht als installiert.")
    return ports


def _ocr_component() -> dict[str, object]:
    manifest = json.loads(MODELS_LOCK.read_text(encoding="utf-8"))
    for component in manifest["components"]:
        if component["id"] == "ocr":
            return component
    raise BuildError("Das Modell-Manifest kennt keinen Bestandteil ocr.")


def _download_tessdata(destination: Path) -> None:
    """Same ``deu``/``eng`` files the app loads: identical URLs and hashes."""

    destination.mkdir(parents=True)
    for item in _ocr_component()["files"]:
        target = destination / item["path"]
        url = item["urls"]["github"]
        print(f"+ lade {url}", flush=True)
        try:
            with (
                urllib.request.urlopen(url, timeout=120) as response,
                target.open("wb") as file,
            ):
                shutil.copyfileobj(response, file)
        except OSError as error:
            raise BuildError(f"Sprachdaten nicht ladbar: {url}") from error
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest != item["sha256"] or target.stat().st_size != item["size"]:
            raise BuildError(f"{item['path']} passt nicht zum Modell-Manifest.")


def _prepare_module() -> ModuleType:
    path = REPOSITORY / "scripts" / "prepare-sidecar-assets.py"
    spec = importlib.util.spec_from_file_location("prepare_sidecar_assets", path)
    if spec is None or spec.loader is None:
        raise BuildError(f"{path} nicht ladbar.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _verify(root: Path, target_cfg: Target, target: str, work: Path) -> None:
    """Only system libraries; runs with an empty environment."""

    program = root / "bin" / target_cfg.executable
    prepare = _prepare_module()
    try:
        if "apple-darwin" in target:
            foreign = [str(path) for path in prepare._macos_dependencies(program, root)]
        else:
            foreign = [
                name for name in prepare.windows_imports(program) if not prepare.is_system_dll(name)
            ]
    except prepare.AssetPreparationError as error:
        raise BuildError(str(error)) from error
    if foreign:
        raise BuildError(f"Tesseract braucht Bibliotheken ausserhalb des Systems: {foreign}")

    # No PATH to Homebrew or vcpkg: the binary runs directly on the user's system.
    if os.name == "nt":
        system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        environment = {"SystemRoot": system_root, "PATH": rf"{system_root}\System32"}
    else:
        environment = {"PATH": "/usr/bin:/bin"}
    with tempfile.TemporaryDirectory(prefix="probe-", dir=work) as probe_dir:
        isolated = Path(probe_dir) / "bin"
        isolated.mkdir()
        shutil.copy2(program, isolated / target_cfg.executable)
        executable = isolated / target_cfg.executable
        version = _run([executable, "--version"], capture_output=True, env=environment)
        print((version.stdout or version.stderr).splitlines()[0])

        image = Path(probe_dir) / "probe.png"
        _render_probe(image)
        result = _run(
            [
                executable,
                image,
                "stdout",
                "-l",
                "deu+eng",
                "--tessdata-dir",
                root / "tessdata",
            ],
            capture_output=True,
            env=environment,
        )
    missing = [word for word in TEST_WORDS if word not in result.stdout]
    if missing:
        raise BuildError(f"Probe nicht gelesen, es fehlt {missing}: {result.stdout!r}")
    print(f"Probe gelesen: {result.stdout.strip()!r}")


def _render_probe(path: Path) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as error:
        raise BuildError("Pillow fehlt; siehe Aufruf im Kopf des Skripts.") from error
    image = Image.new("L", (1200, 200), 255)
    ImageDraw.Draw(image).text((40, 60), TEST_TEXT, fill=0, font=ImageFont.load_default(size=64))
    image.save(path)


if __name__ == "__main__":
    raise SystemExit(main())
