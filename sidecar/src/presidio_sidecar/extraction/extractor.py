"""Offset-preserving extraction without FastAPI or job dependency."""

from __future__ import annotations

import codecs
import csv
import re
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from io import BytesIO, StringIO
from pathlib import Path
from typing import Final

from .docx_units import docx_has_notes, docx_story_containers, docx_units
from .models import (
    BoundingBox,
    CoordinateSpace,
    DocumentFormat,
    ExtractedDocument,
    ExtractionMethod,
    OcrMode,
    SegmentKind,
    StructuredPayload,
    TextSegment,
)
from .ocr import (
    APPLE_VISION,
    OcrBackend,
    OcrUnavailableError,
    OcrWord,
    default_ocr_backend,
    ocr_engine_name,
)


class ExtractionError(RuntimeError):
    """Controlled extraction error without document content in the error message."""


class UnsupportedDocumentError(ExtractionError):
    pass


class EmptyDocumentError(ExtractionError):
    pass


class EncryptedDocumentError(ExtractionError):
    pass


_SUFFIX_FORMATS: Final[dict[str, DocumentFormat]] = {
    ".txt": DocumentFormat.TXT,
    ".text": DocumentFormat.TXT,
    ".md": DocumentFormat.MARKDOWN,
    ".markdown": DocumentFormat.MARKDOWN,
    ".json": DocumentFormat.JSON,
    ".csv": DocumentFormat.CSV,
    ".pdf": DocumentFormat.PDF,
    ".docx": DocumentFormat.DOCX,
    ".xlsx": DocumentFormat.XLSX,
    ".png": DocumentFormat.PNG,
    ".jpg": DocumentFormat.JPEG,
    ".jpeg": DocumentFormat.JPEG,
}
_DIRECT_FORMATS: Final = {
    DocumentFormat.TXT,
    DocumentFormat.MARKDOWN,
    DocumentFormat.JSON,
    DocumentFormat.CSV,
}
_OCR_LANGUAGE = re.compile(r"^[A-Za-z0-9_-]+$")
_SCAN_AREA_RATIO: Final = 0.25
_SCAN_INK_RATIO: Final = 0.10
_SCAN_TEXT_LAYER_WORDS: Final = 20
_INK_GRID: Final = 64
_INK_LEVEL: Final = 250


@dataclass(slots=True)
class _TextAssembler:
    parts: list[str]
    segments: list[TextSegment]
    length: int = 0

    @classmethod
    def create(cls) -> _TextAssembler:
        return cls(parts=[], segments=[])

    @property
    def has_text(self) -> bool:
        return self.length > 0

    def separator(self, value: str) -> None:
        if value:
            self.parts.append(value)
            self.length += len(value)

    def segment(
        self,
        value: str,
        *,
        kind: SegmentKind,
        source_ref: str,
        separator: str = "",
        bbox: BoundingBox | None = None,
        confidence: float | None = None,
    ) -> None:
        if not value:
            return
        self.separator(separator)
        start = self.length
        self.parts.append(value)
        self.length += len(value)
        self.segments.append(
            TextSegment(
                start=start,
                end=self.length,
                kind=kind,
                source_ref=source_ref,
                bbox=bbox,
                confidence=confidence,
            )
        )

    def text(self) -> str:
        return "".join(self.parts)


class DocumentExtractor:
    """Extracts supported documents directly from bytes or a path.

    The byte entry is intended for multipart uploads and creates no temporary
    plaintext file. ``extract_path`` only reads the file and then delegates
    to the same path.
    """

    def __init__(
        self,
        *,
        ocr_backend: OcrBackend | None = None,
        pdf_ocr_dpi: int = 300,
        max_pages: int = 1_000,
        max_image_pixels: int = 100_000_000,
        max_office_uncompressed_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        if pdf_ocr_dpi < 72 or pdf_ocr_dpi > 600:
            raise ValueError("pdf_ocr_dpi muss zwischen 72 und 600 liegen.")
        if max_pages < 1 or max_image_pixels < 1 or max_office_uncompressed_bytes < 1:
            raise ValueError("Extraktionsgrenzen muessen positiv sein.")
        self._ocr_backend = ocr_backend
        self.pdf_ocr_dpi = pdf_ocr_dpi
        self.max_pages = max_pages
        self.max_image_pixels = max_image_pixels
        self.max_office_uncompressed_bytes = max_office_uncompressed_bytes

    @property
    def ocr_backend(self) -> OcrBackend:
        if self._ocr_backend is None:
            self._ocr_backend = default_ocr_backend()
        return self._ocr_backend

    @property
    def ocr_method(self) -> ExtractionMethod:
        """The method of an OCR result: the backend's engine."""

        if ocr_engine_name(self.ocr_backend) == APPLE_VISION:
            return ExtractionMethod.APPLE_VISION
        return ExtractionMethod.TESSERACT

    def extract_path(
        self,
        path: str | Path,
        *,
        ocr: OcrMode | str = OcrMode.AUTO,
        ocr_languages: Sequence[str] = ("deu", "eng"),
        structured_data: str | None = None,
    ) -> ExtractedDocument:
        source = Path(path)
        try:
            payload = source.read_bytes()
        except OSError as error:
            raise ExtractionError("Das Dokument konnte nicht gelesen werden.") from error
        return self.extract_bytes(
            payload,
            filename=source.name,
            ocr=ocr,
            ocr_languages=ocr_languages,
            structured_data=structured_data,
        )

    def extract_bytes(
        self,
        payload: bytes,
        *,
        filename: str,
        ocr: OcrMode | str = OcrMode.AUTO,
        ocr_languages: Sequence[str] = ("deu", "eng"),
        structured_data: str | None = None,
    ) -> ExtractedDocument:
        if not isinstance(payload, bytes):
            raise TypeError("payload muss bytes sein.")
        if not payload:
            raise EmptyDocumentError("Das Dokument ist leer.")
        source_name = _safe_source_name(filename)
        document_format = _format_for(source_name)
        mode = _ocr_mode(ocr)
        languages = _ocr_languages(
            ocr_languages,
            allow_empty=mode is OcrMode.NEVER,
        )
        if structured_data not in {None, "direct", "presidio_structured"}:
            raise ExtractionError("structured_data muss direct oder presidio_structured sein.")

        if document_format in _DIRECT_FORMATS:
            return self._extract_direct(
                payload,
                source_name,
                document_format,
                structured_data=structured_data,
            )
        if document_format is DocumentFormat.PDF:
            return self._extract_pdf(payload, source_name, mode, languages)
        if document_format is DocumentFormat.DOCX:
            return self._extract_docx(payload, source_name)
        if document_format is DocumentFormat.XLSX:
            return self._extract_xlsx(payload, source_name)
        if document_format in {DocumentFormat.PNG, DocumentFormat.JPEG}:
            return self._extract_image(
                payload,
                source_name,
                document_format,
                mode,
                languages,
            )
        raise UnsupportedDocumentError("Nicht unterstuetztes Dokumentformat.")

    def _extract_direct(
        self,
        payload: bytes,
        source_name: str,
        document_format: DocumentFormat,
        *,
        structured_data: str | None,
    ) -> ExtractedDocument:
        text = _decode_text(payload)
        if not text:
            raise EmptyDocumentError("Das Dokument enthaelt keinen Text.")

        structured = None
        if document_format is DocumentFormat.CSV:
            dialect = _csv_dialect(text)
            segments = _csv_segments(text, dialect)
            structured = structured_payload(
                text, document_format, structured_data=structured_data, dialect=dialect
            )
            if not segments:
                segments = _document_segment(text, source_name)
        else:
            segments = _document_segment(text, source_name)

        return ExtractedDocument(
            text=text,
            format=document_format,
            source_name=source_name,
            method=ExtractionMethod.DIRECT,
            segments=segments,
            structured=structured,
        )

    def _extract_pdf(
        self,
        payload: bytes,
        source_name: str,
        mode: OcrMode,
        languages: tuple[str, ...],
    ) -> ExtractedDocument:
        try:
            import fitz

            # ``apply_redactions`` removes every character whose bounding box
            # intersects the redaction box even slightly. PyMuPDF's normal,
            # line-height-aligned
            # character boxes can therefore capture text from a closely adjacent line.
            # The smaller
            # glyph geometry is a process-wide, idempotent setting;
            # we switch it on once and never off per job.
            fitz.TOOLS.set_small_glyph_heights(True)
            document = fitz.open(stream=payload, filetype="pdf")
        except Exception as error:
            raise ExtractionError("Die PDF-Datei ist ungueltig oder beschaedigt.") from error

        assembler = _TextAssembler.create()
        text_layer_pages: list[int] = []
        text_used_pages: list[int] = []
        ocr_pages: list[int] = []
        ink_ratios: dict[int, float] = {}
        try:
            if document.needs_pass:
                raise EncryptedDocumentError(
                    "Passwortgeschuetzte PDF-Dateien koennen nicht extrahiert werden."
                )
            if document.page_count < 1:
                raise EmptyDocumentError("Die PDF-Datei enthaelt keine Seiten.")
            if document.page_count > self.max_pages:
                raise ExtractionError("Die PDF-Datei hat zu viele Seiten.")

            for page_index in range(document.page_count):
                page = document.load_page(page_index)
                words = _pdf_words(page)
                if words:
                    text_layer_pages.append(page_index)
                text_layer_usable = bool(words) and _pdf_text_layer_is_usable(words)

                if words and (
                    mode is OcrMode.NEVER
                    or (
                        mode is OcrMode.AUTO
                        and text_layer_usable
                        and not _pdf_page_has_scan_raster(
                            page,
                            words,
                            max_image_pixels=self.max_image_pixels,
                            ink_ratios=ink_ratios,
                        )
                    )
                ):
                    _append_pdf_words(assembler, words, page_index)
                    text_used_pages.append(page_index)
                    continue
                if mode is OcrMode.NEVER:
                    continue

                scale = self.pdf_ocr_dpi / 72
                if page.rect.width * scale * page.rect.height * scale > self.max_image_pixels:
                    raise ExtractionError("Eine PDF-Seite ist fuer die sichere OCR zu gross.")
                image, width, height = _render_pdf_page(page, self.pdf_ocr_dpi)
                try:
                    ocr_words = self._ocr(image, languages)
                except OcrUnavailableError:
                    # In ``auto``, OCR is a quality decision, not a guarantee. A usable
                    # text layer carries the page even
                    # without text recognition; only ``always`` and pages without a text
                    # layer
                    # may fail on this.
                    if mode is not OcrMode.AUTO or not text_layer_usable:
                        raise
                    _append_pdf_words(assembler, words, page_index)
                    text_used_pages.append(page_index)
                    continue
                ocr_pages.append(page_index)
                _append_ocr_words(
                    assembler,
                    ocr_words,
                    page_index=page_index,
                    coordinate_space=CoordinateSpace.PDF_POINTS,
                    x_scale=page.rect.width / width,
                    y_scale=page.rect.height / height,
                    derotation_matrix=page.derotation_matrix,
                )

            if not assembler.has_text:
                if mode is OcrMode.NEVER:
                    raise EmptyDocumentError(
                        "Die PDF-Datei hat keinen Textlayer und OCR ist deaktiviert."
                    )
                raise EmptyDocumentError("In der PDF-Datei wurde kein Text erkannt.")

            if ocr_pages and text_used_pages:
                method = ExtractionMethod.MIXED_PDF
            elif ocr_pages:
                method = self.ocr_method
            else:
                method = ExtractionMethod.PYMUPDF
            return ExtractedDocument(
                text=assembler.text(),
                format=DocumentFormat.PDF,
                source_name=source_name,
                method=method,
                segments=tuple(assembler.segments),
                ocr_used=bool(ocr_pages),
                page_count=document.page_count,
                text_layer_pages=tuple(text_layer_pages),
                ocr_pages=tuple(ocr_pages),
            )
        except (EmptyDocumentError, EncryptedDocumentError, OcrUnavailableError):
            raise
        except Exception as error:
            raise ExtractionError("Die PDF-Datei konnte nicht extrahiert werden.") from error
        finally:
            document.close()

    def _extract_docx(self, payload: bytes, source_name: str) -> ExtractedDocument:
        _validate_office_archive(payload, self.max_office_uncompressed_bytes)
        try:
            from docx import Document
            from docx.document import Document as DocumentObject

            document: DocumentObject = Document(BytesIO(payload))
            if _docx_has_unsupported_package_parts(document):
                raise ExtractionError(
                    "DOCX-eingebettete oder benutzerdefinierte Inhalte werden "
                    "aus Sicherheitsgruenden nicht unterstuetzt."
                )
            if docx_has_notes(document):
                raise ExtractionError(
                    "DOCX-Fussnoten und -Endnoten werden aus "
                    "Sicherheitsgruenden nicht unterstuetzt."
                )
            if _docx_has_content_controls(document):
                raise ExtractionError(
                    "DOCX-Inhaltssteuerelemente werden aus Sicherheitsgruenden nicht unterstuetzt."
                )
            if _docx_has_revision_markup(document):
                raise ExtractionError(
                    "DOCX-Aenderungsverfolgung wird aus Sicherheitsgruenden nicht unterstuetzt."
                )
            if len(document.comments):
                raise ExtractionError(
                    "DOCX-Kommentare werden aus Sicherheitsgruenden nicht unterstuetzt."
                )
            story_containers = docx_story_containers(document)
            if document.element.xpath(".//*[local-name()='txbxContent']") or any(
                container._element.xpath(  # noqa: SLF001 - OOXML-Sicherheitspruefung.
                    ".//*[local-name()='txbxContent']"
                )
                for _, container in story_containers
            ):
                raise ExtractionError("DOCX-Textfelder werden noch nicht sicher unterstuetzt.")
            units = docx_units(document)
        except ExtractionError:
            raise
        except Exception as error:
            raise ExtractionError("Die DOCX-Datei ist ungueltig oder beschaedigt.") from error

        assembler = _TextAssembler.create()
        for unit in units:
            assembler.segment(
                unit.text,
                kind=unit.kind,
                source_ref=unit.target_id,
                separator=unit.joined_separator(assembler.has_text),
            )

        if not assembler.has_text:
            raise EmptyDocumentError("Die DOCX-Datei enthaelt keinen Text.")
        return ExtractedDocument(
            text=assembler.text(),
            format=DocumentFormat.DOCX,
            source_name=source_name,
            method=ExtractionMethod.PYTHON_DOCX,
            segments=tuple(assembler.segments),
        )

    def _extract_xlsx(self, payload: bytes, source_name: str) -> ExtractedDocument:
        _validate_office_archive(payload, self.max_office_uncompressed_bytes)
        if _office_archive_has_prefix(
            payload,
            ("xl/charts/", "xl/media/"),
        ):
            raise ExtractionError(
                "XLSX-Diagramme und eingebettete Bilder werden aus "
                "Sicherheitsgruenden nicht unterstuetzt."
            )
        try:
            from openpyxl import load_workbook

            workbook = load_workbook(
                BytesIO(payload),
                read_only=True,
                data_only=False,
                keep_links=False,
            )
        except Exception as error:
            raise ExtractionError("Die XLSX-Datei ist ungueltig oder beschaedigt.") from error

        assembler = _TextAssembler.create()
        try:
            workbook_started = False
            for sheet_index, worksheet in enumerate(workbook.worksheets):
                if workbook_started:
                    assembler.separator("\f")
                workbook_started = True
                assembler.segment(
                    worksheet.title,
                    kind=SegmentKind.XLSX_SHEET_TITLE,
                    source_ref=f"sheet:{sheet_index}",
                )
                for row in worksheet.iter_rows():
                    values = [_spreadsheet_value(cell.value) for cell in row]
                    nonempty = [index for index, value in enumerate(values) if value]
                    if not nonempty:
                        continue
                    assembler.separator("\n")

                    first = nonempty[0]
                    last = nonempty[-1]
                    for index in range(first, last + 1):
                        if index > first:
                            assembler.separator("\t")
                        value = values[index]
                        if value:
                            cell = row[index]
                            assembler.segment(
                                value,
                                kind=(
                                    SegmentKind.XLSX_FORMULA
                                    if cell.data_type == "f"
                                    else SegmentKind.XLSX_CELL
                                ),
                                source_ref=f"{worksheet.title}!{cell.coordinate}",
                            )
        except Exception as error:
            raise ExtractionError("Die XLSX-Zellen konnten nicht gelesen werden.") from error
        finally:
            workbook.close()

        if not assembler.has_text:
            raise EmptyDocumentError("Die XLSX-Datei enthaelt keine Zellwerte.")
        return ExtractedDocument(
            text=assembler.text(),
            format=DocumentFormat.XLSX,
            source_name=source_name,
            method=ExtractionMethod.OPENPYXL,
            segments=tuple(assembler.segments),
        )

    def _extract_image(
        self,
        payload: bytes,
        source_name: str,
        document_format: DocumentFormat,
        mode: OcrMode,
        languages: tuple[str, ...],
    ) -> ExtractedDocument:
        if mode is OcrMode.NEVER:
            raise EmptyDocumentError("Bilddateien benoetigen OCR, OCR ist aber deaktiviert.")
        try:
            from PIL import Image, ImageOps

            with Image.open(BytesIO(payload)) as opened:
                if opened.width * opened.height > self.max_image_pixels:
                    raise ExtractionError("Die Bilddatei hat zu viele Pixel.")
                opened.load()
                # The image export edits the same normalized orientation.
                # Only this keeps OCR pixel boxes valid for EXIF-rotated JPEGs.
                image = ImageOps.exif_transpose(opened).convert("RGB")
        except ExtractionError:
            raise
        except Exception as error:
            raise ExtractionError("Die Bilddatei ist ungueltig oder beschaedigt.") from error

        words = self._ocr(image, languages)
        assembler = _TextAssembler.create()
        _append_ocr_words(
            assembler,
            words,
            page_index=0,
            coordinate_space=CoordinateSpace.PIXELS,
        )
        if not assembler.has_text:
            raise EmptyDocumentError("Im Bild wurde kein Text erkannt.")
        return ExtractedDocument(
            text=assembler.text(),
            format=document_format,
            source_name=source_name,
            method=self.ocr_method,
            segments=tuple(assembler.segments),
            ocr_used=True,
            page_count=1,
            ocr_pages=(0,),
            image_size=image.size,
        )

    def _ocr(self, image: object, languages: tuple[str, ...]) -> tuple[OcrWord, ...]:
        return _without_ghost_words(self.ocr_backend.recognize(image, languages=languages))


def extract_document(
    path: str | Path,
    *,
    ocr: OcrMode | str = OcrMode.AUTO,
    ocr_languages: Sequence[str] = ("deu", "eng"),
    structured_data: str | None = None,
) -> ExtractedDocument:
    return DocumentExtractor().extract_path(
        path,
        ocr=ocr,
        ocr_languages=ocr_languages,
        structured_data=structured_data,
    )


def extract_bytes(
    payload: bytes,
    *,
    filename: str,
    ocr: OcrMode | str = OcrMode.AUTO,
    ocr_languages: Sequence[str] = ("deu", "eng"),
    structured_data: str | None = None,
) -> ExtractedDocument:
    return DocumentExtractor().extract_bytes(
        payload,
        filename=filename,
        ocr=ocr,
        ocr_languages=ocr_languages,
        structured_data=structured_data,
    )


def _validate_office_archive(payload: bytes, max_uncompressed_bytes: int) -> None:
    """Limit Office ZIP archives before loading via third-party libraries."""

    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            members = archive.infolist()
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise ExtractionError("Die Office-Datei ist ungueltig oder beschaedigt.") from error

    if len(members) > 10_000:
        raise ExtractionError("Die Office-Datei enthaelt zu viele Archiveintraege.")

    total_size = 0
    for member in members:
        if member.file_size < 0 or member.compress_size < 0:
            raise ExtractionError("Die Office-Datei hat ungueltige Archivgroessen.")
        total_size += member.file_size
        if total_size > max_uncompressed_bytes:
            raise ExtractionError(
                "Die Office-Datei ist entpackt zu gross fuer die sichere Verarbeitung."
            )


def _office_archive_has_prefix(
    payload: bytes,
    prefixes: tuple[str, ...],
) -> bool:
    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            return any(
                member.filename.casefold().startswith(prefixes) for member in archive.infolist()
            )
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise ExtractionError("Die Office-Datei ist ungueltig oder beschaedigt.") from error


def _format_for(source_name: str) -> DocumentFormat:
    suffix = Path(source_name).suffix.casefold()
    try:
        return _SUFFIX_FORMATS[suffix]
    except KeyError as error:
        raise UnsupportedDocumentError(
            f"Nicht unterstuetztes Dokumentformat: {suffix or 'ohne Dateiendung'}."
        ) from error


def _safe_source_name(filename: str) -> str:
    if not isinstance(filename, str):
        raise TypeError("filename muss eine Zeichenkette sein.")
    source_name = Path(filename.replace("\\", "/")).name.strip()
    if not source_name or source_name in {".", ".."}:
        raise UnsupportedDocumentError("Ein sicherer Dateiname mit Endung ist erforderlich.")
    return source_name


def _ocr_mode(raw: OcrMode | str) -> OcrMode:
    try:
        return OcrMode(raw)
    except ValueError as error:
        raise ExtractionError("ocr muss auto, always oder never sein.") from error


def _ocr_languages(
    raw: Sequence[str],
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if isinstance(raw, str):
        raw = (raw,)
    languages = tuple(str(language).strip() for language in raw)
    if (not languages and not allow_empty) or any(
        not _OCR_LANGUAGE.fullmatch(language) for language in languages
    ):
        raise ExtractionError("OCR-Sprachen enthalten einen ungueltigen Sprachcode.")
    return languages


def _decode_text(payload: bytes) -> str:
    encodings: tuple[str, ...]
    if payload.startswith(codecs.BOM_UTF8):
        encodings = ("utf-8-sig",)
    elif payload.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encodings = ("utf-32",)
    elif payload.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encodings = ("utf-16",)
    else:
        encodings = ("utf-8",)
    try:
        return payload.decode(encodings[0])
    except UnicodeDecodeError as error:
        raise ExtractionError(
            "Textdateien muessen UTF-8 oder per BOM gekennzeichnetes UTF-16/32 verwenden."
        ) from error


def _document_segment(text: str, source_name: str) -> tuple[TextSegment, ...]:
    return (
        TextSegment(
            start=0,
            end=len(text),
            kind=SegmentKind.DOCUMENT,
            source_ref=source_name,
        ),
    )


def _csv_dialect(text: str) -> csv.Dialect:
    sample = text[:8192]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return csv.excel


def _csv_segments(text: str, dialect: csv.Dialect) -> tuple[TextSegment, ...]:
    """Lexical CSV cells, without rewriting the canonical raw text."""

    delimiter = dialect.delimiter
    quote = dialect.quotechar
    doublequote = dialect.doublequote
    segments: list[TextSegment] = []
    index = 0
    field_start = 0
    row = 1
    column = 1
    quoted = False

    def append_field(end: int) -> None:
        value = text[field_start:end]
        if value:
            segments.append(
                TextSegment(
                    start=field_start,
                    end=end,
                    kind=SegmentKind.CSV_CELL,
                    source_ref=f"row:{row}/column:{column}",
                )
            )

    while index < len(text):
        character = text[index]
        if quoted:
            if quote and character == quote:
                if doublequote and index + 1 < len(text) and text[index + 1] == quote:
                    index += 2
                    continue
                quoted = False
            index += 1
            continue

        if quote and character == quote and index == field_start:
            quoted = True
            index += 1
            continue
        if character == delimiter:
            append_field(index)
            field_start = index + 1
            column += 1
            index += 1
            continue
        if character in "\r\n":
            append_field(index)
            if character == "\r" and index + 1 < len(text) and text[index + 1] == "\n":
                index += 1
            index += 1
            field_start = index
            row += 1
            column = 1
            continue
        index += 1

    if field_start < len(text):
        append_field(len(text))
    return tuple(segments)


def structured_payload(
    text: str,
    document_format: DocumentFormat,
    *,
    structured_data: str | None,
    dialect: csv.Dialect | None = None,
) -> StructuredPayload | None:
    """The structured view of read text (CSV), otherwise ``None``.

    It depends only on the text: the draft does not save it, but
    rebuilds it upon loading (Plan Draft 3.2). It does not touch the segment offsets.
    """

    if document_format is not DocumentFormat.CSV or structured_data == "direct":
        return None
    structured = _presidio_csv_payload(text, dialect or _csv_dialect(text))
    if structured_data == "presidio_structured" and structured is None:
        raise ExtractionError("CSV konnte nicht sicher mit presidio-structured gelesen werden.")
    return structured


def _presidio_csv_payload(
    text: str,
    dialect: csv.Dialect,
) -> StructuredPayload | None:
    """Generates the native Pandas view of ``presidio-structured``.

    Errors in the optional structured view must not prevent
    offset-preserving raw text analysis.
    """

    try:
        from presidio_structured import CsvReader

        kwargs: dict[str, object] = {
            "sep": dialect.delimiter,
            "doublequote": dialect.doublequote,
            "escapechar": dialect.escapechar,
            "skipinitialspace": dialect.skipinitialspace,
            "dtype": str,
            "keep_default_na": False,
            "engine": "python",
        }
        if dialect.quotechar:
            kwargs["quotechar"] = dialect.quotechar
        frame = CsvReader().read(path=StringIO(text), **kwargs)
    except Exception:
        return None
    return StructuredPayload(
        backend="presidio_structured.pandas",
        data=frame,
        columns=tuple(str(column) for column in frame.columns),
    )


def _pdf_words(page: object) -> tuple[tuple[object, ...], ...]:
    words = page.get_text("words", sort=True)
    return tuple(word for word in words if len(word) >= 8 and str(word[4]))


def _pdf_text_layer_is_usable(
    words: tuple[tuple[object, ...], ...],
) -> bool:
    """Ignore individual page numbers or useless OCR remnants in ``auto``."""

    values = tuple(str(word[4]) for word in words)
    lexical_words = sum(any(character.isalpha() for character in value) for value in values)
    letter_count = sum(character.isalpha() for value in values for character in value)
    return lexical_words >= 2 or letter_count >= 8


def _pdf_page_has_scan_raster(
    page: object,
    words: tuple[tuple[object, ...], ...],
    *,
    max_image_pixels: int,
    ink_ratios: dict[int, float],
) -> bool:
    """Detect scan/hybrid pages despite an apparently usable text layer.

    Measuring only the rectangular area classifies every page-filling
    letterhead as a scan: invoices often place logos, color areas, and
    footers as a full-page, otherwise empty image over the page. Therefore
    only raster area with document-specific ink amount counts here. With a
    thin text layer, every large image remains suspicious, because even a faint
    raster can carry the actual page content.
    """

    page_rect = page.rect
    page_area = float(page_rect.width * page_rect.height)
    if page_area <= 0:
        return False
    thin_text_layer = len(words) < _SCAN_TEXT_LAYER_WORDS
    raster_area = 0.0
    inked_area = 0.0
    for image in page.get_images(full=True):
        xref = int(image[0])
        area = 0.0
        for raw_rect in page.get_image_rects(xref):
            rect = raw_rect & page_rect
            if not rect.is_empty and not rect.is_infinite:
                area += float(rect.width * rect.height)
        if area <= 0:
            continue
        raster_area += area
        if thin_text_layer and raster_area / page_area >= _SCAN_AREA_RATIO:
            return True
        if xref not in ink_ratios:
            # Letterhead repeats pagewise; decoding once is enough.
            ink_ratios[xref] = _image_ink_ratio(
                page.parent,
                xref,
                max_pixels=max_image_pixels,
            )
        if ink_ratios[xref] >= _SCAN_INK_RATIO:
            inked_area += area
            if inked_area / page_area >= _SCAN_AREA_RATIO:
                return True
    return False


def _image_ink_ratio(document: object, xref: int, *, max_pixels: int) -> float:
    """Fraction of coarse grid cells with visible content; default to ``1.0`` if in
    doubt.

    An unreadable or oversized image is intentionally treated as fully printed so that
    scan detection remains fail-closed.
    """

    try:
        from PIL import Image

        payload = document.extract_image(xref)["image"]
        with Image.open(BytesIO(payload)) as opened:
            if opened.width * opened.height > max_pixels:
                return 1.0
            opened.draft("L", (_INK_GRID, _INK_GRID))
            image = opened
            if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
                # Transparent areas are paper white, not ink.
                image = Image.alpha_composite(
                    Image.new("RGBA", image.size, "white"),
                    image.convert("RGBA"),
                )
            grid = image.convert("L").resize((_INK_GRID, _INK_GRID), Image.BOX)
        inked = sum(grid.histogram()[:_INK_LEVEL])
    except Exception:
        return 1.0
    return inked / (_INK_GRID * _INK_GRID)


def _append_pdf_words(
    assembler: _TextAssembler,
    words: tuple[tuple[object, ...], ...],
    page_index: int,
) -> None:
    previous_line: tuple[int, int] | None = None
    page_started = False
    for raw in words:
        x0, y0, x1, y1 = (float(raw[index]) for index in range(4))
        value = str(raw[4])
        block = int(raw[5])
        line = int(raw[6])
        word = int(raw[7])
        line_id = (block, line)
        if not page_started:
            separator = "\f" if assembler.has_text else ""
            page_started = True
        else:
            separator = " " if line_id == previous_line else "\n"
        previous_line = line_id
        assembler.segment(
            value,
            kind=SegmentKind.PDF_WORD,
            source_ref=f"page:{page_index + 1}/block:{block}/line:{line}/word:{word}",
            separator=separator,
            bbox=_bounded_box(
                x0,
                y0,
                x1,
                y1,
                page_index=page_index,
                coordinate_space=CoordinateSpace.PDF_POINTS,
            ),
        )


def _render_pdf_page(page: object, dpi: int) -> tuple[object, int, int]:
    import fitz
    from PIL import Image

    pixmap = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    return image, pixmap.width, pixmap.height


def _append_ocr_words(
    assembler: _TextAssembler,
    words: tuple[OcrWord, ...],
    *,
    page_index: int,
    coordinate_space: CoordinateSpace,
    x_scale: float = 1.0,
    y_scale: float = 1.0,
    derotation_matrix: object | None = None,
) -> None:
    previous_line: tuple[int, int, int, int] | None = None
    page_started = False
    for word_index, word in enumerate(words, start=1):
        if not page_started:
            separator = "\f" if assembler.has_text else ""
            page_started = True
        else:
            separator = " " if word.line_id == previous_line else "\n"
        previous_line = word.line_id
        coordinates = (
            word.left * x_scale,
            word.top * y_scale,
            (word.left + word.width) * x_scale,
            (word.top + word.height) * y_scale,
        )
        if derotation_matrix is not None:
            import fitz

            transformed = fitz.Rect(coordinates) * derotation_matrix
            coordinates = (
                transformed.x0,
                transformed.y0,
                transformed.x1,
                transformed.y1,
            )
        assembler.segment(
            word.text,
            kind=SegmentKind.OCR_WORD,
            source_ref=(
                f"page:{page_index + 1}/ocr-page:{word.line_id[0]}"
                f"/block:{word.line_id[1]}/paragraph:{word.line_id[2]}"
                f"/line:{word.line_id[3]}/word:{word_index}"
            ),
            separator=separator,
            bbox=_bounded_box(
                *coordinates,
                page_index=page_index,
                coordinate_space=coordinate_space,
            ),
            confidence=word.confidence,
        )


#: From this overlap share of the smaller box, an identical word is considered the same
#: word.
_GHOST_OVERLAP = 0.5


def _without_ghost_words(words: tuple[OcrWord, ...]) -> tuple[OcrWord, ...]:
    """Remove words that Tesseract reports twice at the same location.

    With cut-off or bold fonts, Tesseract occasionally outputs a second line containing
    the same words above the first. In the text, the value would appear twice; two
    findings for the same pixels would receive two overlapping ghost boxes, and the
    higher ghost box would erase the neighbor line. The pixels are covered by the first
    word.
    """

    kept: list[OcrWord] = []
    for word in words:
        text = word.text.casefold()
        if not any(
            other.text.casefold() == text and _overlap_share(word, other) >= _GHOST_OVERLAP
            for other in kept
        ):
            kept.append(word)
    return words if len(kept) == len(words) else tuple(kept)


def _overlap_share(first: OcrWord, second: OcrWord) -> float:
    width = min(first.left + first.width, second.left + second.width) - max(first.left, second.left)
    height = min(first.top + first.height, second.top + second.height) - max(first.top, second.top)
    if width <= 0 or height <= 0:
        return 0.0
    smaller = min(first.width * first.height, second.width * second.height)
    return width * height / smaller if smaller > 0 else 0.0


def _bounded_box(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    page_index: int,
    coordinate_space: CoordinateSpace,
) -> BoundingBox:
    safe_x0 = max(0.0, x0)
    safe_y0 = max(0.0, y0)
    safe_x1 = max(safe_x0 + 0.001, x1)
    safe_y1 = max(safe_y0 + 0.001, y1)
    return BoundingBox(
        x0=safe_x0,
        y0=safe_y0,
        x1=safe_x1,
        y1=safe_y1,
        page_index=page_index,
        coordinate_space=coordinate_space,
    )


def _spreadsheet_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return str(value)


def _docx_has_revision_markup(document: object) -> bool:
    """Detect Word revision contents that python-docx deliberately hides."""

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


def _docx_has_unsupported_package_parts(document: object) -> bool:
    unsupported_prefixes = (
        "word/media/",
        "word/embeddings/",
        "word/charts/",
        "word/diagrams/",
        "word/activex/",
    )
    return any(
        str(part.partname).casefold().lstrip("/").startswith(unsupported_prefixes)
        for part in document.part.package.parts
    )


def _docx_has_content_controls(document: object) -> bool:
    for part in document.part.package.parts:
        root = getattr(part, "element", None)
        if root is None:
            continue
        for element in root.iter():
            tag = getattr(element, "tag", "")
            if isinstance(tag, str) and tag.rpartition("}")[2] == "sdt":
                return True
    return False
