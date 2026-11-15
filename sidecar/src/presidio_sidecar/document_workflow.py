"""Adapter between text-based processing and format-accurate export."""

from __future__ import annotations

import math
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .api_common import Utf16Offsets
from .detection import DetectionResult
from .exporting import (
    DocxReplacement,
    ExportResult,
    ExportValidationError,
    ImageRedaction,
    ImageText,
    PdfRedaction,
    PdfRetainedText,
    PdfTextLayerWord,
    XlsxReplacement,
    XlsxSheetReplacement,
    docx_preview_bytes,
    export_docx,
    export_plain_text,
    export_redacted_image,
    export_redacted_pdf,
    export_xlsx,
)
from .extraction import (
    CoordinateSpace,
    DocumentFormat,
    ExtractedDocument,
    OcrBackend,
    SegmentKind,
    TextSegment,
)
from .placement import TextStyle
from .processing import AppliedFinding, ProcessingMode, ProcessingResult


class DocumentWorkflowError(ExportValidationError):
    """A text span cannot be reliably mapped to the source document."""


@dataclass(frozen=True, slots=True)
class _Change:
    finding: AppliedFinding
    start: int
    end: int
    replacement: str


def export_processed_document(
    source: Path,
    destination: Path,
    *,
    document: ExtractedDocument | None,
    source_text: str,
    processing: ProcessingResult,
    pdf_retained_spans: tuple[tuple[int, int], ...] = (),
    ocr_backend: OcrBackend | None = None,
    ocr_languages: tuple[str, ...] = ("deu", "eng"),
    pdf_ocr_dpi: int = 300,
) -> ExportResult:
    """Export exactly the last applied review state.

    For Office and image formats, every applied finding span must uniquely
    point to a structural unit or bounding box. An ambiguous span blocks
    the export with a fail-closed error.
    """

    changes = _changes(source_text, processing)
    if document is None or document.format in {
        DocumentFormat.TXT,
        DocumentFormat.MARKDOWN,
        DocumentFormat.JSON,
        DocumentFormat.CSV,
    }:
        return export_plain_text(source, destination, processing.text)
    if not changes:
        raise DocumentWorkflowError(
            "Ein formatgetreuer Export ohne angewendete Ersetzung ist nicht erforderlich."
        )
    if document.format is DocumentFormat.PDF:
        redactions = _pdf_redactions(document, source_text, changes, source)
        return export_redacted_pdf(
            source,
            destination,
            redactions,
            retained_text=_pdf_retained_text(
                document,
                source_text,
                pdf_retained_spans,
            ),
            text_layer=_pdf_text_layer(document, source_text, changes),
            ocr_backend=ocr_backend,
            ocr_languages=ocr_languages,
            ocr_dpi=pdf_ocr_dpi,
        )
    if document.format is DocumentFormat.DOCX:
        return export_docx(
            source,
            destination,
            _docx_replacements(document, source_text, changes),
        )
    if document.format is DocumentFormat.XLSX:
        return export_xlsx(
            source,
            destination,
            _xlsx_replacements(document, source_text, changes),
        )
    if document.format in {DocumentFormat.PNG, DocumentFormat.JPEG}:
        return export_redacted_image(
            source,
            destination,
            _image_redactions(document, source_text, changes),
            ocr_backend=ocr_backend,
            ocr_languages=ocr_languages,
        )
    raise DocumentWorkflowError("Das Dokumentformat hat keinen sicheren Exportpfad.")


def docx_result_bytes(
    source: Path,
    *,
    document: ExtractedDocument,
    source_text: str,
    processing: ProcessingResult | None,
) -> bytes:
    """In-memory result DOCX of the apply state.

    The same path as saving: same replacements in same runs, same cleanup.
    Without an apply state, it is the cleaned copy of the original.
    """

    if document.format is not DocumentFormat.DOCX:
        raise DocumentWorkflowError("Eine Ergebnis-DOCX gibt es nur fuer Word-Dateien.")
    changes = _changes(source_text, processing) if processing is not None else ()
    return docx_preview_bytes(source, _docx_replacements(document, source_text, changes))


def replacement_spans(
    source_text: str,
    processing: ProcessingResult,
) -> tuple[tuple[int, int, str], ...]:
    """Source range (Python offsets) and replacement text per applied location."""

    return tuple(
        (change.start, change.end, change.replacement)
        for change in _changes(source_text, processing)
    )


def pdf_retained_spans(
    source_text: str,
    detection: DetectionResult,
    processing: ProcessingResult,
) -> tuple[tuple[int, int], ...]:
    """Detected spans intentionally kept in the current review state.

    The document level binds these spans to concrete PDF boxes later. An
    automated finding that has been rejected, which overlaps an applied
    manual correction, is not kept and must not raise an exception.
    """

    offsets = Utf16Offsets(source_text)
    applied_spans = sorted(
        (
            _python_offset(offsets, item.original_start),
            _python_offset(offsets, item.original_end),
        )
        for item in processing.applied_findings
    )
    # Longest end up to each start: A binary search answers whether
    # a finding overlaps an applied location – instead of checking each against each.
    starts = [start for start, _ in applied_spans]
    reach: list[int] = []
    for _, end in applied_spans:
        reach.append(max(end, reach[-1]) if reach else end)
    retained: set[tuple[int, int]] = set()
    for finding in (*detection.confirmed, *detection.suspicious):
        if finding.end > len(source_text):
            raise DocumentWorkflowError(
                "Beibehaltenes Review-Finding liegt ausserhalb des Quelltexts."
            )
        before = bisect_left(starts, finding.end)
        if before and reach[before - 1] > finding.start:
            continue
        retained.add((finding.start, finding.end))
    return tuple(sorted(retained))


def _changes(source_text: str, processing: ProcessingResult) -> tuple[_Change, ...]:
    changes: list[_Change] = []
    source_offsets = Utf16Offsets(source_text)
    output_offsets = Utf16Offsets(processing.text)
    for finding in processing.applied_findings:
        start = _python_offset(source_offsets, finding.original_start)
        end = _python_offset(source_offsets, finding.original_end)
        output_start = _python_offset(output_offsets, finding.output_start)
        output_end = _python_offset(output_offsets, finding.output_end)
        if end <= start or output_end < output_start:
            raise DocumentWorkflowError("Apply-Ergebnis enthaelt einen ungueltigen Span.")
        changes.append(
            _Change(
                finding=finding,
                start=start,
                end=end,
                replacement=processing.text[output_start:output_end],
            )
        )
    return tuple(changes)


def _docx_replacements(
    document: ExtractedDocument,
    source_text: str,
    changes: tuple[_Change, ...],
) -> tuple[DocxReplacement, ...]:
    replacements: list[DocxReplacement] = []
    for change in changes:
        segment = _single_containing_segment(document, change)
        if segment.kind not in {
            SegmentKind.DOCX_PARAGRAPH,
            SegmentKind.DOCX_TABLE_CELL,
        }:
            raise DocumentWorkflowError("DOCX-Finding verweist auf keinen Absatz.")
        replacements.append(
            DocxReplacement(
                target_id=segment.source_ref,
                start=change.start - segment.start,
                end=change.end - segment.start,
                replacement=change.replacement,
                expected_text=source_text[change.start : change.end],
            )
        )
    return tuple(replacements)


def _xlsx_replacements(
    document: ExtractedDocument,
    source_text: str,
    changes: tuple[_Change, ...],
) -> tuple[XlsxReplacement | XlsxSheetReplacement, ...]:
    replacements: list[XlsxReplacement | XlsxSheetReplacement] = []
    for change in changes:
        segment = _single_containing_segment(document, change)
        if segment.kind is SegmentKind.XLSX_SHEET_TITLE:
            prefix, separator, raw_index = segment.source_ref.partition(":")
            if prefix != "sheet" or not separator or not raw_index.isdigit():
                raise DocumentWorkflowError("XLSX-Blattreferenz ist ungueltig.")
            replacements.append(
                XlsxSheetReplacement(
                    sheet_index=int(raw_index),
                    start=change.start - segment.start,
                    end=change.end - segment.start,
                    replacement=change.replacement,
                    expected_text=source_text[change.start : change.end],
                )
            )
            continue
        if segment.kind not in {
            SegmentKind.XLSX_CELL,
            SegmentKind.XLSX_FORMULA,
        }:
            raise DocumentWorkflowError("XLSX-Finding verweist auf keine Zelle.")
        sheet_name, separator, coordinate = segment.source_ref.rpartition("!")
        if not separator or not sheet_name or not coordinate:
            raise DocumentWorkflowError("XLSX-Zellreferenz ist ungueltig.")
        replacements.append(
            XlsxReplacement(
                sheet_name=sheet_name,
                cell=coordinate,
                start=change.start - segment.start,
                end=change.end - segment.start,
                replacement=change.replacement,
                expected_text=source_text[change.start : change.end],
            )
        )
    return tuple(replacements)


def _pdf_redactions(
    document: ExtractedDocument,
    source_text: str,
    changes: tuple[_Change, ...],
    source: Path,
) -> tuple[PdfRedaction, ...]:
    chars = _PdfCharBoxes(source)
    redactions = [
        redaction
        for change in changes
        for redaction in _pdf_change_redactions(document, source_text, change, chars)
    ]
    if not redactions:
        raise DocumentWorkflowError("PDF-Export hat keine Redaktionsboxen.")
    return tuple(redactions)


def _pdf_change_redactions(
    document: ExtractedDocument,
    source_text: str,
    change: _Change,
    chars: _PdfCharBoxes,
) -> list[PdfRedaction]:
    """The redactions for a location: one per line, replacement text in the first."""

    segments = _boxed_segments(document, change.start, change.end)
    by_page: dict[int, list[TextSegment]] = defaultdict(list)
    for segment in segments:
        assert segment.bbox is not None
        if segment.bbox.coordinate_space is not CoordinateSpace.PDF_POINTS:
            raise DocumentWorkflowError("PDF-Finding hat kein PDF-Koordinatensystem.")
        by_page[segment.bbox.page_index].append(segment)

    redactions: list[PdfRedaction] = []
    replacement_written = False
    for page_index in sorted(by_page):
        for line_segments in _line_groups(by_page[page_index]):
            removed = _covered_text(
                source_text,
                change.start,
                change.end,
                line_segments,
            )
            if not removed.strip():
                raise DocumentWorkflowError("PDF-Redaktion hat keinen pruefbaren Quelltext.")
            # Only pad horizontally: lines often touch edge-to-edge;
            # vertical padding would let the box spill into the next line.
            rect = _partial_word_rect(
                _union_rect(line_segments, padding=0.75, vertical_padding=0.0),
                line_segments,
                change.start,
                change.end,
                lambda segment, index: chars.edges(segment, source_text, index),
            )
            # The replacement text belongs in the first line of the value;
            # the export will find space for it (fallback next to the box).
            # Masking uses the "Schwaerzen" style only; otherwise a black bar
            # would appear beside code in the same document.
            masked = change.finding.mode is ProcessingMode.MASK
            replacement = ""
            if not replacement_written and not masked and change.replacement.strip():
                replacement = change.replacement
            replacement_written = replacement_written or bool(replacement)
            redactions.append(
                PdfRedaction(
                    page_index=page_index,
                    rect=rect,
                    removed_text=removed,
                    replacement=replacement,
                    verify_source_text=all(
                        segment.kind is SegmentKind.PDF_WORD for segment in line_segments
                    ),
                    fill=((0.0, 0.0, 0.0) if masked else (1.0, 1.0, 1.0)),
                    text_color=(0.0, 0.0, 0.0),
                    font_size=_pdf_font_size(rect, replacement),
                    finding_text=source_text[change.start : change.end],
                    text_x=_text_start(line_segments, rect),
                    order=change.start,
                )
            )
    return redactions


# -- Placement plan for the view (layout spec) ---------------------


@dataclass(frozen=True, slots=True)
class PageGeometry:
    """What the placement search needs from a page, unrotated.

    ``words`` are all word boxes of the text layer (like ``get_text("words")``),
    ``styles`` their text spans with baseline, size, color and font
    (empty for images), ``matrix`` rotates into the display space (``None`` for
    images).
    """

    width: float
    height: float
    rotation: int
    matrix: tuple[float, float, float, float, float, float] | None
    words: tuple[tuple[float, float, float, float], ...]
    styles: tuple[TextStyle, ...] = ()


def pdf_page_geometry(source: Path, page_index: int) -> PageGeometry:
    """Reads size, rotation, word boxes and text spans of a PDF page."""

    import fitz

    from .placement import page_styles

    with fitz.open(source) as pdf:
        if not 0 <= page_index < pdf.page_count:
            raise DocumentWorkflowError("PDF-Seite gibt es nicht.")
        page = pdf.load_page(page_index)
        crop = page.cropbox
        matrix = page.rotation_matrix
        return PageGeometry(
            width=float(crop.width),
            height=float(crop.height),
            rotation=int(page.rotation),
            matrix=(matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f),
            words=tuple(
                (float(word[0]), float(word[1]), float(word[2]), float(word[3]))
                for word in page.get_text("words")
            ),
            styles=page_styles(page),
        )


def plan_placements(
    document: ExtractedDocument | None,
    source_text: str,
    processing: ProcessingResult,
    page_geometry: Callable[[int], PageGeometry],
    source: Path | None = None,
) -> list[dict[str, object]]:
    """Placement plan of the apply result in display space (``PlacementOut``).

    ``source`` is the source PDF; without it, a location hitting only part
    of a word gets the whole word box (unlike when saving).

    Works with :func:`~presidio_sidecar.placement.plan_replacements` – the
    function that also draws the export – without modifying the file.
    A location that cannot be mapped to word boxes is missing from the plan;
    the view then shows it only marked. When saving, the same
    location aborts the export (fail-closed), here it should not prevent
    the view.
    """

    if document is None or document.format not in {
        DocumentFormat.PDF,
        DocumentFormat.PNG,
        DocumentFormat.JPEG,
    }:
        return []
    image = document.format is not DocumentFormat.PDF
    chars = _PdfCharBoxes(source)
    entries: list[tuple[_Change, PdfRedaction]] = []
    for change in _changes(source_text, processing):
        try:
            if image:
                redactions = _image_change_redactions(document, source_text, change)
            else:
                redactions = _pdf_change_redactions(document, source_text, change, chars)
        except ExportValidationError:
            continue
        entries.extend((change, redaction) for redaction in redactions)

    by_page: dict[int, list[tuple[_Change, PdfRedaction]]] = defaultdict(list)
    for change, redaction in entries:
        by_page[redaction.page_index].append((change, redaction))

    import fitz

    from .placement import placement_out, plan_replacements, untouched_words

    placements: list[dict[str, object]] = []
    for page_index in sorted(by_page):
        page_entries = by_page[page_index]
        geometry = page_geometry(page_index)
        redactions = [redaction for _, redaction in page_entries]
        words = [fitz.Rect(word) for word in geometry.words]
        keep = untouched_words(words, [fitz.Rect(item.rect) for item in redactions])
        plans = plan_replacements(
            fitz.Rect(0, 0, geometry.width, geometry.height),
            [words[index] for index in keep],
            redactions,
            geometry.styles,
        )
        planned = {plan.index: plan for plan in plans}
        grouped: dict[str, list[int]] = {}
        for index, (change, _) in enumerate(page_entries):
            grouped.setdefault(change.finding.finding_id, []).append(index)
        matrix = fitz.Matrix(*geometry.matrix) if geometry.matrix is not None else None
        for indices in grouped.values():
            change = page_entries[indices[0]][0]
            placements.append(
                placement_out(
                    finding_id=change.finding.finding_id,
                    start=change.finding.original_start,
                    end=change.finding.original_end,
                    page=page_index,
                    plans=[(redactions[index], planned[index]) for index in indices],
                    matrix=matrix,
                    rotation=geometry.rotation,
                )
            )
    return placements


def pdf_result_redactions(
    document: ExtractedDocument | None,
    source_text: str,
    processing: ProcessingResult,
    source: Path,
) -> tuple[PdfRedaction, ...]:
    """The redactions that saving would set for this state.

    Control image (``/pages/{n}/result``). Like
    :func:`plan_placements`, a location that cannot be mapped to word
    boxes is missing; saving aborts at it anyway.
    """

    if document is None or document.format is not DocumentFormat.PDF:
        raise DocumentWorkflowError("Ein Kontrollbild gibt es nur fuer PDF-Dateien.")
    chars = _PdfCharBoxes(source)
    redactions: list[PdfRedaction] = []
    for change in _changes(source_text, processing):
        try:
            redactions.extend(_pdf_change_redactions(document, source_text, change, chars))
        except ExportValidationError:
            continue
    return tuple(redactions)


def image_result_redactions(
    document: ExtractedDocument | None,
    source_text: str,
    processing: ProcessingResult,
) -> tuple[ImageRedaction, ...]:
    """The pixel redactions that saving would draw for this state.

    Control image for images; like :func:`pdf_result_redactions`, a
    location that cannot be mapped to OCR boxes is missing.
    """

    if document is None or document.format not in {DocumentFormat.PNG, DocumentFormat.JPEG}:
        raise DocumentWorkflowError("Ein Bild-Kontrollbild gibt es nur fuer PNG und JPG.")
    mappable: list[_Change] = []
    for change in _changes(source_text, processing):
        try:
            _image_change_redactions(document, source_text, change)
        except ExportValidationError:
            continue
        mappable.append(change)
    if not mappable:
        return ()
    return _image_redactions(document, source_text, tuple(mappable))


def image_page_geometry(document: ExtractedDocument) -> PageGeometry:
    """An image is a page in pixels; obstacles are the OCR words."""

    if document.image_size is None:
        raise DocumentWorkflowError("Bildabmessungen fehlen fuer die Redaktion.")
    width, height = document.image_size
    return PageGeometry(
        width=float(width),
        height=float(height),
        rotation=0,
        matrix=None,
        words=tuple(
            (segment.bbox.x0, segment.bbox.y0, segment.bbox.x1, segment.bbox.y1)
            for segment in document.segments
            if segment.bbox is not None and segment.kind is SegmentKind.OCR_WORD
        ),
    )


def _image_change_redactions(
    document: ExtractedDocument,
    source_text: str,
    change: _Change,
) -> list[PdfRedaction]:
    """Like :func:`_pdf_change_redactions`, in pixels.

    Basis for the view (:func:`plan_placements`) and image export
    (:func:`_image_redactions`). The font size is determined by the line height.
    """

    segments = _boxed_segments(document, change.start, change.end)
    if any(
        segment.bbox is None or segment.bbox.coordinate_space is not CoordinateSpace.PIXELS
        for segment in segments
    ):
        raise DocumentWorkflowError("Bild-Finding hat kein Pixel-Koordinatensystem.")
    masked = change.finding.mode is ProcessingMode.MASK
    redactions: list[PdfRedaction] = []
    replacement_written = False
    for line_segments in _line_groups(list(segments)):
        rect = _partial_word_rect(
            _union_rect(line_segments, padding=1.0),
            line_segments,
            change.start,
            change.end,
            _proportional_edges,
        )
        replacement = ""
        if not replacement_written and not masked and change.replacement.strip():
            replacement = change.replacement
        replacement_written = replacement_written or bool(replacement)
        redactions.append(
            PdfRedaction(
                page_index=0,
                rect=rect,
                removed_text=_covered_text(source_text, change.start, change.end, line_segments)
                or source_text[change.start : change.end],
                replacement=replacement,
                verify_source_text=False,
                fill=((0.0, 0.0, 0.0) if masked else (1.0, 1.0, 1.0)),
                text_color=(0.0, 0.0, 0.0),
                font_size=max(1.0, (rect[3] - rect[1]) * 0.75),
                finding_text=source_text[change.start : change.end],
                text_x=_text_start(line_segments, rect),
            )
        )
    return redactions


def _partial_word_rect(
    rect: tuple[float, float, float, float],
    segments: list[TextSegment],
    start: int,
    end: int,
    edges: Callable[[TextSegment, int], tuple[float, float] | None],
) -> tuple[float, float, float, float]:
    """Trims the box to the affected part of the first and last word.

    Strings without spaces are a single word (MRZ of an ID card:
    ``MUELLER<<ANDREA<<<…``). Without trimming, every character in it would
    get the whole word box – the areas would overlap exactly, and the rest
    of the word would disappear. ``edges`` returns the left and right edge
    of the n-th character of a word, ``None`` if the position is unreliable.
    """

    ordered = sorted(segments, key=lambda segment: segment.bbox.x0 if segment.bbox else 0.0)
    first, last = ordered[0], ordered[-1]
    x0, y0, x1, y1 = rect
    if start > first.start and (edge := edges(first, start - first.start)) is not None:
        x0 = max(x0, edge[0])
    if end < last.end and (edge := edges(last, end - 1 - last.start)) is not None:
        x1 = min(x1, edge[1])
    if x1 <= x0:
        return rect
    return (x0, y0, x1, y1)


def _proportional_edges(segment: TextSegment, index: int) -> tuple[float, float] | None:
    # OCR knows no character positions; the proportion of characters is the best
    # estimate.
    assert segment.bbox is not None
    width = segment.bbox.width / (segment.end - segment.start)
    return (segment.bbox.x0 + width * index, segment.bbox.x0 + width * (index + 1))


#: This proportion (of character width) keeps the box within a character at a
#: cut edge: The neighboring glyph must not touch it, otherwise MuPDF removes it.
_PDF_CHAR_INSET: Final = 0.1


class _PdfCharBoxes:
    """Character edges of a PDF word for :func:`_partial_word_rect`.

    Text level: the character boxes of the source PDF, read per page only on
    demand – only needed if a character splits a word, otherwise the PDF is
    never opened. OCR words of a scanned page: proportional as with images.
    """

    def __init__(self, source: Path | None) -> None:
        self._source = source
        self._pages: dict[int, list[tuple[float, float, float, float, str]]] = {}

    def edges(
        self, segment: TextSegment, source_text: str, index: int
    ) -> tuple[float, float] | None:
        if segment.kind is SegmentKind.OCR_WORD and segment.bbox is not None:
            # Scanned page: The text is in pixels, just like in an image.
            return _proportional_edges(segment, index)
        boxes = self._word(segment, source_text[segment.start : segment.end])
        if boxes is None:
            return None
        x0, x1 = boxes[index]
        inset = (x1 - x0) * _PDF_CHAR_INSET
        return (x0 + inset, x1 - inset)

    def _word(self, segment: TextSegment, text: str) -> list[tuple[float, float]] | None:
        bbox = segment.bbox
        if self._source is None or bbox is None or segment.kind is not SegmentKind.PDF_WORD:
            return None
        tolerance = 0.5
        hits = [
            (x0, x1, char)
            for x0, y0, x1, y1, char in self._page(bbox.page_index)
            if not char.isspace()
            and bbox.x0 - tolerance <= (x0 + x1) / 2 <= bbox.x1 + tolerance
            and bbox.y0 - tolerance <= (y0 + y1) / 2 <= bbox.y1 + tolerance
        ]
        # The index is correct only if the characters exactly form the word.
        if "".join(char for _, _, char in hits) != text:
            return None
        return [(x0, x1) for x0, x1, _ in hits]

    def _page(self, page_index: int) -> list[tuple[float, float, float, float, str]]:
        if page_index not in self._pages:
            import fitz

            assert self._source is not None
            with fitz.open(self._source) as pdf:
                raw = pdf.load_page(page_index).get_text("rawdict")
            self._pages[page_index] = [
                (*char["bbox"], char["c"])
                for block in raw["blocks"]
                for line in block.get("lines", ())
                for span in line["spans"]
                for char in span["chars"]
            ]
        return self._pages[page_index]


def _pdf_retained_text(
    document: ExtractedDocument,
    source_text: str,
    spans: tuple[tuple[int, int], ...],
) -> tuple[PdfRetainedText, ...]:
    retained: list[PdfRetainedText] = []
    for start, end in spans:
        if start < 0 or end <= start or end > len(source_text):
            raise DocumentWorkflowError("Beibehaltener PDF-Span ist ungueltig.")
        segments = _boxed_segments(document, start, end)
        by_page: dict[int, list[TextSegment]] = defaultdict(list)
        for segment in segments:
            assert segment.bbox is not None
            if segment.bbox.coordinate_space is not CoordinateSpace.PDF_POINTS:
                raise DocumentWorkflowError(
                    "Beibehaltener PDF-Text hat kein PDF-Koordinatensystem."
                )
            by_page[segment.bbox.page_index].append(segment)
        for page_index in sorted(by_page):
            for line_segments in _line_groups(by_page[page_index]):
                text = _covered_text(source_text, start, end, line_segments)
                if not text.strip():
                    raise DocumentWorkflowError(
                        "Beibehaltener PDF-Text hat keinen pruefbaren Quelltext."
                    )
                retained.append(
                    PdfRetainedText(
                        page_index=page_index,
                        rect=_union_rect(line_segments, padding=0.75),
                        text=text,
                        finding_text=source_text[start:end],
                        verify_source_text=all(
                            segment.kind is SegmentKind.PDF_WORD for segment in line_segments
                        ),
                    )
                )
    return tuple(dict.fromkeys(retained))


def _image_redactions(
    document: ExtractedDocument,
    source_text: str,
    changes: tuple[_Change, ...],
) -> tuple[ImageRedaction, ...]:
    """Pixel redactions according to the placement plan – the same as in the view.

    Area and replacement text are calculated by
    :func:`~presidio_sidecar.placement.plan_replacements` with the same
    inputs as :func:`plan_placements`; what the view overlays on the image,
    the export draws into the pixels.
    """

    import fitz

    from .placement import plan_replacements, untouched_words

    if document.image_size is None:
        raise DocumentWorkflowError("Bildabmessungen fehlen fuer die Redaktion.")
    image_width, image_height = document.image_size
    redactions = [
        redaction
        for change in changes
        for redaction in _image_change_redactions(document, source_text, change)
    ]
    if not redactions:
        raise DocumentWorkflowError("Bildexport hat keine Redaktionsboxen.")
    geometry = image_page_geometry(document)
    words = [fitz.Rect(word) for word in geometry.words]
    keep = untouched_words(words, [fitz.Rect(item.rect) for item in redactions])
    plans = plan_replacements(
        fitz.Rect(0, 0, geometry.width, geometry.height),
        [words[index] for index in keep],
        redactions,
    )

    def pixels(rect: fitz.Rect) -> tuple[int, int, int, int]:
        return (
            max(0, math.floor(rect.x0)),
            max(0, math.floor(rect.y0)),
            min(image_width, math.ceil(rect.x1)),
            min(image_height, math.ceil(rect.y1)),
        )

    result: list[ImageRedaction] = []
    for plan in plans:
        item = redactions[plan.index]
        black = plan.blackened or all(value <= 0.0 for value in item.fill)
        text: ImageText | None = None
        if plan.text_rect is not None and not black:
            text = ImageText(
                value=item.replacement,
                box=pixels(plan.text_rect),
                x=plan.text_rect.x0,
                baseline=plan.baseline,
                size=plan.size,
                color=tuple(round(value * 255) for value in plan.color),  # type: ignore[arg-type]
                font=plan.fontname,
            )
        result.append(
            ImageRedaction(
                rect=pixels(plan.cover),
                fill=(0, 0, 0) if black else (255, 255, 255),
                text=text,
            )
        )
    return tuple(result)


def _pdf_text_layer(
    document: ExtractedDocument,
    source_text: str,
    changes: tuple[_Change, ...],
) -> tuple[PdfTextLayerWord, ...]:
    """The OCR words that remain after masking.

    A word that is touched even partially by a replacement is missing: Its
    pixels are removed, and the code appears as visible text in its place.
    Pages with a text layer have no OCR words and remain untouched.
    """

    spans = sorted((change.start, change.end) for change in changes)
    starts = [start for start, _ in spans]
    reach: list[int] = []
    for _, end in spans:
        reach.append(max(end, reach[-1]) if reach else end)
    words: list[PdfTextLayerWord] = []
    for segment in document.segments:
        if segment.kind is not SegmentKind.OCR_WORD or segment.bbox is None:
            continue
        if segment.bbox.coordinate_space is not CoordinateSpace.PDF_POINTS:
            continue
        before = bisect_left(starts, segment.end)
        if before and reach[before - 1] > segment.start:
            continue
        text = source_text[segment.start : segment.end]
        if not text.strip():
            continue
        box = segment.bbox
        if box.x1 <= box.x0 or box.y1 <= box.y0:
            continue
        words.append(
            PdfTextLayerWord(
                box.page_index, (box.x0, box.y0, box.x1, box.y1), text, order=segment.start
            )
        )
    return tuple(words)


def _single_containing_segment(
    document: ExtractedDocument,
    change: _Change,
) -> TextSegment:
    segments = document.segments_for_span(change.start, change.end)
    containing = tuple(
        segment
        for segment in segments
        if segment.start <= change.start and change.end <= segment.end
    )
    if len(containing) != 1:
        raise DocumentWorkflowError("Finding ueberschreitet eine strukturelle Dokumentgrenze.")
    return containing[0]


def _boxed_segments(
    document: ExtractedDocument,
    start: int,
    end: int,
) -> tuple[TextSegment, ...]:
    segments = tuple(
        segment for segment in document.segments_for_span(start, end) if segment.bbox is not None
    )
    if not segments:
        raise DocumentWorkflowError("Finding hat keine redigierbare Bounding Box.")
    return segments


def _covered_text(
    source_text: str,
    span_start: int,
    span_end: int,
    segments: list[TextSegment],
) -> str:
    parts: list[str] = []
    for segment in segments:
        start = max(span_start, segment.start)
        end = min(span_end, segment.end)
        if end > start:
            parts.append(source_text[start:end])
    return " ".join(parts)


def _union_rect(
    segments: list[TextSegment] | tuple[TextSegment, ...],
    *,
    padding: float,
    vertical_padding: float | None = None,
) -> tuple[float, float, float, float]:
    boxes = [segment.bbox for segment in segments if segment.bbox is not None]
    if not boxes:
        raise DocumentWorkflowError("Bounding-Box-Liste ist leer.")
    vertical = padding if vertical_padding is None else vertical_padding
    return (
        max(0.0, min(box.x0 for box in boxes) - padding),
        max(0.0, min(box.y0 for box in boxes) - vertical),
        max(box.x1 for box in boxes) + padding,
        max(box.y1 for box in boxes) + vertical,
    )


def _line_groups(segments: list[TextSegment]) -> tuple[list[TextSegment], ...]:
    """Groups structurally and geometrically without column gaps."""

    ordered = sorted(
        segments,
        key=lambda segment: (
            segment.bbox.y0 if segment.bbox is not None else 0,
            segment.bbox.x0 if segment.bbox is not None else 0,
        ),
    )
    groups: list[list[TextSegment]] = []
    for segment in ordered:
        assert segment.bbox is not None
        if not groups:
            groups.append([segment])
            continue
        previous_boxes = [item.bbox for item in groups[-1] if item.bbox is not None]
        previous_line_ref = _line_ref(groups[-1][0])
        current_line_ref = _line_ref(segment)
        top = min(box.y0 for box in previous_boxes)
        bottom = max(box.y1 for box in previous_boxes)
        overlap = min(bottom, segment.bbox.y1) - max(top, segment.bbox.y0)
        minimum_height = min(bottom - top, segment.bbox.height)
        right = max(box.x1 for box in previous_boxes)
        horizontal_gap = max(0.0, segment.bbox.x0 - right)
        maximum_gap = max(24.0, minimum_height * 3.0)
        same_structural_line = (
            previous_line_ref == current_line_ref
            if previous_line_ref is not None and current_line_ref is not None
            else True
        )
        if (
            same_structural_line
            and overlap > 0
            and overlap >= minimum_height * 0.45
            and horizontal_gap <= maximum_gap
        ):
            groups[-1].append(segment)
        else:
            groups.append([segment])
    return tuple(groups)


def _line_ref(segment: TextSegment) -> str | None:
    prefix, separator, _word = segment.source_ref.rpartition("/word:")
    if separator and "/line:" in prefix:
        return prefix
    return None


def _pdf_font_size(
    rect: tuple[float, float, float, float],
    replacement: str,
) -> float:
    # Upper limit for the replacement text; how small it actually becomes is
    # determined by the export at the free space of the line.
    if not replacement:
        return 5.0
    return min(8.0, (rect[3] - rect[1]) * 0.75)


def _text_start(segments: Sequence[TextSegment], rect: tuple[float, float, float, float]) -> float:
    # Left margin of the first word, excluding the box's padding.
    start = min(segment.bbox.x0 for segment in segments if segment.bbox is not None)
    return min(max(start, rect[0]), rect[2])


def _python_offset(offsets: Utf16Offsets, offset: int) -> int:
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise DocumentWorkflowError("Apply-Offset ist ungueltig.")
    return offsets.python(offset, _ApplyOffsetError)


class _ApplyOffsetError(DocumentWorkflowError):
    def __init__(self, message: str) -> None:
        super().__init__(f"Apply-{message}")
