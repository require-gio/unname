"""Word in the review view (layout).

- ``docx_units()`` is the single pass shared by extraction, export, and preview: same entries, same
IDs, same order.
- The preview copy adds exactly one anchor at the start of each paragraph, does not modify text and
contains no external relationships, hyperlinks, field codes, or metadata.
- The layout map ``flow`` provides entries with UTF-16 ranges; the endpoint returns the copy
directly from memory without caching.
- The saved DOCX has the same entries; each replacement appears in the entry where the view shows
it. Rendering comparison is done in ``app/src/lib/layout/flow-anchor.spec.ts`` using fixtures from
``scripts/layout-corpus.py --smoke``.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

import pytest
from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from fastapi.testclient import TestClient

from presidio_sidecar.document_workflow import export_processed_document
from presidio_sidecar.docx_preview import build_docx_preview
from presidio_sidecar.exporting import read_docx_text_units
from presidio_sidecar.extraction import DocumentExtractor
from presidio_sidecar.extraction.docx_units import docx_units, iter_docx_paragraphs
from presidio_sidecar.layout import LayoutError, build_layout, docx_unit_segments
from presidio_sidecar.services import PhaseOneServices
from test_layout_map import (  # noqa: F401
    AUTH,
    _submit,
    api,
    corpus,
    layout_corpus,
)

_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _save(document: Any) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _tricky_docx() -> bytes:
    """Merged and nested cells, empty paragraphs, header and footer tables."""

    document = Document()
    document.add_paragraph("Vorne")
    table = document.add_table(rows=2, cols=3)
    table.cell(0, 0).merge(table.cell(0, 1)).text = "Verbunden"
    table.cell(0, 2).text = "C"
    cell = table.cell(1, 0)
    cell.text = "Erste"
    cell.add_paragraph("")
    cell.add_paragraph("Dritte")
    inner = table.cell(1, 1).add_table(rows=1, cols=2)
    inner.cell(0, 0).text = "In1"
    inner.cell(0, 1).text = "In2"
    document.add_paragraph("")
    document.add_paragraph("Hinten\tTab 𝔄")
    section = document.sections[0]
    section.header.paragraphs[0].text = "Kopf"
    footer = section.footer.add_table(rows=1, cols=2, width=1_000_000)
    footer.cell(0, 0).text = "F1"
    footer.cell(0, 1).text = "F2"
    return _save(document)


def _extract(payload: bytes, name: str = "brief.docx") -> Any:
    return DocumentExtractor().extract_bytes(payload, filename=name)


def _write(tmp_path: Path, payload: bytes, name: str = "probe.docx") -> Path:
    path = tmp_path / name
    path.write_bytes(payload)
    return path


def _anchors(paragraph: Any) -> list[str]:
    return [
        element.get(qn("w:name"))
        for element in paragraph._p.iterchildren(qn("w:bookmarkStart"))  # noqa: SLF001
    ]


# -- Single pass -----------------------------------------------------------


@pytest.mark.parametrize("probe", ["brief", "tricky"])
def test_docx_units_are_the_extraction_segments_and_the_export_targets(
    corpus: dict[str, Path],  # noqa: F811
    tmp_path: Path,
    probe: str,
) -> None:
    payload = corpus["brief.docx"].read_bytes() if probe == "brief" else _tricky_docx()
    extracted = _extract(payload)
    units = docx_units(Document(io.BytesIO(payload)))

    segments = docx_unit_segments(extracted)
    assert [(unit.target_id, unit.text) for unit in units] == [
        (segment.source_ref, extracted.text[segment.start : segment.end]) for segment in segments
    ]
    assert [unit.kind for unit in units] == [segment.kind for segment in segments]
    # Export knows each entry by the same ID with the same text –
    # including empty paragraphs that could otherwise be replaced with empty text.
    targets = {
        unit.target_id: unit.text for unit in read_docx_text_units(_write(tmp_path, payload))
    }
    assert all(targets[unit.target_id] == unit.text for unit in units)
    assert set(targets) == {
        target for target, _ in iter_docx_paragraphs(Document(io.BytesIO(payload)))
    }
    assert len(targets) > len(units)


def test_separators_follow_the_table_layout() -> None:
    extracted = _extract(_tricky_docx())
    assert extracted.text == (
        "Vorne\nVerbunden\tC\nErste\nDritte\tIn1\tIn2\nHinten\tTab 𝔄\nKopf\nF1\tF2"
    )


# -- Preview copy ----------------------------------------------------------


def test_preview_has_one_anchor_per_unit_at_the_start_of_its_paragraph(
    corpus: dict[str, Path],  # noqa: F811
) -> None:
    path = corpus["brief.docx"]
    extracted = _extract(path.read_bytes())
    texts = [extracted.text[s.start : s.end] for s in docx_unit_segments(extracted)]
    preview = Document(io.BytesIO(build_docx_preview(path, texts)))

    units = docx_units(preview)
    assert [unit.text for unit in units] == texts
    for index, unit in enumerate(units):
        assert _anchors(unit.paragraph) == [f"unn_u{index}"]
        children = [
            child
            for child in unit.paragraph._p.iterchildren()  # noqa: SLF001
            if child.tag != qn("w:pPr")
        ]
        # Anchor first, then content: docx-preview inserts the span before.
        assert [child.tag for child in children[:2]] == [qn("w:bookmarkStart"), qn("w:bookmarkEnd")]
    # Empty paragraphs get no anchor – they are not a unit.
    empty = [
        paragraph
        for _, paragraph in iter_docx_paragraphs(preview)
        if not any(_anchors(paragraph)) and paragraph.text == ""
    ]
    assert empty


def test_preview_drops_links_relationships_fields_and_metadata(
    corpus: dict[str, Path],  # noqa: F811
    tmp_path: Path,
) -> None:
    document = Document(corpus["brief.docx"])
    # External linked image (A-28): no file in the package, only a reference.
    document.part.relate_to(
        "https://example.invalid/logo.png", RELATIONSHIP_TYPE.IMAGE, is_external=True
    )
    field = document.add_paragraph()
    for kind in ("begin", "end"):
        run = field.add_run()
        char = OxmlElement("w:fldChar")
        char.set(qn("w:fldCharType"), kind)
        run._r.append(char)  # noqa: SLF001
        if kind == "begin":
            instruction = OxmlElement("w:instrText")
            instruction.text = ' HYPERLINK "https://example.invalid" '
            field.add_run()._r.append(instruction)  # noqa: SLF001
    document.core_properties.author = "Maria Hofer"
    document.core_properties.title = "Vertrag Hofer"
    path = _write(tmp_path, _save(document))

    payload = build_docx_preview(path)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = set(archive.namelist())
        xml = {name: archive.read(name).decode("utf-8") for name in names if name.endswith(".rels")}
        body = archive.read("word/document.xml").decode("utf-8")
        core = archive.read("docProps/core.xml").decode("utf-8")
    assert "docProps/app.xml" not in names
    assert not any('TargetMode="External"' in rels for rels in xml.values())
    assert "example.invalid" not in body and "w:hyperlink" not in body
    assert "instrText" not in body and "fldChar" not in body
    assert "Hofer" not in core
    # The link text remains – it belongs to the unit.
    assert "shop@moebelhaus-beispiel.at" in body


def test_preview_bookmark_ids_do_not_collide(tmp_path: Path) -> None:
    document = Document()
    paragraph = document.add_paragraph("Mit Lesezeichen")
    for tag in ("w:bookmarkStart", "w:bookmarkEnd"):
        element = OxmlElement(tag)
        element.set(qn("w:id"), "7")
        if tag == "w:bookmarkStart":
            element.set(qn("w:name"), "_GoBack")
        paragraph._p.append(element)  # noqa: SLF001
    document.add_paragraph("Zweiter")
    preview = Document(io.BytesIO(build_docx_preview(_write(tmp_path, _save(document)))))
    ids = [
        element.get(qn("w:id"))
        for element in preview.element.body.iter(qn("w:bookmarkStart"))
        if element.get(qn("w:name")).startswith("unn_u")
    ]
    assert ids == ["8", "9"]


def test_preview_refuses_a_file_that_no_longer_matches(
    corpus: dict[str, Path],  # noqa: F811
) -> None:
    with pytest.raises(LayoutError):
        build_docx_preview(corpus["brief.docx"], ["Etwas ganz anderes"])


# -- Layout map ------------------------------------------------------------


def test_flow_layout_lists_units_in_utf16(tmp_path: Path) -> None:
    payload = _tricky_docx()
    path = _write(tmp_path, payload)
    extracted = _extract(payload)
    layout = build_layout("j", extracted, path, max_image_pixels=1)

    assert layout["kind"] == "flow" and layout["format"] == "docx"
    assert layout["document"] == "/jobs/j/layout/document"
    units = layout["units"]
    assert [unit["anchor"] for unit in units] == [f"unn_u{n}" for n in range(len(units))]
    # „Hinten\tTab 𝔄": 𝔄 counts double in UTF-16, everything after shifts.
    back = next(unit for unit in units if unit["anchor"] == "unn_u7")
    assert extracted.text[back["start"] : back["start"] + 6] == "Hinten"
    assert back["end"] - back["start"] == len("Hinten\tTab 𝔄") + 1
    head = units[8]
    assert head["start"] == back["end"] + 1


# -- Interface -----------------------------------------------------------


def test_api_serves_flow_layout_and_the_preview_from_memory(
    api: tuple[TestClient, PhaseOneServices],  # noqa: F811
    corpus: dict[str, Path],  # noqa: F811
) -> None:
    client, services = api
    job = _submit(client, corpus["brief.docx"], _DOCX)

    layout = client.get(f"/jobs/{job}/layout", headers=AUTH).json()
    assert layout["kind"] == "flow" and layout["document"] == f"/jobs/{job}/layout/document"
    findings = client.get(f"/jobs/{job}/findings", headers=AUTH).json()
    text = findings["text"]
    assert [text[unit["start"] : unit["end"]] for unit in layout["units"]][:3] == [
        "Max Mustermann",
        "Mustergasse 12/1/30",
        "1110 Wien",
    ]

    response = client.get(f"/jobs/{job}/layout/document", headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == _DOCX
    assert response.headers["cache-control"] == "no-store"
    preview = Document(io.BytesIO(response.content))
    assert len(docx_units(preview)) == len(layout["units"])
    size = services.job_memory().size
    assert client.get(f"/jobs/{job}/layout/document", headers=AUTH).content == response.content
    assert services.job_memory().size == size

    assert client.get(f"/jobs/{job}/layout/document").status_code == 401
    assert client.get(f"/jobs/{job}/pages/0?scale=1", headers=AUTH).status_code == 422
    # Removed from memory: preview cleared, state remains
    # it will be regenerated on the next request.
    assert client.delete(f"/jobs/{job}", headers=AUTH).status_code == 204
    assert services.job_memory().size == 0
    assert not services.jobs().is_loaded(job)
    assert client.get(f"/jobs/{job}/layout/document", headers=AUTH).content == response.content


def test_api_has_no_preview_for_fixed_pages(
    api: tuple[TestClient, PhaseOneServices],  # noqa: F811
    corpus: dict[str, Path],  # noqa: F811
) -> None:
    client, _ = api
    job = _submit(client, corpus["lieferschein.pdf"], "application/pdf")
    assert client.get(f"/jobs/{job}/layout/document", headers=AUTH).status_code == 422


# -- View = File ---------------------------------------------------------


def test_saved_docx_keeps_the_units_and_carries_each_replacement_in_its_unit(
    corpus: dict[str, Path],  # noqa: F811
    tmp_path: Path,
) -> None:
    path = corpus["brief.docx"]
    extracted = _extract(path.read_bytes())
    result = layout_corpus.result_for(extracted, layout_corpus.LETTER_PLACES)
    saved = tmp_path / "brief_geschuetzt.docx"
    export_processed_document(
        path,
        saved,
        document=extracted,
        source_text=extracted.text,
        processing=result,
    )

    segments = docx_unit_segments(extracted)
    expected = []
    for segment in segments:
        text = extracted.text[segment.start : segment.end]
        for finding in sorted(
            result.applied_findings, key=lambda item: item.original_start, reverse=True
        ):
            if segment.start <= finding.original_start and finding.original_end <= segment.end:
                code = result.text[finding.output_start : finding.output_end]
                local = finding.original_start - segment.start
                text = text[:local] + code + text[finding.original_end - segment.start :]
        expected.append(text)
    saved_units = docx_units(Document(saved))
    assert [unit.text for unit in saved_units] == expected
    assert [unit.target_id for unit in saved_units] == [segment.source_ref for segment in segments]
    # The preview of the saved file carries the same anchors.
    preview = Document(io.BytesIO(build_docx_preview(saved, expected)))
    assert [_anchors(unit.paragraph) for unit in docx_units(preview)] == [
        [f"unn_u{n}"] for n in range(len(segments))
    ]
    assert "<<PERSON_name0000>>" in expected[0]
