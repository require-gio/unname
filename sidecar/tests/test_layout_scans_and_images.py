"""Overlay scans and images (layout).

The test validates acceptance criterion A-29 – scans stay scans, images stay
images:

- Image (E-L4): after saving, codes, invented names and placeholders appear as
  pixels where the view shows them (same plan, same drawing as the reference
  image). OCR reads back every drawn word; if it reads something else, the text
  is saved and the output reflects that.
- Scan PDF (E-L3): pixels under each location are removed, the code appears as
  visible text with an invisible text layer containing the remaining OCR words.
  The mandatory review requires: no original value in image or text layer,
  each code readable in the image and present in the text layer the same number
  of times it was inserted.
- The round-trip over the text layer of a saved scan is lossless
  (A-09 with scan; with a real language model:
  ``scripts/llm-round-trip.py --scan``).

Tesseract is not needed: the OCR mock :class:`SeeingOcr` reads exactly what is
in the image – a single word as long as its pixels are untouched, a code only
where new ink is present.
"""

from __future__ import annotations

import io
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageChops

from presidio_sidecar.app import create_app
from presidio_sidecar.config import Settings
from presidio_sidecar.detection import DetectionResult, Finding, FindingSource
from presidio_sidecar.diagnostics import DiagnosticsRecorder
from presidio_sidecar.document_workflow import (
    export_processed_document,
    image_page_geometry,
    image_result_redactions,
    pdf_page_geometry,
    plan_placements,
)
from presidio_sidecar.exporting import (
    PdfRedaction,
    PdfTextLayerWord,
    PdfVerificationError,
    ReadbackError,
    export_redacted_pdf,
    readback_matches,
)
from presidio_sidecar.extraction import DocumentExtractor, OcrWord
from presidio_sidecar.jobs import JobManager
from presidio_sidecar.profiles import Profile, load_profiles
from presidio_sidecar.rendering import render_result_image_png
from presidio_sidecar.restore import candidates
from presidio_sidecar.services import PhaseOneServices
from presidio_sidecar.workers import WorkerCapacity
from test_layout_map import (  # noqa: F401
    _extract,
    corpus,
    layout_corpus,
)
from test_layout_pdf_export import _processing, _span

TOKEN = "layout-phase-p"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CODES = {"method": "reversible", "style": "codes", "categories": ["names", "addresses", "contact"]}
PLACEHOLDER = {
    "method": "irreversible",
    "style": "placeholder",
    "categories": ["names", "addresses", "contact"],
}
#: Samples to mask: name, street, email (header) – one word each and
#: multiple words, plus a duplicate in the footer.
VALUES = ("MUSTERMANN", layout_corpus.STREET, layout_corpus.EMAIL)
_CODE = re.compile(r"<<[A-Z_]+_[0-9a-f]{8}>>")


# -- OCR mock that sees -------------------------------------------------

#: Reads what is in the image (``scripts/layout-corpus.py``); the same mock is
#: used by ``scripts/llm-round-trip.py --scan``.
SeeingOcr = layout_corpus.SehendeOcr
_code_boxes = layout_corpus.code_areas


def _spans(text: str, values: tuple[str, ...]) -> list[tuple[int, int]]:
    """All occurrences of each value – as reported by detection."""

    spans = []
    for value in values:
        offset = 0
        while (start := text.find(value, offset)) >= 0:
            spans.append((start, start + len(value)))
            offset = start + len(value)
    return spans


@dataclass
class ReadingImageOcr:
    """Image OCR: fixed words across the whole image, ``readback`` in the crop."""

    size: tuple[int, int]
    words: tuple[OcrWord, ...]
    readback: str = ""
    crops: list[tuple[int, int]] = field(default_factory=list)

    def recognize(self, image: Image.Image, *, languages: tuple[str, ...]) -> tuple[OcrWord, ...]:
        del languages
        if image.size == self.size:
            return self.words
        self.crops.append(image.size)
        if not self.readback:
            return ()
        return (OcrWord(self.readback, 4, 4, image.width - 8, image.height - 8, (1, 1, 1, 1), 0.9),)


# -- Image (E-L4) -------------------------------------------------------------


def _screenshot_ocr(path: Path) -> ReadingImageOcr:
    with Image.open(path) as image:
        size = image.size
    # Position of the crop (200 dpi, origin at x = 40 pt, y = 150 pt).
    factor = 200 / 72

    def box(text: str, x0: float, y0: float, x1: float, line: int) -> OcrWord:
        left, top = round((x0 - 40) * factor), round((y0 - 150) * factor)
        width, height = round((x1 - x0) * factor), round(11.3 * factor)
        return OcrWord(text, left, top, width, height, (1, 1, 1, line), 0.9)

    replica = fitz.open(stream=layout_corpus.delivery_note_pdf(), filetype="pdf")
    words = [
        box(str(text), x0, y0, x1, line)
        for x0, y0, x1, y1, text, _block, line, _ in replica[0].get_text("words", sort=True)
        if y0 >= 150 and y1 <= 205 and x0 >= 40 and x1 <= 300
    ]
    replica.close()
    return ReadingImageOcr(size, tuple(words))


def test_image_keeps_its_format_and_carries_the_code_in_pixels(
    corpus: dict[str, Path],  # noqa: F811
    tmp_path: Path,
) -> None:
    path = corpus["bildschirmfoto.png"]
    ocr = _screenshot_ocr(path)
    document = _extract(path, ocr=ocr)
    processing = _processing(document.text, [_span(document.text, "MUSTERMANN")])
    (placement,) = plan_placements(
        document, document.text, processing, lambda _: image_page_geometry(document)
    )
    code = placement["text"]["value"]
    ocr.readback = code

    target = tmp_path / "bild.png"
    result = export_processed_document(
        path,
        target,
        document=document,
        source_text=document.text,
        processing=processing,
        ocr_backend=ocr,
    )

    assert result.verification.readback_checked == 1
    assert len(ocr.crops) == 1
    with Image.open(path) as opened:
        original = opened.convert("RGB")
    with Image.open(target) as opened:
        saved = opened.convert("RGB")
    assert saved.size == original.size

    # Pixels outside the planned area must stay unchanged.
    (cover,) = placement["cover"]
    outer = (int(cover[0]), int(cover[1]), int(cover[2]) + 1, int(cover[3]) + 1)
    difference = ImageChops.difference(saved, original)
    assert difference.crop(outer).getbbox() is not None
    difference.paste((0, 0, 0), outer)
    assert difference.getbbox() is None

    # The ink of the code appears where the view renders it.
    text = placement["text"]
    ink = ImageChops.invert(saved.crop(outer).convert("L")).point(lambda v: 255 * (v > 96))
    left, top, right, bottom = ink.getbbox()
    assert abs(outer[0] + left - text["x"]) <= 2
    assert outer[0] + right <= text["x"] + text["width"] + 2
    assert outer[1] + bottom <= text["baseline"] + text["size"] * 0.3
    assert outer[1] + top >= text["baseline"] - text["size"]

    # View = file: the control image is drawn with the same function.
    redactions = image_result_redactions(document, document.text, processing)
    control = Image.open(
        io.BytesIO(render_result_image_png(path, redactions, 1.0, max_image_pixels=10**8))
    ).convert("RGB")
    assert ImageChops.difference(control, saved).getbbox() is None


def test_unreadable_code_in_an_image_fails_without_a_file(
    corpus: dict[str, Path],  # noqa: F811
    tmp_path: Path,
) -> None:
    path = corpus["bildschirmfoto.png"]
    ocr = _screenshot_ocr(path)
    document = _extract(path, ocr=ocr)
    processing = _processing(document.text, [_span(document.text, "MUSTERMANN")])
    ocr.readback = "<<PERSON_0000000O>>"  # O instead of 0: misread

    with pytest.raises(ReadbackError):
        export_processed_document(
            path,
            tmp_path / "bild.png",
            document=document,
            source_text=document.text,
            processing=processing,
            ocr_backend=ocr,
        )
    with pytest.raises(ReadbackError):
        export_processed_document(
            path,
            tmp_path / "ohne-ocr.png",
            document=document,
            source_text=document.text,
            processing=processing,
        )
    assert not list(tmp_path.glob("*.png"))


def test_readback_compares_exactly_but_ignores_spacing_and_guillemets() -> None:
    assert readback_matches("<<PERSON_e395da1b>>", "<<PERSON_e395da1b>>")
    assert readback_matches("« PERSON_e395da1b »", "<<PERSON_e395da1b>>")
    assert readback_matches("Herr <<PERSON_e395da1b>> ruft", "<<PERSON_e395da1b>>")
    assert not readback_matches("<<PERSON_e395dalb>>", "<<PERSON_e395da1b>>")
    assert not readback_matches("<<person_e395da1b>>", "<<PERSON_e395da1b>>")
    assert not readback_matches("", "<<PERSON_e395da1b>>")


# -- Scan-PDF (E-L3) ---------------------------------------------------------


def _scan_export(
    path: Path, tmp_path: Path, values: tuple[str, ...] = VALUES
) -> tuple[Any, list[dict[str, Any]], Path, SeeingOcr, Any]:
    ocr = SeeingOcr(path)
    document = _extract(path, ocr=ocr)
    assert document.ocr_used
    processing = _processing(document.text, _spans(document.text, values))
    placements = plan_placements(
        document,
        document.text,
        processing,
        lambda page: pdf_page_geometry(path, page),
    )
    ocr.codes = _code_boxes(placements)
    target = tmp_path / f"{path.stem}-geschuetzt.pdf"
    result = export_processed_document(
        path,
        target,
        document=document,
        source_text=document.text,
        processing=processing,
        ocr_backend=ocr,
        pdf_ocr_dpi=150,
    )
    return document, placements, target, ocr, result


@pytest.mark.parametrize("name", ["scan.pdf", "scan-rot90.pdf"])
def test_scan_stays_pdf_with_visible_codes_and_a_text_layer(
    corpus: dict[str, Path],  # noqa: F811
    tmp_path: Path,
    name: str,
) -> None:
    path = corpus[name]
    document, placements, target, _, result = _scan_export(path, tmp_path)
    codes = [placement["text"]["value"] for placement in placements]
    # The email address also appears in the footer.
    assert len(codes) == len(VALUES) + 1

    # Each code is read from the image; the text layer holds the remaining words.
    assert result.verification.readback_checked == len(codes)
    assert result.verification.text_layer_words > 20
    with fitz.open(target) as saved:
        page = saved[0]
        assert page.rotation == fitz.open(path)[0].rotation
        text = page.get_text()
        for code in codes:
            assert text.count(code) == 1
        for value in VALUES:
            assert value not in text
        for kept in ("Lieferschein", "ARMLEHNSTUHL", "Rechnungsnummer:"):
            assert kept in text
        # Reading order: each code stays at its position in the text, not ahead;
        # an AI reads “E-Mail: <<…>>” as a single unit.
        assert not text.lstrip().startswith("<<")
        assert re.search(r"E-Mail:\s*<<", text)

        # Codes are rendered visible (mode 0), everything else invisible (mode 3).
        modes: dict[str, set[int]] = {}
        for span in page.get_texttrace():
            chars = "".join(chr(char[0]) for char in span["chars"])
            modes.setdefault("code" if "<<" in chars else "layer", set()).add(span["type"])
        assert modes == {"code": {0}, "layer": {3}}

        # Invisible words sit on their OCR boxes (for marking and searching).
        boxes = {
            document.text[segment.start : segment.end]: segment.bbox
            for segment in document.segments
            if segment.bbox is not None
            and document.text[segment.start : segment.end] in {"Lieferschein", "ARMLEHNSTUHL"}
        }
        saved_words = {word[4]: word for word in page.get_text("words")}
        for word, box in boxes.items():
            x0, y0, x1, y1 = saved_words[word][:4]
            assert abs(x0 - box.x0) < 1 and abs(x1 - box.x1) < 1
            assert y0 < (box.y0 + box.y1) / 2 < y1


def test_scan_pixels_are_gone_under_every_value(
    corpus: dict[str, Path],  # noqa: F811
    tmp_path: Path,
) -> None:
    path = corpus["scan.pdf"]
    document, placements, target, ocr, _ = _scan_export(path, tmp_path)

    # OCR reads the saved scan and finds no original value – only the inserted codes.
    with fitz.open(target) as saved:
        pixmap = saved[0].get_pixmap(dpi=150, alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    read = " ".join(word.text for word in ocr.recognize(image, languages=("deu",)))
    for value in VALUES:
        for part in value.split():
            assert part not in read.split()
    for placement in placements:
        assert placement["text"]["value"] in read


def test_text_layer_may_not_carry_a_removed_value(
    corpus: dict[str, Path],  # noqa: F811
    tmp_path: Path,
) -> None:
    path = corpus["scan.pdf"]
    ocr = SeeingOcr(path)
    document = _extract(path, ocr=ocr)
    (segment,) = [
        item for item in document.segments if document.text[item.start : item.end] == "MUSTERMANN"
    ]
    assert segment.bbox is not None
    rect = (segment.bbox.x0 - 1, segment.bbox.y0 - 1, segment.bbox.x1 + 1, segment.bbox.y1 + 1)
    redaction = PdfRedaction(0, rect, "MUSTERMANN", verify_source_text=False)

    # If the removed value ends up in the text layer, the mandatory check aborts.
    with pytest.raises(PdfVerificationError, match="extrahierbar"):
        export_redacted_pdf(
            path,
            tmp_path / "leck.pdf",
            (redaction,),
            text_layer=(PdfTextLayerWord(0, rect, "MUSTERMANN"),),
            ocr_backend=ocr,
            ocr_dpi=150,
        )
    assert not (tmp_path / "leck.pdf").exists()


def test_every_code_is_in_the_text_layer_exactly_as_often_as_drawn(
    corpus: dict[str, Path],  # noqa: F811
    tmp_path: Path,
) -> None:
    path = corpus["scan.pdf"]
    ocr = SeeingOcr(path)
    document = _extract(path, ocr=ocr)
    processing = _processing(document.text, [_span(document.text, "MUSTERMANN")])
    placements = plan_placements(
        document,
        document.text,
        processing,
        lambda page: pdf_page_geometry(path, page),
    )
    ocr.codes = _code_boxes(placements)
    code = placements[0]["text"]["value"]
    (segment,) = [
        item for item in document.segments if document.text[item.start : item.end] == "MUSTERMANN"
    ]
    assert segment.bbox is not None
    rect = (segment.bbox.x0 - 1, segment.bbox.y0, segment.bbox.x1 + 1, segment.bbox.y1)
    redaction = PdfRedaction(
        0,
        rect,
        "MUSTERMANN",
        replacement=code,
        verify_source_text=False,
        fill=(1.0, 1.0, 1.0),
        font_size=8.0,
        text_x=segment.bbox.x0,
    )
    footer = (56.0, 820.0, 200.0, 830.0)
    with pytest.raises(PdfVerificationError, match="so oft"):
        export_redacted_pdf(
            path,
            tmp_path / "doppelt.pdf",
            (redaction,),
            text_layer=(PdfTextLayerWord(0, footer, code),),
            ocr_backend=ocr,
            ocr_dpi=150,
        )

    # Without the dummy that sees the code in the image, the code is considered unreadable.
    ocr.codes = []
    with pytest.raises(ReadbackError):
        export_redacted_pdf(
            path, tmp_path / "blind.pdf", (redaction,), ocr_backend=ocr, ocr_dpi=150
        )
    assert not list(tmp_path.glob("*.pdf"))


def test_mixed_pdf_gets_a_text_layer_only_on_the_scan_page(
    corpus: dict[str, Path],  # noqa: F811
    tmp_path: Path,
) -> None:
    path = tmp_path / "gemischt.pdf"
    mixed = fitz.open()
    mixed.insert_pdf(fitz.open(stream=layout_corpus.delivery_note_pdf(), filetype="pdf"))
    mixed.insert_pdf(fitz.open(corpus["scan.pdf"]))
    mixed.save(path)
    mixed.close()

    ocr = SeeingOcr(corpus["scan.pdf"])
    document = _extract(path, ocr=ocr)
    assert tuple(document.ocr_pages) == (1,)
    spans = _spans(document.text, ("MUSTERMANN",))
    assert len(spans) == 2
    processing = _processing(document.text, spans)
    placements = plan_placements(
        document, document.text, processing, lambda page: pdf_page_geometry(path, page)
    )
    ocr.codes = _code_boxes([item for item in placements if item["page"] == 1])
    target = tmp_path / "gemischt-geschuetzt.pdf"
    result = export_processed_document(
        path,
        target,
        document=document,
        source_text=document.text,
        processing=processing,
        ocr_backend=ocr,
        pdf_ocr_dpi=150,
    )

    assert result.verification.readback_checked == 1
    with fitz.open(target) as saved:
        text_page, scan_page = saved[0], saved[1]
        assert {span["type"] for span in text_page.get_texttrace()} == {0}
        assert 3 in {span["type"] for span in scan_page.get_texttrace()}
        for page, placement in zip(
            (text_page, scan_page), sorted(placements, key=lambda item: item["page"]), strict=True
        ):
            assert page.get_text().count(placement["text"]["value"]) == 1
            assert "MUSTERMANN" not in page.get_text()


# -- Save via API --------------------------------------------------


class ValuesAnalyzer:
    """Fixed matches for the values from :data:`VALUES` – without models."""

    TYPES = {
        "MUSTERMANN": "PERSON",
        layout_corpus.STREET: "ADDRESS",
        layout_corpus.EMAIL: "EMAIL_ADDRESS",
    }

    def nlp_status(self) -> dict[str, Any]:
        return {"active": "layout-offline", "loaded": True, "device": "cpu"}

    def analyze_for_profile(
        self, text: str, profile: Profile, *, progress: Callable[[str], None]
    ) -> DetectionResult:
        for phase in ("master_data", "rules", "ner", "suspicion"):
            progress(phase)
        findings = []
        for value, entity_type in self.TYPES.items():
            offset = 0
            while (start := text.find(value, offset)) >= 0:
                findings.append(
                    Finding(
                        entity_type=entity_type,
                        start=start,
                        end=start + len(value),
                        score=0.95,
                        source=FindingSource.NER,
                        text=value,
                        detector="layout-offline",
                    )
                )
                offset = start + len(value)
        return DetectionResult(confirmed=tuple(sorted(findings, key=lambda item: item.start)))


@dataclass
class _Api:
    client: TestClient
    services: PhaseOneServices
    tmp_path: Path


def _harness(tmp_path: Path, ocr: Any) -> Iterator[_Api]:
    settings = Settings(
        token=TOKEN,
        state_directory=tmp_path / "state",
        job_ttl_seconds=60,
        max_text_bytes=1024 * 1024,
    )
    analyzer = ValuesAnalyzer()
    services = PhaseOneServices(
        settings,
        DiagnosticsRecorder(),
        analyzer=analyzer,
        jobs=JobManager(
            analyzer,
            max_workers=1,
            ttl_seconds=60,
            max_text_bytes=settings.max_text_bytes,
            root=tmp_path / "jobs",
        ),
        extractor=DocumentExtractor(ocr_backend=ocr, pdf_ocr_dpi=150),
        profiles=load_profiles(),
        capacity=WorkerCapacity(1, 0, 2, 8 * 1024**3),
    )
    services.second_opinion_model = lambda: None  # type: ignore[method-assign]
    with TestClient(create_app(settings, DiagnosticsRecorder(), phase_one=services)) as client:
        yield _Api(client, services, tmp_path)


def _submit(api: _Api, path: Path, media_type: str) -> tuple[str, str]:
    entry = api.client.post("/entries", headers=AUTH, json={"kind": "documents"}).json()["id"]
    response = api.client.post(
        f"/entries/{entry}/files",
        headers=AUTH,
        files=[("file", (path.name, path.read_bytes(), media_type))],
    )
    assert response.status_code == 202, response.text
    file_id = response.json()["accepted"][0]["id"]
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        progress = api.client.get(f"/entries/{entry}/progress", headers=AUTH).json()
        if progress["done"] == progress["total"]:
            assert progress["files"][0]["status"] == "review", progress
            return entry, file_id
        time.sleep(0.01)
    raise AssertionError("Erkennung wurde nicht fertig.")


def _apply(api: _Api, job: str, masking: dict[str, Any]) -> dict[str, Any]:
    response = api.client.post(f"/jobs/{job}/apply", headers=AUTH, json={"masking": masking})
    assert response.status_code == 200, response.text
    return response.json()


def _save(api: _Api, entry: str, masking: dict[str, Any]) -> dict[str, Any]:
    response = api.client.post(
        f"/entries/{entry}/save",
        headers=AUTH,
        json={"masking": masking, "files": [], "confirm_irreversible": True},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("masking", [CODES, PLACEHOLDER], ids=["codes", "platzhalter"])
def test_saving_a_scan_keeps_a_pdf_and_round_trips_through_its_text_layer(
    corpus: dict[str, Path],  # noqa: F811
    tmp_path: Path,
    masking: dict[str, Any],
) -> None:
    ocr = SeeingOcr(corpus["scan.pdf"])
    for api in _harness(tmp_path, ocr):
        entry, job = _submit(api, corpus["scan.pdf"], "application/pdf")
        preview = _apply(api, job, masking)
        ocr.codes = _code_boxes(preview["placements"])
        saved = _save(api, entry, masking)

        (staged,) = saved["staged"]
        assert staged["name"] == "scan_geschützt.pdf"
        output = saved["entry"]["files"][0]["output"]
        assert output["format"] == "pdf"
        assert output["scan_text_layer"] is True
        assert output["from_scan"] is False and output["readback_failed"] is False
        assert output["text_extraction_checked"] is True

        with fitz.open(staged["path"]) as protected:
            layer = protected[0].get_text()
        for value in VALUES:
            assert value not in layer
        if masking is PLACEHOLDER:
            continue

        # A-09 with scan: a language model receives the text layer, changes case and brackets –
        # resetting is lossless.
        # The e-mail also appears in the footer: same code twice.
        codes = list(dict.fromkeys(_CODE.findall(layer)))
        assert len(codes) == len(VALUES)
        answer = layer.replace(codes[0], codes[0].lower()).replace(codes[1], codes[1][2:-2])
        assert len(candidates(answer)) >= len(VALUES)
        restored = api.client.post(
            "/restore/text", headers=AUTH, json={"text": answer, "entry_id": entry}
        )
        assert restored.status_code == 200, restored.text
        body = restored.json()
        assert body["counts"]["unknown"] == 0
        for value in VALUES:
            assert value in body["text"]


def _red_image(path: Path) -> tuple[Path, ReadingImageOcr]:
    """An image with a red block covering the name “MAX MUSTERMANN”."""

    image = Image.new("RGB", (360, 80), "white")
    for x in range(20, 250):
        for y in range(20, 46):
            image.putpixel((x, y), (190, 30, 30))
    image.save(path)
    words = (
        OcrWord("MAX", 20, 20, 60, 26, (1, 1, 1, 1), 0.95),
        OcrWord("MUSTERMANN", 90, 20, 160, 26, (1, 1, 1, 1), 0.95),
    )
    return path, ReadingImageOcr(image.size, words)


@pytest.mark.parametrize("suffix", [".png", ".jpg"])
def test_saving_an_image_with_codes_keeps_the_image(tmp_path: Path, suffix: str) -> None:
    path, ocr = _red_image(tmp_path / f"foto{suffix}")
    for api in _harness(tmp_path, ocr):
        entry, job = _submit(api, path, "image/png" if suffix == ".png" else "image/jpeg")
        (placement,) = _apply(api, job, CODES)["placements"]
        ocr.readback = placement["text"]["value"]
        saved = _save(api, entry, CODES)

        (staged,) = saved["staged"]
        assert staged["name"] == f"foto_geschützt{suffix}"
        output = saved["entry"]["files"][0]["output"]
        assert output["format"] == suffix[1:]
        assert output["from_scan"] is False and output["readback_failed"] is False
        with Image.open(staged["path"]) as protected:
            pixels = protected.convert("RGB")
        # No red under “MUSTERMANN”, dark text instead.
        box = pixels.crop((92, 22, 248, 44))
        assert not any(
            red > 150 and green < 90
            for red, green, _ in box.getdata()  # type: ignore[misc]
        )
        assert min(sum(pixel) for pixel in box.getdata()) < 200  # type: ignore[misc]
        # Control image = saved file (pixel-identical PNG).
        control = api.client.get(f"/jobs/{job}/pages/0/result?scale=1", headers=AUTH)
        assert control.status_code == 200, control.text
        if suffix == ".png":
            drawn = Image.open(io.BytesIO(control.content)).convert("RGB")
            assert ImageChops.difference(drawn, pixels).getbbox() is None


def test_unreadable_image_code_falls_back_to_text_and_says_so(tmp_path: Path) -> None:
    path, ocr = _red_image(tmp_path / "foto.png")
    ocr.readback = "PERS0N"
    for api in _harness(tmp_path, ocr):
        entry, _ = _submit(api, path, "image/png")
        saved = _save(api, entry, CODES)

        (staged,) = saved["staged"]
        assert staged["name"] == "foto_geschützt.txt"
        output = saved["entry"]["files"][0]["output"]
        assert (output["format"], output["from_scan"], output["readback_failed"]) == (
            "txt",
            True,
            True,
        )
        text = Path(staged["path"]).read_text(encoding="utf-8")
        assert "MUSTERMANN" not in text and _CODE.search(text)
        # No half-image file left in the output folder.
        assert [item.name for item in Path(staged["path"]).parent.iterdir()] == [
            "foto_geschützt.txt"
        ]
