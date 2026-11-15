"""All resources are fully local, manifest-based and fail-closed."""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from presidio_sidecar import languages, resources
from presidio_sidecar.detection.ner import GLiNERNerAdapter, SpacyGermanNerAdapter


def _write(path: Path, content: bytes = b"test") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _complete_assets(root: Path) -> dict[str, object]:
    # On macOS use Vision OCR; otherwise include the Tesseract helper.
    mac = sys.platform == "darwin"
    executable = resources._required_ocr_executable()
    tessdata = (
        ()
        if mac
        else (
            "tesseract/tessdata/deu.traineddata",
            "tesseract/tessdata/eng.traineddata",
        )
    )
    paths = (
        "models/gliner/gliner_config.json",
        "models/gliner/model.safetensors",
        "models/de_core_news_lg/config.cfg",
        executable,
        *tessdata,
        "profiles/start.yaml",
        "stopwords/de_business.txt",
        "stopwords/de_sentence_start.txt",
        "stopwords/en_business.txt",
        "stopwords/en_sentence_start.txt",
        "licenses/Apache-2.0.txt",
        "licenses/model-license.txt",
    )
    files: list[dict[str, object]] = []
    for relative in paths:
        path = root / relative
        _write(path, relative.encode("utf-8"))
        files.append(
            {
                "path": relative,
                "size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "kind": "binary" if relative == executable else "data",
            }
        )

    external = {
        name: {
            "path": path,
            "source": f"https://example.invalid/{name}",
            "revision": "1.0.0",
            "license": "Apache-2.0",
            "license_files": ["licenses/Apache-2.0.txt"],
            "tree_sha256": "0" * 64,
        }
        for name, path in {
            "gliner": "models/gliner",
            "spacy_de_core_news_lg": "models/de_core_news_lg",
            **(
                {}
                if mac
                else {
                    "tesseract": "tesseract",
                    "tessdata_deu": "tesseract/tessdata/deu.traineddata",
                    "tessdata_eng": "tesseract/tessdata/eng.traineddata",
                }
            ),
        }.items()
    }
    internal: dict[str, object] = {
        "profiles": {"path": "profiles"},
        "stopwords": {"path": "stopwords"},
    }
    if mac:
        internal["vision_ocr"] = {"path": "vision-ocr"}
    manifest = {
        "schema": 1,
        "target": "test-target",
        "assets": external | internal,
        "files": files,
    }
    (root / resources.ASSET_MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def test_manifest_validates_layout_provenance_and_data_hashes(tmp_path: Path) -> None:
    _complete_assets(tmp_path)

    manifest = resources.validate_bundled_assets(tmp_path, verify_hashes=True)

    assert manifest["schema"] == 1
    assert manifest["assets"]["gliner"]["license"] == "Apache-2.0"


def test_new_language_packs_extend_required_files_automatically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _complete_assets(tmp_path)
    french = replace(
        languages.ENGLISH,
        code="fr",
        business_terms_file="fr_business.txt",
        sentence_starters_file="fr_sentence_start.txt",
    )
    monkeypatch.setattr(languages, "PACKS", {**languages.PACKS, "fr": french})

    with pytest.raises(resources.BundledAssetError, match="fr_business.txt"):
        resources.validate_bundled_assets(tmp_path)


def test_hash_mismatch_is_rejected_as_fail_closed(tmp_path: Path) -> None:
    _complete_assets(tmp_path)
    (tmp_path / "models/gliner/model.safetensors").write_bytes(b"manipuliert")

    with pytest.raises(resources.BundledAssetError, match="Hash-Pruefung"):
        resources.validate_bundled_assets(tmp_path, verify_hashes=True)


def test_missing_license_notice_is_rejected(tmp_path: Path) -> None:
    manifest = _complete_assets(tmp_path)
    manifest["assets"]["gliner"]["license_files"] = []
    (tmp_path / resources.ASSET_MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(resources.BundledAssetError, match="Lizenznachweis"):
        resources.validate_bundled_assets(tmp_path)


def test_required_assets_per_target() -> None:
    """Mac bundle uses Vision OCR instead of Tesseract."""

    assert resources._required_external_assets("darwin") == frozenset()
    assert resources._required_internal_assets("darwin") == {"profiles", "stopwords", "vision_ocr"}
    assert resources._required_ocr_executable("darwin") == "vision-ocr/unname-vision-ocr"
    assert resources._required_external_assets("win32") == {"tesseract"}
    assert resources._required_internal_assets("win32") == {"profiles", "stopwords"}
    assert resources._required_ocr_executable("win32") == "tesseract/bin/tesseract.exe"


def test_without_ocr_program_bundle_is_invalid(tmp_path: Path) -> None:
    manifest = _complete_assets(tmp_path)
    executable = resources._required_ocr_executable()
    manifest["files"] = [item for item in manifest["files"] if item["path"] != executable]
    (tmp_path / resources.ASSET_MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(resources.BundledAssetError, match="Pflichtdateien"):
        resources.validate_bundled_assets(tmp_path)


def test_frozen_runtime_ignores_external_asset_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "frozen"
    external = tmp_path / "extern"
    (runtime / "assets").mkdir(parents=True)
    external.mkdir()
    monkeypatch.setattr(resources.sys, "frozen", True, raising=False)
    monkeypatch.setattr(resources.sys, "_MEIPASS", str(runtime), raising=False)
    monkeypatch.setenv("PRESIDIO_BUNDLE_ASSETS_DIR", str(external))

    assert resources.bundled_assets_root() == runtime / "assets"
    assert resources.profiles_directory() == runtime / "assets" / "profiles"
    assert resources.wordlists_directory() == runtime / "assets" / "stopwords"


def test_ner_adapter_uses_prepared_local_model_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "models/gliner").mkdir(parents=True)
    (tmp_path / "models/de_core_news_lg").mkdir(parents=True)
    monkeypatch.setenv("PRESIDIO_BUNDLE_ASSETS_DIR", str(tmp_path))

    assert GLiNERNerAdapter().model_name == str(tmp_path / "models/gliner")
    assert SpacyGermanNerAdapter().model_name == str(tmp_path / "models/de_core_news_lg")


def test_offline_variables_cannot_be_disabled_by_startup_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "false")

    resources.configure_offline_runtime()

    assert resources.os.environ["HF_HUB_OFFLINE"] == "1"
    assert resources.os.environ["TRANSFORMERS_OFFLINE"] == "1"
    assert resources.os.environ["HF_DATASETS_OFFLINE"] == "1"


def test_manifest_paths_must_not_escape_bundle(tmp_path: Path) -> None:
    manifest = _complete_assets(tmp_path)
    manifest["files"][0]["path"] = "../ausbruch"
    (tmp_path / resources.ASSET_MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(resources.BundledAssetError, match="Unsicherer Pfad"):
        resources.validate_bundled_assets(tmp_path)
