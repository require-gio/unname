"""Layout map: where a spot appears in the rendered document.

The review view shows fixed pages (PDF, scan, image) as page images and draws
the markings on top. It needs a box for each word – exactly the area where the
page image is created. Extraction already provides the boxes
(``TextSegment.bbox``), but unrotated and per format in separate systems. Here we
convert them:

- **Display space** of a page is the page image at scale 1: PDF points
  *after* page rotation, for images the pixels after ``exif_transpose``.
- PDF word boxes are unrotated and already relative to the CropBox (see spec §1.4);
  they pass through ``page.rotation_matrix``. OCR boxes from PDFs are
  unrotated via ``derotation_matrix`` and follow the same path.
- Offsets are UTF-16 as in ``/findings``.

We also handle text that is invisible: white on white, tiny, hidden or outside
the CropBox. It lives in the text layer and thus in the saved file – the view
should be able to show it.

Word files get ``kind = "flow"``: each text unit from extraction gets an anchor
in the preview copy (``docx_preview.py``) and its range in the source text.
Excel and CSV get ``kind = "grid"`` from the table model (``grid_model.py``).
Everything else gets ``kind = "text"``: the linear representation remains the
fallback."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from .extraction import CoordinateSpace, DocumentFormat, ExtractedDocument, SegmentKind

#: Scales for PDF page images: 72, 108, 144 and 216 dpi.
PDF_SCALES: Final = (1.0, 1.5, 2.0, 3.0)
#: Images are already in pixels; we never upscale beyond the original.
IMAGE_SCALES: Final = (0.5, 1.0)
#: Below this font size (pt) nobody can read it.
TINY_FONT_SIZE: Final = 2.0
#: Minimum fraction of word area that must lie in an invisible span.
_HIDDEN_OVERLAP: Final = 0.5
#: Above this brightness (0..1 per channel) text can disappear on paper.
_LIGHT_COLOR: Final = 0.85
#: Color distance (0..255 per channel) up to which a pixel counts as text color.
_COLOR_TOLERANCE: Final = 24
#: Maximum fraction of a box's pixels that may differ from the text color (anti-aliasing).
_VISIBLE_SHARE: Final = 0.02

HiddenReason = Literal["white", "tiny", "invisible", "outside"]
_REASON_RANK: Final[dict[str, int]] = {"invisible": 0, "outside": 1, "white": 2, "tiny": 3}
_FIXED_PAGE_FORMATS: Final = {DocumentFormat.PDF, DocumentFormat.PNG, DocumentFormat.JPEG}


class LayoutError(RuntimeError):
    """The source file no longer matches the extraction."""


@dataclass(frozen=True, slots=True)
class _Page:
    index: int
    width: float
    height: float
    unit: Literal["pt", "px"]
    source: Literal["text", "ocr"]
    scales: tuple[float, ...]
    #: Unrotated box → display space; ``None`` for images (already display space).
    matrix: Any = None


def page_scales(
    width: float,
    height: float,
    candidates: Sequence[float],
    *,
    max_pixels: int,
) -> tuple[float, ...]:
    """Allowed scales for a page; the smallest one is always kept."""

    allowed = tuple(scale for scale in candidates if width * scale * height * scale <= max_pixels)
    return allowed or (min(candidates),)


def build_layout(
    job_id: str,
    document: ExtractedDocument | None,
    source_path: Path,
    *,
    max_image_pixels: int,
) -> dict[str, Any]:
    """Layout map of a completed job as a JSON-compatible dict (``LayoutResponse``)."""

    if document is not None and document.format is DocumentFormat.DOCX:
        return _flow_layout(job_id, document)
    if document is not None and document.format in {DocumentFormat.XLSX, DocumentFormat.CSV}:
        from .grid_model import build_grid

        grid = build_grid(job_id, document, source_path)
        if grid is None:
            return {"job_id": job_id, "kind": "text", "offset_encoding": "utf-16"}
        return grid.layout
    if document is None or document.format not in _FIXED_PAGE_FORMATS:
        return {"job_id": job_id, "kind": "text", "offset_encoding": "utf-16"}
    if not any(segment.bbox is not None for segment in document.segments):
        return {"job_id": job_id, "kind": "text", "offset_encoding": "utf-16"}

    if document.format is DocumentFormat.PDF:
        pages, hidden_ranges = _pdf_pages(document, source_path, max_image_pixels)
    else:
        pages, hidden_ranges = _image_pages(document, max_image_pixels), []

    offsets = Utf16Offsets(document.text)
    return {
        "job_id": job_id,
        "kind": "pages",
        "offset_encoding": "utf-16",
        "pages": [
            {
                "index": page.index,
                "width": _round(page.width),
                "height": _round(page.height),
                "unit": page.unit,
                "source": page.source,
                "scales": list(page.scales),
            }
            for page in pages
        ],
        "words": _words(document, pages, offsets),
        "hidden": [
            {
                "page": page,
                "start": offsets(start),
                "end": offsets(end),
                "reason": reason,
            }
            for page, start, end, reason in hidden_ranges
        ],
    }


# -- Word ---------------------------------------------------------------------

_DOCX_UNIT_KINDS: Final = {SegmentKind.DOCX_PARAGRAPH, SegmentKind.DOCX_TABLE_CELL}


def docx_unit_segments(document: ExtractedDocument) -> list[Any]:
    """Segments that correspond to a text unit (paragraph) – in order."""

    return [segment for segment in document.segments if segment.kind in _DOCX_UNIT_KINDS]


def _flow_layout(job_id: str, document: ExtractedDocument) -> dict[str, Any]:
    from .docx_preview import anchor_name

    offsets = Utf16Offsets(document.text)
    units = [
        {
            "anchor": anchor_name(index),
            "start": offsets(segment.start),
            "end": offsets(segment.end),
        }
        for index, segment in enumerate(docx_unit_segments(document))
    ]
    if not units:
        return {"job_id": job_id, "kind": "text", "offset_encoding": "utf-16"}
    return {
        "job_id": job_id,
        "kind": "flow",
        "offset_encoding": "utf-16",
        "format": "docx",
        "document": f"/jobs/{job_id}/layout/document",
        "units": units,
    }


# -- Pages ------------------------------------------------------------------


def _pdf_pages(
    document: ExtractedDocument,
    source_path: Path,
    max_image_pixels: int,
) -> tuple[list[_Page], list[tuple[int, int, int, str]]]:
    import fitz

    try:
        pdf = fitz.open(source_path)
    except Exception as error:
        raise LayoutError("Die PDF-Datei laesst sich nicht mehr oeffnen.") from error
    try:
        if document.page_count is not None and pdf.page_count != document.page_count:
            raise LayoutError("Die PDF-Datei passt nicht zur Extraktion.")
        ocr_pages = set(document.ocr_pages)
        pages: list[_Page] = []
        hidden: list[tuple[int, int, int, str]] = []
        for index in range(pdf.page_count):
            page = pdf.load_page(index)
            # ``page.rect`` is the CropBox after rotation – the page image.
            width, height = float(page.rect.width), float(page.rect.height)
            pages.append(
                _Page(
                    index=index,
                    width=width,
                    height=height,
                    unit="pt",
                    source="ocr" if index in ocr_pages else "text",
                    scales=page_scales(width, height, PDF_SCALES, max_pixels=max_image_pixels),
                    matrix=page.rotation_matrix,
                )
            )
            if index not in ocr_pages:
                hidden.extend(_hidden_ranges(page, document, index))
        return pages, hidden
    finally:
        pdf.close()


def _image_pages(document: ExtractedDocument, max_image_pixels: int) -> list[_Page]:
    if document.image_size is None:
        raise LayoutError("Der Extraktion fehlt die Bildgroesse.")
    width, height = (float(value) for value in document.image_size)
    return [
        _Page(
            index=0,
            width=width,
            height=height,
            unit="px",
            source="ocr",
            scales=page_scales(width, height, IMAGE_SCALES, max_pixels=max_image_pixels),
        )
    ]


# -- Words -----------------------------------------------------------------


class Utf16Offsets:
    """Python offset → UTF-16 offset; words arrive sorted, so linear processing."""

    def __init__(self, text: str) -> None:
        self._text = text
        # Without characters outside the BMP the two counts match.
        self._plain = all(ord(character) <= 0xFFFF for character in text)
        self._cursor = 0
        self._units = 0

    def __call__(self, offset: int) -> int:
        if self._plain:
            return offset
        if offset < self._cursor:
            self._cursor = 0
            self._units = 0
        for character in self._text[self._cursor : offset]:
            self._units += 2 if ord(character) > 0xFFFF else 1
        self._cursor = offset
        return self._units


def _words(
    document: ExtractedDocument,
    pages: Sequence[_Page],
    offsets: Utf16Offsets,
) -> list[list[float | int]]:
    """[page, x0, y0, x1, y1, start, end, line] per word, in reading order."""

    import fitz

    by_index = {page.index: page for page in pages}
    lines: dict[str, int] = {}
    words: list[list[float | int]] = []
    for segment in document.segments:
        box = segment.bbox
        if box is None or segment.kind not in {SegmentKind.PDF_WORD, SegmentKind.OCR_WORD}:
            continue
        page = by_index.get(box.page_index)
        if page is None:
            continue
        rect = (box.x0, box.y0, box.x1, box.y1)
        if box.coordinate_space is CoordinateSpace.PDF_POINTS and page.matrix is not None:
            turned = fitz.Rect(rect) * page.matrix
            rect = (turned.x0, turned.y0, turned.x1, turned.y1)
        line_key = segment.source_ref.rsplit("/word:", 1)[0]
        line = lines.setdefault(line_key, len(lines))
        words.append(
            [
                box.page_index,
                *(_round(value) for value in rect),
                offsets(segment.start),
                offsets(segment.end),
                line,
            ]
        )
    return words


# -- Unsichtbarer Text -------------------------------------------------------


def _hidden_ranges(
    page: Any,
    document: ExtractedDocument,
    page_index: int,
) -> list[tuple[int, int, int, str]]:
    """Return invisible-text ranges (Python offsets) for a text page."""

    spans = _hidden_spans(page)
    if not spans:
        return []
    hits: list[tuple[int, int, str]] = []
    for segment in document.segments:
        box = segment.bbox
        if box is None or segment.kind is not SegmentKind.PDF_WORD or box.page_index != page_index:
            continue
        area = box.width * box.height
        reasons = [
            reason
            for (x0, y0, x1, y1), reason in spans
            if _overlap(box.x0, box.y0, box.x1, box.y1, x0, y0, x1, y1) >= _HIDDEN_OVERLAP * area
        ]
        if reasons:
            hits.append((segment.start, segment.end, min(reasons, key=_REASON_RANK.__getitem__)))
    return [(page_index, start, end, reason) for start, end, reason in _merge(hits, document.text)]


def _hidden_spans(page: Any) -> list[tuple[tuple[float, float, float, float], str]]:
    """Spans from ``get_texttrace`` (unrotated, CropBox-relative) that are not visible."""

    try:
        trace = page.get_texttrace()
    except Exception:
        return []
    crop = page.cropbox
    width, height = float(crop.width), float(crop.height)
    hidden: list[tuple[tuple[float, float, float, float], str]] = []
    for span in trace:
        bbox = tuple(float(value) for value in span.get("bbox", (0, 0, 0, 0)))
        if len(bbox) != 4 or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            continue
        reason = _span_reason(page, span, bbox, width, height)
        if reason is not None:
            hidden.append((bbox, reason))  # type: ignore[arg-type]
    return hidden


def _span_reason(
    page: Any,
    span: dict[str, Any],
    bbox: tuple[float, ...],
    width: float,
    height: float,
) -> str | None:
    # Render mode 3 (type 3) or full transparency: never drawn.
    if int(span.get("type", 0)) == 3 or float(span.get("opacity", 1.0)) <= 0.0:
        return "invisible"
    x0, y0, x1, y1 = bbox
    if x1 <= 0 or y1 <= 0 or x0 >= width or y0 >= height:
        return "outside"
    color = tuple(float(value) for value in span.get("color", (0.0,)) or (0.0,))
    if _is_light(color) and _blends_into_page(page, bbox, color):
        return "white"
    if float(span.get("size", TINY_FONT_SIZE)) < TINY_FONT_SIZE:
        return "tiny"
    return None


def _is_light(color: tuple[float, ...]) -> bool:
    return bool(color) and all(value >= _LIGHT_COLOR for value in color)


def _blends_into_page(page: Any, bbox: tuple[float, ...], color: tuple[float, ...]) -> bool:
    """Does the page image under the box look like the text color itself?

    Only for light text: white on white paper is a common trick.
    The renderer draws only the clipped area; a dark background behind
    light text (logo, colored area) keeps the glyphs visible.
    """

    import fitz

    rgb = _rgb(color)
    clip = fitz.Rect(bbox) * page.rotation_matrix
    try:
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(4, 4),
            clip=clip,
            colorspace=fitz.csRGB,
            alpha=False,
        )
    except Exception:
        return False
    samples = pixmap.samples
    total = pixmap.width * pixmap.height
    if total == 0:
        return False
    different = 0
    for offset in range(0, total * 3, 3):
        if any(
            abs(samples[offset + channel] - rgb[channel]) > _COLOR_TOLERANCE for channel in range(3)
        ):
            different += 1
    return different <= _VISIBLE_SHARE * total


def _rgb(color: tuple[float, ...]) -> tuple[int, int, int]:
    if len(color) == 1:
        level = round(color[0] * 255)
        return level, level, level
    if len(color) == 4:  # CMYK
        c, m, y, k = color
        return tuple(round(255 * (1 - min(1.0, value + k))) for value in (c, m, y))  # type: ignore[return-value]
    return tuple(round(value * 255) for value in color[:3])  # type: ignore[return-value]


def _overlap(
    ax0: float, ay0: float, ax1: float, ay1: float, bx0: float, by0: float, bx1: float, by1: float
) -> float:
    width = min(ax1, bx1) - max(ax0, bx0)
    height = min(ay1, by1) - max(ay0, by0)
    return width * height if width > 0 and height > 0 else 0.0


def _merge(hits: Iterable[tuple[int, int, str]], text: str) -> list[tuple[int, int, str]]:
    """Merge consecutive words with the same base into a single span."""

    merged: list[tuple[int, int, str]] = []
    for start, end, reason in sorted(hits):
        if merged:
            previous_start, previous_end, previous_reason = merged[-1]
            if (
                previous_reason == reason
                and start >= previous_end
                and not text[previous_end:start].strip()
                and "\f" not in text[previous_end:start]
            ):
                merged[-1] = (previous_start, end, reason)
                continue
        merged.append((start, end, reason))
    return merged


def _round(value: float) -> float:
    return round(float(value), 2)


__all__ = [
    "IMAGE_SCALES",
    "LayoutError",
    "PDF_SCALES",
    "TINY_FONT_SIZE",
    "Utf16Offsets",
    "build_layout",
    "docx_unit_segments",
    "page_scales",
]
