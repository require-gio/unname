"""Office engine for page-faithful Word view (layout plan, spike S; E-L5).

The engine converts a DOCX to a PDF – DOCX in, PDF out. It is a separate program
(``office-engine/``, ``unname-office-engine``) with two commands: ``info`` and
``docx-to-pdf``. Data is passed via stdin/stdout; the engine never sees a file
path, writes no files and does not access the network. This keeps it replaceable
until the spike decides which engine to use.

It is found

- in the bundle under ``assets/office-engine/`` – only when the build includes
  it. In a frozen sidecar this is the only location: no external program is
  allowed (spec §10);
- during development via ``PRESIDIO_OFFICE_ENGINE`` or the release build in the
  repository (``office-engine/target/release``).

Without an engine the “page-faithful” mode is unavailable; Word falls back to
the preview copy (docx-preview).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final

from .resources import bundled_assets_root, is_frozen, repository_root

OFFICE_ENGINE_ENV: Final = "PRESIDIO_OFFICE_ENGINE"
#: Interface version defined in ``office-engine/src/main.rs``.
PROTOCOL: Final = 1
#: Max time for a conversion; the engine is terminated afterwards.
CONVERT_TIMEOUT_SECONDS: Final = 60.0
_INFO_TIMEOUT_SECONDS: Final = 5.0
#: PDFs larger than this are rejected.
MAX_PDF_BYTES: Final = 256 * 1024 * 1024
#: Only environment variables required for startup – no session data.
_ENVIRONMENT_KEYS: Final = ("HOME", "LOCALAPPDATA", "SYSTEMROOT", "WINDIR", "TMPDIR", "TEMP", "TMP")

#: One conversion at a time: the engine may use several hundred MB.
_CONVERT_LOCK = threading.Lock()


class OfficeEngineError(RuntimeError):
    """Engine missing or failed; no document text available."""


@dataclass(frozen=True, slots=True)
class OfficeEngine:
    executable: Path
    #: Name and version from the helper file, e.g. ``office2pdf``.
    name: str
    version: str


def engine_executable() -> Path | None:
    """Path to the program or ``None`` if not present."""

    name = "unname-office-engine.exe" if sys.platform == "win32" else "unname-office-engine"
    assets = bundled_assets_root()
    if assets is not None:
        bundled = assets / "office-engine" / name
        if bundled.is_file() or is_frozen():
            return bundled if bundled.is_file() else None
    configured = os.environ.get(OFFICE_ENGINE_ENV)
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_file() else None
    built = repository_root() / "office-engine" / "target" / "release" / name
    return built if built.is_file() else None


@lru_cache(maxsize=1)
def office_engine() -> OfficeEngine | None:
    """Engine for this process, queried once; ``None`` if no usable engine."""

    executable = engine_executable()
    if executable is None:
        return None
    try:
        completed = subprocess.run(
            [str(executable), "info"],
            capture_output=True,
            timeout=_INFO_TIMEOUT_SECONDS,
            env=_environment(),
            check=False,
            **_platform_options(),
        )
        info = json.loads(completed.stdout.decode("utf-8"))
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError, ValueError):
        return None
    if (
        completed.returncode != 0
        or not isinstance(info, dict)
        or info.get("protocol") != PROTOCOL
        or not isinstance(info.get("engine"), str)
        or not isinstance(info.get("version"), str)
    ):
        return None
    return OfficeEngine(executable=executable, name=info["engine"], version=info["version"])


def docx_to_pdf(engine: OfficeEngine, data: bytes) -> bytes:
    """Convert a DOCX (bytes) to a PDF (bytes)."""

    with _CONVERT_LOCK:
        try:
            completed = subprocess.run(
                [str(engine.executable), "docx-to-pdf"],
                input=data,
                capture_output=True,
                timeout=CONVERT_TIMEOUT_SECONDS,
                env=_environment(),
                check=False,
                **_platform_options(),
            )
        except subprocess.TimeoutExpired as error:
            raise OfficeEngineError("Die Office-Engine hat zu lange gebraucht.") from error
        except (OSError, subprocess.SubprocessError) as error:
            raise OfficeEngineError("Die Office-Engine laesst sich nicht starten.") from error
    if completed.returncode != 0:
        raise OfficeEngineError(
            f"Die Office-Engine konnte die Datei nicht umwandeln (Code {completed.returncode})."
        )
    pdf = completed.stdout
    if not pdf.startswith(b"%PDF-") or len(pdf) > MAX_PDF_BYTES:
        raise OfficeEngineError("Die Office-Engine hat kein brauchbares PDF geliefert.")
    return pdf


def _environment() -> dict[str, str]:
    return {key: value for key in _ENVIRONMENT_KEYS if (value := os.environ.get(key))}


def _platform_options() -> dict[str, int]:
    # On Windows without a flashing console window.
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}
