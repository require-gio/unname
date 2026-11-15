"""Framework-independent data types for document extraction.

All offsets refer to ``ExtractedDocument.text`` and use Python slice semantics
``[start, end)``. For text formats, this text is the unchanged, decoded file
content. Office, PDF, and image formats are converted into a deterministic
linear text representation; their segments maintain the link back to page,
cell, or paragraph.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class DocumentFormat(StrEnum):
    TXT = "txt"
    MARKDOWN = "md"
    JSON = "json"
    CSV = "csv"
    PDF = "pdf"
    DOCX = "docx"
    XLSX = "xlsx"
    PNG = "png"
    JPG = "jpg"
    JPEG = "jpg"


class ExtractionMethod(StrEnum):
    DIRECT = "direct"
    PYMUPDF = "pymupdf"
    PYTHON_DOCX = "python_docx"
    OPENPYXL = "openpyxl"
    TESSERACT = "tesseract"
    #: OCR with Apple Vision (macOS).
    APPLE_VISION = "apple_vision"
    MIXED_PDF = "mixed_pdf"


class OcrMode(StrEnum):
    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"


class CoordinateSpace(StrEnum):
    """Coordinate system of a box.

    ``PDF_POINTS`` refers to the (possibly rotated) PyMuPDF page.
    ``PIXELS`` refers to the image surface passed to Tesseract.
    """

    PDF_POINTS = "pdf_points"
    PIXELS = "pixels"


class SegmentKind(StrEnum):
    DOCUMENT = "document"
    CSV_CELL = "csv_cell"
    PDF_WORD = "pdf_word"
    OCR_WORD = "ocr_word"
    DOCX_PARAGRAPH = "docx_paragraph"
    DOCX_TABLE_CELL = "docx_table_cell"
    XLSX_CELL = "xlsx_cell"
    XLSX_FORMULA = "xlsx_formula"
    XLSX_SHEET_TITLE = "xlsx_sheet_title"


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """Rectangle of a text segment; pages are zero-based."""

    x0: float
    y0: float
    x1: float
    y1: float
    page_index: int
    coordinate_space: CoordinateSpace

    def __post_init__(self) -> None:
        coordinates = (self.x0, self.y0, self.x1, self.y1)
        if not all(math.isfinite(value) for value in coordinates):
            raise ValueError("Bounding-Box-Koordinaten muessen endlich sein.")
        if self.x0 < 0 or self.y0 < 0 or self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("Bounding Box braucht eine positive Flaeche.")
        if self.page_index < 0:
            raise ValueError("page_index darf nicht negativ sein.")
        object.__setattr__(self, "coordinate_space", CoordinateSpace(self.coordinate_space))

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass(frozen=True, slots=True)
class TextSegment:
    """Offset segment with a back-reference to the document source."""

    start: int
    end: int
    kind: SegmentKind
    source_ref: str
    bbox: BoundingBox | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        if self.start < 0 or self.end <= self.start:
            raise ValueError("Ein Textsegment braucht einen nicht-leeren Span.")
        object.__setattr__(self, "kind", SegmentKind(self.kind))
        source_ref = self.source_ref.strip()
        if not source_ref:
            raise ValueError("source_ref darf nicht leer sein.")
        object.__setattr__(self, "source_ref", source_ref)
        if self.confidence is not None and (
            not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1
        ):
            raise ValueError("confidence muss zwischen 0 und 1 liegen.")

    def overlaps(self, start: int, end: int) -> bool:
        if start < 0 or end <= start:
            raise ValueError("Der Suchspan muss nicht-leer und positiv sein.")
        return max(self.start, start) < min(self.end, end)


@dataclass(frozen=True, slots=True)
class StructuredPayload:
    """Structured view directly passable to ``presidio-structured``.

    ``data`` is currently a Pandas DataFrame for CSV, as expected by
    ``PandasAnalysisBuilder`` and ``StructuredEngine``. The plain text is
    intentionally excluded from this object; it remains the canonical offset source.
    """

    backend: str
    data: Any = field(repr=False, compare=False)
    columns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        backend = self.backend.strip()
        if not backend:
            raise ValueError("backend darf nicht leer sein.")
        object.__setattr__(self, "backend", backend)
        object.__setattr__(self, "columns", tuple(str(column) for column in self.columns))


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    """Unified result for detection and later editing."""

    text: str
    format: DocumentFormat
    source_name: str
    method: ExtractionMethod
    segments: tuple[TextSegment, ...]
    ocr_used: bool = False
    page_count: int | None = None
    text_layer_pages: tuple[int, ...] = ()
    ocr_pages: tuple[int, ...] = ()
    structured: StructuredPayload | None = None
    image_size: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "format", DocumentFormat(self.format))
        object.__setattr__(self, "method", ExtractionMethod(self.method))
        object.__setattr__(self, "segments", tuple(self.segments))
        object.__setattr__(self, "text_layer_pages", tuple(self.text_layer_pages))
        object.__setattr__(self, "ocr_pages", tuple(self.ocr_pages))
        if not isinstance(self.text, str):
            raise TypeError("text muss eine Zeichenkette sein.")
        if not self.source_name:
            raise ValueError("source_name darf nicht leer sein.")
        if self.page_count is not None and self.page_count < 1:
            raise ValueError("page_count muss positiv sein.")
        if self.image_size is not None and (
            len(self.image_size) != 2
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 1
                for value in self.image_size
            )
        ):
            raise ValueError("image_size braucht positive Pixelabmessungen.")
        if self.ocr_used != bool(self.ocr_pages):
            raise ValueError("ocr_used und ocr_pages sind widerspruechlich.")
        previous_start = -1
        for segment in self.segments:
            if segment.end > len(self.text):
                raise ValueError("Textsegment liegt ausserhalb des extrahierten Texts.")
            if segment.start < previous_start:
                raise ValueError("Textsegmente muessen nach Offset sortiert sein.")
            previous_start = segment.start
        if self.page_count is not None:
            page_indexes = (*self.text_layer_pages, *self.ocr_pages)
            if any(page < 0 or page >= self.page_count for page in page_indexes):
                raise ValueError("Seitenindex liegt ausserhalb des Dokuments.")

    @property
    def has_text_layer(self) -> bool:
        return bool(self.text_layer_pages)

    @property
    def regions(self) -> tuple[TextSegment, ...]:
        """Compatible alias for callers that refer to regions."""

        return self.segments

    def segments_for_span(self, start: int, end: int) -> tuple[TextSegment, ...]:
        if end > len(self.text):
            raise ValueError("Der Suchspan liegt ausserhalb des extrahierten Texts.")
        return tuple(segment for segment in self.segments if segment.overlaps(start, end))

    def boxes_for_span(self, start: int, end: int) -> tuple[BoundingBox, ...]:
        """Provides word boxes for a finding span, in document order."""

        return tuple(
            segment.bbox
            for segment in self.segments_for_span(start, end)
            if segment.bbox is not None
        )
