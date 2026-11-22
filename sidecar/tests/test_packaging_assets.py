"""Build contract for deterministic assets and Tauri binary names."""

from __future__ import annotations

import importlib.util
import json
import runpy
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest

from presidio_sidecar.resources import validate_bundled_assets

REPOSITORY = Path(__file__).resolve().parents[2]
PREPARE_SCRIPT = REPOSITORY / "scripts" / "prepare-sidecar-assets.py"
BUILD_SCRIPT = REPOSITORY / "scripts" / "build-sidecar.mjs"
SPEC_FILE = REPOSITORY / "sidecar" / "presidio-sidecar.spec"


def _load_prepare_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("prepare_sidecar_assets", PREPARE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def prepare_module() -> ModuleType:
    return _load_prepare_module()


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _fake_sources(module: ModuleType, tmp_path: Path, destination: Path):  # noqa: ANN202
    gliner = tmp_path / "gliner"
    _write(
        gliner / "gliner_config.json",
        json.dumps({"encoder_config": {"model_type": "deberta-v2"}}).encode(),
    )
    for name in (
        "model.safetensors",
        "tokenizer_config.json",
        "spm.model",
        "pytorch_model.bin",
    ):
        _write(gliner / name, name.encode())

    spacy = tmp_path / "spacy"
    _write(spacy / "config.cfg", b"[nlp]\nlang = 'de'\n")
    _write(
        spacy / "meta.json",
        json.dumps({"lang": "de", "version": "3.8.0", "license": "MIT"}).encode(),
    )
    _write(spacy / "LICENSE", b"MIT model license")
    _write(spacy / "LICENSES_SOURCES", b"training source notices")

    tesseract = tmp_path / "tesseract" / "bin" / "tesseract"
    _write(tesseract, b"native tesseract")
    tesseract.chmod(0o755)
    tessdata = tmp_path / "tessdata"
    _write(tessdata / "deu.traineddata", b"deutsch")
    _write(tessdata / "eng.traineddata", b"english")

    trust = module.AssetTrust(
        lock_path=PREPARE_SCRIPT,
        asset_id="test-linux-x64",
        target="x86_64-unknown-linux-gnu",
        gliner_revision=module.GLINER_REVISION,
        tessdata_revision="a" * 40,
        gliner_tree_sha256=module._source_tree_sha256(gliner),
        tesseract_tree_sha256=module._source_tree_sha256(tesseract.parent.parent),
        tessdata_tree_sha256=module._source_tree_sha256(tessdata),
    )
    return module.AssetSources(
        repository_root=REPOSITORY,
        destination=destination,
        target="x86_64-unknown-linux-gnu",
        gliner_snapshot=gliner,
        gliner_revision=module.GLINER_REVISION,
        spacy_model=spacy,
        tesseract_root=tesseract.parent.parent,
        tessdata_directory=tessdata,
        tessdata_revision="a" * 40,
        trust=trust,
        source_date_epoch=123,
    )


def test_asset_manifest_is_deterministically_hashed_and_licensed(
    prepare_module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prepare_module, "_tesseract_version", lambda _path: "5.5.0")
    monkeypatch.setattr(prepare_module, "_linux_dependencies", lambda _path: ())
    first = tmp_path / "assets-1"
    second = tmp_path / "assets-2"

    first_manifest = prepare_module.prepare_assets(
        _fake_sources(prepare_module, tmp_path / "source-1", first)
    )
    second_manifest = prepare_module.prepare_assets(
        _fake_sources(prepare_module, tmp_path / "source-2", second)
    )

    assert first_manifest == second_manifest
    assert (first / "asset-manifest.json").read_bytes() == (
        second / "asset-manifest.json"
    ).read_bytes()
    assert not (first / "models/gliner/pytorch_model.bin").exists()
    assert first_manifest["assets"]["gliner"]["revision"] == prepare_module.GLINER_REVISION
    assert first_manifest["assets"]["spacy_de_core_news_lg"]["license"] == "MIT"
    assert all(
        entry["kind"] == "binary"
        for entry in first_manifest["files"]
        if entry["path"] == "tesseract/bin/tesseract"
    )
    validate_bundled_assets(first, verify_hashes=True)


def test_asset_prep_rejects_pickle_only_gliner(
    prepare_module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prepare_module, "_tesseract_version", lambda _path: "5.5.0")
    monkeypatch.setattr(prepare_module, "_linux_dependencies", lambda _path: ())
    sources = _fake_sources(prepare_module, tmp_path, tmp_path / "assets")
    (sources.gliner_snapshot / "model.safetensors").unlink()
    sources = replace(
        sources,
        trust=replace(
            sources.trust,
            gliner_tree_sha256=prepare_module._source_tree_sha256(sources.gliner_snapshot),
        ),
    )

    with pytest.raises(prepare_module.AssetPreparationError, match="GLiNER unvollstaendig"):
        prepare_module.prepare_assets(sources)


def test_asset_prep_does_not_run_manipulated_binary_before_trust_check(
    prepare_module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = _fake_sources(prepare_module, tmp_path, tmp_path / "assets")
    executable = sources.tesseract_root / "bin" / "tesseract"
    executable.write_bytes(b"nach-einschreibung-manipuliert")
    executed = False

    def unexpected_execution(_path: Path) -> str:
        nonlocal executed
        executed = True
        raise AssertionError("Untrusted Tesseract darf nicht ausgefuehrt werden")

    monkeypatch.setattr(prepare_module, "_tesseract_version", unexpected_execution)

    with pytest.raises(prepare_module.AssetPreparationError, match="Tesseract-Quellhash"):
        prepare_module.prepare_assets(sources)

    assert executed is False


def test_bundle_trust_rejects_symlinks_fail_closed(
    prepare_module: ModuleType,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _write(source / "payload.bin", b"trusted")
    link = source / "alias.bin"
    try:
        link.symlink_to(source / "payload.bin")
    except OSError:
        pytest.skip("Testhost erlaubt keine Symlinks")

    with pytest.raises(prepare_module.AssetPreparationError, match="Symlink"):
        prepare_module._source_tree_sha256(source)


def test_empty_repository_trust_lock_blocks_production_package(
    prepare_module: ModuleType,
) -> None:
    lock = REPOSITORY / "sidecar" / "packaging" / "bundle-inputs.lock.json"

    with pytest.raises(
        prepare_module.AssetPreparationError,
        match="Produktionshashes muessen .* eingeschrieben werden",
    ):
        prepare_module._load_asset_trust(
            lock,
            "windows-x64",
            target="x86_64-pc-windows-msvc",
            gliner_revision=prepare_module.GLINER_REVISION,
            tessdata_revision="a" * 40,
        )


def test_package_without_models_is_the_default(
    prepare_module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prepare_module, "_tesseract_version", lambda _path: "5.5.0")
    monkeypatch.setattr(prepare_module, "_linux_dependencies", lambda _path: ())
    full = _fake_sources(prepare_module, tmp_path / "quelle", tmp_path / "assets")
    trust = replace(
        full.trust,
        gliner_revision=None,
        tessdata_revision=None,
        gliner_tree_sha256=None,
        tessdata_tree_sha256=None,
    )
    sources = replace(
        full,
        gliner_snapshot=None,
        gliner_revision=None,
        spacy_model=None,
        tessdata_directory=None,
        tessdata_revision=None,
        trust=trust,
    )

    manifest = prepare_module.prepare_assets(sources)

    assert not (tmp_path / "assets" / "models").exists()
    assert not (tmp_path / "assets" / "tesseract" / "tessdata").exists()
    assert set(manifest["assets"]) == {"profiles", "stopwords", "tesseract"}
    validate_bundled_assets(tmp_path / "assets", verify_hashes=True)

    # The lock entry now contains only the target and Tesseract – nothing else.
    lock = tmp_path / "lock.json"
    monkeypatch.setattr(prepare_module, "__file__", str(tmp_path / "scripts" / "x.py"))
    lock.write_text(
        json.dumps(
            {
                "schema": 1,
                "assets": {
                    "linux-x64": {
                        "target": "x86_64-unknown-linux-gnu",
                        "tesseract_tree_sha256": "b" * 64,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    loaded = prepare_module._load_asset_trust(
        lock,
        "linux-x64",
        target="x86_64-unknown-linux-gnu",
        gliner_revision=None,
        tessdata_revision=None,
    )
    assert (loaded.tesseract_tree_sha256, loaded.gliner_tree_sha256) == ("b" * 64, None)
    with pytest.raises(prepare_module.AssetPreparationError, match="unvollstaendig"):
        prepare_module._load_asset_trust(
            lock,
            "linux-x64",
            target="x86_64-unknown-linux-gnu",
            gliner_revision=prepare_module.GLINER_REVISION,
            tessdata_revision="a" * 40,
        )


def test_licenses_of_statically_linked_libraries_are_bundled(
    prepare_module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prepare_module, "_tesseract_version", lambda _path: "5.5.2")
    monkeypatch.setattr(prepare_module, "_linux_dependencies", lambda _path: ())
    sources = _fake_sources(prepare_module, tmp_path / "quelle", tmp_path / "assets")
    _write(sources.tesseract_root / "licenses" / "leptonica.txt", b"BSD-2-Clause")
    _write(sources.tesseract_root / "licenses" / "zlib.txt", b"zlib")
    sources = replace(
        sources,
        trust=replace(
            sources.trust,
            tesseract_tree_sha256=prepare_module._source_tree_sha256(sources.tesseract_root),
        ),
    )

    prepare_module.prepare_assets(sources)

    notices = json.loads((tmp_path / "assets" / "THIRD_PARTY_NOTICES.json").read_text())
    tesseract = next(item for item in notices["components"] if item["name"] == "tesseract")
    assert tesseract["license_files"] == [
        "licenses/Apache-2.0.txt",
        "licenses/tesseract/leptonica.txt",
        "licenses/tesseract/zlib.txt",
    ]
    assert (tmp_path / "assets" / "licenses" / "tesseract" / "leptonica.txt").read_bytes() == (
        b"BSD-2-Clause"
    )
    validate_bundled_assets(tmp_path / "assets", verify_hashes=True)


def test_mac_package_uses_vision_helper_instead_of_tesseract(
    prepare_module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On macOS we use the vision helper instead of Tesseract, so no Tesseract binary,
    tessdata, or Tesseract licenses are required."""

    def never_run(_path: Path) -> str:
        raise AssertionError("Auf dem Mac wird kein Tesseract gebraucht")

    monkeypatch.setattr(prepare_module, "_tesseract_version", never_run)
    helper = tmp_path / "build" / "unname-vision-ocr"
    _write(helper, b"mach-o vision")
    helper.chmod(0o755)
    target = "aarch64-apple-darwin"
    trust = prepare_module.AssetTrust(
        lock_path=PREPARE_SCRIPT,
        asset_id="macos-arm64",
        target=target,
        gliner_revision=None,
        tessdata_revision=None,
        gliner_tree_sha256=None,
        tesseract_tree_sha256=None,
        tessdata_tree_sha256=None,
    )
    sources = prepare_module.AssetSources(
        repository_root=REPOSITORY,
        destination=tmp_path / "assets",
        target=target,
        gliner_snapshot=None,
        gliner_revision=None,
        spacy_model=None,
        tesseract_root=None,
        tessdata_directory=None,
        tessdata_revision=None,
        trust=trust,
        source_date_epoch=123,
        vision_ocr=helper,
    )

    manifest = prepare_module.prepare_assets(sources)

    assets = tmp_path / "assets"
    assert set(manifest["assets"]) == {"profiles", "stopwords", "vision_ocr"}
    assert not (assets / "tesseract").exists()
    assert not (assets / "licenses" / "tesseract").exists()
    vision = manifest["assets"]["vision_ocr"]
    assert vision["source"] == "repository://vision-ocr"
    assert vision["license"] == "MIT"
    assert {"swift", "sdk"} <= set(vision)
    (vision_entry,) = [item for item in manifest["files"] if item["path"].startswith("vision-ocr/")]
    assert vision_entry == {
        **vision_entry,
        "path": "vision-ocr/unname-vision-ocr",
        "kind": "binary",
    }
    notices = json.loads((assets / "THIRD_PARTY_NOTICES.json").read_text())
    assert notices["components"] == [], "Vision gehoert zum System; kein Hinweis"
    validate_bundled_assets(assets, verify_hashes=True)


def test_mac_trust_lock_has_no_tesseract(
    prepare_module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock = tmp_path / "lock.json"
    monkeypatch.setattr(prepare_module, "__file__", str(tmp_path / "scripts" / "x.py"))
    lock.write_text(
        json.dumps(
            {
                "schema": 1,
                "assets": {
                    "macos-arm64": {"target": "aarch64-apple-darwin"},
                    "macos-alt": {
                        "target": "aarch64-apple-darwin",
                        "tesseract_tree_sha256": "b" * 64,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    loaded = prepare_module._load_asset_trust(
        lock,
        "macos-arm64",
        target="aarch64-apple-darwin",
        gliner_revision=None,
        tessdata_revision=None,
    )
    assert loaded.tesseract_tree_sha256 is None
    with pytest.raises(prepare_module.AssetPreparationError, match="unvollstaendig"):
        prepare_module._load_asset_trust(
            lock,
            "macos-alt",
            target="aarch64-apple-darwin",
            gliner_revision=None,
            tessdata_revision=None,
        )
    args = prepare_module.parse_args(
        ["--target", "aarch64-apple-darwin", "--tesseract-root", str(tmp_path)]
    )
    with pytest.raises(prepare_module.AssetPreparationError, match="kein Tesseract"):
        prepare_module._input_paths_from_args(args)


def test_macos_runtime_copies_no_unhashed_dylib_from_buildhost(
    prepare_module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "trusted-tesseract"
    executable = root / "bin" / "tesseract"
    external = tmp_path / "homebrew" / "libuntrusted.dylib"
    _write(executable, b"mach-o")
    _write(external, b"dylib")
    monkeypatch.setattr(
        prepare_module,
        "_otool_references",
        lambda _binary: (str(external),),
    )

    with pytest.raises(
        prepare_module.AssetPreparationError,
        match="ausserhalb des gehashten Quellbaums",
    ):
        prepare_module._macos_dependencies(executable, root)


def _pe_with_imports(imports: tuple[str, ...], delay: tuple[str, ...] = ()) -> bytes:
    """Minimal PE32+ file with a section containing both import tables."""

    image = bytearray(0x400)
    image[0:2] = b"MZ"
    image[0x3C:0x40] = (0x40).to_bytes(4, "little")
    image[0x40:0x44] = b"PE\0\0"
    coff = 0x44
    image[coff : coff + 2] = (0x8664).to_bytes(2, "little")
    image[coff + 2 : coff + 4] = (1).to_bytes(2, "little")
    image[coff + 16 : coff + 18] = (240).to_bytes(2, "little")
    optional = coff + 20
    image[optional : optional + 2] = (0x20B).to_bytes(2, "little")
    image[optional + 108 : optional + 112] = (16).to_bytes(4, "little")
    directories = optional + 112
    image[directories + 8 : directories + 12] = (0x1000).to_bytes(4, "little")
    if delay:
        image[directories + 104 : directories + 108] = (0x1080).to_bytes(4, "little")
    section = optional + 240
    image[section : section + 8] = b".idata\0\0"
    for offset, value in ((8, 0x200), (12, 0x1000), (16, 0x200), (20, 0x200)):
        image[section + offset : section + offset + 4] = value.to_bytes(4, "little")

    def rva(offset: int) -> int:
        return 0x200 + offset - 0x1000

    name = 0x1100
    for index, dll in enumerate(imports):
        start = rva(0x1000 + 20 * index)
        image[start + 12 : start + 16] = name.to_bytes(4, "little")
        image[rva(name) : rva(name) + len(dll)] = dll.encode("ascii")
        name += 0x20
    for index, dll in enumerate(delay):
        start = rva(0x1080 + 32 * index)
        image[start : start + 4] = (1).to_bytes(4, "little")
        image[start + 4 : start + 8] = name.to_bytes(4, "little")
        image[rva(name) : rva(name) + len(dll)] = dll.encode("ascii")
        name += 0x20
    return bytes(image)


def test_pe_imports_are_read_without_dumpbin(prepare_module: ModuleType, tmp_path: Path) -> None:
    binary = tmp_path / "tesseract.exe"
    binary.write_bytes(
        _pe_with_imports(("KERNEL32.dll", "leptonica-1.87.0.dll"), delay=("libcurl.dll",))
    )

    assert prepare_module.windows_imports(binary) == (
        "KERNEL32.dll",
        "leptonica-1.87.0.dll",
        "libcurl.dll",
    )
    assert prepare_module.is_system_dll("KERNEL32.dll")
    assert prepare_module.is_system_dll("api-ms-win-crt-runtime-l1-1-0.dll")
    assert prepare_module.is_system_dll("XmlLite.dll")
    assert not prepare_module.is_system_dll("leptonica-1.87.0.dll")

    binary.write_bytes(b"MZ kaputt")
    with pytest.raises(prepare_module.AssetPreparationError, match="PE-Importe"):
        prepare_module.windows_imports(binary)


def test_static_windows_tesseract_needs_no_dlls(prepare_module: ModuleType, tmp_path: Path) -> None:
    root = tmp_path / "tesseract"
    executable = root / "bin" / "tesseract.exe"
    _write(executable, _pe_with_imports(("KERNEL32.dll", "WS2_32.dll", "ADVAPI32.dll")))

    prepare_module._prepare_tesseract_runtime(
        executable, root, tmp_path / "out", target="x86_64-pc-windows-msvc", epoch=0
    )
    assert [path.name for path in (tmp_path / "out" / "bin").iterdir()] == ["tesseract.exe"]

    _write(executable, _pe_with_imports(("KERNEL32.dll", "leptonica-1.87.0.dll")))
    with pytest.raises(prepare_module.AssetPreparationError, match="leptonica-1.87.0.dll"):
        prepare_module._prepare_tesseract_runtime(
            executable, root, tmp_path / "out2", target="x86_64-pc-windows-msvc", epoch=0
        )


def test_pyinstaller_spec_treats_tesseract_as_native_binaries() -> None:
    source = SPEC_FILE.read_text(encoding="utf-8")

    assert 'if entry.get("kind") == "binary":' in source
    assert "binaries.append(item)" in source
    assert "codesign_identity=CODESIGN_IDENTITY" in source
    assert "entitlements_file=ENTITLEMENTS_FILE" in source


def test_pyinstaller_spec_passes_binaries_signature_and_entitlements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import PyInstaller.utils.hooks

    assets = tmp_path / "assets"
    native = assets / "tesseract/lib/libtesseract.dylib"
    data = assets / "models/gliner/model.safetensors"
    _write(native, b"mach-o")
    _write(data, b"weights")
    (assets / "asset-manifest.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "target": "aarch64-apple-darwin",
                "files": [
                    {"path": "tesseract/lib/libtesseract.dylib", "kind": "binary"},
                    {"path": "models/gliner/model.safetensors", "kind": "data"},
                ],
            }
        ),
        encoding="utf-8",
    )
    entitlements = tmp_path / "SidecarEntitlements.plist"
    entitlements.write_text("<plist/>", encoding="utf-8")
    monkeypatch.setenv("PRESIDIO_SIDECAR_TARGET", "aarch64-apple-darwin")
    monkeypatch.setenv("PRESIDIO_SIDECAR_BINARY_NAME", "presidio-sidecar-test")
    monkeypatch.setenv("PRESIDIO_SIDECAR_LAYOUT", "onedir")
    monkeypatch.setenv("PRESIDIO_SIDECAR_EXECUTABLE_NAME", "presidio-sidecar")
    monkeypatch.setenv("PRESIDIO_SIDECAR_ASSETS", str(assets))
    monkeypatch.setenv("PRESIDIO_CODESIGN_IDENTITY", "Developer ID Application: Test")
    monkeypatch.setenv("PRESIDIO_ENTITLEMENTS_FILE", str(entitlements))
    monkeypatch.setattr(PyInstaller.utils.hooks, "collect_all", lambda _package: ([], [], []))
    monkeypatch.setattr(PyInstaller.utils.hooks, "collect_submodules", lambda _package: [])
    monkeypatch.setattr(
        PyInstaller.utils.hooks,
        "copy_metadata",
        lambda _distribution, recursive: [],
    )

    captured: dict[str, object] = {}

    class FakeAnalysis:
        def __init__(self, _scripts, **kwargs) -> None:  # noqa: ANN001
            captured["analysis"] = kwargs
            self.pure = []
            self.scripts = []
            self.binaries = kwargs["binaries"]
            self.datas = kwargs["datas"]

    exe_object = object()

    def fake_exe(*args, **kwargs):  # noqa: ANN202
        captured["exe_args"] = args
        captured["exe"] = kwargs
        return exe_object

    def fake_collect(*args, **kwargs):  # noqa: ANN202
        captured["collect_args"] = args
        captured["collect"] = kwargs
        return object()

    runpy.run_path(
        str(SPEC_FILE),
        init_globals={
            "SPECPATH": str(REPOSITORY / "sidecar"),
            "Analysis": FakeAnalysis,
            "PYZ": lambda _pure: object(),
            "EXE": fake_exe,
            "COLLECT": fake_collect,
        },
    )

    analysis = captured["analysis"]
    assert (str(native), "assets/tesseract/lib") in analysis["binaries"]
    assert (str(data), "assets/models/gliner") in analysis["datas"]
    executable = captured["exe"]
    assert executable["codesign_identity"] == "Developer ID Application: Test"
    assert executable["entitlements_file"] == str(entitlements)
    assert executable["target_arch"] == "arm64"
    # onedir: the executable leaves the binaries to COLLECT, which signs them
    # with the identity and entitlements it inherits from the executable.
    assert executable["exclude_binaries"] is True
    assert executable["name"] == "presidio-sidecar"
    collect_args = captured["collect_args"]
    assert collect_args[0] is exe_object
    assert (str(native), "assets/tesseract/lib") in collect_args[1]
    assert (str(data), "assets/models/gliner") in collect_args[2]
    assert captured["collect"]["name"] == "presidio-sidecar-test"


def test_pyinstaller_spec_builds_onefile_without_collect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import PyInstaller.utils.hooks

    assets = tmp_path / "assets"
    native = assets / "tesseract/tesseract.exe"
    _write(native, b"MZ")
    (assets / "asset-manifest.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "target": "x86_64-pc-windows-msvc",
                "files": [{"path": "tesseract/tesseract.exe", "kind": "binary"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PRESIDIO_SIDECAR_TARGET", "x86_64-pc-windows-msvc")
    monkeypatch.setenv("PRESIDIO_SIDECAR_BINARY_NAME", "presidio-sidecar-test")
    monkeypatch.setenv("PRESIDIO_SIDECAR_LAYOUT", "onefile")
    monkeypatch.delenv("PRESIDIO_SIDECAR_EXECUTABLE_NAME", raising=False)
    monkeypatch.setenv("PRESIDIO_SIDECAR_ASSETS", str(assets))
    monkeypatch.delenv("PRESIDIO_CODESIGN_IDENTITY", raising=False)
    monkeypatch.delenv("PRESIDIO_ENTITLEMENTS_FILE", raising=False)
    monkeypatch.setattr(PyInstaller.utils.hooks, "collect_all", lambda _package: ([], [], []))
    monkeypatch.setattr(PyInstaller.utils.hooks, "collect_submodules", lambda _package: [])
    monkeypatch.setattr(
        PyInstaller.utils.hooks,
        "copy_metadata",
        lambda _distribution, recursive: [],
    )

    captured: dict[str, object] = {}

    class FakeAnalysis:
        def __init__(self, _scripts, **kwargs) -> None:  # noqa: ANN001
            self.pure = []
            self.scripts = []
            self.binaries = kwargs["binaries"]
            self.datas = kwargs["datas"]

    def fake_exe(*args, **kwargs):  # noqa: ANN202
        captured["exe_args"] = args
        captured["exe"] = kwargs
        return object()

    def no_collect(*_args, **_kwargs):  # noqa: ANN202
        raise AssertionError("onefile darf COLLECT nicht aufrufen")

    runpy.run_path(
        str(SPEC_FILE),
        init_globals={
            "SPECPATH": str(REPOSITORY / "sidecar"),
            "Analysis": FakeAnalysis,
            "PYZ": lambda _pure: object(),
            "EXE": fake_exe,
            "COLLECT": no_collect,
        },
    )

    executable = captured["exe"]
    assert executable["name"] == "presidio-sidecar-test"
    assert "exclude_binaries" not in executable
    # onefile packs binaries and data into the executable itself.
    assert (str(native), "assets/tesseract") in captured["exe_args"][2]


@pytest.mark.parametrize(
    ("layout", "executable_name", "message"),
    [
        ("", "presidio-sidecar", "PRESIDIO_SIDECAR_LAYOUT"),
        ("onedir", "", "PRESIDIO_SIDECAR_EXECUTABLE_NAME"),
    ],
)
def test_pyinstaller_spec_rejects_missing_layout_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    layout: str,
    executable_name: str,
    message: str,
) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "asset-manifest.json").write_text(
        json.dumps({"schema": 1, "target": "aarch64-apple-darwin", "files": []}),
        encoding="utf-8",
    )
    monkeypatch.setenv("PRESIDIO_SIDECAR_TARGET", "aarch64-apple-darwin")
    monkeypatch.setenv("PRESIDIO_SIDECAR_BINARY_NAME", "presidio-sidecar-test")
    monkeypatch.setenv("PRESIDIO_SIDECAR_LAYOUT", layout)
    monkeypatch.setenv("PRESIDIO_SIDECAR_EXECUTABLE_NAME", executable_name)
    monkeypatch.setenv("PRESIDIO_SIDECAR_ASSETS", str(assets))

    with pytest.raises(SystemExit, match=message):
        runpy.run_path(str(SPEC_FILE), init_globals={"SPECPATH": str(REPOSITORY / "sidecar")})


def test_build_script_accepts_target_alias_and_tauri_naming_convention(
    tmp_path: Path,
) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js ist fuer den plattformneutralen Buildvertrag nicht installiert")
    target = subprocess.run(
        [node, str(BUILD_SCRIPT), "--print-target"],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    ).stdout.strip()
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "asset-manifest.json").write_text(
        json.dumps({"schema": 1, "target": target}), encoding="utf-8"
    )

    result = subprocess.run(
        [
            node,
            str(BUILD_SCRIPT),
            "--target",
            target,
            "--skip-prepare",
            "--assets-only",
            "--assets-dir",
            str(assets),
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    source = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert "presidio-sidecar-${target}" in source
    assert 'app", "src-tauri", "binaries' in source
