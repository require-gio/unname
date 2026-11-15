"""Regression: Invoice as PDF – page 1 scanned, page 2 with text layer (October 2026).

The fixture uses a sample invoice: first page is a single scanned image,
second page is a bookkeeping export with a real text layer. Without OCR the file
is rejected with ``ocr_unavailable``. That stays: the text page alone would be
read, the scanned page would remain unprotected. With OCR each page follows its
own path.

All values are fabricated for the test.
"""

from __future__ import annotations

import shutil

import fitz
import pytest

from presidio_sidecar import resources
from presidio_sidecar.extraction import (
    DocumentExtractor,
    ExtractionMethod,
    OcrUnavailableError,
    OcrWord,
    TesseractOcrBackend,
)
from presidio_sidecar.jobs import _extraction_error_code

A4 = (595.2, 841.9)
SCAN_LINES = (
    "Werbetechnik Lindner GmbH",
    "Rechnung Nr. 27",
    "Frau Martina Brettschneider",
    "Gesamtbetrag brutto: 30,00 EUR",
)
TEXT_LAYER_LINES = (
    "Rechnungsnummer",
    "27",
    "Bezeichnung Kostenart",
    "Aussenwerbung - 5010400000",
    "Gesamtbetrag brutto: 30,00 €",
    "Generiert am 08.04.2025 um 09:09 Uhr von Jonas Ebersberger",
)


def _mixed_invoice() -> bytes:
    """Page 1: image without text layer (scan); Page 2: real text."""

    template = fitz.open()
    page = template.new_page(width=A4[0], height=A4[1])
    for index, line in enumerate(SCAN_LINES):
        page.insert_text((60, 90 + 28 * index), line, fontsize=16)
    raster = page.get_pixmap(dpi=150)
    template.close()

    document = fitz.open()
    scan = document.new_page(width=A4[0], height=A4[1])
    scan.insert_image(scan.rect, pixmap=raster)
    text = document.new_page(width=A4[0], height=A4[1])
    for index, line in enumerate(TEXT_LAYER_LINES):
        text.insert_text((60, 90 + 22 * index), line, fontsize=11)
    payload = document.tobytes()
    document.close()
    return payload


class UnavailableOcr:
    def recognize(self, image, *, languages):  # noqa: ANN001, ANN201
        del image, languages
        raise OcrUnavailableError("Tesseract oder die Sprachdaten deu/eng sind nicht verfuegbar.")


class ScanOcr:
    """Read the scanned page exactly as Tesseract would."""

    def __init__(self) -> None:
        self.calls = 0

    def recognize(self, image, *, languages):  # noqa: ANN001, ANN201
        del image, languages
        self.calls += 1
        return tuple(
            OcrWord(
                text=word,
                left=60 + 80 * position,
                top=60 + 40 * line_index,
                width=70,
                height=24,
                line_id=(1, 1, 1, line_index + 1),
                confidence=0.95,
            )
            for line_index, line in enumerate(SCAN_LINES)
            for position, word in enumerate(line.split())
        )


def test_fixture_has_a_pure_scan_page_and_a_text_page() -> None:
    document = fitz.open(stream=_mixed_invoice(), filetype="pdf")
    try:
        assert document[0].get_text().strip() == ""
        assert len(document[0].get_images()) == 1
        assert "Rechnungsnummer" in document[1].get_text()
    finally:
        document.close()


def test_without_ocr_the_whole_file_is_rejected() -> None:
    with pytest.raises(OcrUnavailableError) as raised:
        DocumentExtractor(ocr_backend=UnavailableOcr()).extract_bytes(
            _mixed_invoice(), filename="rechnung.pdf", ocr="auto"
        )
    # This code path triggers the UI message “Texterkennung laden”.
    assert _extraction_error_code(raised.value) == "ocr_unavailable"


def test_with_ocr_each_page_contributes_via_its_own_path() -> None:
    ocr = ScanOcr()
    result = DocumentExtractor(ocr_backend=ocr).extract_bytes(
        _mixed_invoice(), filename="rechnung.pdf", ocr="auto"
    )

    assert ocr.calls == 1, "nur die Scanseite geht durch die Texterkennung"
    assert result.method is ExtractionMethod.MIXED_PDF
    assert result.ocr_pages == (0,)
    assert result.text_layer_pages == (1,)
    scan_text, text_layer = result.text.split("\f")
    assert "Martina Brettschneider" in scan_text
    assert "Jonas Ebersberger" in text_layer


def _tesseract_with_german() -> bool:
    # Mirrors the backend: use bundled Tesseract from ``.bundle-inputs`` or
    # load language data before the system program.
    try:
        if resources.tesseract_bundle() is not None:
            return True
    except resources.BundledAssetError:
        return False
    if shutil.which("tesseract") is None:
        return False
    import pytesseract

    try:
        return {"deu", "eng"} <= set(pytesseract.get_languages(config=""))
    except pytesseract.TesseractError:
        return False


@pytest.mark.skipif(not _tesseract_with_german(), reason="Tesseract mit deu/eng fehlt")
def test_real_tesseract_reads_the_scan_page() -> None:
    result = DocumentExtractor(ocr_backend=TesseractOcrBackend()).extract_bytes(
        _mixed_invoice(), filename="rechnung.pdf", ocr="auto"
    )

    assert result.method is ExtractionMethod.MIXED_PDF
    scan_text = result.text.split("\f")[0]
    assert "Brettschneider" in scan_text
    assert "Rechnung" in scan_text


# -- Save as PDF instead of text -----------------------------------------
#
# The file was saved as ``.txt`` after review: Tesseract read every code on the
# whole page together with scan remnants and confused the value ``0`` with ``O``;
# a single mis-read triggers a fallback to text. It also reported the phone line twice.


class GhostLineOcr:
    """Reports the phone line twice, the second box higher – like Tesseract."""

    def recognize(self, image, *, languages):  # noqa: ANN001, ANN201
        del image, languages
        phone_line = (("Telefon:", 100), ("0761", 180), ("/", 225), ("201-4393", 240))
        real = tuple(
            OcrWord(text, left, 100, 60 if len(text) > 1 else 8, 20, (1, 1, 1, 1), 0.9)
            for text, left in phone_line
        )
        ghost = tuple(
            OcrWord(text, left + 4, 98, 60 if len(text) > 1 else 8, 44, (1, 1, 1, 2), 0.6)
            for text, left in phone_line
        )
        fax = OcrWord("Telefax:", 100, 130, 60, 20, (1, 1, 1, 3), 0.9)
        return (*real, *ghost, fax)


def test_doubly_reported_ocr_line_appears_once_in_text() -> None:
    document = fitz.open()
    page = document.new_page(width=A4[0], height=A4[1])
    page.insert_image(page.rect, pixmap=fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 50, 50), 0))
    payload = document.tobytes()
    document.close()

    result = DocumentExtractor(ocr_backend=GhostLineOcr()).extract_bytes(
        payload, filename="rechnung.pdf", ocr="auto"
    )

    assert result.text.count("201-4393") == 1
    assert "Telefax:" in result.text


def test_code_on_scan_page_counts_as_read_despite_o_instead_of_zero() -> None:
    from presidio_sidecar.exporting import _scan_readback_matches, readback_matches

    code = "<<LOCATION_0cfd0de3>>"
    assert _scan_readback_matches("<<LOCATION_Ocfd0de3>>", code)
    assert _scan_readback_matches("«LOCATION_0cfdode3»,", code)
    # Only in the review value, and only for zero.
    assert not _scan_readback_matches("<<L0CATION_0cfd0de3>>", code)
    assert not _scan_readback_matches("<<LOCATION_Ocfd0de8>>", code)
    assert not _scan_readback_matches("<<LOCATION_O0cfd0de3>>", code)
    # In the image it stays strict: only the pixel image carries the code.
    assert not readback_matches("<<LOCATION_Ocfd0de3>>", code)
