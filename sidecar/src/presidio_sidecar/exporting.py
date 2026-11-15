"""Ensure format-faithful document exports.

The module is deliberately independent of FastAPI, jobs, and the detection
pipeline. Extraction must only translate format-related positions into small
replacement data types.

Security invariants:

* The source file is opened read-only.
* The target must be new and must not point to the source, even via symlinks.
* A completed export is published atomically without overwriting.
* PDF redactions are applied via ``Page.apply_redactions()`` and must be
  rendered and checked for extractable remaining text before publication.
"""

from __future__ import annotations

import hashlib
import io
import math
import os
import re
import tempfile
import unicodedata
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Final

import fitz
from docx import Document
from docx.document import Document as DocxDocument
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.text.paragraph import Paragraph
from openpyxl import load_workbook
from openpyxl.packaging.core import DocumentProperties
from openpyxl.utils.cell import coordinate_to_tuple
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

from .extraction.docx_units import (
    docx_has_notes,
    docx_paragraph_runs,
    docx_paragraph_text,
    iter_docx_paragraphs,
)
from .extraction.ocr import readback_layout_of
from .placement import (
    BASE14_FONTS,
    PDF_REPLACEMENT_FONT,
    TextStyle,
    page_styles,
    plan_replacements,
    untouched_words,
)

# Redactions in PyMuPDF remove every character with a non-empty bounding-box
# overlap. The compact glyph geometry prevents closely adjacent lines from being
# cut off by the redaction box of the target line. This setting is process-wide;
# setting it to ``True`` is therefore deterministic even for parallel jobs.
fitz.TOOLS.set_small_glyph_heights(True)

if TYPE_CHECKING:
    from .extraction.ocr import OcrBackend, OcrWord

SHA256_CHUNK_SIZE: Final = 1024 * 1024
# ``Page.apply_redactions`` writes replacement text via a loop that shrinks
# the font in 0.5 steps and stops at 4 pt. Below that, the box remains silently
# empty — the redaction takes effect, but the caption is missing.
PDF_MIN_REPLACEMENT_FONT_SIZE: Final = 4.0


class ExportError(RuntimeError):
    """Base class for controlled export errors."""


class ExportValidationError(ExportError, ValueError):
    """Source, target, or replacement position is invalid."""


class PdfVerificationError(ExportError):
    """A mandatory PDF verification check failed."""


class OriginalChangedError(ExportError):
    """The source file changed unexpectedly during export."""


class ReadbackError(ExportError):
    """Replacement text drawn in pixels could not be read back.

    Images and scanned PDFs do not reliably carry codes; saving falls back to a
    text file.
    """


@dataclass(frozen=True, slots=True)
class PdfRedaction:
    """A real PDF redaction on a zero-based page.

    ``removed_text`` is used in memory only for the fail-closed check and is
    intentionally excluded from ``repr`` or export metadata.
    """

    page_index: int
    rect: tuple[float, float, float, float]
    removed_text: str = field(repr=False)
    replacement: str = field(default="", repr=False)
    verify_source_text: bool = True
    fill: tuple[float, float, float] = (0.0, 0.0, 0.0)
    text_color: tuple[float, float, float] = (1.0, 1.0, 1.0)
    font_size: float = 5.0
    finding_text: str | None = field(default=None, repr=False)
    #: Start of the first word of the line: where the replacement text begins;
    #: ``None`` means the start of the box.
    text_x: float | None = None
    #: Position in the source text: reading order of the code in the text layer of
    #: a scan (:class:`PdfTextLayerWord`).
    order: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.page_index, bool) or self.page_index < 0:
            raise ExportValidationError("PDF-Seitenindex muss nullbasiert und positiv sein.")
        if len(self.rect) != 4 or not all(
            isinstance(value, int | float) and math.isfinite(value) for value in self.rect
        ):
            raise ExportValidationError("PDF-Rechteck braucht vier endliche Koordinaten.")
        x0, y0, x1, y1 = (float(value) for value in self.rect)
        if x1 <= x0 or y1 <= y0:
            raise ExportValidationError("PDF-Rechteck muss eine positive Flaeche haben.")
        if not isinstance(self.removed_text, str) or not self.removed_text.strip():
            raise ExportValidationError("PDF-Redaktion braucht den zu pruefenden entfernten Text.")
        finding_text = self.removed_text if self.finding_text is None else self.finding_text
        if not isinstance(finding_text, str) or not finding_text.strip():
            raise ExportValidationError("PDF-Redaktion braucht den vollstaendigen Findingtext.")
        if not isinstance(self.replacement, str):
            raise ExportValidationError("PDF-Replacement muss Text sein.")
        if not isinstance(self.verify_source_text, bool):
            raise ExportValidationError("verify_source_text muss boolesch sein.")
        if len(self.fill) != 3 or not all(
            isinstance(value, int | float) and 0.0 <= value <= 1.0 for value in self.fill
        ):
            raise ExportValidationError("PDF-Fuellfarbe braucht drei Werte zwischen 0 und 1.")
        if len(self.text_color) != 3 or not all(
            isinstance(value, int | float) and 0.0 <= value <= 1.0 for value in self.text_color
        ):
            raise ExportValidationError("PDF-Textfarbe braucht drei Werte zwischen 0 und 1.")
        if (
            isinstance(self.font_size, bool)
            or not isinstance(self.font_size, int | float)
            or not math.isfinite(self.font_size)
            or self.font_size <= 0
        ):
            raise ExportValidationError("PDF-Schriftgroesse muss positiv und endlich sein.")
        object.__setattr__(self, "rect", (x0, y0, x1, y1))
        object.__setattr__(self, "fill", tuple(float(value) for value in self.fill))
        object.__setattr__(
            self,
            "text_color",
            tuple(float(value) for value in self.text_color),
        )
        object.__setattr__(self, "font_size", float(self.font_size))
        object.__setattr__(self, "finding_text", finding_text)
        if self.text_x is not None:
            if (
                isinstance(self.text_x, bool)
                or not isinstance(self.text_x, int | float)
                or not x0 <= self.text_x <= x1
            ):
                raise ExportValidationError("PDF-Textanfang muss in der Box liegen.")
            object.__setattr__(self, "text_x", float(self.text_x))


@dataclass(frozen=True, slots=True)
class PdfRetainedText:
    """A PDF text region intentionally retained during review.

    The region binds a plain-text exception to exactly the detected finding.
    This allows the verification to permit identical but differently decided values
    without exposing unknown further occurrences of the same value.
    """

    page_index: int
    rect: tuple[float, float, float, float]
    text: str = field(repr=False)
    finding_text: str | None = field(default=None, repr=False)
    verify_source_text: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.page_index, bool) or self.page_index < 0:
            raise ExportValidationError("PDF-Seitenindex muss nullbasiert und positiv sein.")
        if len(self.rect) != 4 or not all(
            isinstance(value, int | float) and math.isfinite(value) for value in self.rect
        ):
            raise ExportValidationError("PDF-Rechteck braucht vier endliche Koordinaten.")
        x0, y0, x1, y1 = (float(value) for value in self.rect)
        if x1 <= x0 or y1 <= y0:
            raise ExportValidationError("PDF-Rechteck muss eine positive Flaeche haben.")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ExportValidationError("Beibehaltener PDF-Text darf nicht leer sein.")
        finding_text = self.text if self.finding_text is None else self.finding_text
        if not isinstance(finding_text, str) or not finding_text.strip():
            raise ExportValidationError("Beibehaltener PDF-Findingtext darf nicht leer sein.")
        if not isinstance(self.verify_source_text, bool):
            raise ExportValidationError("verify_source_text muss boolesch sein.")
        object.__setattr__(self, "rect", (x0, y0, x1, y1))
        object.__setattr__(self, "finding_text", finding_text)


@dataclass(frozen=True, slots=True)
class PdfTextLayerWord:
    """A word from the invisible text layer of a scan.

    The box is unrotated in PDF points, just like the OCR segments from extraction;
    ``text`` is a word that remains after masking.
    """

    page_index: int
    rect: tuple[float, float, float, float]
    text: str = field(repr=False)
    #: Position in the source: Words and codes appear in the text layer in
    #: this order – so an AI reads "Tel: <<PHONE_…>>" together.
    order: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.page_index, bool) or self.page_index < 0:
            raise ExportValidationError("PDF-Seitenindex muss nullbasiert und positiv sein.")
        if len(self.rect) != 4 or not all(
            isinstance(value, int | float) and math.isfinite(value) for value in self.rect
        ):
            raise ExportValidationError("PDF-Rechteck braucht vier endliche Koordinaten.")
        x0, y0, x1, y1 = (float(value) for value in self.rect)
        if x1 <= x0 or y1 <= y0:
            raise ExportValidationError("PDF-Rechteck muss eine positive Flaeche haben.")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ExportValidationError("Ein Wort der Textebene darf nicht leer sein.")
        object.__setattr__(self, "rect", (x0, y0, x1, y1))


@dataclass(frozen=True, slots=True)
class DocxReplacement:
    """Replacement within a DOCX text unit.

    Stable ``target_id`` values are provided by :func:`read_docx_text_units`. Offsets
    refer to the concatenated text of all runs in the paragraph.
    """

    target_id: str
    start: int
    end: int
    replacement: str = field(repr=False)
    expected_text: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _validate_text_replacement(
            self.target_id,
            self.start,
            self.end,
            self.replacement,
            self.expected_text,
        )


@dataclass(frozen=True, slots=True)
class XlsxReplacement:
    """Replacement inside a string cell of an XLSX workbook."""

    sheet_name: str
    cell: str
    start: int
    end: int
    replacement: str = field(repr=False)
    expected_text: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.sheet_name, str) or not self.sheet_name:
            raise ExportValidationError("XLSX-Replacement braucht einen Blattnamen.")
        try:
            coordinate_to_tuple(self.cell)
        except (TypeError, ValueError) as error:
            raise ExportValidationError("Ungueltige XLSX-Zelladresse.") from error
        _validate_text_replacement(
            f"{self.sheet_name}!{self.cell}",
            self.start,
            self.end,
            self.replacement,
            self.expected_text,
        )
        object.__setattr__(self, "cell", self.cell.upper())


@dataclass(frozen=True, slots=True)
class XlsxSheetReplacement:
    """Replacement inside a zero-based indexed sheet name."""

    sheet_index: int
    start: int
    end: int
    replacement: str = field(repr=False)
    expected_text: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.sheet_index, bool) or self.sheet_index < 0:
            raise ExportValidationError("XLSX-Blattindex muss nullbasiert und positiv sein.")
        _validate_text_replacement(
            f"sheet:{self.sheet_index}",
            self.start,
            self.end,
            self.replacement,
            self.expected_text,
        )


@dataclass(frozen=True, slots=True)
class ImageText:
    """Replacement text drawn into an image export.

    Position and size come from the placement plan (``x``, ``baseline``,
    ``size`` in pixels); ``box`` is the text box where the reverse-reading check
    reads, ``font`` is the Base-14 font whose metrics the plan calculated.
    """

    value: str = field(repr=False)
    box: tuple[int, int, int, int]
    x: float
    baseline: float
    size: float
    color: tuple[int, int, int] = (0, 0, 0)
    font: str = PDF_REPLACEMENT_FONT

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value.strip():
            raise ExportValidationError("Bild-Ersatztext darf nicht leer sein.")
        _validate_pixel_rect(self.box, "Bild-Textbox")
        if (
            not all(
                isinstance(value, int | float)
                and not isinstance(value, bool)
                and math.isfinite(value)
                for value in (self.x, self.baseline, self.size)
            )
            or self.size <= 0
        ):
            raise ExportValidationError("Bild-Ersatztext braucht Lage und positive Groesse.")
        if self.font not in BASE14_FONTS.values():
            raise ExportValidationError("Bild-Ersatztext braucht eine Base-14-Schrift.")
        _validate_pixel_color(self.color, "Bild-Textfarbe")


@dataclass(frozen=True, slots=True)
class ImageRedaction:
    """Pixel-level redaction in an EXIF-oriented PNG/JPG.

    Coordinates are half-open (``left, top, right, bottom``) and refer to the
    image normalized by :func:`PIL.ImageOps.exif_transpose`.
    With ``text``, the export additionally draws the replacement text into the area
    and reads it back via OCR afterwards.
    """

    rect: tuple[int, int, int, int]
    fill: tuple[int, int, int] = (0, 0, 0)
    text: ImageText | None = None

    def __post_init__(self) -> None:
        if len(self.rect) != 4 or not all(
            isinstance(value, int) and not isinstance(value, bool) for value in self.rect
        ):
            raise ExportValidationError("Bild-Rechteck braucht vier Ganzzahlkoordinaten.")
        left, top, right, bottom = self.rect
        if left < 0 or top < 0 or right <= left or bottom <= top:
            raise ExportValidationError("Bild-Rechteck muss eine positive Flaeche haben.")
        _validate_pixel_color(self.fill, "Bild-Fuellfarbe")
        if self.text is not None and not isinstance(self.text, ImageText):
            raise ExportValidationError("Bild-Ersatztext hat einen ungueltigen Typ.")


def _validate_pixel_rect(rect: Sequence[object], label: str) -> None:
    if len(rect) != 4 or not all(
        isinstance(value, int) and not isinstance(value, bool) for value in rect
    ):
        raise ExportValidationError(f"{label} braucht vier Ganzzahlkoordinaten.")
    left, top, right, bottom = rect  # type: ignore[misc]
    if left < 0 or top < 0 or right <= left or bottom <= top:
        raise ExportValidationError(f"{label} muss eine positive Flaeche haben.")


def _validate_pixel_color(color: Sequence[object], label: str) -> None:
    if len(color) != 3 or not all(
        isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 255
        for value in color
    ):
        raise ExportValidationError(f"{label} braucht drei Byte-Werte.")


@dataclass(frozen=True, slots=True)
class DocxTextUnit:
    """Ein adressierbarer Absatz samt flachem Text."""

    target_id: str
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ExportVerification:
    rendered_pages: int = 0
    text_extraction_checked: bool = False
    verified_targets: int = 0
    #: placeholder texts in pixels that OCR read back (image, scan).
    readback_checked: int = 0
    #: Words from the invisible text layer of a scan.
    text_layer_words: int = 0


@dataclass(frozen=True, slots=True)
class ExportResult:
    path: Path
    sha256: str
    size_bytes: int
    source_sha256: str
    verification: ExportVerification


def read_docx_text_units(source: Path | str) -> tuple[DocxTextUnit, ...]:
    """Reads the text sections addressable by the DOCX exporter."""

    source_path = Path(source)
    if not source_path.is_file():
        raise ExportValidationError("DOCX-Quelldatei wurde nicht gefunden.")
    document = Document(source_path)
    _validate_supported_docx_content(document)
    if _docx_has_revision_markup(document):
        raise ExportValidationError(
            "DOCX-Aenderungsverfolgung wird aus Sicherheitsgruenden nicht unterstuetzt."
        )
    return tuple(
        DocxTextUnit(target_id, _paragraph_text(paragraph))
        for target_id, paragraph in _iter_docx_paragraphs(document)
    )


def export_redacted_pdf(
    source: Path | str,
    destination: Path | str,
    redactions: Iterable[PdfRedaction],
    *,
    retained_text: Iterable[PdfRetainedText] = (),
    text_layer: Iterable[PdfTextLayerWord] = (),
    ocr_backend: OcrBackend | None = None,
    ocr_languages: tuple[str, ...] = ("deu", "eng"),
    ocr_dpi: int = 300,
) -> ExportResult:
    """Creates a new, genuinely redacted and mandatory reviewed PDF.

    ``text_layer`` contains the OCR words that remain for scans: They
    appear as an invisible text layer (Render Mode 3) over the page image,
    as with OCRmyPDF. The codes themselves appear as visible text –
    together an AI reads the text including codes.
    """

    source_path, destination_path, source_hash = _prepare_export(
        source,
        destination,
        suffixes={".pdf"},
    )
    requested = tuple(redactions)
    if not requested or not all(isinstance(item, PdfRedaction) for item in requested):
        raise ExportValidationError("PDF-Export braucht mindestens eine gueltige Redaktion.")
    retained = tuple(retained_text)
    if not all(isinstance(item, PdfRetainedText) for item in retained):
        raise ExportValidationError("Beibehaltener PDF-Text braucht positionierte Regionen.")
    layer = tuple(text_layer)
    if not all(isinstance(item, PdfTextLayerWord) for item in layer):
        raise ExportValidationError("Die Textebene braucht positionierte Woerter.")
    ocr_redactions = tuple(item for item in requested if not item.verify_source_text)
    _validate_pdf_ocr_verification(
        ocr_redactions,
        ocr_backend=ocr_backend,
        ocr_languages=ocr_languages,
        ocr_dpi=ocr_dpi,
    )

    temporary = _new_temporary_path(destination_path)
    try:
        with fitz.open(source_path) as document:
            if document.page_count < 1:
                raise ExportValidationError("PDF hat keine Seiten.")
            grouped: dict[int, list[PdfRedaction]] = defaultdict(list)
            for item in requested:
                if item.page_index >= document.page_count:
                    raise ExportValidationError("PDF-Redaktion verweist auf keine Seite.")
                grouped[item.page_index].append(item)
            if any(item.page_index >= document.page_count for item in layer):
                raise ExportValidationError("Ein Wort der Textebene verweist auf keine Seite.")

            placed: dict[PdfRedaction, PdfRedaction] = {}
            # Codes on scanned pages set the text layer first, in reading order.
            codes: list[_PendingText] = []
            for page_index, page_redactions in grouped.items():
                placed |= redact_pdf_page(
                    document.load_page(page_index), page_redactions, scan_codes=codes
                )
            requested = tuple(placed.get(item, item) for item in requested)

            _sanitize_pdf_document(document)
            # Only after cleaning: ``scrub(hidden_text=True)`` removes
            # invisible text – the old text from the original, but also the
            # new one.
            _insert_text_layer(document, layer, codes)
            document.save(
                temporary,
                garbage=4,
                clean=True,
                deflate=True,
            )

        verification = verify_redacted_pdf(
            temporary,
            (item.finding_text for item in requested),
            positioned_redactions=requested,
            required_redactions=(item for item in requested if item.replacement),
            retained_text=retained,
            ocr_redactions=ocr_redactions,
            ocr_backend=ocr_backend,
            ocr_languages=ocr_languages,
            ocr_dpi=ocr_dpi,
        )
        return _publish_export(
            source_path,
            destination_path,
            temporary,
            source_hash,
            replace(verification, text_layer_words=len(layer)),
        )
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


#: Position of the baseline of an invisible word: Proportion of the box height above
#: the bottom edge (space for descenders).
_TEXT_LAYER_DESCENT: Final = 0.2


@dataclass(frozen=True, slots=True)
class _PendingText:
    """A visible code on a scan page, set with the text layer."""

    page_index: int
    order: int
    origin: tuple[float, float]
    text: str = field(repr=False)
    fontname: str
    size: float
    color: tuple[float, float, float]


def _insert_text_layer(
    document: fitz.Document,
    words: Sequence[PdfTextLayerWord],
    codes: Sequence[_PendingText] = (),
) -> None:
    """Places the invisible text layer of a scan over the pages.

    Each word is stretched horizontally to the width of its OCR box, so
    that selecting and searching in a viewer lands where the word is in the image.
    The visible codes of the page appear in their place in the source
    in between: Anyone reading the text in content order (copying, an AI)
    gets "Tel: <<PHONE_…>>" instead of all codes first. Set
    unrotated like the replacement texts.
    """

    by_page: dict[int, list[PdfTextLayerWord | _PendingText]] = defaultdict(list)
    for item in (*words, *codes):
        by_page[item.page_index].append(item)
    for page_index, items in sorted(by_page.items()):
        page = document.load_page(page_index)
        rotation = page.rotation
        if rotation:
            page.set_rotation(0)
        try:
            for word in sorted(items, key=lambda item: item.order):
                if isinstance(word, _PendingText):
                    page.insert_text(
                        word.origin,
                        word.text,
                        fontname=word.fontname,
                        fontsize=word.size,
                        color=word.color,
                    )
                    continue
                rect = fitz.Rect(word.rect)
                size = rect.height
                length = fitz.get_text_length(word.text, PDF_REPLACEMENT_FONT, size)
                if length <= 0:
                    continue
                origin = fitz.Point(rect.x0, rect.y1 - size * _TEXT_LAYER_DESCENT)
                page.insert_text(
                    origin,
                    word.text,
                    fontname=PDF_REPLACEMENT_FONT,
                    fontsize=size,
                    render_mode=3,
                    morph=(origin, fitz.Matrix(rect.width / length, 1)),
                )
        finally:
            if rotation:
                page.set_rotation(rotation)


def redact_pdf_page(
    page: fitz.Page,
    redactions: Sequence[PdfRedaction],
    *,
    scan_codes: list[_PendingText] | None = None,
) -> dict[PdfRedaction, PdfRedaction]:
    """Edit a page in memory: remove content, then draw areas and replacements.

    The export calls this per page, as does the view's control image
    (:func:`~presidio_sidecar.rendering.render_result_page_png`) – so
    the control image shows exactly what would be saved. Returns
    the redaction versions that the mandatory check validates.

    With ``scan_codes``, OCR location codes are not drawn but
    collected there; the export merges them with the text layer
    (same position, same font).
    """

    _validate_pdf_redaction_sources(page, redactions)
    protected = _protected_pdf_words(page, redactions)
    # Typography of the places: read before removal.
    styles = page_styles(page)
    for item in redactions:
        # Remove only, do not draw: ``_draw_pdf_replacements`` sets the area and
        # replacement text itself afterwards.
        page.add_redact_annot(_pdf_removal_rect(item), fill=False, cross_out=False)
    # Explicitly remove the content stream. A Shape/Rectangle alone would
    # not be a redaction and is not an allowed code path here; it is drawn only after
    # removal.
    applied = page.apply_redactions(
        images=fitz.PDF_REDACT_IMAGE_PIXELS,
        graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,
        text=fitz.PDF_REDACT_TEXT_REMOVE,
    )
    if not applied:
        raise ExportError("PyMuPDF hat keine PDF-Redaktion angewendet.")
    placed = _draw_pdf_replacements(
        page, redactions, [box for box, _ in protected], styles, scan_codes=scan_codes
    )
    _verify_protected_pdf_words(page, protected)
    return placed


#: Proportion of line height that the removal box leaves free above and below.
#: MuPDF removes every character whose (full) glyph box touches the box;
#: in tightly spaced lines, these boxes protrude into the neighboring line. The center
#: of the line, however, only touches its own characters.
PDF_REMOVAL_INSET: Final = 0.3


def _pdf_removal_rect(item: PdfRedaction) -> fitz.Rect:
    rect = fitz.Rect(item.rect)
    if not item.verify_source_text:
        # OCR text is embedded in image pixels: the whole box must be removed.
        return rect
    inset = rect.height * PDF_REMOVAL_INSET
    return fitz.Rect(rect.x0, rect.y0 + inset, rect.x1, rect.y1 - inset)


def _protected_pdf_words(
    page: fitz.Page, redactions: Sequence[PdfRedaction]
) -> tuple[tuple[fitz.Rect, str], ...]:
    """Words that are not meant as redactions – they must survive the export."""

    words = [(fitz.Rect(word[:4]), str(word[4])) for word in page.get_text("words")]
    keep = untouched_words([box for box, _ in words], [fitz.Rect(item.rect) for item in redactions])
    return tuple(words[index] for index in keep)


def _verify_protected_pdf_words(
    page: fitz.Page, protected: Sequence[tuple[fitz.Rect, str]]
) -> None:
    remaining = Counter(
        (str(word[4]), round(word[0]), round(word[1])) for word in page.get_text("words")
    )
    for box, text in protected:
        key = (text, round(box.x0), round(box.y0))
        if remaining[key] > 0:
            remaining[key] -= 1
            continue
        raise PdfVerificationError(
            "Die PDF-Redaktion hat Text ausserhalb der markierten Stellen entfernt."
        )


def _draw_pdf_replacements(
    page: fitz.Page,
    redactions: Sequence[PdfRedaction],
    obstacles: Sequence[fitz.Rect],
    styles: Sequence[TextStyle] = (),
    *,
    scan_codes: list[_PendingText] | None = None,
) -> dict[PdfRedaction, PdfRedaction]:
    """Draws areas and replacement texts after removal.

    Where and what is placed is calculated by
    :func:`~presidio_sidecar.placement.plan_replacements` – the same function
    that the Apply uses to plan the view. ``obstacles`` are the protected
    words of the page: exactly the set that the Apply also takes as obstacles;
    ``styles`` are the text spans of the page before removal.

    Returns for each redaction the version to be validated: with the
    actually described box, or without replacement text if it found no space.
    """

    rotation = page.rotation
    if rotation:
        page.set_rotation(0)
    try:
        result: dict[PdfRedaction, PdfRedaction] = {}
        for plan in plan_replacements(page.rect, obstacles, redactions, styles):
            item = redactions[plan.index]
            if plan.blackened:
                verified = replace(item, replacement="", fill=(0.0, 0.0, 0.0))
            elif plan.text_rect is not None:
                verified = replace(item, rect=tuple(plan.cover))
            else:
                verified = item
            page.draw_rect(
                fitz.Rect(verified.rect),
                color=None,
                fill=verified.fill,
                width=0,
                overlay=True,
            )
            origin = plan.origin
            if origin is not None and scan_codes is not None and not item.verify_source_text:
                scan_codes.append(
                    _PendingText(
                        page_index=page.number,
                        order=item.order,
                        origin=(origin.x, origin.y),
                        text=item.replacement,
                        fontname=plan.fontname,
                        size=plan.size,
                        color=plan.color,
                    )
                )
            elif origin is not None:
                page.insert_text(
                    origin,
                    item.replacement,
                    fontname=plan.fontname,
                    fontsize=plan.size,
                    color=plan.color,
                )
            result[item] = verified
        return result
    finally:
        if rotation:
            page.set_rotation(rotation)


def pdf_replacement_fits(
    rect: tuple[float, float, float, float],
    replacement: str,
    font_size: float,
) -> bool:
    """Whether PyMuPDF would really write this replacement text into this box.

    ``apply_redactions`` reports success even if the replacement text does not fit in
    the box: the redaction takes effect, the text is missing. The mandatory check
    will then inevitably fail and abort the entire export. Whoever
    sets a replacement text must ask this question beforehand. The answer
    comes from the same calculation PyMuPDF performs internally — including
    vertical centering and the shrinking loop down to 4 pt.
    """

    if not replacement:
        return False
    width = rect[2] - rect[0]
    height = rect[3] - rect[1]
    if width <= 0 or height <= 0 or font_size < PDF_MIN_REPLACEMENT_FONT_SIZE:
        return False
    try:
        text_width = fitz.get_text_length(replacement, PDF_REPLACEMENT_FONT, font_size)
    except (ValueError, RuntimeError):
        return False

    target = fitz.Rect(1.0, 1.0, 1.0 + width, 1.0 + height)
    estimated = math.ceil(text_width / target.width) * font_size * 1.2
    if estimated < target.height:
        target.y0 = (target.y0 + target.y1 - estimated) * 0.5

    probe = fitz.open()
    try:
        page = probe.new_page(width=width + 2.0, height=height + 2.0)
        size = font_size
        while size >= PDF_MIN_REPLACEMENT_FONT_SIZE:
            if (
                page.insert_textbox(
                    target,
                    replacement,
                    fontname=PDF_REPLACEMENT_FONT,
                    fontsize=size,
                    align=fitz.TEXT_ALIGN_CENTER,
                )
                >= 0
            ):
                return True
            size -= 0.5
    except (ValueError, RuntimeError):
        return False
    finally:
        probe.close()
    return False


def verify_redacted_pdf(
    pdf_path: Path | str,
    removed_fragments: Iterable[str],
    *,
    positioned_redactions: Iterable[PdfRedaction] = (),
    required_redactions: Iterable[PdfRedaction] = (),
    retained_text: Iterable[PdfRetainedText] = (),
    ocr_redactions: Iterable[PdfRedaction] = (),
    ocr_backend: OcrBackend | None = None,
    ocr_languages: tuple[str, ...] = ("deu", "eng"),
    ocr_dpi: int = 300,
) -> ExportVerification:
    """Renders a PDF and checks multiple extraction paths for residual text.

    The function is public so that integrations can use the mandatory check for
    intermediate states already generated in a private job folder.
    ``export_redacted_pdf`` calls it itself.
    """

    fragments = tuple(removed_fragments)
    positioned = tuple(positioned_redactions)
    supplied_required = tuple(required_redactions)
    retained = tuple(retained_text)
    supplied_ocr = tuple(ocr_redactions)
    if not fragments or not all(
        isinstance(fragment, str) and fragment.strip() for fragment in fragments
    ):
        raise ExportValidationError("PDF-Pruefung braucht nicht-leere Resttextfragmente.")
    if not all(isinstance(item, PdfRedaction) for item in positioned):
        raise ExportValidationError("PDF-Positionspruefung braucht gueltige Redaktionen.")
    if not all(isinstance(item, PdfRedaction) and item.replacement for item in supplied_required):
        raise ExportValidationError("PDF-Replacement-Pruefung braucht positionierte Redaktionen.")
    if not all(isinstance(item, PdfRedaction) for item in supplied_ocr):
        raise ExportValidationError("PDF-OCR-Pruefung braucht positionierte Redaktionen.")
    if not all(isinstance(item, PdfRetainedText) for item in retained):
        raise ExportValidationError("Beibehaltener PDF-Text braucht positionierte Regionen.")
    if retained and not positioned:
        raise ExportValidationError(
            "PDF-Klartextausnahmen brauchen lokal pruefbare Redaktionsboxen."
        )
    if retained and _normalized_fragment_counts(
        item.finding_text or item.removed_text for item in positioned
    ) != (_normalized_fragment_counts(fragments)):
        raise ExportValidationError(
            "PDF-Klartextausnahmen muessen alle entfernten Texte positioniert pruefen."
        )
    required = tuple(
        dict.fromkeys((*supplied_required, *(item for item in positioned if item.replacement)))
    )
    ocr_required = tuple(
        dict.fromkeys(
            (*supplied_ocr, *(item for item in positioned if not item.verify_source_text))
        )
    )
    # Codes present on a scan: The OCR must read them in the image, and the
    # text layer must carry each exactly as often as it was set
    # The set versions
    # (``positioned``) are decisive, whose box encloses the text box.
    readback = tuple(
        dict.fromkeys(
            item for item in positioned if not item.verify_source_text and item.replacement
        )
    )
    _validate_pdf_ocr_verification(
        ocr_required,
        ocr_backend=ocr_backend,
        ocr_languages=ocr_languages,
        ocr_dpi=ocr_dpi,
    )
    fragments_by_key = {_normalized_text(fragment): fragment for fragment in fragments}
    retained = tuple(
        item
        for item in retained
        if any(
            _contains_sensitive_fragment(item.finding_text or item.text, fragment)
            for fragment in fragments_by_key.values()
        )
    )
    retained_ocr = tuple(item for item in retained if not item.verify_source_text)
    if ocr_backend is not None:
        _validate_pdf_ocr_settings(
            ocr_backend=ocr_backend,
            ocr_languages=ocr_languages,
            ocr_dpi=ocr_dpi,
        )

    rendered_pages = 0
    try:
        with fitz.open(pdf_path) as document:
            if document.page_count < 1:
                raise PdfVerificationError("Exportierte PDF hat keine Seiten.")
            if document.embfile_names():
                raise PdfVerificationError(
                    "Exportierte PDF enthaelt weiterhin eingebettete Dateien."
                )
            if document.get_xml_metadata().strip() or any(
                document.metadata.get(field)
                for field in (
                    "title",
                    "author",
                    "subject",
                    "keywords",
                    "creator",
                    "producer",
                    "creationDate",
                    "modDate",
                )
            ):
                raise PdfVerificationError("Exportierte PDF enthaelt weiterhin Dokumentmetadaten.")
            for page in document:
                if page.first_annot is not None or page.first_widget is not None:
                    raise PdfVerificationError(
                        "Exportierte PDF enthaelt weiterhin Annotationen oder Formularwerte."
                    )
                # Rendering is a safety check, not a preview:
                # every page must be able to be successfully converted by MuPDF into
                # real pixel data.
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False, annots=True)
                if pixmap.width < 1 or pixmap.height < 1 or not pixmap.samples:
                    raise PdfVerificationError("Eine PDF-Seite konnte nicht gerendert werden.")
                rendered_pages += 1

            for item in retained:
                if item.page_index >= document.page_count:
                    raise PdfVerificationError(
                        "Beibehaltener PDF-Text verweist auf keine exportierte Seite."
                    )
                if not item.verify_source_text:
                    continue
                page = document.load_page(item.page_index)
                rect = fitz.Rect(item.rect)
                local_variants = (
                    page.get_text("text", clip=rect),
                    " ".join(str(word[4]) for word in page.get_text("words", clip=rect)),
                    _raw_pdf_characters(page.get_text("rawdict", clip=rect)),
                )
                _validate_retained_pdf_region(item, local_variants, fragments_by_key.values())

            local_variants_by_redaction: dict[PdfRedaction, tuple[str, ...]] = {}
            for item in positioned:
                if item.page_index >= document.page_count:
                    raise PdfVerificationError(
                        "PDF-Redaktion verweist auf keine exportierte Seite."
                    )
                page = document.load_page(item.page_index)
                rect = fitz.Rect(item.rect)
                local_variants = (
                    page.get_text("text", clip=rect),
                    " ".join(str(word[4]) for word in page.get_text("words", clip=rect)),
                    _raw_pdf_characters(page.get_text("rawdict", clip=rect)),
                )
                local_variants_by_redaction[item] = local_variants
                if any(
                    _contains_sensitive_fragment(text, item.removed_text) for text in local_variants
                ):
                    raise PdfVerificationError(
                        "Entfernter Text ist in einer PDF-Redaktionsbox weiterhin extrahierbar."
                    )

            for item in required:
                if item.page_index >= document.page_count:
                    raise PdfVerificationError(
                        "PDF-Replacement verweist auf keine exportierte Seite."
                    )
                local_variants = local_variants_by_redaction.get(item)
                if local_variants is None:
                    page = document.load_page(item.page_index)
                    rect = fitz.Rect(item.rect)
                    local_variants = (
                        page.get_text("text", clip=rect),
                        " ".join(str(word[4]) for word in page.get_text("words", clip=rect)),
                        _raw_pdf_characters(page.get_text("rawdict", clip=rect)),
                    )
                if not any(
                    _contains_sensitive_fragment(text, item.replacement) for text in local_variants
                ):
                    raise PdfVerificationError(
                        "Ein PDF-Replacement ist nicht in seiner Redaktionsbox extrahierbar."
                    )

            if readback:
                planned = Counter(item.replacement for item in required)
                page_texts = [page.get_text("text") for page in document]
                for code in dict.fromkeys(item.replacement for item in readback):
                    found = sum(
                        _normalized_text(text).count(_normalized_text(code)) for text in page_texts
                    )
                    if found != planned[code]:
                        raise PdfVerificationError(
                            "Ein Ersatztext steht nicht so oft in der Textebene wie gesetzt."
                        )

            readback_by_page: dict[int, list[PdfRedaction]] = defaultdict(list)
            for item in readback:
                readback_by_page[item.page_index].append(item)
            ocr_by_page: dict[int, list[PdfRedaction]] = defaultdict(list)
            for item in ocr_required:
                if item.page_index >= document.page_count:
                    raise PdfVerificationError("OCR-Pruefung verweist auf keine exportierte Seite.")
                ocr_by_page[item.page_index].append(item)
            retained_ocr_by_page: dict[int, list[PdfRetainedText]] = defaultdict(list)
            for item in retained_ocr:
                retained_ocr_by_page[item.page_index].append(item)

            # The local OCR checks run on the real export before
            # the allowed regions below are only hidden for the global
            # residual text search.
            #: OCR per page; the overall check below takes them for each page
            #: again, which remains unchanged up to that point (Phase U: one
            #: detection per page less).
            page_ocr: dict[int, tuple[OcrWord, ...]] = {}
            for page_index in sorted(ocr_by_page.keys() | retained_ocr_by_page.keys()):
                assert ocr_backend is not None
                page = document.load_page(page_index)
                words, pixmap_width, pixmap_height = _ocr_pdf_page(
                    page,
                    backend=ocr_backend,
                    languages=ocr_languages,
                    dpi=ocr_dpi,
                )
                page_ocr[page_index] = words
                for item in ocr_by_page.get(page_index, ()):
                    local_ocr_text = _ocr_text_in_pdf_rect(
                        words,
                        page=page,
                        pixmap_width=pixmap_width,
                        pixmap_height=pixmap_height,
                        rect=fitz.Rect(item.rect),
                    )
                    if _contains_sensitive_fragment(local_ocr_text, item.removed_text):
                        raise PdfVerificationError(
                            "Entfernter OCR-Text ist in seiner Redaktionsbox weiterhin sichtbar."
                        )

                page_readback = readback_by_page.get(page_index, ())
                if page_readback and not _readback_pdf_codes(
                    page,
                    page_readback,
                    backend=ocr_backend,
                    languages=ocr_languages,
                    dpi=ocr_dpi,
                ):
                    raise ReadbackError(
                        "Ein Ersatztext auf einer Scan-Seite liess sich nicht zuruecklesen."
                    )

                for item in retained_ocr_by_page.get(page_index, ()):
                    local_ocr_text = _ocr_text_in_pdf_rect(
                        words,
                        page=page,
                        pixmap_width=pixmap_width,
                        pixmap_height=pixmap_height,
                        rect=fitz.Rect(item.rect),
                    )
                    _validate_retained_pdf_region(
                        item,
                        (local_ocr_text,),
                        fragments_by_key.values(),
                        ocr=True,
                    )

            # For the global check, only the explicitly detected and intentionally
            # retained boxes in this private in-memory view are blacked out. After that,
            # the strict null-hit rule applies again. Overlapping findings, multi-line
            # values, and unknown duplicates cannot exploit counting exceptions.
            retained_by_page: dict[int, list[PdfRetainedText]] = defaultdict(list)
            for item in retained:
                retained_by_page[item.page_index].append(item)
            for page_index, page_regions in retained_by_page.items():
                page = document.load_page(page_index)
                for item in page_regions:
                    page.add_redact_annot(
                        fitz.Rect(item.rect),
                        fill=(1.0, 1.0, 1.0),
                        cross_out=False,
                    )
                applied = page.apply_redactions(
                    images=fitz.PDF_REDACT_IMAGE_PIXELS,
                    graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,
                    text=fitz.PDF_REDACT_TEXT_REMOVE,
                )
                if not applied:
                    raise PdfVerificationError(
                        "Beibehaltener PDF-Text konnte nicht sicher ausgeblendet werden."
                    )

            affected_pages: set[int] = set()
            affected_keys: set[str] = set()
            for page in document:
                variants = (
                    page.get_text("text"),
                    " ".join(str(word[4]) for word in page.get_text("words")),
                    _raw_pdf_characters(page.get_text("rawdict")),
                )
                for key, fragment in fragments_by_key.items():
                    if any(_contains_sensitive_fragment(text, fragment) for text in variants):
                        affected_keys.add(key)
                        affected_pages.add(page.number + 1)

            if affected_keys:
                rendered_pages_list = ", ".join(str(number) for number in sorted(affected_pages))
                raise PdfVerificationError(
                    "Entfernter Text ist in der exportierten PDF weiterhin extrahierbar: "
                    f"{len(affected_keys)} von {len(fragments_by_key)} geprueften Stellen, "
                    f"auf Seite {rendered_pages_list}. Meist steht derselbe Wert noch an "
                    "einer weiteren Stelle, die im Review nicht bestaetigt wurde."
                )

            # If at least one OCR-based target or exception box exists, the masked view
            # is fully OCR-checked, not just on the target pages. This ensures that an
            # unknown duplicate on another scanned page is not missed.
            if ocr_backend is not None:
                assert ocr_backend is not None
                for page in document:
                    if page.number in page_ocr and page.number not in retained_by_page:
                        words = page_ocr[page.number]
                    else:
                        words, _width, _height = _ocr_pdf_page(
                            page,
                            backend=ocr_backend,
                            languages=ocr_languages,
                            dpi=ocr_dpi,
                        )
                    ocr_text = " ".join(word.text for word in words)
                    if any(
                        _contains_sensitive_fragment(ocr_text, fragment)
                        for fragment in fragments_by_key.values()
                    ):
                        raise PdfVerificationError(
                            "Entfernter OCR-Text ist im gerenderten PDF weiterhin sichtbar."
                        )
    except (PdfVerificationError, ReadbackError):
        raise
    except Exception as error:
        raise PdfVerificationError(
            "Exportierte PDF konnte nicht vollstaendig nachgeprueft werden."
        ) from error

    return ExportVerification(
        rendered_pages=rendered_pages,
        text_extraction_checked=True,
        verified_targets=len(fragments),
        readback_checked=len(readback),
    )


#: What an OCR reads for the double angle brackets of a code. The core and type must
#: match exactly; the brackets are recognized tolerantly by the restore step
#: (``restore.candidates``).
_READBACK_EQUIVALENTS: Final = str.maketrans({"«": "<<", "»": ">>", "‹": "<", "›": ">"})
_READBACK_CODE: Final = re.compile(r"<<([A-Z][A-Z0-9_]*)_([0-9a-f]{4,16})>>")
#: Cyrillic lookalikes of Latin characters. Apple Vision occasionally reads them in
#: codes (OCR-Gate); a code only knows ASCII.
_READBACK_HOMOGLYPHS: Final = str.maketrans(
    {
        "а": "a",
        "с": "c",
        "е": "e",
        "о": "o",
        "р": "p",
        "х": "x",
        "б": "6",
        "З": "3",
        "А": "A",
        "В": "B",
        "С": "C",
        "Е": "E",
        "К": "K",
        "М": "M",
        "Н": "H",
        "О": "O",
        "Р": "P",
        "Т": "T",
        "Х": "X",
    }
)


def _readback_compact(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).translate(_READBACK_EQUIVALENTS)
    return "".join(normalized.split())


def readback_matches(read: str, expected: str) -> bool:
    """Whether the OCR read the exact text used (whitespace irrelevant)."""

    wanted = _readback_compact(expected)
    return bool(wanted) and wanted in _readback_compact(read)


def _scan_readback_matches(read: str, expected: str) -> bool:
    """``readback_matches`` for codes on a scanned page.

    In the checksum, a read ``O`` is treated as ``0``: The OCR regularly confuses the
    two in the replacement script, and a hexadecimal checksum knows no ``O``. Unlike in
    the image, the text layer carries the code exactly here, and ``verify_redacted_pdf``
    checks this separately and strictly.

    Since Phase U (Apple Vision), the following are also considered read: underscores in
    the type and before the checksum, which the OCR reads as whitespace; case in the
    hexadecimal checksum; a single instead of double angle bracket; Cyrillic lookalikes.
    The type and checksum must match completely.
    """

    if readback_matches(read, expected):
        return True
    code = _READBACK_CODE.fullmatch(_readback_compact(expected))
    if code is None:
        return False
    digest = "".join(
        "[0Oo]" if char == "0" else f"[{char}{char.upper()}]" if char.isalpha() else char
        for char in code[2]
    )
    kind = "_*".join(re.escape(part) for part in code[1].split("_"))
    pattern = "<{1,2}" + kind + "_*" + digest + ">{1,2}"
    return re.search(pattern, _readback_compact(read).translate(_READBACK_HOMOGLYPHS)) is not None


def _validate_pdf_ocr_verification(
    redactions: tuple[PdfRedaction, ...],
    *,
    ocr_backend: OcrBackend | None,
    ocr_languages: tuple[str, ...],
    ocr_dpi: int,
) -> None:
    if not all(isinstance(item, PdfRedaction) for item in redactions):
        raise ExportValidationError("PDF-OCR-Pruefung braucht positionierte Redaktionen.")
    if not redactions:
        return
    _validate_pdf_ocr_settings(
        ocr_backend=ocr_backend,
        ocr_languages=ocr_languages,
        ocr_dpi=ocr_dpi,
    )


def _validate_pdf_ocr_settings(
    *,
    ocr_backend: OcrBackend | None,
    ocr_languages: tuple[str, ...],
    ocr_dpi: int,
) -> None:
    if ocr_backend is None:
        raise ExportValidationError(
            "OCR-basierte PDF-Redaktionen brauchen eine erneute OCR-Pruefung."
        )
    if (
        not isinstance(ocr_languages, tuple)
        or not ocr_languages
        or any(not isinstance(language, str) or not language.strip() for language in ocr_languages)
    ):
        raise ExportValidationError("PDF-OCR-Pruefung braucht mindestens eine Sprache.")
    if isinstance(ocr_dpi, bool) or not isinstance(ocr_dpi, int) or not 72 <= ocr_dpi <= 600:
        raise ExportValidationError("PDF-OCR-Pruefung braucht 72 bis 600 DPI.")


def _sanitize_pdf_document(document: fitz.Document) -> None:
    """Remove alternative PDF paths that may carry clear text."""

    # Redaction annotations are already removed after ``apply_redactions``. All other
    # comments, file attachments, and form values must be removed before ``scrub``,
    # because scrub invalidates the annotation proxies.
    for page_index in range(document.page_count):
        page = document.load_page(page_index)
        while page.first_annot is not None:
            page.delete_annot(page.first_annot)
        while page.first_widget is not None:
            page.delete_widget(page.first_widget)

    # MuPDF entfernt hier unter anderem JavaScript, Attachments, eingebettete
    # Files, hidden text, preview images and both metadata layers.
    document.scrub(
        attached_files=True,
        clean_pages=True,
        embedded_files=True,
        hidden_text=True,
        javascript=True,
        metadata=True,
        redactions=True,
        remove_links=True,
        reset_fields=True,
        reset_responses=True,
        thumbnails=True,
        xml_metadata=True,
    )
    for name in tuple(document.embfile_names()):
        document.embfile_del(name)
    document.set_metadata({})
    document.del_xml_metadata()


def export_docx(
    source: Path | str,
    destination: Path | str,
    replacements: Iterable[DocxReplacement],
) -> ExportResult:
    """Replaces text in DOCX runs while preserving paragraph/table structure."""

    source_path, destination_path, source_hash = _prepare_export(
        source,
        destination,
        suffixes={".docx"},
    )
    requested = tuple(replacements)
    if not requested or not all(isinstance(item, DocxReplacement) for item in requested):
        raise ExportValidationError("DOCX-Export braucht mindestens ein gueltiges Replacement.")
    temporary = _new_temporary_path(destination_path)
    try:
        document = _open_supported_docx(source_path)
        expected_outputs = _apply_docx_replacements(document, requested)
        _sanitize_docx_document(document)
        document.save(temporary)
        _verify_docx_targets(temporary, expected_outputs)
        return _publish_export(
            source_path,
            destination_path,
            temporary,
            source_hash,
            ExportVerification(verified_targets=len(expected_outputs)),
        )
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def docx_preview_bytes(
    source: Path | str,
    replacements: Iterable[DocxReplacement] = (),
) -> bytes:
    """The DOCX that saving would write – only in memory.

    For the page-faithful view: the same replacements and cleaning as ``export_docx``,
    but without a file. Without replacements, it is the cleaned copy of the original.
    """

    requested = tuple(replacements)
    if not all(isinstance(item, DocxReplacement) for item in requested):
        raise ExportValidationError("Ungueltiges DOCX-Replacement.")
    document = _open_supported_docx(Path(source))
    if requested:
        _apply_docx_replacements(document, requested)
    _sanitize_docx_document(document)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _open_supported_docx(source: Path) -> DocxDocument:
    document = Document(source)
    _validate_supported_docx_content(document)
    if _docx_has_revision_markup(document):
        raise ExportValidationError(
            "DOCX-Aenderungsverfolgung wird aus Sicherheitsgruenden nicht unterstuetzt."
        )
    return document


def _apply_docx_replacements(
    document: DocxDocument,
    requested: tuple[DocxReplacement, ...],
) -> dict[str, str]:
    """Ersetzt in den Runs; Rueckgabe: erwarteter Text je geaendertem Absatz."""

    paragraphs = dict(_iter_docx_paragraphs(document))
    expected_outputs: dict[str, str] = {}
    for target_id, target_replacements in _group_docx_replacements(requested).items():
        try:
            paragraph = paragraphs[target_id]
        except KeyError as error:
            raise ExportValidationError(f"DOCX-Ziel {target_id!r} wurde nicht gefunden.") from error
        original_text = _paragraph_text(paragraph)
        _validate_replacement_spans(original_text, target_replacements)
        expected_outputs[target_id] = _replace_text(original_text, target_replacements)
        for item in reversed(target_replacements):
            _replace_docx_runs(paragraph, item.start, item.end, item.replacement)
        if _paragraph_text(paragraph) != expected_outputs[target_id]:
            raise ExportError("DOCX-Replacement konnte nicht verlustfrei angewendet werden.")
    return expected_outputs


def export_xlsx(
    source: Path | str,
    destination: Path | str,
    replacements: Iterable[XlsxReplacement | XlsxSheetReplacement],
) -> ExportResult:
    """Replaces string spans in XLSX cells without rebuilding tables/styles."""

    source_path, destination_path, source_hash = _prepare_export(
        source,
        destination,
        suffixes={".xlsx"},
    )
    requested = tuple(replacements)
    if not requested or not all(
        isinstance(item, XlsxReplacement | XlsxSheetReplacement) for item in requested
    ):
        raise ExportValidationError("XLSX-Export braucht mindestens ein gueltiges Replacement.")
    cell_replacements = tuple(item for item in requested if isinstance(item, XlsxReplacement))
    sheet_replacements = tuple(item for item in requested if isinstance(item, XlsxSheetReplacement))
    grouped = _group_xlsx_replacements(cell_replacements)
    grouped_sheets = _group_xlsx_sheet_replacements(sheet_replacements)
    temporary = _new_temporary_path(destination_path)
    try:
        _validate_supported_xlsx_archive(source_path)
        workbook = load_workbook(source_path, data_only=False, keep_links=False)
        expected_outputs: dict[tuple[int, str], str] = {}
        for (sheet_name, coordinate), target_replacements in grouped.items():
            if sheet_name not in workbook.sheetnames:
                raise ExportValidationError(f"XLSX-Blatt {sheet_name!r} wurde nicht gefunden.")
            worksheet = workbook[sheet_name]
            sheet_index = workbook.worksheets.index(worksheet)
            cell = worksheet[coordinate]
            original_text = _xlsx_cell_text(cell.value)
            if not original_text:
                raise ExportValidationError(
                    "XLSX-Replacement ist nur fuer nicht-leere Zellen erlaubt."
                )
            _validate_replacement_spans(original_text, target_replacements)
            result_text = _replace_text(original_text, target_replacements)
            # openpyxl round-trips an empty string as ``None``. A completely removed PII
            # cell is still a valid and frequent export result.
            cell.value = result_text or None
            cell.hyperlink = None
            cell.comment = None
            expected_outputs[(sheet_index, coordinate)] = result_text

        expected_sheet_titles: dict[int, str] = {}
        for sheet_index, target_replacements in grouped_sheets.items():
            if sheet_index >= len(workbook.worksheets):
                raise ExportValidationError("XLSX-Blattreplacement verweist auf kein Blatt.")
            worksheet = workbook.worksheets[sheet_index]
            original_title = worksheet.title
            _validate_replacement_spans(original_title, target_replacements)
            result_title = _replace_text(original_title, target_replacements)
            worksheet.title = _safe_xlsx_sheet_title(result_title, sheet_index)
            expected_sheet_titles[sheet_index] = worksheet.title

        _sanitize_xlsx_workbook(workbook)
        workbook.save(temporary)
        workbook.close()
        _verify_xlsx_targets(
            temporary,
            expected_outputs,
            expected_sheet_titles,
        )
        return _publish_export(
            source_path,
            destination_path,
            temporary,
            source_hash,
            ExportVerification(verified_targets=len(expected_outputs) + len(expected_sheet_titles)),
        )
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def export_redacted_image(
    source: Path | str,
    destination: Path | str,
    redactions: Iterable[ImageRedaction],
    *,
    ocr_backend: OcrBackend | None = None,
    ocr_languages: tuple[str, ...] = ("deu", "eng"),
) -> ExportResult:
    """Permanently fills mapped OCR boxes with new PNG/JPG pixels.

    Redactions with ``text`` have the replacement text drawn into the area (codes,
    invented names, placeholders). The OCR then reads each of these boxes back from the
    saved file; if the exact text used is not obtained, the export aborts with
    ``ReadbackError`` – saving then falls back to text.

    Metadata and embedded thumbnails are intentionally not copied: EXIF/GPS or a JPEG
    thumbnail could otherwise carry unredacted content.
    """

    source_path, destination_path, source_hash = _prepare_export(
        source,
        destination,
        suffixes={".png", ".jpg", ".jpeg"},
    )
    if _normalized_image_suffix(source_path) != _normalized_image_suffix(destination_path):
        raise ExportValidationError("Bildexport muss das Quellformat beibehalten.")
    requested = tuple(redactions)
    if not requested or not all(isinstance(item, ImageRedaction) for item in requested):
        raise ExportValidationError("Bildexport braucht mindestens eine gueltige Redaktion.")
    texts = tuple(item.text for item in requested if item.text is not None)
    if texts:
        if ocr_backend is None:
            # Without OCR, the drawn text cannot be read back for verification.
            raise ReadbackError("Ersatztext im Bild braucht eine OCR zum Zuruecklesen.")
        _validate_pdf_ocr_settings(ocr_backend=ocr_backend, ocr_languages=ocr_languages, ocr_dpi=72)

    temporary = _new_temporary_path(destination_path)
    try:
        with Image.open(source_path) as source_image:
            image = ImageOps.exif_transpose(source_image).convert("RGB")
        draw_image_redactions(image, requested)

        if _normalized_image_suffix(destination_path) == ".png":
            image.save(temporary, format="PNG", optimize=True)
            tolerance = 0
        else:
            image.save(
                temporary,
                format="JPEG",
                quality=95,
                subsampling=0,
                optimize=True,
            )
            # JPEG is lossy. Small DCT deviations from black must not falsely block
            # pixel verification.
            tolerance = 24
        _verify_image_redactions(
            temporary,
            expected=image,
            redactions=requested,
            tolerance=tolerance,
        )
        if texts:
            assert ocr_backend is not None
            _readback_image(temporary, texts, backend=ocr_backend, languages=ocr_languages)
        return _publish_export(
            source_path,
            destination_path,
            temporary,
            source_hash,
            ExportVerification(verified_targets=len(requested), readback_checked=len(texts)),
        )
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def draw_image_redactions(image: Image.Image, redactions: Sequence[ImageRedaction]) -> None:
    """Fills the areas and draws the replacement texts in memory.

    The export calls this before writing, as does the control image of the view
    (:func:`~presidio_sidecar.rendering.render_result_image_png`).
    """

    _validate_image_rectangles(image.size, redactions)
    draw = ImageDraw.Draw(image)
    for item in redactions:
        left, top, right, bottom = item.rect
        draw.rectangle((left, top, right - 1, bottom - 1), fill=item.fill)
    # Texts after all areas: No area may cut into a code.
    # Drawn as a mask, cropped to its own area – a protruding glyph pixel would
    # otherwise alter the image outside the plan.
    for item in redactions:
        text = item.text
        if text is None:
            continue
        left, top, right, bottom = item.rect
        mask = Image.new("L", (right - left, bottom - top), 0)
        ImageDraw.Draw(mask).text(
            (text.x - left, text.baseline - top),
            text.value,
            font=_image_font(text.font, text.size),
            fill=255,
            anchor="ls",
        )
        image.paste(text.color, (left, top, right, bottom), mask)


def export_plain_text(
    source: Path | str,
    destination: Path | str,
    result_text: str,
    *,
    encoding: str = "utf-8",
) -> ExportResult:
    """Safely writes TXT/Markdown/JSON/CSV content to a new file."""

    if not isinstance(result_text, str):
        raise ExportValidationError("Text-Export braucht einen String.")
    source_path, destination_path, source_hash = _prepare_export(
        source,
        destination,
        suffixes=None,
    )
    temporary = _new_temporary_path(destination_path)
    try:
        encoded = result_text.encode(encoding)
        temporary.write_bytes(encoded)
        if temporary.read_bytes() != encoded:
            raise ExportError("Text-Export konnte nicht nachgeprueft werden.")
        return _publish_export(
            source_path,
            destination_path,
            temporary,
            source_hash,
            ExportVerification(verified_targets=1),
        )
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def sha256_file(path: Path | str) -> str:
    """Calculates a SHA-256 hash without saving file contents."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(SHA256_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_text_replacement(
    target: str,
    start: int,
    end: int,
    replacement: str,
    expected_text: str | None,
) -> None:
    if not isinstance(target, str) or not target:
        raise ExportValidationError("Replacement braucht ein strukturelles Ziel.")
    if isinstance(start, bool) or isinstance(end, bool) or start < 0 or end <= start:
        raise ExportValidationError("Replacement braucht einen nicht-leeren positiven Span.")
    if not isinstance(replacement, str):
        raise ExportValidationError("Replacement-Wert muss Text sein.")
    if expected_text is not None and (
        not isinstance(expected_text, str) or len(expected_text) != end - start
    ):
        raise ExportValidationError(
            "expected_text muss, falls gesetzt, exakt der Spanlaenge entsprechen."
        )


def _prepare_export(
    source: Path | str,
    destination: Path | str,
    *,
    suffixes: set[str] | None,
) -> tuple[Path, Path, str]:
    source_path = Path(source)
    destination_path = Path(destination)
    if not source_path.is_file():
        raise ExportValidationError("Quelldatei wurde nicht gefunden.")
    if not destination_path.parent.is_dir():
        raise ExportValidationError("Exportordner wurde nicht gefunden.")
    try:
        source_resolved = source_path.resolve(strict=True)
        destination_resolved = destination_path.resolve(strict=False)
    except OSError as error:
        raise ExportValidationError(
            "Exportpfade konnten nicht sicher aufgeloest werden."
        ) from error
    if source_resolved == destination_resolved:
        raise ExportValidationError("Originaldatei darf niemals als Exportziel dienen.")
    if os.path.lexists(destination_path):
        raise FileExistsError(f"Exportziel existiert bereits: {destination_path}")
    if suffixes is not None and (
        source_path.suffix.lower() not in suffixes
        or destination_path.suffix.lower() not in suffixes
    ):
        allowed = ", ".join(sorted(suffixes))
        raise ExportValidationError(f"Quelle und Ziel brauchen eines dieser Formate: {allowed}.")
    return source_path, destination_path, sha256_file(source_path)


def _new_temporary_path(destination: Path) -> Path:
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{destination.stem}-",
        suffix=destination.suffix,
        dir=destination.parent,
    )
    os.close(descriptor)
    return Path(raw_path)


def _publish_export(
    source: Path,
    destination: Path,
    temporary: Path,
    source_hash: str,
    verification: ExportVerification,
) -> ExportResult:
    if sha256_file(source) != source_hash:
        raise OriginalChangedError(
            "Originaldatei hat sich waehrend des Exports veraendert; Export abgebrochen."
        )
    if not temporary.is_file() or temporary.stat().st_size < 1:
        raise ExportError("Exportergebnis ist leer.")
    # The hardlink is atomic within the same target folder and fails if a target has
    # already been created, rather than overwriting it.
    os.link(temporary, destination)
    try:
        output_hash = sha256_file(destination)
        size_bytes = destination.stat().st_size
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)
    return ExportResult(
        path=destination,
        sha256=output_hash,
        size_bytes=size_bytes,
        source_sha256=source_hash,
        verification=verification,
    )


def _validate_pdf_redaction_sources(
    page: fitz.Page,
    redactions: Sequence[PdfRedaction],
) -> None:
    # PyMuPDF always provides text and word coordinates in the unrotated page space.
    # ``page.rect``, however, is already rotated according to ``page.rotation`` and
    # would truncate valid boxes on 90/270-degree pages.
    unrotated_page_rect = fitz.Rect(page.rect) * page.derotation_matrix
    for item in redactions:
        rect = fitz.Rect(item.rect) & unrotated_page_rect
        if rect.is_empty or rect.is_infinite:
            raise ExportValidationError("PDF-Redaktion liegt ausserhalb der Seite.")
        if not item.verify_source_text:
            continue
        clipped_text = page.get_text("text", clip=rect)
        clipped_words = " ".join(str(word[4]) for word in page.get_text("words", clip=rect))
        if not (
            _contains_sensitive_fragment(clipped_text, item.removed_text)
            or _contains_sensitive_fragment(clipped_words, item.removed_text)
        ):
            raise ExportValidationError(
                "PDF-Redaktionsrechteck enthaelt nicht den erwarteten Text."
            )


def _contains_sensitive_fragment(extracted: str, fragment: str) -> bool:
    return _count_sensitive_fragments(extracted, fragment) > 0


def _count_sensitive_fragments(extracted: str, fragment: str) -> int:
    haystack = _normalized_text(extracted)
    needle = _normalized_text(fragment)
    if not needle:
        return 0
    direct_count = haystack.count(needle)
    # PDF text layers separate words differently depending on font/line breaks. A
    # second, whitespace-tolerant check prevents exactly this bypass.
    return max(direct_count, _count_matches_across_whitespace(haystack, needle))


def _normalized_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _normalized_fragment_counts(fragments: Iterable[str]) -> Counter[str]:
    return Counter(_normalized_text(fragment) for fragment in fragments)


def _validate_retained_pdf_region(
    item: PdfRetainedText,
    local_variants: Sequence[str],
    removed_fragments: Iterable[str],
    *,
    ocr: bool = False,
) -> None:
    label = "OCR-Text" if ocr else "PDF-Text"
    if not any(_contains_sensitive_fragment(text, item.text) for text in local_variants):
        raise PdfVerificationError(
            f"Bewusst beibehaltener {label} fehlt in seiner erkannten Region."
        )

    finding_text = item.finding_text or item.text
    checked = {_normalized_text(item.text): item.text}
    checked.update({_normalized_text(fragment): fragment for fragment in removed_fragments})
    for fragment in checked.values():
        declared = _count_sensitive_fragments(finding_text, fragment)
        actual = max(_count_sensitive_fragments(text, fragment) for text in local_variants)
        if actual > declared:
            raise PdfVerificationError(
                "Eine beibehaltene PDF-Region umfasst mehr sensiblen Text als das Review-Finding."
            )


def _ocr_pdf_page(
    page: fitz.Page,
    *,
    backend: OcrBackend,
    languages: tuple[str, ...],
    dpi: int,
) -> tuple[tuple[OcrWord, ...], int, int]:
    scale = dpi / 72
    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(scale, scale),
        colorspace=fitz.csRGB,
        alpha=False,
        annots=True,
    )
    image = Image.frombytes(
        "RGB",
        (pixmap.width, pixmap.height),
        pixmap.samples,
    )
    words = tuple(backend.recognize(image, languages=languages))
    return words, pixmap.width, pixmap.height


def _ocr_text_in_pdf_rect(
    words: Sequence[OcrWord],
    *,
    page: fitz.Page,
    pixmap_width: int,
    pixmap_height: int,
    rect: fitz.Rect,
) -> str:
    """OCR words within an unrotated PDF redaction box.

    Tesseract provides pixel coordinates of the rendered, already rotated page image.
    The redaction box, however, comes from the unrotated PDF text space. This mapping
    allows local mandatory verification without treating a consciously maintained
    identical occurrence elsewhere on the page as a redaction leak.
    """

    page_rect = fitz.Rect(page.rect)
    if page_rect.width <= 0 or page_rect.height <= 0:
        raise PdfVerificationError("OCR-Pruefung hat ungueltige Seitenabmessungen.")
    rotated = fitz.Rect(rect) * page.rotation_matrix
    scale_x = pixmap_width / page_rect.width
    scale_y = pixmap_height / page_rect.height
    pixel_rect = fitz.Rect(
        (rotated.x0 - page_rect.x0) * scale_x,
        (rotated.y0 - page_rect.y0) * scale_y,
        (rotated.x1 - page_rect.x0) * scale_x,
        (rotated.y1 - page_rect.y0) * scale_y,
    )
    selected: list[str] = []
    for word in words:
        word_rect = fitz.Rect(
            word.left,
            word.top,
            word.left + word.width,
            word.top + word.height,
        )
        if not (word_rect & pixel_rect).is_empty:
            selected.append(word.text)
    return " ".join(selected)


#: Maximum zoom for reading back a code (600 dpi).
_READBACK_MAX_SCALE: Final = 600 / 72


def _readback_pdf_codes(
    page: fitz.Page,
    items: Sequence[PdfRedaction],
    *,
    backend: OcrBackend,
    languages: tuple[str, ...],
    dpi: int,
) -> bool:
    """Reads back the set codes of a scan page.

    The page is read where everything is white except for the code boxes: With scan
    remnants beside them, the OCR reads small codes as a word with their neighbors and
    misreads them. What does not come out exactly at the usual resolution is read again
    at 600 dpi – the OCR's confusions are random, an exact hit is not.

    With the code sheet (Vision), the line height also changes: Vision misreads a code
    with the same geometry always the same, with different geometry mostly not (OCR
    gate: from 300 codes, 0–1 remain after two passes).
    """

    pending = list(items)
    page_rect = fitz.Rect(page.rect)
    base = dpi / 72
    for scale in dict.fromkeys((base, max(base, _READBACK_MAX_SCALE))):
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(scale, scale),
            colorspace=fitz.csRGB,
            alpha=False,
            annots=True,
        )
        rendered = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        scale_x = pixmap.width / page_rect.width
        scale_y = pixmap.height / page_rect.height
        boxes: list[tuple[int, int, int, int]] = []
        for item in pending:
            # The box is in the unrotated text space, the image in the rotated one.
            shown = fitz.Rect(item.rect) * page.rotation_matrix
            boxes.append(
                (
                    max(0, math.floor((shown.x0 - page_rect.x0) * scale_x)),
                    max(0, math.floor((shown.y0 - page_rect.y0) * scale_y)),
                    min(pixmap.width, math.ceil((shown.x1 - page_rect.x0) * scale_x)),
                    min(pixmap.height, math.ceil((shown.y1 - page_rect.y0) * scale_y)),
                )
            )
        if readback_layout_of(backend) == "sheet":
            # Vision rarely reads small text on a large page (6 pt: 12 of 72; on the
            # code sheet 142 of 144, OCR gate).
            # On a rotated page, a code in the image is horizontal; on the sheet it
            # stands upright (PIL rotates counter-clockwise).
            crops = [
                rendered.crop(box).rotate(page.rotation, expand=True)
                if box[0] < box[2] and box[1] < box[3]
                else None
                for box in boxes
            ]
            if scale != base:
                heights = _SHEET_PASSES[1:]
            elif base < _READBACK_MAX_SCALE:
                heights = _SHEET_PASSES[:1]
            else:
                # The review already runs at 600 dpi: all heights here.
                heights = _SHEET_PASSES
            for line_height in heights:
                read = _read_code_sheet(
                    crops, backend=backend, languages=languages, line_height=line_height
                )
                left = [
                    (item, crop)
                    for item, crop, text in zip(pending, crops, read, strict=True)
                    if not _scan_readback_matches(text, item.replacement)
                ]
                pending = [item for item, _crop in left]
                crops = [crop for _item, crop in left]
                if not pending:
                    return True
            continue
        else:
            codes_only = Image.new("RGB", rendered.size, (255, 255, 255))
            for box in boxes:
                if box[0] < box[2] and box[1] < box[3]:
                    codes_only.paste(rendered.crop(box), box[:2])
            words = tuple(backend.recognize(codes_only, languages=languages))
            read = [
                _ocr_text_in_pdf_rect(
                    words,
                    page=page,
                    pixmap_width=pixmap.width,
                    pixmap_height=pixmap.height,
                    rect=fitz.Rect(item.rect),
                )
                for item in pending
            ]
        pending = [
            item
            for item, text in zip(pending, read, strict=True)
            if not _scan_readback_matches(text, item.replacement)
        ]
        if not pending:
            return True
    return False


#: Line height of a code on the code sheet. Measured in the OCR gate (Phase U, 144 codes
#: in 6–12 pt, 300 dpi, one pass): 32 and 40 px 142, 48 px 140, 64 px 117 – the larger,
#: the more often Vision fails to read the underscore.
_SHEET_LINE_HEIGHT: Final = 40
#: White margin around and spacing between the lines of the code sheet.
_SHEET_MARGIN: Final = 32
_SHEET_GAP: Final = 24
#: At most this many codes fit on one sheet; more go onto further sheets so that Vision
#: does not shrink the lines.
_SHEET_MAX_CODES: Final = 30
#: line heights of the passes: first in the resolution of the review, the
#: remaining codes afterwards at 600 dpi with different geometry (see above).
_SHEET_PASSES: Final = (_SHEET_LINE_HEIGHT, 32, 48)


def _read_code_sheet(
    crops: Sequence[Image.Image | None],
    *,
    backend: OcrBackend,
    languages: tuple[str, ...],
    line_height: int = _SHEET_LINE_HEIGHT,
) -> list[str]:
    """Reads crops as a code sheet: one per line, at 40 px line height.

    One call per sheet instead of per code; assignment happens via the line. What
    the OCR reads between two lines belongs to no code.
    """

    read = ["" for _ in crops]
    present = [index for index, crop in enumerate(crops) if crop is not None]
    for start in range(0, len(present), _SHEET_MAX_CODES):
        chunk = present[start : start + _SHEET_MAX_CODES]
        scaled: list[Image.Image] = []
        for index in chunk:
            crop = crops[index]
            assert crop is not None
            factor = line_height / max(1, crop.height)
            scaled.append(
                crop.resize(
                    (max(1, round(crop.width * factor)), line_height),
                    Image.Resampling.LANCZOS,
                )
            )
        width = max(image.width for image in scaled) + 2 * _SHEET_MARGIN
        height = 2 * _SHEET_MARGIN + len(scaled) * line_height + (len(scaled) - 1) * _SHEET_GAP
        sheet = Image.new("RGB", (width, height), (255, 255, 255))
        rows: list[tuple[int, int]] = []
        top = _SHEET_MARGIN
        for image in scaled:
            sheet.paste(image, (_SHEET_MARGIN, top))
            rows.append((top, top + line_height))
            top += line_height + _SHEET_GAP
        words = backend.recognize(sheet, languages=languages)
        texts: list[list[tuple[int, str]]] = [[] for _ in chunk]
        for word in words:
            middle = word.top + word.height / 2
            for row, (row_top, row_bottom) in enumerate(rows):
                if row_top <= middle < row_bottom:
                    texts[row].append((word.left, word.text))
                    break
        # Within a line from left to right: Vision sometimes splits a code into two
        # observations with slightly offset top edges.
        for index, words_in_row in zip(chunk, texts, strict=True):
            read[index] = " ".join(text for _left, text in sorted(words_in_row))
    return read


def _count_matches_across_whitespace(haystack: str, needle: str) -> int:
    """Counts ``needle`` across whitespace, but not across word boundaries.

    Removing all whitespace from both sides and then checking with ``in``
    sticks unrelated neighbors together: "Erika Musterfrau" reports a match in
    "erikamusterfrau97@example.org" and blocks a
    fully correctly edited export. The boundary check at both ends keeps the bypass
    detection without generating this false positive.
    """

    characters = [character for character in needle if not character.isspace()]
    if not characters:
        return 0
    if len(characters) > 512:
        # Such a long fragment does not collide by chance; the favorable
        # direct comparison is sufficient and keeps the search pattern small.
        return "".join(haystack.split()).count("".join(characters))
    pattern = r"\s*".join(re.escape(character) for character in characters)
    if characters[0].isalnum():
        pattern = r"(?<![^\W_])" + pattern
    if characters[-1].isalnum():
        pattern = pattern + r"(?![^\W_])"
    return sum(1 for _match in re.finditer(pattern, haystack))


def _raw_pdf_characters(raw: dict[str, object]) -> str:
    characters: list[str] = []
    for block in raw.get("blocks", []):  # type: ignore[union-attr]
        if not isinstance(block, dict):
            continue
        for line in block.get("lines", []):
            if not isinstance(line, dict):
                continue
            for span in line.get("spans", []):
                if not isinstance(span, dict):
                    continue
                chars = span.get("chars", [])
                if isinstance(chars, list):
                    characters.extend(
                        str(char.get("c", "")) for char in chars if isinstance(char, dict)
                    )
    return "".join(characters)


def _iter_docx_paragraphs(document: DocxDocument) -> Iterator[tuple[str, Paragraph]]:
    """All paragraphs with their ``target_id`` – the same pass as the extraction."""

    return iter_docx_paragraphs(document)


_paragraph_text = docx_paragraph_text
_paragraph_runs = docx_paragraph_runs


def _docx_has_revision_markup(document: DocxDocument) -> bool:
    """Detect revision nodes that do not appear in python-docx runs."""

    exact_names = {
        "ins",
        "del",
        "delText",
        "moveFrom",
        "moveTo",
        "moveFromRangeStart",
        "moveFromRangeEnd",
        "moveToRangeStart",
        "moveToRangeEnd",
        "customXmlInsRangeStart",
        "customXmlInsRangeEnd",
        "customXmlDelRangeStart",
        "customXmlDelRangeEnd",
        "cellIns",
        "cellDel",
        "cellMerge",
        "conflictIns",
        "conflictDel",
    }
    for part in document.part.package.parts:
        root = getattr(part, "element", None)
        if root is None:
            continue
        for element in root.iter():
            tag = getattr(element, "tag", "")
            local_name = tag.rpartition("}")[2] if isinstance(tag, str) else ""
            if local_name in exact_names or local_name.endswith("PrChange"):
                return True
    return False


def _validate_supported_docx_content(document: DocxDocument) -> None:
    part_names = {str(part.partname).casefold().lstrip("/") for part in document.part.package.parts}
    unsupported_prefixes = (
        "word/media/",
        "word/embeddings/",
        "word/charts/",
        "word/diagrams/",
        "word/activex/",
    )
    if any(name.startswith(unsupported_prefixes) for name in part_names):
        raise ExportValidationError(
            "DOCX-eingebettete oder benutzerdefinierte Inhalte werden aus "
            "Sicherheitsgruenden nicht unterstuetzt."
        )
    if docx_has_notes(document):
        raise ExportValidationError(
            "DOCX-Fussnoten und -Endnoten werden aus Sicherheitsgruenden nicht unterstuetzt."
        )
    for part in document.part.package.parts:
        root = getattr(part, "element", None)
        if root is None:
            continue
        if any(
            isinstance(getattr(element, "tag", ""), str) and element.tag.rpartition("}")[2] == "sdt"
            for element in root.iter()
        ):
            raise ExportValidationError(
                "DOCX-Inhaltssteuerelemente werden aus Sicherheitsgruenden nicht unterstuetzt."
            )


def _sanitize_docx_document(document: DocxDocument) -> None:
    """Remove hidden link targets and personal core attributes."""

    for _, paragraph in _iter_docx_paragraphs(document):
        for hyperlink in tuple(paragraph._p.xpath(".//w:hyperlink")):  # noqa: SLF001
            parent = hyperlink.getparent()
            if parent is None:
                continue
            insertion_index = parent.index(hyperlink)
            for child in tuple(hyperlink):
                parent.insert(insertion_index, child)
                insertion_index += 1
            parent.remove(hyperlink)

    for part in document.part.package.parts:
        for relationship_id, relationship in tuple(part.rels.items()):
            if relationship.is_external:
                part.drop_rel(relationship_id)
                continue
            target_part = getattr(relationship, "target_part", None)
            part_name = str(getattr(target_part, "partname", "")).casefold().lstrip("/")
            if relationship.reltype == RELATIONSHIP_TYPE.HYPERLINK or part_name.startswith(
                "customxml/"
            ):
                part.drop_rel(relationship_id)

    removable_package_parts = {
        "docprops/app.xml",
        "docprops/custom.xml",
        "docprops/thumbnail.jpeg",
        "docprops/thumbnail.jpg",
        "docprops/thumbnail.png",
    }
    for relationship_id, relationship in tuple(document.part.package.rels.items()):
        target_part = getattr(relationship, "target_part", None)
        part_name = str(getattr(target_part, "partname", "")).casefold().lstrip("/")
        if part_name in removable_package_parts:
            del document.part.package.rels[relationship_id]

    settings_element = document.settings.element
    for local_name in ("docVars", "mailMerge"):
        for element in tuple(settings_element.xpath(f".//*[local-name()='{local_name}']")):
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)

    for part in document.part.package.parts:
        root = getattr(part, "element", None)
        if root is None:
            continue
        for element in tuple(root.iter()):
            tag = getattr(element, "tag", "")
            local_name = tag.rpartition("}")[2] if isinstance(tag, str) else ""
            if local_name in {"instrText", "delInstrText", "fldChar"}:
                parent = element.getparent()
                if parent is not None:
                    parent.remove(element)
            elif local_name == "fldSimple":
                for attribute in tuple(element.attrib):
                    if attribute.rpartition("}")[2] == "instr":
                        del element.attrib[attribute]

    properties = document.core_properties
    for field_name in (
        "title",
        "subject",
        "author",
        "keywords",
        "comments",
        "category",
        "last_modified_by",
        "identifier",
        "language",
        "version",
    ):
        setattr(properties, field_name, "")


def _group_docx_replacements(
    replacements: Sequence[DocxReplacement],
) -> dict[str, tuple[DocxReplacement, ...]]:
    grouped: dict[str, list[DocxReplacement]] = defaultdict(list)
    for item in replacements:
        grouped[item.target_id].append(item)
    return {
        target: tuple(sorted(items, key=lambda item: (item.start, item.end)))
        for target, items in grouped.items()
    }


def _group_xlsx_replacements(
    replacements: Sequence[XlsxReplacement],
) -> dict[tuple[str, str], tuple[XlsxReplacement, ...]]:
    grouped: dict[tuple[str, str], list[XlsxReplacement]] = defaultdict(list)
    for item in replacements:
        grouped[(item.sheet_name, item.cell)].append(item)
    return {
        target: tuple(sorted(items, key=lambda item: (item.start, item.end)))
        for target, items in grouped.items()
    }


def _group_xlsx_sheet_replacements(
    replacements: Sequence[XlsxSheetReplacement],
) -> dict[int, tuple[XlsxSheetReplacement, ...]]:
    grouped: dict[int, list[XlsxSheetReplacement]] = defaultdict(list)
    for item in replacements:
        grouped[item.sheet_index].append(item)
    return {
        sheet_index: tuple(sorted(items, key=lambda item: (item.start, item.end)))
        for sheet_index, items in grouped.items()
    }


def _validate_replacement_spans(
    original: str,
    replacements: Sequence[DocxReplacement | XlsxReplacement | XlsxSheetReplacement],
) -> None:
    previous_end = -1
    for item in replacements:
        if item.end > len(original):
            raise ExportValidationError("Replacement-Span liegt ausserhalb des Zieltexts.")
        if item.start < previous_end:
            raise ExportValidationError("Replacement-Spans duerfen sich nicht ueberlappen.")
        if item.expected_text is not None and original[item.start : item.end] != item.expected_text:
            raise ExportValidationError(
                "Replacement-Span stimmt nicht mehr mit dem erwarteten Text ueberein."
            )
        previous_end = item.end


def _replace_text(
    original: str,
    replacements: Sequence[DocxReplacement | XlsxReplacement | XlsxSheetReplacement],
) -> str:
    result = original
    for item in reversed(replacements):
        result = result[: item.start] + item.replacement + result[item.end :]
    return result


def _replace_docx_runs(paragraph: Paragraph, start: int, end: int, value: str) -> None:
    positions: list[tuple[object, int, int]] = []
    cursor = 0
    for run in _paragraph_runs(paragraph):
        run_end = cursor + len(run.text)
        if start < run_end and end > cursor:
            positions.append((run, cursor, run_end))
        cursor = run_end
    if not positions:
        raise ExportValidationError("DOCX-Replacement trifft keinen Text-Run.")

    first_run, first_start, _ = positions[0]
    last_run, last_start, _ = positions[-1]
    if not hasattr(first_run, "text") or not hasattr(last_run, "text"):
        raise ExportError("DOCX-Run konnte nicht bearbeitet werden.")
    first_offset = start - first_start
    last_offset = end - last_start
    if first_run is last_run:
        first_run.text = first_run.text[:first_offset] + value + first_run.text[last_offset:]
        return

    first_run.text = first_run.text[:first_offset] + value
    for run, _, _ in positions[1:-1]:
        run.text = ""
    last_run.text = last_run.text[last_offset:]


def _verify_docx_targets(path: Path, expected_outputs: dict[str, str]) -> None:
    try:
        document = Document(path)
        actual = {
            target: _paragraph_text(paragraph)
            for target, paragraph in _iter_docx_paragraphs(document)
        }
        forbidden_parts = {
            "docprops/app.xml",
            "docprops/custom.xml",
            "docprops/thumbnail.jpeg",
            "docprops/thumbnail.jpg",
            "docprops/thumbnail.png",
        }
        remaining_parts = {
            str(part.partname).casefold().lstrip("/") for part in document.part.package.parts
        }
        if forbidden_parts & remaining_parts or any(
            name.startswith("customxml/") for name in remaining_parts
        ):
            raise ExportError("DOCX-Export enthaelt weiterhin nicht analysierte Paketdaten.")
        for part in document.part.package.parts:
            root = getattr(part, "element", None)
            if root is None:
                continue
            for element in root.iter():
                tag = getattr(element, "tag", "")
                local_name = tag.rpartition("}")[2] if isinstance(tag, str) else ""
                if local_name in {
                    "instrText",
                    "delInstrText",
                    "fldChar",
                    "docVars",
                    "mailMerge",
                }:
                    raise ExportError(
                        "DOCX-Export enthaelt weiterhin nicht analysierte "
                        "Feld- oder Seriendruckdaten."
                    )
                if local_name == "fldSimple" and any(
                    attribute.rpartition("}")[2] == "instr" for attribute in element.attrib
                ):
                    raise ExportError("DOCX-Export enthaelt weiterhin eine Feldanweisung.")
    except Exception as error:
        raise ExportError("DOCX-Export konnte nicht wieder geoeffnet werden.") from error
    for target, expected in expected_outputs.items():
        if actual.get(target) != expected:
            raise ExportError("DOCX-Export enthaelt nicht den geprueften Zieltext.")


def _verify_xlsx_targets(
    path: Path,
    expected_outputs: dict[tuple[int, str], str],
    expected_sheet_titles: dict[int, str],
) -> None:
    try:
        workbook = load_workbook(path, data_only=False, keep_links=False)
        for (sheet_index, coordinate), expected in expected_outputs.items():
            if sheet_index >= len(workbook.worksheets):
                raise ExportError("XLSX-Export hat ein Zielblatt verloren.")
            cell = workbook.worksheets[sheet_index][coordinate]
            if _xlsx_cell_text(cell.value) != expected:
                raise ExportError("XLSX-Export enthaelt nicht den geprueften Zelltext.")
            if cell.hyperlink is not None or cell.comment is not None:
                raise ExportError(
                    "XLSX-Export enthaelt am ersetzten Ziel weiterhin versteckte Daten."
                )
        for sheet_index, expected in expected_sheet_titles.items():
            if (
                sheet_index >= len(workbook.worksheets)
                or workbook.worksheets[sheet_index].title != expected
            ):
                raise ExportError("XLSX-Export enthaelt nicht den geprueften Blattnamen.")
        if any(worksheet.data_validations.dataValidation for worksheet in workbook.worksheets):
            raise ExportError(
                "XLSX-Export enthaelt weiterhin nicht analysierte Datenvalidierungen."
            )
        workbook.close()
    except ExportError:
        raise
    except Exception as error:
        raise ExportError("XLSX-Export konnte nicht wieder geoeffnet werden.") from error


def _sanitize_xlsx_workbook(workbook: object) -> None:
    """Remove unanalyzed OOXML metadata and link targets."""

    workbook.properties = DocumentProperties()
    workbook.custom_doc_props.props.clear()
    workbook._external_links = []  # noqa: SLF001 – openpyxl has no public setter.
    for name in tuple(workbook.defined_names):
        if not name.startswith("_xlnm."):
            del workbook.defined_names[name]
    for worksheet in workbook.worksheets:
        worksheet.data_validations.dataValidation.clear()
        for row in worksheet.iter_rows():
            for cell in row:
                # Covered parts of merged cells carry neither link nor comment.
                if cell.__class__.__name__ == "MergedCell":
                    continue
                cell.hyperlink = None
                cell.comment = None
        for header_footer in (
            worksheet.oddHeader,
            worksheet.oddFooter,
            worksheet.evenHeader,
            worksheet.evenFooter,
            worksheet.firstHeader,
            worksheet.firstFooter,
        ):
            for part in (
                header_footer.left,
                header_footer.center,
                header_footer.right,
            ):
                part.text = None
                part.font = None
                part.size = None
                part.color = None


def _validate_supported_xlsx_archive(source: Path) -> None:
    try:
        with zipfile.ZipFile(source) as archive:
            if any(
                member.filename.casefold().startswith(("xl/charts/", "xl/media/"))
                for member in archive.infolist()
            ):
                raise ExportValidationError(
                    "XLSX-Diagramme und eingebettete Bilder werden aus "
                    "Sicherheitsgruenden nicht unterstuetzt."
                )
    except ExportValidationError:
        raise
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise ExportValidationError("Die XLSX-Datei ist ungueltig oder beschaedigt.") from error


def _safe_xlsx_sheet_title(value: str, sheet_index: int) -> str:
    """Translate processing markers into a valid Excel sheet name."""

    translated = value.translate(
        str.maketrans(
            {
                "[": "(",
                "]": ")",
                ":": "-",
                "*": "-",
                "?": "-",
                "/": "-",
                "\\": "-",
            }
        )
    )
    translated = re.sub(r"\s+", " ", translated).strip(" '")[:31]
    return translated or f"Redigiert {sheet_index + 1}"


def _normalized_image_suffix(path: Path) -> str:
    suffix = path.suffix.lower()
    return ".jpg" if suffix == ".jpeg" else suffix


def _xlsx_cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return str(value)


def _validate_image_rectangles(
    size: tuple[int, int],
    redactions: Sequence[ImageRedaction],
) -> None:
    width, height = size
    for item in redactions:
        _, _, right, bottom = item.rect
        if right > width or bottom > height:
            raise ExportValidationError("Bild-Redaktion liegt ausserhalb der Pixelflaeche.")


#: JPEG smears the edges of drawn text. In an area with text, this pixel share may
#: deviate more strongly from the drawing's tolerance than the original pixels under the
#: area would.
_JPEG_TEXT_EDGE_SHARE: Final = 0.05


def _verify_image_redactions(
    path: Path,
    *,
    expected: Image.Image,
    redactions: Sequence[ImageRedaction],
    tolerance: int,
) -> None:
    """Each area shows exactly what was drawn, nothing from the original.

    Without text, this is the fill color; with text, the drawing in memory
    (``expected``) – pixel-perfect for PNG, up to font edges for JPEG.
    """

    try:
        with Image.open(path) as saved:
            image = saved.convert("RGB")
            if image.size != expected.size:
                raise ExportError("Bildexport hat unerwartete Abmessungen.")
            texts = [item.text.box for item in redactions if item.text is not None]
            for item in redactions:
                crop = image.crop(item.rect)
                if any(_boxes_touch(item.rect, box) for box in texts):
                    _verify_drawn_region(crop, expected.crop(item.rect), tolerance)
                    continue
                extrema = crop.getextrema()
                for (minimum, maximum), wanted in zip(extrema, item.fill, strict=True):
                    if minimum < max(0, wanted - tolerance) or maximum > min(
                        255, wanted + tolerance
                    ):
                        raise ExportError("Bild-Redaktion enthaelt weiterhin abweichende Pixel.")
    except ExportError:
        raise
    except Exception as error:
        raise ExportError("Bildexport konnte nicht nachgeprueft werden.") from error


def _boxes_touch(a: Sequence[int], b: Sequence[int]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _verify_drawn_region(saved: Image.Image, drawn: Image.Image, tolerance: int) -> None:
    difference = ImageChops.difference(saved, drawn).convert("L")
    histogram = difference.histogram()
    off = sum(histogram[tolerance + 1 :])
    allowed = 0 if tolerance == 0 else _JPEG_TEXT_EDGE_SHARE * saved.width * saved.height
    if off > allowed:
        raise ExportError("Bild-Redaktion enthaelt weiterhin abweichende Pixel.")


@lru_cache(maxsize=8)
def _font_program(fontname: str) -> bytes:
    # The Base-14 fonts of MuPDF (Nimbus): the same metrics used by the placement plan
    # to calculate width, without a separate font file.
    return bytes(fitz.Font(fontname).buffer)


def _image_font(fontname: str, size: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(io.BytesIO(_font_program(fontname)), size)


#: OCR reads small text poorly; the text box is scaled up to at least this pixel font
#: size before reading.
_READBACK_MIN_SIZE: Final = 32.0
_READBACK_BORDER: Final = 16


def _readback_image(
    path: Path,
    texts: Sequence[ImageText],
    *,
    backend: OcrBackend,
    languages: tuple[str, ...],
) -> None:
    """Read every drawn replacement text from the saved file."""

    try:
        with Image.open(path) as saved:
            image = saved.convert("RGB")
        if readback_layout_of(backend) == "sheet":
            pending = list(texts)
            for line_height in _SHEET_PASSES:
                read = _read_code_sheet(
                    [image.crop(text.box) for text in pending],
                    backend=backend,
                    languages=languages,
                    line_height=line_height,
                )
                pending = [
                    text
                    for value, text in zip(read, pending, strict=True)
                    if not readback_matches(value, text.value)
                ]
                if not pending:
                    return
            raise ReadbackError("Ein Ersatztext im Bild liess sich nicht zuruecklesen.")
        for text in texts:
            crop = image.crop(text.box)
            factor = max(1.0, _READBACK_MIN_SIZE / text.size)
            if factor > 1.0:
                crop = crop.resize(
                    (round(crop.width * factor), round(crop.height * factor)),
                    Image.Resampling.LANCZOS,
                )
            crop = ImageOps.expand(crop, border=_READBACK_BORDER, fill=(255, 255, 255))
            read = " ".join(word.text for word in backend.recognize(crop, languages=languages))
            if not readback_matches(read, text.value):
                raise ReadbackError("Ein Ersatztext im Bild liess sich nicht zuruecklesen.")
    except ReadbackError:
        raise
    except Exception as error:
        raise ReadbackError("Ersatztext im Bild konnte nicht zurueckgelesen werden.") from error
