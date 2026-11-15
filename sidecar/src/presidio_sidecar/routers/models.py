"""GET /models – lists models available on this device.

Since the initial phase the shell loads the models once (``models.rs``):
named-entity recognition (GLiNER ONNX), language packs German and English
(spaCy, added later) and OCR data (Tesseract de/eng); optionally a second-opinion
classifier. On macOS the system OCR (Apple Vision) is used: it is provided by the
system and is neither downloaded nor removed.
Downloading, reviewing and removing are handled by the shell; the sidecar only
reports what it actually finds and its source: the shell’s model folder
(``download``), the signed bundle (``bundle``) or the development environment
(``local``).

Sizes are counted per folder and state once. This is deliberately omitted from
``/health``: the shell’s live check must stay cheap, and counting a model folder
on first run would be unnecessary.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from .. import resources
from ..api_models import ModelsResponse

_LANGUAGES = {"ner": [], "de": ["de"], "en": ["en"], "ocr": ["deu", "eng"], "classifier": []}


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/models")
    async def models() -> Any:
        return await run_in_threadpool(_inventory)

    return router


def _inventory() -> dict[str, Any]:
    components = [
        _component("ner", resources.gliner_location),
        _component("de", lambda: resources.spacy_location("de")),
        _component("en", lambda: resources.spacy_location("en")),
        _component("ocr", resources.ocr_location),
        _component("classifier", resources.classifier_location),
    ]
    payload = ModelsResponse.model_validate(
        {
            "components": components,
            "total_bytes": sum(item["size_bytes"] or 0 for item in components),
            "setup_required": resources.setup_required(
                {item["id"]: {"state": item["state"]} for item in components}
            ),
        }
    )
    return payload.model_dump(mode="json")


def _component(identifier: str, locate: Any) -> dict[str, Any]:
    try:
        location = locate()
    except (OSError, resources.BundledAssetError):
        location = resources.ComponentLocation(None, "download")
    if location.path is None:
        return {
            "id": identifier,
            "state": "missing",
            "source": location.source,
            "size_bytes": None,
            "languages": [],
        }
    if identifier == "ocr" and location.source == "system":
        # Apple Vision: part of macOS, no separate size.
        return {
            "id": identifier,
            "state": "ready",
            "source": "system",
            "size_bytes": None,
            "languages": ["de-DE", "en-US"],
        }
    # Development system Tesseract: size and languages are managed by the OS;
    # we only report its presence.
    system = identifier == "ocr" and location.source == "local"
    return {
        "id": identifier,
        "state": "ready",
        "source": location.source,
        "size_bytes": None if system else _size(location.path),
        "languages": [] if system else _LANGUAGES[identifier],
    }


def _size(path: Path) -> int:
    marker = path / resources.INSTALL_MARKER
    try:
        stamp = (marker if marker.is_file() else path).stat().st_mtime_ns
    except OSError:
        stamp = 0
    return _counted_size(str(path), stamp)


@lru_cache(maxsize=16)
def _counted_size(path: str, _stamp: int) -> int:
    total = 0
    for directory, _, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += (Path(directory) / name).lstat().st_size
            except OSError:
                continue
    return total


__all__ = ["build_router"]
