"""Text recognition via Apple Vision using the ``unname-vision-ocr`` helper.

The helper is a standalone Swift program (``vision-ocr/``) with two commands:
``info`` and ``recognize``. The image is sent as PPM via stdin; words are
returned as a single JSON line in reading order. The helper receives no path,
writes no file, and has no network access; a crash or timeout affects only this
process.

It is located like the Office engine (``resources.vision_ocr_executable``):
frozen only in the bundle, or in development via ``PRESIDIO_VISION_OCR`` or
``vision-ocr/.build/release``.

The first call per process, location, and macOS build takes ≈ 50 s: Vision
compiles its models for the Neural Engine (``ANECompilerService``, cache under
``/Library/Caches/com.apple.aned/<Build>``; measured in U0, no network traffic).
Therefore, the sidecar warms up at startup, and the first call of a session
gets a longer timeout.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from ..resources import vision_ocr_executable
from .ocr import APPLE_VISION, OcrEngine, OcrUnavailableError, OcrWord

#: Version of the interface in ``vision-ocr/Sources/VisionOCRCore/Protocol.swift``.
PROTOCOL: Final = 1
#: Level 1 locks revision 3; if missing, the helper fails (Exit 5).
TEXT_REVISION: Final = 3
#: Tesseract-Sprachcodes des Sidecars → Vision.
LANGUAGES: Final = {"deu": "de-DE", "eng": "en-US"}
RECOGNIZE_TIMEOUT_SECONDS: Final = 120.0
#: The first call of a session may trigger compilation (≈ 50 s).
FIRST_RECOGNIZE_TIMEOUT_SECONDS: Final = 300.0
_INFO_TIMEOUT_SECONDS: Final = 10.0
#: Matches ``max_image_pixels`` in the extractor and the limit in the helper.
MAX_IMAGE_PIXELS: Final = 100_000_000
#: Only what the helper needs to start – no session variables.
_ENVIRONMENT_KEYS: Final = ("HOME", "TMPDIR")

#: Exit-Codes des Helfers (Protokoll 1).
_EXIT_MESSAGES: Final = {
    2: "Der Vision-Helfer wurde falsch aufgerufen.",
    3: "Das Bild ist fuer die Texterkennung zu gross.",
    4: "Die Texterkennung mit Apple Vision ist gescheitert.",
    5: "Apple Vision unterstuetzt die Sprache oder Revision auf diesem macOS nicht.",
}


@dataclass(frozen=True, slots=True)
class VisionHelperInfo:
    executable: Path
    helper: str
    os_version: str
    os_build: str
    text_revisions: tuple[int, ...]
    document_revisions: tuple[int, ...]
    languages: tuple[str, ...]

    def engine(self, *, request: str = "text") -> OcrEngine:
        return OcrEngine(
            name=APPLE_VISION,
            version=self.helper,
            request=request,
            revision=TEXT_REVISION if request == "text" else 1,
            os_version=self.os_version,
            os_build=self.os_build,
            readback_layout="sheet",
        )


_probe_lock = threading.Lock()
_probe: VisionHelperInfo | OcrUnavailableError | None = None
_probe_executable: Path | None = None


def vision_helper_info(*, refresh: bool = False) -> VisionHelperInfo:
    """Queries the helper once per process (``info``); fails with a reason.

    Validates the protocol, revision 3, and languages de-DE and en-US.
    The result – even a failure – persists for the process as long as the same
    helper is used.
    """

    global _probe, _probe_executable
    executable = vision_ocr_executable()
    with _probe_lock:
        if not refresh and _probe is not None and _probe_executable == executable:
            if isinstance(_probe, OcrUnavailableError):
                raise _probe
            return _probe
        _probe_executable = executable
        try:
            _probe = _ask_info(executable)
        except OcrUnavailableError as error:
            _probe = error
            raise
        return _probe


def cached_probe_failure() -> OcrUnavailableError | None:
    """The failure of the last probe, without a new call (for ``/health``)."""

    probe = _probe
    return probe if isinstance(probe, OcrUnavailableError) else None


def cached_helper_info() -> VisionHelperInfo | None:
    probe = _probe
    return probe if isinstance(probe, VisionHelperInfo) else None


def reset_probe() -> None:
    """Only for tests: discard the stored sample."""

    global _probe, _probe_executable
    with _probe_lock:
        _probe = None
        _probe_executable = None


def _ask_info(executable: Path | None) -> VisionHelperInfo:
    if executable is None:
        raise OcrUnavailableError("Der Vision-Helfer fehlt.")
    try:
        completed = subprocess.run(
            [str(executable), "info"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=_INFO_TIMEOUT_SECONDS,
            env=_environment(),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise OcrUnavailableError("Der Vision-Helfer laesst sich nicht starten.") from error
    if completed.returncode == 5:
        raise OcrUnavailableError("Apple Vision ist auf diesem macOS nicht verfuegbar.")
    if completed.returncode != 0:
        raise OcrUnavailableError(
            f"Der Vision-Helfer meldet einen Fehler (Code {completed.returncode})."
        )
    try:
        info = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise OcrUnavailableError("Der Vision-Helfer antwortet nicht im Protokoll.") from error
    if (
        not isinstance(info, dict)
        or info.get("engine") != APPLE_VISION
        or info.get("protocol") != PROTOCOL
        or not _strings(info.get("languages"))
        or not _integers(info.get("text_revisions"))
        or not _integers(info.get("document_revisions", []))
        or not all(isinstance(info.get(key), str) for key in ("helper", "os", "os_build"))
    ):
        raise OcrUnavailableError("Der Vision-Helfer antwortet nicht im Protokoll.")
    if TEXT_REVISION not in info["text_revisions"]:
        raise OcrUnavailableError(
            f"Apple Vision bietet auf diesem macOS die Revision {TEXT_REVISION} nicht an."
        )
    missing = [code for code in LANGUAGES.values() if code not in info["languages"]]
    if missing:
        raise OcrUnavailableError(
            "Apple Vision kennt auf diesem macOS die Sprache nicht: " + ", ".join(missing)
        )
    return VisionHelperInfo(
        executable=executable,
        helper=info["helper"],
        os_version=info["os"],
        os_build=info["os_build"],
        text_revisions=tuple(info["text_revisions"]),
        document_revisions=tuple(info.get("document_revisions", [])),
        languages=tuple(info["languages"]),
    )


def vision_languages(languages: tuple[str, ...]) -> tuple[str, ...]:
    """``deu``/``eng`` → ``de-DE``/``en-US``; unknown languages are unavailable."""

    mapped: list[str] = []
    for language in languages:
        code = LANGUAGES.get(language)
        if code is None:
            raise OcrUnavailableError(
                "Apple Vision ist nur fuer Deutsch und Englisch eingerichtet."
            )
        if code not in mapped:
            mapped.append(code)
    if not mapped:
        raise OcrUnavailableError("Die Texterkennung braucht mindestens eine Sprache.")
    return tuple(mapped)


def image_to_netpbm(image: object) -> tuple[bytes, int, int]:
    """PIL image → PPM (P6) or PGM (P5), without encoding."""

    from PIL import Image

    if not isinstance(image, Image.Image):
        raise OcrUnavailableError("Die Texterkennung erwartet ein Bild.")
    if image.mode not in {"RGB", "L"}:
        image = image.convert("RGB")
    width, height = image.size
    if width < 1 or height < 1:
        raise OcrUnavailableError("Das Bild ist leer.")
    if width * height > MAX_IMAGE_PIXELS:
        raise OcrUnavailableError("Das Bild ist fuer die Texterkennung zu gross.")
    magic = b"P6" if image.mode == "RGB" else b"P5"
    return magic + b"\n%d %d\n255\n" % (width, height) + image.tobytes(), width, height


class VisionOcrBackend:
    """``OcrBackend`` with Apple Vision; one helper process per call (E-U4)."""

    engine_name = APPLE_VISION

    def __init__(
        self,
        *,
        timeout_seconds: float = RECOGNIZE_TIMEOUT_SECONDS,
        first_timeout_seconds: float = FIRST_RECOGNIZE_TIMEOUT_SECONDS,
        tiles: str = "auto",
        language_correction: bool = False,
    ) -> None:
        if timeout_seconds <= 0 or first_timeout_seconds <= 0:
            raise ValueError("Zeitlimits muessen positiv sein.")
        if tiles not in {"auto", "1x1", "2x2", "3x3"}:
            raise ValueError("tiles muss auto, 1x1, 2x2 oder 3x3 sein.")
        self.timeout_seconds = timeout_seconds
        self.first_timeout_seconds = first_timeout_seconds
        self.tiles = tiles
        self.language_correction = language_correction
        self._warm = False
        self._last_elapsed_ms: int | None = None

    @property
    def engine(self) -> OcrEngine | None:
        info = cached_helper_info()
        return info.engine() if info is not None else None

    @property
    def warmed_up(self) -> bool:
        return self._warm

    @property
    def last_elapsed_ms(self) -> int | None:
        return self._last_elapsed_ms

    def recognize(self, image: object, *, languages: tuple[str, ...]) -> tuple[OcrWord, ...]:
        info = vision_helper_info()
        mapped = vision_languages(languages)
        payload, width, height = image_to_netpbm(image)
        command = [
            str(info.executable),
            "recognize",
            "--languages",
            ",".join(mapped),
            "--tiles",
            self.tiles,
            "--correction",
            "on" if self.language_correction else "off",
            "--qos",
            "user-initiated",
        ]
        timeout = self.timeout_seconds if self._warm else self.first_timeout_seconds
        try:
            completed = subprocess.run(
                command,
                input=payload,
                capture_output=True,
                timeout=timeout,
                env=_environment(),
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise OcrUnavailableError("Die Texterkennung hat zu lange gebraucht.") from error
        except (OSError, subprocess.SubprocessError) as error:
            raise OcrUnavailableError("Der Vision-Helfer laesst sich nicht starten.") from error
        if completed.returncode != 0:
            raise OcrUnavailableError(
                _EXIT_MESSAGES.get(
                    completed.returncode,
                    f"Der Vision-Helfer ist gescheitert (Code {completed.returncode}).",
                )
            )
        words, elapsed = parse_response(completed.stdout, width=width, height=height)
        self._warm = True
        self._last_elapsed_ms = elapsed
        return words


def parse_response(raw: bytes, *, width: int, height: int) -> tuple[tuple[OcrWord, ...], int]:
    """Strictly validates the response and converts it to ``OcrWord``.

    Types, image size, and every box in the image; an error names no content.
    """

    try:
        response = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise OcrUnavailableError("Der Vision-Helfer antwortet nicht im Protokoll.") from error
    if (
        not isinstance(response, dict)
        or response.get("protocol") != PROTOCOL
        or response.get("width") != width
        or response.get("height") != height
        or not isinstance(response.get("words"), list)
        or not _integer(response.get("elapsed_ms"))
    ):
        raise OcrUnavailableError("Der Vision-Helfer antwortet nicht im Protokoll.")
    if response.get("request") == "text" and response.get("revision") != TEXT_REVISION:
        raise OcrUnavailableError("Der Vision-Helfer hat eine andere Revision verwendet.")
    words: list[OcrWord] = []
    for item in response["words"]:
        words.append(_word(item, width=width, height=height))
    return tuple(words), response["elapsed_ms"]


def _word(item: Any, *, width: int, height: int) -> OcrWord:
    invalid = OcrUnavailableError("Der Vision-Helfer lieferte ungueltige Positionsdaten.")
    if not isinstance(item, dict):
        raise invalid
    text = item.get("text")
    box = [item.get(key) for key in ("left", "top", "width", "height")]
    line = item.get("line")
    confidence = item.get("confidence")
    if (
        not isinstance(text, str)
        or not text.strip()
        or not all(_integer(value) for value in box)
        or not isinstance(line, list)
        or len(line) != 3
        or not all(_integer(value) and value >= 1 for value in line)
        or isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(confidence)
        or not 0 <= confidence <= 1
    ):
        raise invalid
    left, top, box_width, box_height = box
    if (
        left < 0
        or top < 0
        or box_width < 1
        or box_height < 1
        or left + box_width > width
        or top + box_height > height
    ):
        raise invalid
    return OcrWord(
        text=text,
        left=left,
        top=top,
        width=box_width,
        height=box_height,
        # Like Tesseract: page, block, paragraph, line.
        line_id=(1, line[0], line[1], line[2]),
        confidence=float(confidence),
    )


def warm_up(backend: VisionOcrBackend | None = None) -> int:
    """Recognize a mini-image; duration in milliseconds.

    If the call triggers Neural Engine compilation, it takes ≈ 50 s – better
    here in the background than during the first scan.
    """

    from PIL import Image, ImageDraw

    image = Image.new("L", (320, 96), 255)
    ImageDraw.Draw(image).text((16, 24), "unname", fill=0, font_size=40)
    started = time.monotonic()
    (backend or VisionOcrBackend()).recognize(image, languages=("deu", "eng"))
    return round((time.monotonic() - started) * 1000)


def _environment() -> dict[str, str]:
    # Each child process receives its own stdin explicitly (``input=`` or ``DEVNULL``),
    # never the sidecar's: the guardian reads from this.
    return {key: value for key in _ENVIRONMENT_KEYS if (value := os.environ.get(key))}


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _integers(value: object) -> bool:
    return isinstance(value, list) and all(_integer(item) for item in value)


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)
