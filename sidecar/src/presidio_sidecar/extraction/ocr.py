"""Text detection: the seamless ``OcrBackend`` interface and its platform-specific
selection.
selection.

On macOS, Apple Vision (``ocr_vision.py``, helper ``unname-vision-ocr``) is used;
otherwise Tesseract with the box schema from ``presidio-image-redactor``.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from threading import BoundedSemaphore, Lock
from typing import Any, Literal, Protocol

from ..resources import (
    APPLE_VISION,
    OCR_ENGINE_ENV,
    TESSERACT,
    BundledAssetError,
    selected_ocr_engine,
    tesseract_bundle,
)

__all__ = [
    "APPLE_VISION",
    "OCR_ENGINE_ENV",
    "TESSERACT",
    "LimitedOcrBackend",
    "OcrBackend",
    "OcrEngine",
    "OcrUnavailableError",
    "OcrWord",
    "TesseractOcrBackend",
    "default_ocr_backend",
    "ocr_engine_name",
    "readback_layout_of",
    "ocr_engine_of",
    "selected_ocr_engine",
]


class OcrUnavailableError(RuntimeError):
    """Text detection or the requested languages are unavailable."""


@dataclass(frozen=True, slots=True)
class OcrEngine:
    """Which OCR works – content-free, for diagnosis and ``/health``."""

    name: str
    version: str | None
    #: Vision: ``text`` (level 1) or ``document`` (level 2).
    request: str | None = None
    revision: int | None = None
    os_version: str | None = None
    os_build: str | None = None
    #: Code readback path: ``sheet`` (code sheet, Vision) or ``page``
    #: (page with white background, Tesseract) – Windows remains unchanged
    #: until measured there.
    readback_layout: Literal["sheet", "page"] = "page"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OcrWord:
    text: str
    left: int
    top: int
    width: int
    height: int
    line_id: tuple[int, int, int, int]
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not self.text or self.text.isspace():
            raise ValueError("Ein OCR-Wort darf nicht leer sein.")
        if self.left < 0 or self.top < 0 or self.width < 1 or self.height < 1:
            raise ValueError("Ein OCR-Wort braucht eine positive Pixelbox.")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("OCR-Konfidenz muss zwischen 0 und 1 liegen.")


class OcrBackend(Protocol):
    def recognize(self, image: object, *, languages: tuple[str, ...]) -> tuple[OcrWord, ...]: ...


def ocr_engine_name(backend: object | None) -> str | None:
    """Name of the backend's engine (``apple-vision``, ``tesseract``), without probing."""

    name = getattr(backend, "engine_name", None)
    return name if isinstance(name, str) else None


def ocr_engine_of(backend: object | None) -> OcrEngine | None:
    """The engine of a backend, if it names one; without calling a program."""

    engine = getattr(backend, "engine", None)
    return engine if isinstance(engine, OcrEngine) else None


def readback_layout_of(backend: object | None) -> Literal["sheet", "page"]:
    """Code readback path: ``sheet`` for Vision, ``page`` otherwise."""

    engine = ocr_engine_of(backend)
    if engine is not None:
        return engine.readback_layout
    return "sheet" if ocr_engine_name(backend) == APPLE_VISION else "page"


def default_ocr_backend() -> OcrBackend:
    """The OCR backend for this platform (E-U1: on the Mac without fallback)."""

    if selected_ocr_engine() == APPLE_VISION:
        from .ocr_vision import VisionOcrBackend

        return VisionOcrBackend()
    return TesseractOcrBackend()


class LimitedOcrBackend:
    """Limit parallel calls to an OCR backend.

    The runtime matrix uses ``0`` for devices where no OCR job
    should run *parallel* to another. OCR remains fully
    available there and thus gets a serial execution path.
    """

    def __init__(self, backend: OcrBackend, *, parallel_slots: int) -> None:
        if not isinstance(parallel_slots, int):
            raise TypeError("parallel_slots muss eine ganze Zahl sein.")
        if parallel_slots < 0:
            raise ValueError("parallel_slots darf nicht negativ sein.")
        self.backend = backend
        self.parallel_slots = parallel_slots
        self.max_concurrency = max(1, parallel_slots)
        self._semaphore = BoundedSemaphore(self.max_concurrency)

    @property
    def engine(self) -> OcrEngine | None:
        return ocr_engine_of(self.backend)

    @property
    def engine_name(self) -> str | None:
        return ocr_engine_name(self.backend)

    def recognize(self, image: object, *, languages: tuple[str, ...]) -> tuple[OcrWord, ...]:
        with self._semaphore:
            return self.backend.recognize(image, languages=languages)


class TesseractOcrBackend:
    """Local Tesseract via pytesseract.

    ``image_to_data(Output.DICT)`` matches the raw format used by
    ``presidio-image-redactor.TesseractOCR``. Parsing it here avoids loading
    the heavy redactor module for pure-text jobs.
    """

    engine_name = TESSERACT

    def __init__(self, *, timeout_seconds: float = 120.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds muss positiv sein.")
        self.timeout_seconds = timeout_seconds
        self._version: str | None = None
        self._version_lock = Lock()

    @property
    def engine(self) -> OcrEngine:
        """Tesseract; version is known only after first recognition."""

        return OcrEngine(name=TESSERACT, version=self._version, readback_layout="page")

    def _remember_version(self, pytesseract: Any) -> None:
        if self._version is not None:
            return
        with self._version_lock:
            if self._version is None:
                try:
                    self._version = str(pytesseract.get_tesseract_version())
                except Exception:  # noqa: BLE001 – version is for diagnostics only
                    self._version = "unbekannt"

    def recognize(self, image: object, *, languages: tuple[str, ...]) -> tuple[OcrWord, ...]:
        try:
            import pytesseract
            from presidio_image_redactor import TesseractOCR
        except ImportError as error:  # pragma: no cover - covered by project dependency
            raise OcrUnavailableError("Die lokale Tesseract-Anbindung fehlt.") from error

        try:
            bundled = tesseract_bundle()
        except BundledAssetError as error:
            # Missing program or language data means "OCR missing"
            # – not that the file is unreadable.
            raise OcrUnavailableError(str(error)) from error
        if bundled is not None:
            # Redactor and our import share the same pytesseract instance.
            # In release no PATH binary can replace the signed Tesseract;
            # its language data also comes from the bundle.
            pytesseract.pytesseract.tesseract_cmd = str(bundled.executable)
            os.environ["TESSDATA_PREFIX"] = str(bundled.tessdata)

        try:
            result = TesseractOCR().perform_ocr(
                image,
                lang="+".join(languages),
                timeout=self.timeout_seconds,
            )
        except (
            RuntimeError,
            pytesseract.TesseractNotFoundError,
            pytesseract.TesseractError,
        ) as error:
            raise OcrUnavailableError(
                "Tesseract oder die Sprachdaten deu/eng sind nicht verfuegbar."
            ) from error

        try:
            count = len(result["text"])
            words: list[OcrWord] = []
            for index in range(count):
                text = str(result["text"][index])
                if not text or text.isspace():
                    continue
                confidence = _confidence(result.get("conf", [None] * count)[index])
                words.append(
                    OcrWord(
                        text=text,
                        left=int(result["left"][index]),
                        top=int(result["top"][index]),
                        width=int(result["width"][index]),
                        height=int(result["height"][index]),
                        line_id=(
                            _integer_at(result, "page_num", index),
                            _integer_at(result, "block_num", index),
                            _integer_at(result, "par_num", index),
                            _integer_at(result, "line_num", index),
                        ),
                        confidence=confidence,
                    )
                )
        except (IndexError, KeyError, TypeError, ValueError) as error:
            raise OcrUnavailableError("Tesseract lieferte ungueltige Positionsdaten.") from error
        self._remember_version(pytesseract)
        return tuple(words)


def _integer_at(result: dict[str, object], key: str, index: int) -> int:
    values = result.get(key)
    if not isinstance(values, (list, tuple)):
        return 0
    return int(values[index])


def _confidence(raw: object) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return min(value / 100, 1.0)
