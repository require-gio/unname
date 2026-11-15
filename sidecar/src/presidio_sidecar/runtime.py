"""Runtime info for the health endpoint.

The report shows what is actually loaded—not what would be expected.
A sidecar reporting an unloaded model as ready is the silent failure spec §2 forbids.

The one-time device detection lives in ``hardware.py`` and is fed into the
services. This fallback is only for isolated callers of
``health_report``; it claims no GPU without this finding.
"""

from __future__ import annotations

import os
import platform
import sys
import time
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from . import __version__

#: Packages whose version appears in the health report. For debugging the
#: Presidio version is the first question.
TRACKED_PACKAGES = (
    "presidio-analyzer",
    "presidio-anonymizer",
    "presidio-structured",
    "presidio-image-redactor",
    "fastapi",
    "uvicorn",
    "pymupdf",
    "python-docx",
    "openpyxl",
    "onnxruntime",
    "tokenizers",
    "spacy",
)

_STARTED_AT = time.monotonic()


@lru_cache(maxsize=1)
def _component_versions() -> tuple[tuple[str, str | None], ...]:
    """Read package metadata once per process.

    ``importlib.metadata.version`` scans the install paths each call.
    The health endpoint is the live check of the shell and is called every
    second; dozens of directory scans per call would add overhead.
    Versions don't change during a session.
    """

    versions: list[tuple[str, str | None]] = []
    for package in TRACKED_PACKAGES:
        try:
            versions.append((package, version(package)))
        except PackageNotFoundError:
            versions.append((package, None))
    return tuple(versions)


def component_versions() -> dict[str, str | None]:
    """Installed versions; ``None`` if a package is missing."""
    return dict(_component_versions())


def nlp_engine_status() -> dict[str, Any]:
    """State of the NLP layer.

    Models are deliberately loaded lazily from local artifacts in phase 1.
    Until the first job, ``loaded`` is correctly ``False``.
    """
    return {
        "preferred": "gliner",
        "fallback": "de_core_news_lg",
        "loaded": False,
        "active": None,
        "load_policy": "lazy_local_only",
    }


def device_status() -> dict[str, Any]:
    """Conservative fallback without the cached capability check."""

    requested = os.environ.get("PRESIDIO_DEVICE", "auto")
    return {
        "requested": requested,
        "active": "cpu",
        "probe": "not_run",
        "mps_fallback_env": os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "1",
        "available": [],
        "gpus": [],
        "selection_reason": "capability_check_unavailable",
        "fallback_from": None,
        "fallback_reason": None,
    }


def uptime_seconds() -> float:
    return round(time.monotonic() - _STARTED_AT, 3)


def health_report(
    *,
    nlp_engine: dict[str, Any] | None = None,
    master_data: dict[str, Any] | None = None,
    workers: dict[str, Any] | None = None,
    device: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report = {
        "status": "ok",
        "service": "presidio-sidecar",
        "version": __version__,
        "uptime_s": uptime_seconds(),
        "python": sys.version.split()[0],
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
        },
        "components": component_versions(),
        "nlp_engine": nlp_engine or nlp_engine_status(),
        "device": device or device_status(),
    }
    if master_data is not None:
        report["master_data"] = master_data
    if workers is not None:
        report["workers"] = workers
    return report
