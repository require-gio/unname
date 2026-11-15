"""Placement plan (layout plan, phase N).

The result view overlays the overpaint from the apply response onto the
page image. Checks:

- Apply returns ``placements`` for fixed pages (PDF, rotated PDF, image) in the
  display space of the layout map, without touching the source file.
- Geometry comes from the same function that draws the export: what the export
  plans when saving appears – rotated into display space – exactly as the apply
  plan.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz
import pytest
from fastapi.testclient import TestClient

from presidio_sidecar import exporting
from presidio_sidecar.document_workflow import (
    export_processed_document,
    image_page_geometry,
    pdf_page_geometry,
    plan_placements,
)
from presidio_sidecar.layout import build_layout
from presidio_sidecar.placement import plan_replacements, untouched_words
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
)

CODES = {"method": "reversible", "style": "codes", "categories": ["names"]}
BLACK = {"method": "irreversible", "style": "mask", "categories": ["names"]}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _apply(client: TestClient, job: str, masking: dict[str, Any]) -> dict[str, Any]:
    response = client.post(f"/jobs/{job}/apply", headers=AUTH, json={"masking": masking})
    assert response.status_code == 200, response.text
    return response.json()


def _word_box(layout: dict[str, Any], start: int, end: int) -> list[float]:
    words = [word for word in layout["words"] if word[5] < end and word[6] > start]
    return [
        min(word[1] for word in words),
        min(word[2] for word in words),
        max(word[3] for word in words),
        max(word[4] for word in words),
    ]


def _contains(outer: list[float], inner: list[float], tolerance: float = 0.01) -> bool:
    return (
        outer[0] <= inner[0] + tolerance
        and outer[1] <= inner[1] + tolerance
        and outer[2] >= inner[2] - tolerance
        and outer[3] >= inner[3] - tolerance
    )


@pytest.mark.parametrize("name", ["lieferschein.pdf", "lieferschein-rot90.pdf"])
def test_apply_returns_placements_in_display_space(
    api: tuple[TestClient, PhaseOneServices],  # noqa: F811
    corpus: dict[str, Path],  # noqa: F811
    name: str,
) -> None:
    client, services = api
    job = _submit(client, corpus[name], "application/pdf")
    source = services.jobs().get(job).source_path
    before = _sha(source)

    applied = _apply(client, job, CODES)
    layout = client.get(f"/jobs/{job}/layout", headers=AUTH).json()

    (placement,) = applied["placements"]
    (finding,) = applied["applied_findings"]
    assert placement["finding_id"] == finding["finding_id"]
    assert (placement["start"], placement["end"]) == (
        finding["original_start"],
        finding["original_end"],
    )
    assert placement["page"] == 0 and placement["fill"] == "white"
    # The cover area encloses the word in the layout map (display space).
    (cover,) = placement["cover"]
    assert _contains(cover, _word_box(layout, placement["start"], placement["end"]))
    page = layout["pages"][0]
    assert 0 <= cover[0] < cover[2] <= page["width"] and 0 <= cover[1] < cover[3] <= page["height"]

    text = placement["text"]
    code = applied["text"][finding["output_start"] : finding["output_end"]]
    assert text["value"] == code
    # Original typography: Times 10 pt, black.
    assert text["font"] == "serif" and not text["bold"]
    assert text["color"] == "#000000" and text["size"] == 10
    assert text["rotate"] == fitz.open(corpus[name])[0].rotation
    # Baseline lies within the cover; text flows left-to-right.
    if text["rotate"] == 0:
        assert cover[0] <= text["x"] and text["x"] + text["width"] <= cover[2] + 0.01
        assert cover[1] < text["baseline"] < cover[3]
    else:
        assert cover[0] < text["x"] < cover[2]
        assert cover[1] <= text["baseline"] <= cover[3] + 0.01

    # Dry-run: source file unchanged.
    assert _sha(source) == before


def test_masking_style_black_has_no_text(
    api: tuple[TestClient, PhaseOneServices],  # noqa: F811
    corpus: dict[str, Path],  # noqa: F811
) -> None:
    client, _ = api
    job = _submit(client, corpus["lieferschein.pdf"], "application/pdf")
    (placement,) = _apply(client, job, BLACK)["placements"]
    assert placement["fill"] == "black" and placement["text"] is None


def test_text_formats_have_no_placements(
    api: tuple[TestClient, PhaseOneServices],  # noqa: F811
    corpus: dict[str, Path],  # noqa: F811
) -> None:
    client, _ = api
    job = _submit(client, corpus["kunden.csv"], "text/csv")
    assert _apply(client, job, CODES)["placements"] == []


def test_image_placements_are_in_pixels(corpus: dict[str, Path]) -> None:  # noqa: F811
    path = corpus["bildschirmfoto.png"]
    ocr = RecordingOcr([("MAX", (40, 30, 90, 52)), ("MUSTERMANN", (96, 30, 260, 52))])
    document = _extract(path, ocr=ocr)
    processing = _processing(document.text, "MUSTERMANN", "<<PERSON_e395da1b>>")
    layout = build_layout("j", document, path, max_image_pixels=10**8)

    (placement,) = plan_placements(
        document, document.text, processing, lambda _: image_page_geometry(document)
    )
    (cover,) = placement["cover"]
    assert _contains(cover, _word_box(layout, placement["start"], placement["end"]))
    # Code does not find space right of the word and does not cover "MAX".
    assert cover[0] >= 90
    assert layout["pages"][0]["unit"] == "px"
    text = placement["text"]
    assert text["rotate"] == 0 and text["value"] == "<<PERSON_e395da1b>>"
    # Font size from line height (box 22 px + 2 px margin), not PDF points.
    assert 2.5 <= text["size"] <= 24 * 0.75


# -- View = Export --------------------------------------------------------


@dataclass(frozen=True)
class _Processing:
    """What plan and export read from the apply result: text and replacements."""

    text: str
    applied_findings: tuple[AppliedFinding, ...]


def _processing(text: str, value: str, code: str) -> ProcessingResult:
    start = text.index(value)
    output = text[:start] + code + text[start + len(value) :]
    return _Processing(  # type: ignore[return-value]
        text=output,
        applied_findings=(
            AppliedFinding(
                finding_id="f-1",
                kind=FindingKind.CONFIRMED,
                entity_type="PERSON",
                source="test",
                score=1.0,
                mode=ProcessingMode.TOKEN_DETERMINISTIC,
                reversibility=Reversibility.VAULT,
                original_start=start,
                original_end=start + len(value),
                output_start=start,
                output_end=start + len(code),
            ),
        ),
    )


@pytest.mark.parametrize("name", ["lieferschein.pdf", "lieferschein-rot90.pdf"])
def test_export_draws_what_apply_planned(
    corpus: dict[str, Path],  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    path = corpus[name]
    document = _extract(path, ocr="never")
    processing = _processing(document.text, "MUSTERMANN", "<<PERSON_e395da1b>>")

    planned = plan_placements(
        document,
        document.text,
        processing,
        lambda page: pdf_page_geometry(path, page),
    )

    drawn: list[Any] = []

    def spy(bounds: fitz.Rect, obstacles: Any, redactions: Any, styles: Any = ()) -> Any:
        plans = plan_replacements(bounds, obstacles, redactions, styles)
        drawn.extend(plans)
        return plans

    monkeypatch.setattr(exporting, "plan_replacements", spy)
    export_processed_document(
        path,
        tmp_path / "out.pdf",
        document=document,
        source_text=document.text,
        processing=processing,
    )

    (placement,) = planned
    (plan,) = drawn
    matrix = fitz.open(path)[0].rotation_matrix
    cover = plan.cover * matrix
    assert placement["cover"] == [[round(value, 2) for value in cover]]
    origin = plan.origin * matrix
    assert (placement["text"]["x"], placement["text"]["baseline"]) == (
        round(origin.x, 2),
        round(origin.y, 2),
    )
    assert placement["text"]["size"] == round(plan.size, 2)


def test_untouched_words_are_the_obstacles() -> None:
    words = [fitz.Rect(10, 10, 30, 20), fitz.Rect(32, 10, 60, 20), fitz.Rect(10, 30, 40, 40)]
    # The editor hits the second word; the others stay as obstacles.
    assert untouched_words(words, [fitz.Rect(31, 10, 61, 20)]) == (0, 2)
