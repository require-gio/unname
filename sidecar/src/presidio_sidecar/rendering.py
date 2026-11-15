"""Page images for the review view.

The page image is generated with the same engine that extracts and edits:
PDF via MuPDF (rotated as displayed, CropBox as in viewer), images via
Pillow with ``exif_transpose`` – same normalization as OCR and export.
Thus the layout-map word boxes align with the image without further conversion.

Page images contain clear text. They are kept only in memory
(LRU with fixed budget), never written to disk, and disappear with the job.
"""

from __future__ import annotations

import io
import threading
from collections import OrderedDict
from collections.abc import Hashable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Final

from .extraction import DocumentFormat

if TYPE_CHECKING:
    from .exporting import ImageRedaction, PdfRedaction

# Approximate memory budget per sidecar process.
DEFAULT_CACHE_BYTES: Final = 64 * 1024 * 1024


class RenderError(RuntimeError):
    """Failed to generate the page image; no document content in the text."""


def render_page_png(
    source_path: Path,
    document_format: DocumentFormat,
    page_index: int,
    scale: float,
    *,
    max_image_pixels: int,
) -> bytes:
    """Render a page as PNG in the view area multiplied by ``scale``."""

    if document_format is DocumentFormat.PDF:
        return _render_pdf(source_path, page_index, scale, max_image_pixels)
    if document_format in {DocumentFormat.PNG, DocumentFormat.JPEG}:
        if page_index != 0:
            raise RenderError("Ein Bild hat genau eine Seite.")
        return _render_image(source_path, scale, max_image_pixels)
    raise RenderError("Dieses Format hat keine festen Seiten.")


def render_pdf_bytes_png(
    pdf: bytes,
    page_index: int,
    scale: float,
    *,
    max_image_pixels: int,
) -> bytes:
    """Render a PDF page in memory with page-faithful layout."""

    return _render_pdf(pdf, page_index, scale, max_image_pixels)


def _render_pdf(
    source: Path | bytes, page_index: int, scale: float, max_image_pixels: int
) -> bytes:
    import fitz

    try:
        document = (
            fitz.open(stream=source, filetype="pdf")
            if isinstance(source, bytes)
            else fitz.open(source)
        )
    except Exception as error:
        raise RenderError("Die PDF-Datei laesst sich nicht oeffnen.") from error
    try:
        if not 0 <= page_index < document.page_count:
            raise RenderError("Diese Seite gibt es nicht.")
        page = document.load_page(page_index)
        if page.rect.width * scale * page.rect.height * scale > max_image_pixels:
            raise RenderError("Das Seitenbild waere zu gross.")
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(scale, scale),
            colorspace=fitz.csRGB,
            alpha=False,
        )
        return pixmap.tobytes("png")
    except RenderError:
        raise
    except Exception as error:
        raise RenderError("Die Seite konnte nicht gerendert werden.") from error
    finally:
        document.close()


def _render_image(source_path: Path, scale: float, max_image_pixels: int) -> bytes:
    from PIL import Image, ImageOps

    try:
        with Image.open(source_path) as opened:
            if opened.width * opened.height > max_image_pixels:
                raise RenderError("Das Bild hat zu viele Pixel.")
            opened.load()
            image = ImageOps.exif_transpose(opened).convert("RGB")
        if scale != 1:
            size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
            image = image.resize(size, Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        # Without exif/icc_profile: metadata not included in the window.
        image.save(buffer, format="PNG", compress_level=3)
        return buffer.getvalue()
    except RenderError:
        raise
    except Exception as error:
        raise RenderError("Das Bild konnte nicht gerendert werden.") from error


def render_result_page_png(
    source_path: Path,
    redactions: Sequence[PdfRedaction],
    page_index: int,
    scale: float,
    *,
    max_image_pixels: int,
) -> bytes:
    """Preview image: the PDF page after actual redaction, kept only in memory.

    Redaction uses the same function as when saving
    (:func:`~presidio_sidecar.exporting.redact_pdf_page`); the file is not written. Tests compare
    the image with the overpaint of the view (A-26).
    """

    import fitz

    from .exporting import redact_pdf_page

    try:
        document = fitz.open(source_path)
    except Exception as error:
        raise RenderError("Die PDF-Datei laesst sich nicht oeffnen.") from error
    try:
        if not 0 <= page_index < document.page_count:
            raise RenderError("Diese Seite gibt es nicht.")
        page = document.load_page(page_index)
        if page.rect.width * scale * page.rect.height * scale > max_image_pixels:
            raise RenderError("Das Seitenbild waere zu gross.")
        own = [item for item in redactions if item.page_index == page_index]
        if own:
            redact_pdf_page(page, own)
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(scale, scale),
            colorspace=fitz.csRGB,
            alpha=False,
        )
        return pixmap.tobytes("png")
    except RenderError:
        raise
    except Exception as error:
        raise RenderError("Die Seite konnte nicht redigiert gerendert werden.") from error
    finally:
        document.close()


def render_result_image_png(
    source_path: Path,
    redactions: Sequence[ImageRedaction],
    scale: float,
    *,
    max_image_pixels: int,
) -> bytes:
    """Control image of a page: areas and replacement texts as in the saved version.

    Rendering uses :func:`~presidio_sidecar.exporting.draw_image_redactions`,
    the same function as the export step, but operates in memory.
    """

    from PIL import Image, ImageOps

    from .exporting import draw_image_redactions

    try:
        with Image.open(source_path) as opened:
            if opened.width * opened.height > max_image_pixels:
                raise RenderError("Das Bild hat zu viele Pixel.")
            opened.load()
            image = ImageOps.exif_transpose(opened).convert("RGB")
        if redactions:
            draw_image_redactions(image, redactions)
        if scale != 1:
            size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
            image = image.resize(size, Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", compress_level=3)
        return buffer.getvalue()
    except RenderError:
        raise
    except Exception as error:
        raise RenderError("Das Bild konnte nicht redigiert gerendert werden.") from error


class JobMemoryCache:
    """LRU cache for page images and layout maps, releasable per job; thread-safe.

    Keys are ``(job_id, …)``; values are bytes (PNG or JSON) so the budget counts real sizes.
    A single entry larger than the budget is served but not retained.
    """

    def __init__(self, max_bytes: int = DEFAULT_CACHE_BYTES) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes muss positiv sein.")
        self.max_bytes = max_bytes
        self._items: OrderedDict[tuple[str, Hashable], bytes] = OrderedDict()
        self._size = 0
        self._lock = threading.Lock()

    @property
    def size(self) -> int:
        with self._lock:
            return self._size

    def get(self, job_id: str, key: Hashable) -> bytes | None:
        with self._lock:
            value = self._items.get((job_id, key))
            if value is not None:
                self._items.move_to_end((job_id, key))
            return value

    def put(self, job_id: str, key: Hashable, value: bytes) -> None:
        if len(value) > self.max_bytes:
            return
        with self._lock:
            previous = self._items.pop((job_id, key), None)
            if previous is not None:
                self._size -= len(previous)
            self._items[(job_id, key)] = value
            self._size += len(value)
            while self._size > self.max_bytes:
                _, evicted = self._items.popitem(last=False)
                self._size -= len(evicted)

    def forget(self, job_id: str) -> None:
        """Release all images for a job – when the job is deleted or expires."""

        with self._lock:
            for key in [key for key in self._items if key[0] == job_id]:
                self._size -= len(self._items.pop(key))

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._size = 0


__all__ = [
    "DEFAULT_CACHE_BYTES",
    "JobMemoryCache",
    "RenderError",
    "render_page_png",
    "render_result_page_png",
]
