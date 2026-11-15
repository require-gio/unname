"""Security and format-faithful tests for phase-3 exports."""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import fitz
import pytest
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableStyleInfo
from PIL import Image, ImageDraw

from presidio_sidecar.document_workflow import _pdf_font_size
from presidio_sidecar.exporting import (
    DocxReplacement,
    ExportValidationError,
    ImageRedaction,
    PdfRedaction,
    PdfRetainedText,
    PdfVerificationError,
    XlsxReplacement,
    XlsxSheetReplacement,
    _contains_sensitive_fragment,
    export_docx,
    export_plain_text,
    export_redacted_image,
    export_redacted_pdf,
    export_xlsx,
    pdf_replacement_fits,
    read_docx_text_units,
    sha256_file,
    verify_redacted_pdf,
)
from presidio_sidecar.extraction import OcrWord


def _create_pdf(path: Path, lines: tuple[str, ...]) -> None:
    document = fitz.open()
    page = document.new_page()
    for index, line in enumerate(lines):
        page.insert_text((72, 72 + index * 30), line, fontsize=12)
    document.save(path)
    document.close()


def test_pdf_is_actually_redacted_rendered_and_checked_for_remaining_text(
    tmp_path: Path,
) -> None:
    source = tmp_path / "original.pdf"
    destination = tmp_path / "export.pdf"
    secret = "Hans Meier"
    _create_pdf(source, (f"Rechnung fuer {secret}", "Betrag 100 EUR"))
    source_before = source.read_bytes()
    with fitz.open(source) as document:
        rectangles = document[0].search_for(secret)
    assert len(rectangles) == 1

    result = export_redacted_pdf(
        source,
        destination,
        (
            PdfRedaction(
                page_index=0,
                rect=tuple(rectangles[0]),
                removed_text=secret,
            ),
        ),
    )

    assert source.read_bytes() == source_before
    assert result.path == destination
    assert result.sha256 == sha256_file(destination)
    assert result.verification.rendered_pages == 1
    assert result.verification.text_extraction_checked is True
    with fitz.open(destination) as exported:
        extracted = "".join(page.get_text("text") for page in exported)
        assert secret not in extracted
        assert "Betrag 100 EUR" in extracted
        assert exported[0].get_pixmap().samples


class ResidualScanOcr:
    def recognize(
        self,
        image: object,
        *,
        languages: tuple[str, ...],
    ) -> tuple[OcrWord, ...]:
        del image
        assert languages == ("deu", "eng")
        return (
            OcrWord("Hans", 20, 20, 30, 12, (1, 1, 1, 1), 0.99),
            OcrWord("Meier", 54, 20, 32, 12, (1, 1, 1, 1), 0.99),
        )


class DuplicateScanOcr:
    """Detects the two test spots only while their pixels are not flat."""

    def recognize(
        self,
        image: object,
        *,
        languages: tuple[str, ...],
    ) -> tuple[OcrWord, ...]:
        assert isinstance(image, Image.Image)
        assert languages == ("deu", "eng")
        words: list[OcrWord] = []
        for left in (20, 160):
            extrema = image.convert("L").crop((left, 20, left + 72, 34)).getextrema()
            if extrema[0] == extrema[1]:
                continue
            words.extend(
                (
                    OcrWord("Hans", left, 20, 26, 14, (1, 1, 1, left), 0.99),
                    OcrWord("Meier", left + 30, 20, 32, 14, (1, 1, 1, left), 0.99),
                )
            )
        return tuple(words)


class SecretOnSecondPageOcr:
    def __init__(self) -> None:
        self.calls = 0

    def recognize(
        self,
        image: object,
        *,
        languages: tuple[str, ...],
    ) -> tuple[OcrWord, ...]:
        del image
        assert languages == ("deu", "eng")
        self.calls += 1
        if self.calls == 1:
            return ()
        return (
            OcrWord("Hans", 20, 20, 30, 12, (1, 1, 1, 1), 0.99),
            OcrWord("Meier", 54, 20, 32, 12, (1, 1, 1, 1), 0.99),
        )


def _create_two_page_scan(path: Path) -> None:
    raster = Image.new("RGB", (300, 100), "white")
    ImageDraw.Draw(raster).text((20, 20), "Hans Meier", fill="black")
    stream = BytesIO()
    raster.save(stream, format="PNG")
    document = fitz.open()
    for _index in range(2):
        page = document.new_page(width=300, height=100)
        page.insert_image(page.rect, stream=stream.getvalue())
    document.save(path)
    document.close()


def test_scan_pdf_export_blocks_when_ocr_text_remains_visible(
    tmp_path: Path,
) -> None:
    source = tmp_path / "scan.pdf"
    destination = tmp_path / "scan-export.pdf"
    raster = Image.new("RGB", (200, 100), "white")
    ImageDraw.Draw(raster).text((20, 20), "Hans Meier", fill="black")
    stream = BytesIO()
    raster.save(stream, format="PNG")
    document = fitz.open()
    page = document.new_page(width=200, height=100)
    page.insert_image(page.rect, stream=stream.getvalue())
    document.save(source)
    document.close()

    with pytest.raises(PdfVerificationError, match="OCR-Text"):
        export_redacted_pdf(
            source,
            destination,
            (
                PdfRedaction(
                    page_index=0,
                    rect=(150, 70, 190, 90),
                    removed_text="Hans Meier",
                    verify_source_text=False,
                ),
            ),
            ocr_backend=ResidualScanOcr(),
            ocr_dpi=150,
        )

    assert not destination.exists()


def test_scan_pdf_checks_deliberately_kept_duplicate_only_in_target_box(
    tmp_path: Path,
) -> None:
    source = tmp_path / "scan-duplicate.pdf"
    destination = tmp_path / "scan-duplicate-export.pdf"
    raster = Image.new("RGB", (300, 100), "white")
    drawing = ImageDraw.Draw(raster)
    drawing.text((20, 20), "Hans Meier", fill="black")
    drawing.text((160, 20), "Hans Meier", fill="black")
    stream = BytesIO()
    raster.save(stream, format="PNG")
    document = fitz.open()
    page = document.new_page(width=300, height=100)
    page.insert_image(page.rect, stream=stream.getvalue())
    document.save(source)
    document.close()

    result = export_redacted_pdf(
        source,
        destination,
        (
            PdfRedaction(
                page_index=0,
                rect=(15, 15, 100, 40),
                removed_text="Hans Meier",
                verify_source_text=False,
            ),
        ),
        retained_text=(
            PdfRetainedText(
                page_index=0,
                rect=(155, 15, 250, 40),
                text="Hans Meier",
                verify_source_text=False,
            ),
        ),
        ocr_backend=DuplicateScanOcr(),
        ocr_dpi=72,
    )

    assert result.verification.verified_targets == 1
    assert destination.is_file()


def test_scan_pdf_checks_other_pages_for_unknown_duplicates(
    tmp_path: Path,
) -> None:
    source = tmp_path / "zwei-scan-seiten.pdf"
    destination = tmp_path / "unsicher.pdf"
    _create_two_page_scan(source)

    with pytest.raises(PdfVerificationError, match="OCR-Text"):
        export_redacted_pdf(
            source,
            destination,
            (
                PdfRedaction(
                    page_index=0,
                    rect=(15, 15, 100, 40),
                    removed_text="Hans Meier",
                    verify_source_text=False,
                ),
            ),
            ocr_backend=DuplicateScanOcr(),
            ocr_dpi=72,
        )

    assert not destination.exists()


def test_scan_pdf_allows_positioned_duplicate_on_other_page(
    tmp_path: Path,
) -> None:
    source = tmp_path / "zwei-scan-seiten.pdf"
    destination = tmp_path / "export.pdf"
    _create_two_page_scan(source)

    result = export_redacted_pdf(
        source,
        destination,
        (
            PdfRedaction(
                page_index=0,
                rect=(15, 15, 100, 40),
                removed_text="Hans Meier",
                verify_source_text=False,
            ),
        ),
        retained_text=(
            PdfRetainedText(
                page_index=1,
                rect=(15, 15, 100, 40),
                text="Hans Meier",
                verify_source_text=False,
            ),
        ),
        ocr_backend=DuplicateScanOcr(),
        ocr_dpi=72,
    )

    assert result.verification.rendered_pages == 2
    assert destination.is_file()


def test_mixed_pdf_checks_scan_page_even_with_text_layer_redaction(
    tmp_path: Path,
) -> None:
    source = tmp_path / "mixed.pdf"
    destination = tmp_path / "unsicher.pdf"
    raster = Image.new("RGB", (300, 100), "white")
    ImageDraw.Draw(raster).text((20, 20), "Hans Meier", fill="black")
    stream = BytesIO()
    raster.save(stream, format="PNG")
    document = fitz.open()
    text_page = document.new_page(width=300, height=100)
    text_page.insert_text((20, 32), "Hans Meier", fontsize=12)
    scan_page = document.new_page(width=300, height=100)
    scan_page.insert_image(scan_page.rect, stream=stream.getvalue())
    document.save(source)
    document.close()
    with fitz.open(source) as opened:
        rectangle = tuple(opened[0].search_for("Hans Meier")[0])
    ocr = SecretOnSecondPageOcr()

    with pytest.raises(PdfVerificationError, match="OCR-Text"):
        export_redacted_pdf(
            source,
            destination,
            (PdfRedaction(0, rectangle, "Hans Meier"),),
            ocr_backend=ocr,
            ocr_dpi=72,
        )

    assert ocr.calls == 2
    assert not destination.exists()


def test_pdf_export_blocks_when_same_plaintext_is_still_extractable(
    tmp_path: Path,
) -> None:
    source = tmp_path / "original.pdf"
    destination = tmp_path / "unsicher.pdf"
    secret = "Hans Meier"
    _create_pdf(source, (secret, secret))
    source_hash = sha256_file(source)
    with fitz.open(source) as document:
        rectangles = document[0].search_for(secret)
    assert len(rectangles) == 2

    with pytest.raises(PdfVerificationError, match="weiterhin extrahierbar"):
        export_redacted_pdf(
            source,
            destination,
            (
                PdfRedaction(
                    page_index=0,
                    rect=tuple(rectangles[0]),
                    removed_text=secret,
                ),
            ),
        )

    assert not destination.exists()
    assert sha256_file(source) == source_hash


def test_pdf_export_allows_deliberately_kept_identical_occurrence(
    tmp_path: Path,
) -> None:
    source = tmp_path / "original.pdf"
    destination = tmp_path / "export.pdf"
    secret = "Hans Meier"
    _create_pdf(source, (secret, secret))
    with fitz.open(source) as document:
        rectangles = document[0].search_for(secret)
    assert len(rectangles) == 2

    result = export_redacted_pdf(
        source,
        destination,
        (
            PdfRedaction(
                page_index=0,
                rect=tuple(rectangles[0]),
                removed_text=secret,
            ),
        ),
        # The workflow only makes this exception for a detected finding that was deliberately not
        # applied in the current review state.
        retained_text=(PdfRetainedText(0, tuple(rectangles[1]), secret),),
    )

    assert result.verification.verified_targets == 1
    with fitz.open(destination) as exported:
        assert len(exported[0].search_for(secret)) == 1


def test_pdf_export_blocks_unknown_third_identical_occurrence(
    tmp_path: Path,
) -> None:
    source = tmp_path / "drei-namen.pdf"
    destination = tmp_path / "unsicher.pdf"
    secret = "Hans Meier"
    _create_pdf(source, (secret, secret, secret))
    with fitz.open(source) as document:
        rectangles = document[0].search_for(secret)
    assert len(rectangles) == 3

    with pytest.raises(PdfVerificationError, match="weiterhin extrahierbar"):
        export_redacted_pdf(
            source,
            destination,
            (PdfRedaction(0, tuple(rectangles[0]), secret),),
            retained_text=(PdfRetainedText(0, tuple(rectangles[1]), secret),),
        )

    assert not destination.exists()


def test_pdf_export_blocks_oversized_kept_region(
    tmp_path: Path,
) -> None:
    source = tmp_path / "drei-namen.pdf"
    destination = tmp_path / "unsicher.pdf"
    secret = "Hans Meier"
    _create_pdf(source, (secret, secret, secret))
    with fitz.open(source) as document:
        rectangle = tuple(document[0].search_for(secret)[0])
        whole_page = tuple(document[0].rect)

    with pytest.raises(PdfVerificationError, match="mehr sensiblen Text"):
        export_redacted_pdf(
            source,
            destination,
            (PdfRedaction(0, rectangle, secret),),
            retained_text=(PdfRetainedText(0, whole_page, secret),),
        )

    assert not destination.exists()


def test_pdf_multiline_finding_blocks_no_legitimate_first_name(
    tmp_path: Path,
) -> None:
    source = tmp_path / "mehrzeilig.pdf"
    destination = tmp_path / "export.pdf"
    document = fitz.open()
    page = document.new_page(width=300, height=200)
    page.insert_text((30, 40), "Hans", fontsize=12)
    page.insert_text((30, 65), "Meier", fontsize=12)
    page.insert_text((30, 100), "Hans GmbH", fontsize=12)
    document.save(source)
    document.close()
    with fitz.open(source) as opened:
        hans = tuple(opened[0].search_for("Hans")[0])
        meier = tuple(opened[0].search_for("Meier")[0])

    result = export_redacted_pdf(
        source,
        destination,
        (
            PdfRedaction(0, hans, "Hans", finding_text="Hans\nMeier"),
            PdfRedaction(0, meier, "Meier", finding_text="Hans\nMeier"),
        ),
    )

    assert result.verification.verified_targets == 2
    with fitz.open(destination) as exported:
        assert "Hans GmbH" in exported[0].get_text("text")


def test_pdf_plaintext_exception_requires_positioned_redaction_check(
    tmp_path: Path,
) -> None:
    source = tmp_path / "unredigiert.pdf"
    secret = "Hans Meier"
    _create_pdf(source, (secret,))
    with fitz.open(source) as document:
        rectangle = tuple(document[0].search_for(secret)[0])

    with pytest.raises(ExportValidationError, match="Redaktionsboxen"):
        verify_redacted_pdf(
            source,
            (secret,),
            retained_text=(PdfRetainedText(0, rectangle, secret),),
        )


def test_pdf_plaintext_exception_never_skips_local_box_check(
    tmp_path: Path,
) -> None:
    source = tmp_path / "unredigiert.pdf"
    secret = "Hans Meier"
    _create_pdf(source, (secret, secret))
    with fitz.open(source) as document:
        rectangles = tuple(document[0].search_for(secret))

    with pytest.raises(PdfVerificationError, match="Redaktionsbox"):
        verify_redacted_pdf(
            source,
            (secret,),
            positioned_redactions=(PdfRedaction(0, tuple(rectangles[0]), secret),),
            retained_text=(PdfRetainedText(0, tuple(rectangles[1]), secret),),
        )


def test_pdf_export_blocks_wrong_mapping_before_any_output(tmp_path: Path) -> None:
    source = tmp_path / "original.pdf"
    destination = tmp_path / "export.pdf"
    _create_pdf(source, ("Hans Meier",))
    with fitz.open(source) as document:
        rectangle = tuple(document[0].search_for("Hans Meier")[0])

    with pytest.raises(ExportValidationError, match="erwarteten Text"):
        export_redacted_pdf(
            source,
            destination,
            (PdfRedaction(0, rectangle, "Petra Novak"),),
        )

    assert not destination.exists()


def test_pdf_export_removes_attachments_annotations_and_metadata(
    tmp_path: Path,
) -> None:
    source = tmp_path / "original.pdf"
    destination = tmp_path / "export.pdf"
    secret = "Hans Meier"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), secret)
    page.add_text_annot((40, 40), secret)
    document.embfile_add("original.txt", secret.encode())
    document.set_metadata(
        {
            "title": secret,
            "author": secret,
            "keywords": secret,
        }
    )
    document.save(source)
    document.close()
    with fitz.open(source) as opened:
        rectangle = tuple(opened[0].search_for(secret)[0])

    export_redacted_pdf(
        source,
        destination,
        (PdfRedaction(0, rectangle, secret),),
    )

    with fitz.open(destination) as exported:
        assert exported.embfile_names() == []
        assert exported.get_xml_metadata() == ""
        assert not any(
            exported.metadata.get(field)
            for field in ("title", "author", "subject", "keywords", "creator")
        )
        assert exported[0].first_annot is None
        assert exported[0].first_widget is None
        assert secret not in exported[0].get_text()


def test_pdf_code_not_fitting_in_box_uses_free_space_of_line(
    tmp_path: Path,
) -> None:
    """Previously this produced an implicit black bar."""

    source = tmp_path / "original.pdf"
    destination = tmp_path / "export.pdf"
    replacement = "<<PHONE_NUMBER_0123abcd>>"
    _create_pdf(source, ("Tel: 0664 1234", "Betrag 100 EUR"))
    with fitz.open(source) as opened:
        rectangle = tuple(opened[0].search_for("0664 1234")[0])

    result = export_redacted_pdf(
        source,
        destination,
        (PdfRedaction(0, rectangle, "0664 1234", replacement=replacement, fill=(1, 1, 1)),),
    )

    assert result.verification.text_extraction_checked
    with fitz.open(destination) as exported:
        text = exported[0].get_text("text")
    assert replacement in text
    assert "0664 1234" not in text
    assert "Tel:" in text and "Betrag 100 EUR" in text


def test_pdf_verification_requires_replacement_text_in_its_box(tmp_path: Path) -> None:
    source = tmp_path / "ohne-ersatz.pdf"
    _create_pdf(source, ("Rechnung", "Betrag 100 EUR"))
    with fitz.open(source) as opened:
        rectangle = tuple(opened[0].search_for("Rechnung")[0])

    with pytest.raises(PdfVerificationError, match="Redaktionsbox"):
        verify_redacted_pdf(
            source,
            ("Hans Meier",),
            required_redactions=(
                PdfRedaction(0, rectangle, "Hans Meier", replacement="<<PERSON_0123abcd>>"),
            ),
        )


def test_pdf_redaction_keeps_tightly_spaced_neighbor_lines(tmp_path: Path) -> None:
    """Regression delivery note: lines edge-to-edge, 8 pt vs 9 pt spacing.

    The full glyph box of the neighboring line extends into the match box;
    MuPDF removed “Auftragsdatum: 15.” and “MUSTERBERGSTRA”.
    """

    source = tmp_path / "eng.pdf"
    destination = tmp_path / "eng-export.pdf"
    document = fitz.open()
    page = document.new_page()
    lines = (
        "ANDREA MUSTERMANN",
        "MUSTERBERGSTRASSE 12/1/30",
        "Tel: +436641234567",
        "Auftragsdatum: 15.07.2026",
    )
    for index, line in enumerate(lines):
        page.insert_text((60, 100 + index * 9), line, fontsize=8)
    document.save(source)
    document.close()
    with fitz.open(source) as opened:
        name = tuple(opened[0].search_for("ANDREA MUSTERMANN")[0])
        phone = tuple(opened[0].search_for("+436641234567")[0])

    export_redacted_pdf(
        source,
        destination,
        (
            PdfRedaction(0, name, "ANDREA MUSTERMANN", replacement="<<PERSON_0123abcd>>"),
            PdfRedaction(0, phone, "+436641234567", replacement="<<PHONE_NUMBER_0123abcd>>"),
        ),
    )

    with fitz.open(destination) as exported:
        text = exported[0].get_text("text")
    assert "MUSTERBERGSTRASSE 12/1/30" in text
    assert "Auftragsdatum: 15.07.2026" in text
    assert "<<PERSON_0123abcd>>" in text and "<<PHONE_NUMBER_0123abcd>>" in text


def test_pdf_verification_reports_count_and_page_without_naming_finding(
    tmp_path: Path,
) -> None:
    source = tmp_path / "original.pdf"
    destination = tmp_path / "unsicher.pdf"
    secret = "Hans Meier"
    _create_pdf(source, (secret, secret))
    with fitz.open(source) as document:
        rectangles = document[0].search_for(secret)

    with pytest.raises(PdfVerificationError) as raised:
        export_redacted_pdf(
            source,
            destination,
            (PdfRedaction(page_index=0, rect=tuple(rectangles[0]), removed_text=secret),),
        )

    message = str(raised.value)
    assert "1 von 1 geprueften Stellen" in message
    assert "Seite 1" in message
    assert secret not in message


def test_pdf_replacement_text_lands_in_first_box_carrying_it(tmp_path: Path) -> None:
    # PyMuPDF only writes replacement text for fonts >= 4 pt; smaller boxes stay empty.
    # The workflow must detect this early instead of letting the export fail at the mandatory check.
    narrow = (72.0, 60.0, 82.0, 63.0)
    wide = (72.0, 90.0, 260.0, 104.0)
    assert pdf_replacement_fits(wide, "[PERSON-1]", _pdf_font_size(wide, "[PERSON-1]"))
    assert not pdf_replacement_fits(narrow, "[PERSON-1]", _pdf_font_size(narrow, "[PERSON-1]"))


def test_pdf_export_survives_too_small_box_for_replacement_text(
    tmp_path: Path,
) -> None:
    source = tmp_path / "original.pdf"
    destination = tmp_path / "export.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 100), "AB", fontsize=4)
    document.save(source)
    document.close()
    with fitz.open(source) as opened:
        rectangle = tuple(opened[0].search_for("AB")[0])

    # Without replacement text the redaction stays testable; with a replacement that does not fit,
    # the whole export would fail.
    assert not pdf_replacement_fits(rectangle, "[PERSON-1]", 8.0)
    result = export_redacted_pdf(
        source,
        destination,
        (PdfRedaction(0, rectangle, "AB"),),
    )

    assert result.path == destination
    with fitz.open(destination) as exported:
        assert "AB" not in exported[0].get_text("text")


@pytest.mark.parametrize(
    ("extracted_text", "fragment", "expected"),
    [
        # The name appears verbatim in the derived address. Without a word boundary the check
        # would report a false positive.
        ("E-Mail: erikamusterfrau97@example.org", "Erika Musterfrau", False),
        ("Kunde: Erika Musterfrau, Wien", "Erika Musterfrau", True),
        # The actual bypass remains detected: the text layer separates differently.
        ("Kunde: Erika\nMusterfrau, Wien", "Erika Musterfrau", True),
        ("Kunde: E r i k a Musterfrau", "Erika Musterfrau", True),
        ("Rechnung XI5866140 / 2026", "XI5866140", True),
        # Intentional asymmetry: the literal substring stays a finding. Only the whitespace-tolerant
        # path adds word boundaries because it first creates a neighborhood that does not exist in
        # the document.
        ("Auftrag 7YV8II26760 offen", "7YV8II", True),
    ],
)
def test_residual_text_check_separates_word_boundaries_from_real_findings(
    extracted_text: str,
    fragment: str,
    expected: bool,
) -> None:
    assert _contains_sensitive_fragment(extracted_text, fragment) is expected


def _add_hyperlink(paragraph: object, text: str, target: str) -> None:
    relationship_id = paragraph.part.relate_to(
        target,
        RELATIONSHIP_TYPE.HYPERLINK,
        is_external=True,
    )
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), relationship_id)
    run = OxmlElement("w:r")
    value = OxmlElement("w:t")
    value.text = text
    run.append(value)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def test_docx_header_and_hyperlink_text_replaced_link_target_removed(
    tmp_path: Path,
) -> None:
    source = tmp_path / "original.docx"
    destination = tmp_path / "export.docx"
    document = Document()
    document.core_properties.title = "Hans Meier"
    document.sections[0].header.paragraphs[0].text = "Hans Meier"
    paragraph = document.add_paragraph("Kontakt: ")
    _add_hyperlink(paragraph, "hans@example.com", "mailto:hans@example.com")
    document.save(source)

    units = read_docx_text_units(source)
    header = next(unit for unit in units if unit.text == "Hans Meier")
    contact = next(unit for unit in units if "hans@example.com" in unit.text)
    export_docx(
        source,
        destination,
        (
            DocxReplacement(
                header.target_id,
                0,
                len(header.text),
                "[Person]",
                expected_text=header.text,
            ),
            DocxReplacement(
                contact.target_id,
                contact.text.index("hans@example.com"),
                len(contact.text),
                "[E-Mail]",
                expected_text="hans@example.com",
            ),
        ),
    )

    exported = Document(destination)
    assert exported.sections[0].header.paragraphs[0].text == "[Person]"
    assert exported.paragraphs[0].text == "Kontakt: [E-Mail]"
    assert exported.paragraphs[0].hyperlinks == []
    assert exported.core_properties.title == ""
    assert not any(
        relationship.reltype == RELATIONSHIP_TYPE.HYPERLINK
        for part in exported.part.package.parts
        for relationship in part.rels.values()
    )


def test_docx_replaces_across_runs_and_keeps_base_layout_and_table(
    tmp_path: Path,
) -> None:
    source = tmp_path / "original.docx"
    destination = tmp_path / "export.docx"
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.add_run("Kunde ")
    first_name = paragraph.add_run("Hans ")
    first_name.bold = True
    paragraph.add_run("Meier")
    paragraph.add_run(" bezahlt.")
    table = document.add_table(rows=2, cols=2)
    table.style = "Table Grid"
    table.cell(0, 0).text = "Feld"
    table.cell(0, 1).text = "Wert"
    table.cell(1, 0).text = "IBAN"
    table.cell(1, 1).text = "DE02120300000000202051"
    document.save(source)
    source_hash = sha256_file(source)

    units = read_docx_text_units(source)
    paragraph_unit = next(unit for unit in units if "Hans Meier" in unit.text)
    table_unit = next(unit for unit in units if unit.text.startswith("DE02"))
    result = export_docx(
        source,
        destination,
        (
            DocxReplacement(
                paragraph_unit.target_id,
                paragraph_unit.text.index("Hans Meier"),
                paragraph_unit.text.index("Hans Meier") + len("Hans Meier"),
                "[Person]",
                expected_text="Hans Meier",
            ),
            DocxReplacement(
                table_unit.target_id,
                0,
                len(table_unit.text),
                "[IBAN]",
                expected_text=table_unit.text,
            ),
        ),
    )

    assert sha256_file(source) == source_hash
    assert result.verification.verified_targets == 2
    exported = Document(destination)
    assert exported.paragraphs[0].text == "Kunde [Person] bezahlt."
    assert exported.paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.CENTER
    # Replacement preserves the formatting of the first affected run.
    assert exported.paragraphs[0].runs[1].bold is True
    assert len(exported.tables) == 1
    assert len(exported.tables[0].rows) == 2
    assert len(exported.tables[0].columns) == 2
    assert exported.tables[0].style.name == "Table Grid"
    assert exported.tables[0].cell(1, 1).text == "[IBAN]"


def test_docx_stale_offsets_write_no_file(tmp_path: Path) -> None:
    source = tmp_path / "original.docx"
    destination = tmp_path / "export.docx"
    document = Document()
    document.add_paragraph("Hans Meier")
    document.save(source)
    target = read_docx_text_units(source)[0].target_id

    with pytest.raises(ExportValidationError, match="erwarteten Text"):
        export_docx(
            source,
            destination,
            (DocxReplacement(target, 0, 4, "[Person]", expected_text="Anna"),),
        )

    assert not destination.exists()


def test_xlsx_replaces_cell_span_and_keeps_style_formula_and_table(
    tmp_path: Path,
) -> None:
    source = tmp_path / "original.xlsx"
    destination = tmp_path / "export.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Kunden"
    sheet.append(("Kennung", "Name", "Laenge"))
    sheet.append(("K-1", "Hans Meier / aktiv", "=LEN(B2)"))
    sheet["B2"].font = Font(name="Arial", bold=True, color="FFFFFF")
    sheet["B2"].fill = PatternFill("solid", fgColor="4472C4")
    sheet.column_dimensions["B"].width = 28
    table = Table(displayName="Kundentabelle", ref="A1:C2")
    table.tableStyleInfo = TableStyleInfo(
        name="TableStyleMedium2",
        showFirstColumn=False,
        showLastColumn=False,
        showRowStripes=True,
        showColumnStripes=False,
    )
    sheet.add_table(table)
    workbook.save(source)
    workbook.close()
    source_hash = sha256_file(source)

    result = export_xlsx(
        source,
        destination,
        (
            XlsxReplacement(
                "Kunden",
                "B2",
                0,
                len("Hans Meier"),
                "[Person]",
                expected_text="Hans Meier",
            ),
        ),
    )

    assert sha256_file(source) == source_hash
    assert result.verification.verified_targets == 1
    exported = load_workbook(destination, data_only=False)
    sheet = exported["Kunden"]
    assert sheet["B2"].value == "[Person] / aktiv"
    assert sheet["B2"].font.bold is True
    assert sheet["B2"].font.name == "Arial"
    assert sheet["B2"].fill.fgColor.rgb == "004472C4"
    assert sheet.column_dimensions["B"].width == 28
    assert sheet["C2"].value == "=LEN(B2)"
    assert "Kundentabelle" in sheet.tables
    assert sheet.tables["Kundentabelle"].ref == "A1:C2"
    exported.close()


def test_xlsx_removes_whole_cell_and_hidden_oo_xml_data(
    tmp_path: Path,
) -> None:
    source = tmp_path / "original.xlsx"
    destination = tmp_path / "export.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Hans Meier"
    sheet["A1"] = "hans@example.com"
    sheet["A1"].hyperlink = "mailto:hans@example.com"
    sheet["A1"].comment = Comment(
        "Hans Meier",
        "Hans Meier",
    )
    sheet.oddHeader.center.text = "Hans Meier"
    workbook.properties.creator = "Hans Meier"
    workbook.save(source)
    workbook.close()

    result = export_xlsx(
        source,
        destination,
        (
            XlsxReplacement(
                "Hans Meier",
                "A1",
                0,
                len("hans@example.com"),
                "",
                expected_text="hans@example.com",
            ),
            XlsxSheetReplacement(
                0,
                0,
                len("Hans Meier"),
                "[Person]",
                expected_text="Hans Meier",
            ),
        ),
    )

    assert result.verification.verified_targets == 2
    exported = load_workbook(destination, data_only=False, keep_links=False)
    sheet = exported.worksheets[0]
    assert sheet.title == "(Person)"
    assert sheet["A1"].value is None
    assert sheet["A1"].hyperlink is None
    assert sheet["A1"].comment is None
    assert sheet.oddHeader.center.text is None
    assert exported.properties.creator in {None, "openpyxl"}
    exported.close()


def test_xlsx_removes_unanalyzed_data_validation_texts(
    tmp_path: Path,
) -> None:
    source = tmp_path / "validation.xlsx"
    destination = tmp_path / "validation-export.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Daten"
    sheet["A1"] = "Petra Novak"
    validation = DataValidation(type="whole")
    validation.promptTitle = "Hans Meier"
    validation.prompt = "hans@example.com"
    validation.errorTitle = "Hans Meier"
    validation.error = "hans@example.com"
    validation.add("A1")
    sheet.add_data_validation(validation)
    workbook.save(source)
    workbook.close()

    export_xlsx(
        source,
        destination,
        (
            XlsxReplacement(
                "Daten",
                "A1",
                0,
                len("Petra Novak"),
                "[Person]",
                expected_text="Petra Novak",
            ),
        ),
    )

    exported = load_workbook(destination, data_only=False, keep_links=False)
    assert exported.active.data_validations.dataValidation == []
    exported.close()
    serialized = destination.read_bytes()
    assert b"Hans Meier" not in serialized
    assert b"hans@example.com" not in serialized


@pytest.mark.parametrize("suffix", [".png", ".jpg"])
def test_image_export_blacks_out_bbox_in_new_pixels(
    tmp_path: Path,
    suffix: str,
) -> None:
    source = tmp_path / f"original{suffix}"
    destination = tmp_path / f"export{suffix}"
    image = Image.new("RGB", (80, 40), (255, 255, 255))
    for x in range(20, 60):
        for y in range(10, 30):
            image.putpixel((x, y), (180, 20, 20))
    image.save(source)
    source_hash = sha256_file(source)

    result = export_redacted_image(
        source,
        destination,
        (ImageRedaction((20, 10, 60, 30)),),
    )

    assert sha256_file(source) == source_hash
    assert result.verification.verified_targets == 1
    with Image.open(destination) as exported:
        extrema = exported.convert("RGB").crop((22, 12, 58, 28)).getextrema()
    assert max(channel[1] for channel in extrema) <= (24 if suffix == ".jpg" else 0)
    with Image.open(source) as original:
        assert original.convert("RGB").getpixel((30, 20))[0] > 100


def test_export_target_always_new_and_original_never_allowed(tmp_path: Path) -> None:
    source = tmp_path / "original.txt"
    source.write_text("Hans Meier", encoding="utf-8")
    destination = tmp_path / "export.txt"
    destination.write_text("bestehend", encoding="utf-8")
    existing_hash = sha256_file(destination)

    with pytest.raises(FileExistsError):
        export_plain_text(source, destination, "[Person]")
    assert sha256_file(destination) == existing_hash

    with pytest.raises(ExportValidationError, match="Originaldatei"):
        export_plain_text(source, source, "[Person]")
    assert source.read_text(encoding="utf-8") == "Hans Meier"


def test_plaintext_export_returns_hash_of_new_file(tmp_path: Path) -> None:
    source = tmp_path / "original.csv"
    destination = tmp_path / "export.csv"
    source.write_text("name\nHans Meier\n", encoding="utf-8")

    result = export_plain_text(source, destination, "name\n[Person]\n")

    assert destination.read_text(encoding="utf-8") == "name\n[Person]\n"
    assert result.sha256 == hashlib.sha256(destination.read_bytes()).hexdigest()
