"""Align export (layout plan, phase O).

The test checks the result after A-26 – the saved PDF should look like the
result view:

- Typography per 4.7: The replacement text is left-aligned at the start of the
  position, on the original baseline, using original size, color and
  font class; if it does not fit it shifts left and becomes smaller.
- Contract: What Apply returns as ``placements`` is stored unchanged – areas,
  baseline, size, font and color are read back from the saved PDF (also for
  rotated pages and CropBox offsets).
- Control image ``/pages/{n}/result``: the page after real editing in memory;
  outside the placement areas it matches the original.

Pixel comparison between the control image and the overpainted view is done by
the Playwright test (``layout.spec.ts``), because only there the view is drawn.
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageChops

from presidio_sidecar.document_workflow import (
    export_processed_document,
    image_page_geometry,
    pdf_page_geometry,
    plan_placements,
)
from presidio_sidecar.placement import BASE14_FONTS, font_class
from presidio_sidecar.processing import (
    AppliedFinding,
    FindingKind,
    ProcessingMode,
    ProcessingResult,
    Reversibility,
)
from presidio_sidecar.services import PhaseOneServices
from test_layout_map import (  # noqa: F401
    AUTH,
    RecordingOcr,
    _extract,
    _submit,
    api,
    corpus,
    layout_corpus,
)
from test_layout_placements import CODES, _apply

#: Base-14 font → name as it appears in the saved PDF.
_PDF_FONT_NAMES = {
    "helv": "Helvetica",
    "hebo": "Helvetica-Bold",
    "tiro": "Times-Roman",
    "tibo": "Times-Bold",
    "cour": "Courier",
    "cobo": "Courier-Bold",
}


@dataclass(frozen=True)
class _Processing:
    text: str
    applied_findings: tuple[AppliedFinding, ...]


def _processing(text: str, spans: list[tuple[int, int]]) -> ProcessingResult:
    """Apply result: each area becomes its own code."""

    output = ""
    cursor = 0
    applied = []
    for number, (start, end) in enumerate(sorted(spans)):
        output += text[cursor:start]
        begin = len(output)
        output += f"<<PERSON_{number:08x}>>"
        cursor = end
        applied.append(
            AppliedFinding(
                finding_id=f"f-{number}",
                kind=FindingKind.CONFIRMED,
                entity_type="PERSON",
                source="test",
                score=1.0,
                mode=ProcessingMode.TOKEN_DETERMINISTIC,
                reversibility=Reversibility.VAULT,
                original_start=start,
                original_end=end,
                output_start=begin,
                output_end=len(output),
            )
        )
    output += text[cursor:]
    return _Processing(output, tuple(applied))  # type: ignore[return-value]


def _span(text: str, value: str, *, after: str = "") -> tuple[int, int]:
    start = text.index(value, text.index(after) if after else 0)
    return start, start + len(value)


def _plan_and_export(
    path: Path, spans: list[tuple[int, int]] | None, tmp_path: Path, values: tuple[str, ...] = ()
) -> tuple[list[dict[str, Any]], fitz.Document]:
    document = _extract(path, ocr="never")
    wanted = spans if spans is not None else [_span(document.text, value) for value in values]
    processing = _processing(document.text, wanted)
    placements = plan_placements(
        document, document.text, processing, lambda page: pdf_page_geometry(path, page), path
    )
    target = tmp_path / f"{path.stem}-out.pdf"
    export_processed_document(
        path, target, document=document, source_text=document.text, processing=processing
    )
    return placements, fitz.open(target)


def _code_spans(page: fitz.Page) -> list[dict[str, Any]]:
    return [
        span
        for block in page.get_text("dict")["blocks"]
        for line in block.get("lines", ())
        for span in line["spans"]
        if "<<PERSON_" in span["text"]
    ]


def _close(a: float, b: float, tolerance: float = 0.02) -> bool:
    return abs(a - b) <= tolerance


# -- Typography (spec §4.7) ---------------------------------------------------------


def _typography_pdf(path: Path) -> None:
    document = fitz.open()
    page = document.new_page(width=420, height=300)
    page.insert_text((40, 50), "Kunde: Hans Meier", fontsize=12, fontname="hebo", color=(0, 0, 0.6))
    page.insert_text((40, 80), "Konto Pia Wolf", fontsize=9, fontname="cour")
    page.insert_text((40, 110), "Gruss Eva Brandt", fontsize=10, fontname="tiro", color=(0.3,) * 3)
    page.draw_rect(fitz.Rect(30, 125, 300, 150), color=None, fill=(0.1, 0.1, 0.3))
    page.insert_text((40, 142), "Logo Ida Roth", fontsize=11, color=(1, 1, 1))
    # Eng: The code does not fit in full size up to the next word.
    for x, word in ((40, "Ab"), (60, "Kurz"), (130, "Ende")):
        page.insert_text((x, 190), word, fontsize=10)
    document.save(path)


def test_replacement_takes_over_the_original_typography(tmp_path: Path) -> None:
    path = tmp_path / "typografie.pdf"
    _typography_pdf(path)
    values = ("Hans Meier", "Pia Wolf", "Eva Brandt", "Ida Roth", "Kurz")
    placements, saved = _plan_and_export(path, None, tmp_path, values)
    by_value = {
        value: placement
        for value, placement in zip(
            values, sorted(placements, key=lambda item: item["start"]), strict=True
        )
    }
    words = {word[4]: word for word in fitz.open(path)[0].get_text("words")}

    hans = by_value["Hans Meier"]["text"]
    assert (hans["font"], hans["bold"], hans["size"], hans["color"]) == (
        "sans",
        True,
        12,
        "#000099",
    )
    # Left-aligned at the start of the span, on the original baseline.
    assert _close(hans["x"], words["Hans"][0]) and _close(hans["baseline"], 50)

    pia = by_value["Pia Wolf"]["text"]
    assert (pia["font"], pia["bold"], pia["size"]) == ("mono", False, 9)
    assert _close(pia["x"], words["Pia"][0]) and _close(pia["baseline"], 80)

    eva = by_value["Eva Brandt"]["text"]
    assert (eva["font"], eva["size"], eva["color"]) == ("serif", 10, "#4d4d4d")

    # White text on dark logo would be invisible on the white area.
    assert by_value["Ida Roth"]["text"]["color"] == "#000000"

    # No space until “Ende”: the code shifts left behind “Ab” and
    # becomes smaller, but does not cover either neighboring word.
    short = by_value["Kurz"]["text"]
    assert short["size"] < 10
    assert short["x"] >= words["Ab"][2] and short["x"] + short["width"] <= words["Ende"][0]

    # The saved file contains exactly this text in the appropriate Base-14 font.
    drawn = {span["text"]: span for span in _code_spans(saved[0])}
    for placement in placements:
        text = placement["text"]
        span = drawn[text["value"]]
        assert span["font"] == _PDF_FONT_NAMES[BASE14_FONTS[(text["font"], text["bold"])]]
        assert font_class(span["font"], span["flags"]) == (text["font"], text["bold"])


def test_replica_address_keeps_its_column(corpus: dict[str, Path], tmp_path: Path) -> None:  # noqa: F811
    """Regression test 1.4: address column alignment stays correct."""

    path = corpus["lieferschein.pdf"]
    text = _extract(path, ocr="never").text
    placements, saved = _plan_and_export(
        path, [_span(text, layout_corpus.NAME), _span(text, layout_corpus.STREET)], tmp_path
    )
    for placement in placements:
        assert placement["text"]["font"] == "serif" and placement["text"]["size"] == 10
        # Address column x-position: 59, matching “1110 WIEN” below.
        assert _close(placement["text"]["x"], 59.0)
    spans = _code_spans(saved[0])
    assert [round(span["origin"][1], 2) for span in spans] == [182.0, 195.0]
    assert all(span["font"] == "Times-Roman" and span["size"] == 10 for span in spans)


# -- Geometry saved from the UI -------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "lieferschein.pdf",
        "lieferschein-rot90.pdf",
        "lieferschein-rot180.pdf",
        "lieferschein-rot270.pdf",
        "lieferschein-cropbox.pdf",
    ],
)
def test_saved_pdf_contains_exactly_the_planned_geometry(
    corpus: dict[str, Path],  # noqa: F811
    tmp_path: Path,
    name: str,
) -> None:
    path = corpus[name]
    text = _extract(path, ocr="never").text
    # A span covering multiple lines (street to city), serif, sans and a bold table header.
    # (Mono checks the typography test: “4QK2AB” also appears in the control text,
    # mandatory check should run.)
    spans = [
        _span(text, "MUSTERMANN"),
        (text.index(layout_corpus.STREET), text.index(layout_corpus.ORT) + len(layout_corpus.ORT)),
        _span(text, "Auftragsnummer"),
        _span(text, "Artikelbezeichnung"),
    ]
    placements, saved = _plan_and_export(path, spans, tmp_path)
    assert len(placements) == 4
    page = saved[0]
    matrix = page.rotation_matrix

    fills = [
        fitz.Rect(item["rect"]) * matrix
        for item in page.get_drawings()
        if item.get("fill") is not None and item["type"] == "f"
    ]
    drawn = {span["text"]: span for span in _code_spans(page)}
    assert len(drawn) == 4
    for placement in placements:
        # Each planned area is filled exactly as expected.
        for cover in placement["cover"]:
            assert any(
                all(_close(a, b, 0.05) for a, b in zip(cover, fill, strict=True)) for fill in fills
            ), (name, placement["finding_id"], cover)
        text_plan = placement["text"]
        span = drawn[text_plan["value"]]
        origin = fitz.Point(span["origin"]) * matrix
        assert _close(origin.x, text_plan["x"]) and _close(origin.y, text_plan["baseline"]), name
        assert _close(span["size"], text_plan["size"])
        assert font_class(span["font"], span["flags"]) == (text_plan["font"], text_plan["bold"])
        assert f"#{span['color']:06x}" == text_plan["color"]
        assert text_plan["rotate"] == page.rotation


# -- Kontrollbild ---------------------------------------------------------------


def test_result_image_matches_the_original_outside_the_covers(
    api: tuple[TestClient, PhaseOneServices],  # noqa: F811
    corpus: dict[str, Path],  # noqa: F811
) -> None:
    client, services = api
    job = _submit(client, corpus["lieferschein-rot90.pdf"], "application/pdf")
    # Before applying, there is no state that a control image could show.
    assert client.get(f"/jobs/{job}/pages/0/result?scale=1", headers=AUTH).status_code == 409

    (placement,) = _apply(client, job, CODES)["placements"]
    response = client.get(f"/jobs/{job}/pages/0/result?scale=1", headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store"
    result = Image.open(io.BytesIO(response.content)).convert("L")
    original = Image.open(
        io.BytesIO(client.get(f"/jobs/{job}/pages/0?scale=1", headers=AUTH).content)
    ).convert("L")
    assert result.size == original.size

    # Outside the placement area (plus 1 px edge smoothing) each pixel stays unchanged,
    # inside something changed.
    x0, y0, x1, y1 = placement["cover"][0]
    box = (int(x0) - 1, int(y0) - 1, int(x1) + 2, int(y1) + 2)
    difference = ImageChops.difference(result, original)
    assert difference.crop(box).getbbox() is not None
    difference.paste(0, box)
    assert difference.getbbox() is None

    # A scale outside the map is not a control image. (Images have been covered by
    # test_layout_scans_and_images.)
    assert client.get(f"/jobs/{job}/pages/0/result?scale=1.25", headers=AUTH).status_code == 422
    assert services.jobs().get(job).source_path.exists()


def test_findings_inside_one_ocr_word_get_their_own_cover(
    corpus: dict[str, Path],  # noqa: F811
) -> None:
    # The OCR reads the MRZ of an ID as a single word; both names need separate
    # areas, otherwise the hover shows the other pseudonym.
    path = corpus["bildschirmfoto.png"]
    word = "MUELLER<<ANDREA<<<<<<<<"
    document = _extract(path, ocr=RecordingOcr([(word, (20, 30, 480, 52))]))
    processing = _processing(
        document.text,
        [_span(document.text, "MUELLER"), _span(document.text, "ANDREA")],
    )
    first, second = plan_placements(
        document, document.text, processing, lambda _: image_page_geometry(document)
    )
    (left,) = first["cover"]
    (right,) = second["cover"]
    assert left[2] <= right[0]
    # ANDREA starts after "MUELLER<<" – 9 of 23 characters.
    assert right[0] >= 20 + 460 * 9 / 23 - 0.01


@pytest.mark.parametrize("font", ["helv", "cour", "tiro"])
def test_findings_inside_one_pdf_word_get_their_own_cover(tmp_path: Path, font: str) -> None:
    # Same in a PDF: the areas follow the character boxes, removing each name precisely
    # while filler characters remain.
    source = fitz.open()
    page = source.new_page(width=420, height=120)
    page.insert_text((20, 60), "Pass MUELLER<<ANDREA<<<<KL Ende", fontsize=11, fontname=font)
    path = tmp_path / "mrz.pdf"
    source.save(path)
    source.close()

    placements, saved = _plan_and_export(path, None, tmp_path, ("MUELLER", "ANDREA"))
    first, second = sorted(placements, key=lambda placement: placement["start"])
    (left,) = first["cover"]
    (right,) = second["cover"]
    assert left[2] <= right[0]
    text = saved[0].get_text()
    assert "MUELLER" not in text and "ANDREA" not in text
    assert "Pass" in text and "<<<<KL" in text and "Ende" in text
    assert text.count("<<PERSON_") == 2


# -- A-23: no degradation -----------------------------------------------


def test_planning_with_typography_stays_cheap(corpus: dict[str, Path]) -> None:  # noqa: F811
    path = corpus["lieferschein.pdf"]
    document = _extract(path, ocr="never")
    processing = _processing(
        document.text,
        [_span(document.text, layout_corpus.NAME), _span(document.text, layout_corpus.STREET)],
    )
    geometry = pdf_page_geometry(path, 0)
    started = time.perf_counter()
    for _ in range(20):
        plan_placements(document, document.text, processing, lambda _: geometry)
    # The computation runs on every apply; geometry and spans are cached.
    assert (time.perf_counter() - started) / 20 < 0.02
