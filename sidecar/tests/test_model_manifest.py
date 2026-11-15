"""Manifest of the signed app and tool for the model package."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path, PurePosixPath
from types import ModuleType
from urllib.parse import urlparse

import pytest

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "app" / "src-tauri" / "models.lock.json"


def _tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("modelle", REPO / "scripts/models/modelle.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_loads_only_data_over_https_from_allowed_hosts() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    tool = _tool()
    assert manifest["schema"] == 1
    assert [component["id"] for component in manifest["components"]] == [
        "ner",
        "de",
        "en",
        "ocr",
        "classifier",
    ]
    for component in manifest["components"]:
        for file_entry in component["files"]:
            paths = [
                file_entry["path"],
                *(f["path"] for f in file_entry.get("unpack", {}).get("files", [])),
            ]
            for path in paths:
                assert not PurePosixPath(path).is_absolute() and ".." not in path
                assert not path.endswith(tool.FORBIDDEN), path
                assert "pytorch_model" not in path
            assert len(file_entry["sha256"]) == 64 and file_entry["size"] > 0
            assert set(file_entry["urls"]) == set(manifest["sources"])
            for source, url in file_entry["urls"].items():
                address = urlparse(url)
                hosts = manifest["sources"][source]["hosts"]
                assert address.scheme == "https"
                assert any(
                    address.hostname == host
                    or (host.startswith("*.") and address.hostname.endswith(host[1:]))
                    for host in hosts
                ), url
    # Only Windows includes tessdata; macOS uses Apple Vision.
    platforms = {
        component["id"]: component.get("platforms") for component in manifest["components"]
    }
    assert platforms == {
        "ner": None,
        "de": None,
        "en": None,
        "ocr": ["windows"],
        "classifier": None,
    }
    # The name detection model is ONNX; the language model is provided as data only.
    ner = manifest["components"][0]
    assert any(file_entry["path"].endswith(".onnx") for file_entry in ner["files"])
    # Language packages: each language gets a small spaCy model, hash matches uv.lock.
    for component in manifest["components"][1:3]:
        assert component["kind"] == "language"
        package, expected = tool.LANGUAGE_PACKAGES[component["id"]]
        wheel = component["files"][0]
        assert wheel["sha256"] == expected
        assert wheel["path"].startswith(package) and wheel["size"] < 20_000_000
        assert expected in (REPO / "sidecar" / "uv.lock").read_text(encoding="utf-8")
        assert all(not f["path"].endswith(".py") for f in wheel["unpack"]["files"])
        assert any(f["path"] == "ner/model" for f in wheel["unpack"]["files"])


def test_package_contains_only_verified_files(tmp_path: Path) -> None:
    tool = _tool()
    content = b"onnx" * 100
    (tmp_path / "ner").mkdir()
    (tmp_path / "ner" / "model.int8.onnx").write_bytes(content)
    manifest = {
        "revision": "test",
        "components": [
            {
                "id": "ner",
                "files": [
                    {
                        "path": "model.int8.onnx",
                        "size": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                ],
            }
        ],
    }
    package = tmp_path / "paket.zip"
    tool.build_package(manifest, {"ner": tmp_path / "ner"}, package)
    with zipfile.ZipFile(package) as archive:
        assert archive.read("ner/model.int8.onnx") == content
        assert archive.getinfo("ner/model.int8.onnx").compress_type == zipfile.ZIP_STORED
        header = json.loads(archive.read("unname-modellpaket.json"))
        assert header == {
            "format": "unname-modellpaket",
            "manifest_revision": "test",
            "components": ["ner"],
        }

    (tmp_path / "ner" / "model.int8.onnx").write_bytes(b"x" * len(content))
    with pytest.raises(SystemExit, match="weicht vom Manifest ab"):
        tool.build_package(manifest, {"ner": tmp_path / "ner"}, tmp_path / "zwei.zip")
