"""Formats, offsets, text layer and local OCR positions."""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

import fitz
import pytest
from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from openpyxl import Workbook
from PIL import Image, ImageDraw

from presidio_sidecar.exporting import (
    DocxReplacement,
    ExportValidationError,
    PdfRedaction,
    export_docx,
    export_redacted_pdf,
)
from presidio_sidecar.extraction import (
    CoordinateSpace,
    DocumentExtractor,
    DocumentFormat,
    EmptyDocumentError,
    ExtractionError,
    ExtractionMethod,
    OcrUnavailableError,
    OcrWord,
    SegmentKind,
    TesseractOcrBackend,
    UnsupportedDocumentError,
    extract_bytes,
)


class RecordingOcr:
    def __init__(self, words: tuple[OcrWord, ...] | None = None) -> None:
        self.words = words or (
            OcrWord(
                text="Max",
                left=10,
                top=20,
                width=30,
                height=10,
                line_id=(1, 1, 1, 1),
                confidence=0.96,
            ),
            OcrWord(
                text="Mustermann",
                left=45,
                top=20,
                width=70,
                height=10,
                line_id=(1, 1, 1, 1),
                confidence=0.93,
            ),
        )
        self.calls: list[tuple[tuple[int, int], tuple[str, ...]]] = []

    def recognize(self, image, *, languages):  # noqa: ANN001, ANN201
        self.calls.append((image.size, languages))
        return self.words


class ForbiddenOcr:
    def recognize(self, image, *, languages):  # noqa: ANN001, ANN201
        del image, languages
        raise AssertionError("OCR darf bei vorhandenem Textlayer nicht laufen")


class UnavailableOcr:
    def recognize(self, image, *, languages):  # noqa: ANN001, ANN201
        del image, languages
        raise OcrUnavailableError("Tesseract oder die Sprachdaten deu/eng sind nicht verfuegbar.")


@pytest.mark.parametrize(
    ("filename", "expected_format", "payload"),
    [
        ("notiz.txt", DocumentFormat.TXT, "Grüße\r\nMax".encode()),
        ("readme.md", DocumentFormat.MARKDOWN, b"# Titel\r\nText"),
        ("daten.json", DocumentFormat.JSON, b'{"name": "Max"}\r\n'),
    ],
)
def test_direct_text_formats_keep_offsets(
    filename: str,
    expected_format: DocumentFormat,
    payload: bytes,
) -> None:
    result = extract_bytes(payload, filename=filename)

    expected = payload.decode()
    assert result.text == expected
    assert result.format is expected_format
    assert result.method is ExtractionMethod.DIRECT
    assert result.segments[0].start == 0
    assert result.segments[0].end == len(expected)
    assert result.text[result.segments[0].start : result.segments[0].end] == expected
    assert result.ocr_used is False


def test_utf16_bom_decoded_without_newline_normalization() -> None:
    payload = "Zeile 1\r\nZeile 2".encode("utf-16")

    result = extract_bytes(payload, filename="notiz.txt")

    assert result.text == "Zeile 1\r\nZeile 2"


def test_csv_keeps_raw_text_and_provides_presidio_structured_view() -> None:
    text = 'name;ort\r\n"Max Mustermann";"Wien; Österreich"\r\n'

    result = extract_bytes(text.encode(), filename="../../kontakte.csv")

    assert result.source_name == "kontakte.csv"
    assert result.text == text
    assert result.structured is not None
    assert result.structured.backend == "presidio_structured.pandas"
    assert result.structured.columns == ("name", "ort")
    assert result.structured.data.iloc[0].to_dict() == {
        "name": "Max Mustermann",
        "ort": "Wien; Österreich",
    }
    max_segment = next(
        segment
        for segment in result.segments
        if "Max Mustermann" in result.text[segment.start : segment.end]
    )
    assert max_segment.kind is SegmentKind.CSV_CELL
    assert max_segment.source_ref == "row:2/column:1"
    assert result.text[max_segment.start : max_segment.end] == '"Max Mustermann"'


def test_csv_profile_can_explicitly_disable_presidio_structured() -> None:
    result = extract_bytes(
        b"name;ort\nMax Mustermann;Wien\n",
        filename="kontakte.csv",
        structured_data="direct",
    )

    assert result.structured is None
    assert "Max Mustermann" in result.text


def test_extract_path_delegates_to_same_offset_preserving_byte_path(tmp_path: Path) -> None:
    path = tmp_path / "notiz.md"
    path.write_bytes(b"A\r\nB")

    result = DocumentExtractor().extract_path(path)

    assert result.text == "A\r\nB"
    assert result.source_name == "notiz.md"


def test_pdf_textlayer_provides_positions_and_skips_ocr() -> None:
    payload = _pdf_with_text("Erika Mustermann")

    result = DocumentExtractor(ocr_backend=ForbiddenOcr()).extract_bytes(
        payload,
        filename="brief.pdf",
        ocr="auto",
    )

    assert result.text == "Erika Mustermann"
    assert result.method is ExtractionMethod.PYMUPDF
    assert result.has_text_layer is True
    assert result.text_layer_pages == (0,)
    assert result.ocr_used is False
    assert all(segment.kind is SegmentKind.PDF_WORD for segment in result.segments)
    start = result.text.index("Mustermann")
    boxes = result.boxes_for_span(start, start + len("Mustermann"))
    assert len(boxes) == 1
    assert boxes[0].page_index == 0
    assert boxes[0].coordinate_space is CoordinateSpace.PDF_POINTS


def test_pdf_redaction_deletes_no_text_from_tightly_adjacent_line(
    tmp_path: Path,
) -> None:
    source = tmp_path / "enge-zeilen.pdf"
    destination = tmp_path / "enge-zeilen-redigiert.pdf"
    retained = "KEEP@example.test"
    secret = "DELETE-123"
    document = fitz.open()
    page = document.new_page(width=300, height=100)
    # With 12 pt baseline spacing, PyMuPDF's default line-aligned boxes overlap.
    # Adding padding for the second line would otherwise remove characters from the first.
    page.insert_text((20, 40), retained, fontsize=10)
    page.insert_text((20, 52), secret, fontsize=10)
    document.save(source)
    document.close()

    extracted = DocumentExtractor(ocr_backend=ForbiddenOcr()).extract_path(source)
    start = extracted.text.index(secret)
    boxes = extracted.boxes_for_span(start, start + len(secret))
    assert len(boxes) == 1
    box = boxes[0]
    padding = 0.75

    export_redacted_pdf(
        source,
        destination,
        (
            PdfRedaction(
                page_index=0,
                rect=(
                    box.x0 - padding,
                    box.y0 - padding,
                    box.x1 + padding,
                    box.y1 + padding,
                ),
                removed_text=secret,
            ),
        ),
    )

    with fitz.open(destination) as redacted:
        text = redacted[0].get_text("text")
    assert retained in text
    assert secret not in text


def test_rotated_pdf_uses_unrotated_box_space_until_real_redaction(
    tmp_path: Path,
) -> None:
    source = tmp_path / "rotiert.pdf"
    destination = tmp_path / "redigiert.pdf"
    secret = "Hans Meier"
    document = fitz.open()
    page = document.new_page(width=400, height=200)
    page.insert_text((20, 40), "Behalten", fontsize=12)
    # x > 200 is outside the page width after a 90° rotation,
    # but still inside the original unrotated coordinate space.
    page.insert_text((260, 100), secret, fontsize=12)
    page.set_rotation(90)
    document.save(source)
    document.close()

    extracted = DocumentExtractor(ocr_backend=ForbiddenOcr()).extract_path(source)
    start = extracted.text.index(secret)
    boxes = extracted.boxes_for_span(start, start + len(secret))

    assert extracted.text_layer_pages == (0,)
    assert extracted.ocr_used is False
    assert len(boxes) == 2
    with fitz.open(source) as original:
        rotated_page = original[0]
        assert rotated_page.rotation == 90
        rotated_rect = rotated_page.rect
        unrotated_rect = fitz.Rect(rotated_rect) * rotated_page.derotation_matrix
        assert any(box.x0 > rotated_rect.width for box in boxes)
        assert all(
            unrotated_rect.x0 <= box.x0 < box.x1 <= unrotated_rect.x1
            and unrotated_rect.y0 <= box.y0 < box.y1 <= unrotated_rect.y1
            for box in boxes
        )

    redaction_rect = fitz.Rect(
        boxes[0].x0,
        boxes[0].y0,
        boxes[0].x1,
        boxes[0].y1,
    )
    for box in boxes[1:]:
        redaction_rect.include_rect(fitz.Rect(box.x0, box.y0, box.x1, box.y1))

    export = export_redacted_pdf(
        source,
        destination,
        (PdfRedaction(0, tuple(redaction_rect), secret),),
    )

    assert export.verification.rendered_pages == 1
    assert export.verification.text_extraction_checked is True
    with fitz.open(destination) as redacted:
        assert redacted[0].rotation == 90
        remaining_text = redacted[0].get_text("text")
        assert secret not in remaining_text
        assert "Behalten" in remaining_text


def test_pdf_auto_uses_ocr_only_for_pages_without_textlayer() -> None:
    document = fitz.open()
    first = document.new_page(width=200, height=100)
    first.insert_text((20, 40), "Textlayer")
    document.new_page(width=200, height=100)
    payload = document.tobytes()
    document.close()
    ocr = RecordingOcr()

    result = DocumentExtractor(ocr_backend=ocr, pdf_ocr_dpi=72).extract_bytes(
        payload,
        filename="gemischt.pdf",
        ocr="auto",
    )

    assert result.text == "Textlayer\fMax Mustermann"
    assert result.method is ExtractionMethod.MIXED_PDF
    assert result.text_layer_pages == (0,)
    assert result.ocr_pages == (1,)
    assert result.ocr_used is True
    assert len(ocr.calls) == 1
    assert ocr.calls[0][1] == ("deu", "eng")
    ocr_segment = next(
        segment for segment in result.segments if segment.kind is SegmentKind.OCR_WORD
    )
    assert ocr_segment.bbox is not None
    assert ocr_segment.bbox.page_index == 1
    assert ocr_segment.bbox.coordinate_space is CoordinateSpace.PDF_POINTS


def test_pdf_auto_ignores_single_page_number_as_unusable_textlayer() -> None:
    document = fitz.open()
    page = document.new_page(width=200, height=100)
    page.insert_text((180, 90), "1")
    payload = document.tobytes()
    document.close()
    ocr = RecordingOcr()

    result = DocumentExtractor(ocr_backend=ocr, pdf_ocr_dpi=72).extract_bytes(
        payload,
        filename="scan-mit-seitenzahl.pdf",
        ocr="auto",
    )

    assert result.text == "Max Mustermann"
    assert result.method is ExtractionMethod.TESSERACT
    assert result.text_layer_pages == (0,)
    assert result.ocr_pages == (0,)
    assert len(ocr.calls) == 1


def test_pdf_auto_checks_large_scan_area_despite_usable_textlayer() -> None:
    raster = Image.new("RGB", (200, 100), "white")
    stream = BytesIO()
    raster.save(stream, format="PNG")
    document = fitz.open()
    page = document.new_page(width=200, height=100)
    page.insert_image(page.rect, stream=stream.getvalue())
    page.insert_text((20, 90), "Document")
    payload = document.tobytes()
    document.close()
    ocr = RecordingOcr()

    result = DocumentExtractor(ocr_backend=ocr, pdf_ocr_dpi=72).extract_bytes(
        payload,
        filename="hybrid.pdf",
        ocr="auto",
    )

    assert result.text == "Max Mustermann"
    assert result.text_layer_pages == (0,)
    assert result.ocr_pages == (0,)
    assert len(ocr.calls) == 1


def _pdf_with_full_page_raster(ink_height: int, *, lines: int) -> bytes:
    """Page with a full-page image and a text layer consisting of ``lines`` lines."""

    raster = Image.new("RGB", (400, 400), "white")
    if ink_height:
        ImageDraw.Draw(raster).rectangle((0, 0, 399, ink_height - 1), fill="black")
    stream = BytesIO()
    raster.save(stream, format="PNG")
    document = fitz.open()
    page = document.new_page(width=400, height=400)
    page.insert_image(page.rect, stream=stream.getvalue())
    for index in range(lines):
        page.insert_text((20, 120 + index * 14), f"Rechnungszeile {index} mit Betrag")
    payload = document.tobytes()
    document.close()
    return payload


def test_pdf_auto_does_not_treat_blank_letterhead_as_scan_area() -> None:
    # Invoices often place logo and footer as a full-page, otherwise empty image.
    # Their text layer must not be replaced by OCR – and should not fail when Tesseract
    # is unavailable.
    payload = _pdf_with_full_page_raster(20, lines=8)

    result = DocumentExtractor(ocr_backend=ForbiddenOcr(), pdf_ocr_dpi=72).extract_bytes(
        payload,
        filename="briefpapier.pdf",
        ocr="auto",
    )

    assert result.method is ExtractionMethod.PYMUPDF
    assert result.ocr_used is False
    assert result.text_layer_pages == (0,)
    assert "Rechnungszeile 0 mit Betrag" in result.text


def test_pdf_auto_still_detects_printed_full_page_raster_as_scan() -> None:
    payload = _pdf_with_full_page_raster(200, lines=8)
    ocr = RecordingOcr()

    result = DocumentExtractor(ocr_backend=ocr, pdf_ocr_dpi=72).extract_bytes(
        payload,
        filename="scanflaeche.pdf",
        ocr="auto",
    )

    assert result.text == "Max Mustermann"
    assert result.ocr_pages == (0,)
    assert len(ocr.calls) == 1


def test_pdf_auto_falls_back_to_textlayer_without_ocr() -> None:
    payload = _pdf_with_full_page_raster(200, lines=8)

    result = DocumentExtractor(ocr_backend=UnavailableOcr(), pdf_ocr_dpi=72).extract_bytes(
        payload,
        filename="scanflaeche.pdf",
        ocr="auto",
    )

    assert result.method is ExtractionMethod.PYMUPDF
    assert result.ocr_used is False
    assert "Rechnungszeile 0 mit Betrag" in result.text


def test_pdf_always_reports_missing_ocr_as_error() -> None:
    payload = _pdf_with_full_page_raster(200, lines=8)

    with pytest.raises(OcrUnavailableError):
        DocumentExtractor(ocr_backend=UnavailableOcr(), pdf_ocr_dpi=72).extract_bytes(
            payload,
            filename="scanflaeche.pdf",
            ocr="always",
        )


def test_pdf_auto_reports_missing_ocr_without_usable_text_layer() -> None:
    document = fitz.open()
    page = document.new_page(width=200, height=100)
    page.insert_text((180, 90), "1")
    payload = document.tobytes()
    document.close()

    with pytest.raises(OcrUnavailableError):
        DocumentExtractor(ocr_backend=UnavailableOcr(), pdf_ocr_dpi=72).extract_bytes(
            payload,
            filename="scan-mit-seitenzahl.pdf",
            ocr="auto",
        )


def test_image_pdf_ocr_boxes_recalculated_to_pdf_points() -> None:
    document = fitz.open()
    document.new_page(width=200, height=100)
    payload = document.tobytes()
    document.close()

    result = DocumentExtractor(ocr_backend=RecordingOcr(), pdf_ocr_dpi=72).extract_bytes(
        payload,
        filename="scan.pdf",
    )

    assert result.method is ExtractionMethod.TESSERACT
    assert result.text == "Max Mustermann"
    assert result.has_text_layer is False
    max_box = result.boxes_for_span(0, 3)[0]
    assert max_box.coordinate_space is CoordinateSpace.PDF_POINTS
    assert max_box.x0 == pytest.approx(10)
    assert max_box.y0 == pytest.approx(20)
    assert max_box.width == pytest.approx(30)


@pytest.mark.parametrize(
    ("filename", "pillow_format", "expected_format"),
    [
        ("scan.png", "PNG", DocumentFormat.PNG),
        ("scan.jpg", "JPEG", DocumentFormat.JPEG),
    ],
)
def test_png_and_jpg_provide_ocr_pixel_boxes(
    filename: str,
    pillow_format: str,
    expected_format: DocumentFormat,
) -> None:
    image = Image.new("RGB", (160, 80), "white")
    stream = BytesIO()
    image.save(stream, format=pillow_format)
    ocr = RecordingOcr()

    result = DocumentExtractor(ocr_backend=ocr).extract_bytes(
        stream.getvalue(),
        filename=filename,
    )

    assert result.format is expected_format
    assert result.text == "Max Mustermann"
    assert result.ocr_used is True
    assert result.ocr_pages == (0,)
    box = result.segments[0].bbox
    assert box is not None
    assert box.coordinate_space is CoordinateSpace.PIXELS
    assert (box.x0, box.y0, box.width, box.height) == (10, 20, 30, 10)
    assert ocr.calls == [((160, 80), ("deu", "eng"))]


def test_jpeg_ocr_normalizes_exif_orientation_like_image_export() -> None:
    image = Image.new("RGB", (40, 20), "white")
    exif = image.getexif()
    exif[274] = 6
    stream = BytesIO()
    image.save(stream, format="JPEG", exif=exif)
    ocr = RecordingOcr()

    DocumentExtractor(ocr_backend=ocr).extract_bytes(
        stream.getvalue(),
        filename="gedreht.jpg",
    )

    assert ocr.calls[0][0] == (20, 40)


def test_docx_extracts_paragraphs_and_tables_in_document_order() -> None:
    document = Document()
    document.add_paragraph("Vor der Tabelle")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Max"
    table.cell(0, 0).add_paragraph("Mustermann")
    table.cell(0, 1).text = "Wien"
    document.add_paragraph("Nach der Tabelle")
    stream = BytesIO()
    document.save(stream)

    result = extract_bytes(stream.getvalue(), filename="brief.docx")

    assert result.method is ExtractionMethod.PYTHON_DOCX
    assert result.text == "Vor der Tabelle\nMax\nMustermann\tWien\nNach der Tabelle"
    assert [segment.kind for segment in result.segments] == [
        SegmentKind.DOCX_PARAGRAPH,
        SegmentKind.DOCX_TABLE_CELL,
        SegmentKind.DOCX_TABLE_CELL,
        SegmentKind.DOCX_TABLE_CELL,
        SegmentKind.DOCX_PARAGRAPH,
    ]
    assert [segment.source_ref for segment in result.segments] == [
        "body/p:0",
        "body/t:0/r:0/c:0/p:0",
        "body/t:0/r:0/c:0/p:1",
        "body/t:0/r:0/c:1/p:0",
        "body/p:1",
    ]
    assert result.text[result.segments[2].start : result.segments[2].end] == "Mustermann"


def test_docx_extracts_header_footer_and_hyperlink_text() -> None:
    document = Document()
    document.sections[0].header.paragraphs[0].text = "Hans Meier"
    document.sections[0].footer.paragraphs[0].text = "Petra Novak"
    paragraph = document.add_paragraph("Kontakt: ")
    relationship_id = paragraph.part.relate_to(
        "mailto:hans@example.com",
        RELATIONSHIP_TYPE.HYPERLINK,
        is_external=True,
    )
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), relationship_id)
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "hans@example.com"
    run.append(text)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)
    stream = BytesIO()
    document.save(stream)

    result = extract_bytes(stream.getvalue(), filename="brief.docx")

    assert "Kontakt: hans@example.com" in result.text
    assert "Hans Meier" in result.text
    assert "Petra Novak" in result.text
    assert any(
        segment.source_ref.startswith("part:word/header")
        and result.text[segment.start : segment.end] == "Hans Meier"
        for segment in result.segments
    )


def test_docx_with_comments_rejected_as_fail_closed() -> None:
    document = Document()
    paragraph = document.add_paragraph("Hans Meier")
    document.add_comment(
        paragraph.runs[0],
        text="Vertraulicher Kommentar",
        author="Petra Novak",
    )
    stream = BytesIO()
    document.save(stream)

    with pytest.raises(ExtractionError, match="Kommentare"):
        extract_bytes(stream.getvalue(), filename="kommentiert.docx")


_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NOTE_RELATIONSHIP = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/{tag}s"
_NOTE_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.{tag}s+xml"


def _docx_with_note_parts(*, real_note: bool = False, ref: bool = False) -> bytes:
    """DOCX with footnotes.xml and endnotes.xml as produced by Word: includes separator lines,
    optionally a real footnote and a reference in the text."""

    document = Document()
    paragraph = document.add_paragraph("Hans Meier")
    if ref:
        run = OxmlElement("w:r")
        reference = OxmlElement("w:footnoteReference")
        reference.set(qn("w:id"), "1")
        run.append(reference)
        paragraph._p.append(run)  # noqa: SLF001
    stream = BytesIO()
    document.save(stream)

    def note_part(tag: str) -> str:
        notes_xml = (
            f'<w:{tag} w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:{tag}>'
            f'<w:{tag} w:type="continuationSeparator" w:id="0"><w:p><w:r>'
            f"<w:continuationSeparator/></w:r></w:p></w:{tag}>"
        )
        if real_note and tag == "footnote":
            notes_xml += f'<w:{tag} w:id="1"><w:p><w:r><w:t>Petra Novak</w:t></w:r></w:p></w:{tag}>'
        return (
            f'<?xml version="1.0" encoding="UTF-8"?><w:{tag}s xmlns:w="{_W}">{notes_xml}</w:{tag}s>'
        )

    zip_in = zipfile.ZipFile(BytesIO(stream.getvalue()))
    target = BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zip_out:
        for entry in zip_in.infolist():
            content = zip_in.read(entry.filename)
            if entry.filename == "[Content_Types].xml":
                overrides = "".join(
                    f'<Override PartName="/word/{tag}s.xml" '
                    f'ContentType="{_NOTE_CONTENT_TYPE.format(tag=tag)}"/>'
                    for tag in ("footnote", "endnote")
                )
                content = content.replace(b"</Types>", overrides.encode() + b"</Types>")
            if entry.filename == "word/_rels/document.xml.rels":
                relationships = "".join(
                    f'<Relationship Id="rIdNote{tag}" '
                    f'Type="{_NOTE_RELATIONSHIP.format(tag=tag)}" Target="{tag}s.xml"/>'
                    for tag in ("footnote", "endnote")
                )
                content = content.replace(
                    b"</Relationships>", relationships.encode() + b"</Relationships>"
                )
            zip_out.writestr(entry, content)
        for tag in ("footnote", "endnote"):
            zip_out.writestr(f"word/{tag}s.xml", note_part(tag))
    return target.getvalue()


def test_docx_with_empty_note_parts_from_word_read_and_saved(
    tmp_path: Path,
) -> None:
    # Word creates footnotes.xml and endnotes.xml with separators even when there are no notes;
    # such files could not be imported before.
    payload = _docx_with_note_parts()

    extracted = extract_bytes(payload, filename="guide.docx")

    assert extracted.text == "Hans Meier"
    source = tmp_path / "guide.docx"
    source.write_bytes(payload)
    export_docx(
        source,
        tmp_path / "guide_geschuetzt.docx",
        [DocxReplacement("body/p:0", 0, 4, "<<PERSON_1>>", "Hans")],
    )
    assert Document(tmp_path / "guide_geschuetzt.docx").paragraphs[0].text == "<<PERSON_1>> Meier"


@pytest.mark.parametrize("variant", ["note", "verweis"])
def test_docx_with_real_footnotes_remains_fail_closed(tmp_path: Path, variant: str) -> None:
    payload = _docx_with_note_parts(real_note=variant == "note", ref=variant == "verweis")
    with pytest.raises(ExtractionError, match="Fussnoten"):
        extract_bytes(payload, filename="noten.docx")
    source = tmp_path / "noten.docx"
    source.write_bytes(payload)
    with pytest.raises(ExportValidationError, match="Fussnoten"):
        export_docx(
            source,
            tmp_path / "noten_geschuetzt.docx",
            [DocxReplacement("body/p:0", 0, 4, "<<PERSON_1>>", "Hans")],
        )


def test_docx_with_track_changes_rejected_as_fail_closed() -> None:
    document = Document()
    paragraph = document.add_paragraph("Petra Novak")
    insertion = OxmlElement("w:ins")
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "Hans Meier"
    run.append(text)
    insertion.append(run)
    paragraph._p.insert(0, insertion)
    stream = BytesIO()
    document.save(stream)

    with pytest.raises(ExtractionError, match="Aenderungsverfolgung"):
        extract_bytes(stream.getvalue(), filename="revision.docx")


def test_xlsx_extracts_sheet_names_cell_values_and_formulas_keeping_offsets() -> None:
    workbook = Workbook()
    first = workbook.active
    first.title = "Kunden"
    first["A1"] = "Max Mustermann"
    first["C1"] = "Wien"
    first["A2"] = '=CONCAT("M","ax")'
    second = workbook.create_sheet("Kontakt")
    second["B2"] = "max@example.at"
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()

    result = extract_bytes(stream.getvalue(), filename="kunden.xlsx")

    assert result.method is ExtractionMethod.OPENPYXL
    assert result.text == (
        'Kunden\nMax Mustermann\t\tWien\n=CONCAT("M","ax")\fKontakt\nmax@example.at'
    )
    assert [segment.source_ref for segment in result.segments] == [
        "sheet:0",
        "Kunden!A1",
        "Kunden!C1",
        "Kunden!A2",
        "sheet:1",
        "Kontakt!B2",
    ]
    assert [segment.kind for segment in result.segments] == [
        SegmentKind.XLSX_SHEET_TITLE,
        SegmentKind.XLSX_CELL,
        SegmentKind.XLSX_CELL,
        SegmentKind.XLSX_FORMULA,
        SegmentKind.XLSX_SHEET_TITLE,
        SegmentKind.XLSX_CELL,
    ]
    email = result.segments[-1]
    assert result.text[email.start : email.end] == "max@example.at"


@pytest.mark.parametrize("filename", ["brief.docx", "kunden.xlsx"])
def test_office_zip_limit_checks_unpacked_sum_before_loading(
    filename: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = BytesIO()
    if filename.endswith(".docx"):
        document = Document()
        document.add_paragraph("Max Mustermann")
        document.save(stream)

        import docx

        monkeypatch.setattr(
            docx,
            "Document",
            lambda *_args, **_kwargs: pytest.fail(
                "python-docx darf nach ueberschrittenem ZIP-Limit nicht laden"
            ),
        )
    else:
        workbook = Workbook()
        workbook.active["A1"] = "Max Mustermann"
        workbook.save(stream)
        workbook.close()

        import openpyxl

        monkeypatch.setattr(
            openpyxl,
            "load_workbook",
            lambda *_args, **_kwargs: pytest.fail(
                "openpyxl darf nach ueberschrittenem ZIP-Limit nicht laden"
            ),
        )

    payload = stream.getvalue()
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        uncompressed_size = sum(member.file_size for member in archive.infolist())
    assert len(payload) < uncompressed_size
    extractor = DocumentExtractor(
        max_office_uncompressed_bytes=uncompressed_size - 1,
    )

    with pytest.raises(ExtractionError, match="entpackt zu gross"):
        extractor.extract_bytes(payload, filename=filename)


def test_tesseract_backend_uses_deu_and_eng_and_parses_boxes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pytesseract

    received: dict[str, object] = {}

    def fake_image_to_data(  # noqa: ANN202
        image,  # noqa: ANN001
        *,
        lang,
        output_type,
        timeout,
    ):
        received.update(
            image=image,
            lang=lang,
            output_type=output_type,
            timeout=timeout,
        )
        return {
            "text": ["", "Max"],
            "left": [0, 12],
            "top": [0, 24],
            "width": [0, 30],
            "height": [0, 11],
            "conf": ["-1", "97.5"],
            "page_num": [1, 1],
            "block_num": [0, 1],
            "par_num": [0, 1],
            "line_num": [0, 1],
        }

    monkeypatch.setattr(pytesseract, "image_to_data", fake_image_to_data)
    marker = object()

    words = TesseractOcrBackend().recognize(marker, languages=("deu", "eng"))

    assert received["image"] is marker
    assert received["lang"] == "deu+eng"
    assert received["output_type"] == pytesseract.Output.DICT
    assert received["timeout"] == 120.0
    assert words == (
        OcrWord(
            text="Max",
            left=12,
            top=24,
            width=30,
            height=11,
            line_id=(1, 1, 1, 1),
            confidence=0.975,
        ),
    )


def test_image_with_disabled_ocr_rejected_as_fail_closed() -> None:
    image = Image.new("RGB", (10, 10), "white")
    stream = BytesIO()
    image.save(stream, format="PNG")

    with pytest.raises(EmptyDocumentError, match="OCR"):
        extract_bytes(stream.getvalue(), filename="scan.png", ocr="never")


def test_invalid_extension_and_ocr_language_rejected() -> None:
    with pytest.raises(UnsupportedDocumentError):
        extract_bytes(b"Inhalt", filename="dokument.exe")
    with pytest.raises(ExtractionError, match="Sprachcode"):
        extract_bytes(b"Inhalt", filename="dokument.txt", ocr_languages=("deu+eng",))


def _pdf_with_text(text: str) -> bytes:
    document = fitz.open()
    page = document.new_page(width=300, height=100)
    page.insert_text((20, 40), text)
    payload = document.tobytes()
    document.close()
    return payload
