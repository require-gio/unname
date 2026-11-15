"""Contract of ``scripts/tesseract-portable.py`` (M-9) without the actual build."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
SCRIPT = REPOSITORY / "scripts" / "tesseract-portable.py"


@pytest.fixture(scope="module")
def build() -> ModuleType:
    spec = importlib.util.spec_from_file_location("tesseract_portabel", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_vcpkg_pinned_to_full_commit(build: ModuleType) -> None:
    assert re.fullmatch(r"[0-9a-f]{40}", build.VCPKG_COMMIT)


def test_targets_cover_package_matrix_and_link_statically(build: ModuleType) -> None:
    workflow = (REPOSITORY / ".github" / "workflows" / "package-smoke.yml").read_text()
    for target in build.TARGETS:
        assert f"target: {target}" in workflow
    for target_spec in build.TARGETS.values():
        assert "set(VCPKG_LIBRARY_LINKAGE static)" in target_spec.triplet_cmake
        assert "set(VCPKG_BUILD_TYPE release)" in target_spec.triplet_cmake
    # Windows needs the static CRT to avoid the Visual C++ redistributable.
    assert "set(VCPKG_CRT_LINKAGE static)" in build.TARGETS["x86_64-pc-windows-msvc"].triplet_cmake


def test_language_data_comes_from_model_manifest_as_in_app(build: ModuleType) -> None:
    component = build._ocr_component()
    assert {item["path"] for item in component["files"]} == {
        "deu.traineddata",
        "eng.traineddata",
    }
    workflow = (REPOSITORY / ".github" / "workflows" / "bundle-inputs.yml").read_text()
    revision = str(component["origin"]).rpartition("@")[2]
    assert f"TESSDATA_REVISION: {revision}" in workflow


def test_ports_read_from_vcpkg_ledger(build: ModuleType, tmp_path: Path) -> None:
    info = tmp_path / "vcpkg" / "info"
    info.mkdir(parents=True)
    for name in (
        "tesseract_5.5.2_arm64-osx-unname.list",
        "leptonica_1.87.0_arm64-osx-unname.list",
        "vcpkg-cmake_2024-04-23_arm64-osx-unname.list",
        "zlib_1.3.1_x64-windows.list",
    ):
        (info / name).write_text("")

    assert build._installed_ports(tmp_path, "arm64-osx-unname") == {
        "leptonica": "1.87.0",
        "tesseract": "5.5.2",
    }
    with pytest.raises(build.BuildError, match="tesseract"):
        build._installed_ports(tmp_path, "x64-windows")


def test_build_only_on_target_system(build: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build.sys, "platform", "linux")
    with pytest.raises(build.BuildError, match="Zielsystem"):
        build._check_host("aarch64-apple-darwin")
    monkeypatch.setattr(build.sys, "platform", "darwin")
    build._check_host("aarch64-apple-darwin")
