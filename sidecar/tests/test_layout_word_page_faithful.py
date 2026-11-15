"""Word page-faithful in the review view (layout plan, phase S).

- The resulting DOCX is built in memory the same way as when saving
  (``docx_preview_bytes``): same units, same text.
- ``side_units`` maps each unit to the text of a view page and each character to the source range;
replacement text overlays its position.
- ``paged_layout`` places the units in the PDF from the engine – across line and page breaks, header
and footer on each page – and reports what cannot be found.
- The engine is only taken from the bundle when the sidecar is frozen; it receives bytes, not paths,
and errors have no content.
- Endpoints provide map and page images only with the engine, only for Word, only from memory and
for the state they name.

The tests use a small test engine (Python, same protocol as
``office-engine``). When the real engine is built, the final test checks it
against the corpus letter.
"""

from __future__ import annotations

import io
import json
import stat
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import fitz
import pytest
from docx import Document
from fastapi.testclient import TestClient

from presidio_sidecar import office_engine as engine_module
from presidio_sidecar.document_workflow import docx_result_bytes, export_processed_document
from presidio_sidecar.extraction import DocumentExtractor
from presidio_sidecar.extraction.docx_units import docx_units
from presidio_sidecar.layout import docx_unit_segments
from presidio_sidecar.office_engine import (
    OfficeEngine,
    OfficeEngineError,
    docx_to_pdf,
    engine_executable,
    office_engine,
)
from presidio_sidecar.paged_docx import paged_layout, side_units
from presidio_sidecar.processing import (
    AppliedFinding,
    FindingKind,
    ProcessingMode,
    Reversibility,
)
from presidio_sidecar.services import PhaseOneServices
from test_layout_map import (  # noqa: F401
    AUTH,
    _submit,
    api,
    corpus,
    layout_corpus,
)

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="Die Test-Engine ist ein Skript mit Shebang."
)

_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MAX_PIXELS = 40_000_000

#: Set paragraphs linewise (20 characters per line), 6 lines per page, header
#: at top and footer at bottom on each page – enough to break paragraphs over lines and pages.
#: ``--drop TEXT`` removes a paragraph.
_PROBE_ENGINE = """\
#!{python}
import io, json, sys, textwrap
if sys.argv[1:] == ["info"]:
    print(json.dumps({{"engine": "probe", "version": "1", "protocol": {protocol}}}))
    sys.exit(0)
if sys.argv[1:2] != ["docx-to-pdf"]:
    sys.exit(2)
import fitz
from docx import Document
data = sys.stdin.buffer.read()
if data == b"kaputt":
    sys.stderr.write("Umwandlung gescheitert.")
    sys.exit(4)
document = Document(io.BytesIO(data))
paragraphs = [p.text for p in document.paragraphs]
for table in document.tables:
    for row in table.rows:
        paragraphs.extend(cell.text for cell in row.cells)
drop = {drop!r}
lines = []
for text in paragraphs:
    if text == drop:
        continue
    lines.extend(textwrap.wrap(text.replace("\\t", " "), 20) or [])
section = document.sections[0]
head = " ".join(p.text for p in section.header.paragraphs).replace("\\t", " ")
foot = " ".join(p.text for p in section.footer.paragraphs).replace("\\t", " ")
pdf = fitz.open()
for first in range(0, max(1, len(lines)), 6):
    page = pdf.new_page(width=300, height=400)
    page.insert_text((20, 30), head, fontsize=9)
    for k, line in enumerate(lines[first : first + 6]):
        page.insert_text((20, 120 + 22 * k), line, fontsize=11)
    page.insert_text((20, 385), foot, fontsize=9)
sys.stdout.buffer.write(pdf.tobytes())
"""


def _probe_engine(tmp_path: Path, *, drop: str = "", protocol: int = 1) -> Path:
    path = tmp_path / "unname-office-engine"
    path.write_text(
        _PROBE_ENGINE.format(python=sys.executable, drop=drop, protocol=protocol),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = _probe_engine(tmp_path)
    monkeypatch.setenv(engine_module.OFFICE_ENGINE_ENV, str(path))
    monkeypatch.setattr(engine_module, "bundled_assets_root", lambda: None)
    office_engine.cache_clear()
    yield path
    office_engine.cache_clear()


@pytest.fixture
def no_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(engine_module.OFFICE_ENGINE_ENV, str(tmp_path / "fehlt"))
    monkeypatch.setattr(engine_module, "bundled_assets_root", lambda: None)
    office_engine.cache_clear()
    yield
    office_engine.cache_clear()


def _letter() -> bytes:
    """A multi-page letter: name appears in body, table, header and footer."""

    document = Document()
    document.add_paragraph("MUSTERMANN wohnt in Wien.")
    document.add_paragraph(
        "Dieser Absatz ist so lang, dass ihn die Engine ueber mehrere Zeilen und "
        "ueber eine Seitengrenze bricht, bevor MUSTERMANN am Ende noch einmal kommt."
    )
    for n in range(4):
        document.add_paragraph(f"Fuellzeile {n}")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Kunde"
    table.cell(0, 1).text = "MUSTERMANN"
    section = document.sections[0]
    section.header.paragraphs[0].text = "Kopf MUSTERMANN"
    section.footer.paragraphs[0].text = "Fuss Seite"
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _extract(payload: bytes) -> Any:
    return DocumentExtractor().extract_bytes(payload, filename="brief.docx")


def _result(extracted: Any, value: str = "MUSTERMANN") -> Any:
    """Apply result in small scale: each occurrence replaced by a code."""

    text = extracted.text
    starts = [i for i in range(len(text)) if text.startswith(value, i)]
    output, cursor, applied = "", 0, []
    for n, start in enumerate(starts, 1):
        output += text[cursor:start]
        begin = len(output)
        output += f"<<PERSON_{n:08d}>>"
        cursor = start + len(value)
        applied.append(
            AppliedFinding(
                finding_id=f"f{n}",
                kind=FindingKind.CONFIRMED,
                entity_type="PERSON",
                source="ner",
                score=1.0,
                mode=ProcessingMode.TOKEN_DETERMINISTIC,
                reversibility=Reversibility.VAULT,
                original_start=start,
                original_end=cursor,
                output_start=begin,
                output_end=len(output),
            )
        )
    output += text[cursor:]
    return SimpleNamespace(text=output, applied_findings=tuple(applied))


def _write(tmp_path: Path, payload: bytes, name: str = "brief.docx") -> Path:
    path = tmp_path / name
    path.write_bytes(payload)
    return path


def _words_in(layout: dict[str, Any], start: int, end: int) -> list[list[Any]]:
    return [word for word in layout["words"] if word[5] < end and word[6] > start]


# -- DOCX result in memory -----------------------------------------


def test_result_docx_in_memory_is_what_saving_writes(tmp_path: Path) -> None:
    payload = _letter()
    path = _write(tmp_path, payload)
    extracted = _extract(payload)
    result = _result(extracted)

    in_memory = docx_result_bytes(
        path, document=extracted, source_text=extracted.text, processing=result
    )
    saved = tmp_path / "brief_geschuetzt.docx"
    export_processed_document(
        path, saved, document=extracted, source_text=extracted.text, processing=result
    )
    assert [unit.text for unit in docx_units(Document(io.BytesIO(in_memory)))] == [
        unit.text for unit in docx_units(Document(saved))
    ]
    assert "MUSTERMANN" not in "".join(
        unit.text for unit in docx_units(Document(io.BytesIO(in_memory)))
    )
    # Without apply state: cleaned copy with original text.
    original = docx_result_bytes(
        path, document=extracted, source_text=extracted.text, processing=None
    )
    assert [unit.text for unit in docx_units(Document(io.BytesIO(original)))] == [
        extracted.text[s.start : s.end] for s in docx_unit_segments(extracted)
    ]


# -- Units per page of the view -------------------------------------------


def test_side_units_carry_the_text_of_each_side_and_its_source_ranges(tmp_path: Path) -> None:
    payload = _letter()
    path = _write(tmp_path, payload)
    extracted = _extract(payload)
    result = _result(extracted)
    text = extracted.text

    original = side_units(extracted, text, "original", None)
    assert [unit.text for unit in original] == [
        text[s.start : s.end] for s in docx_unit_segments(extracted)
    ]
    assert all(
        unit.sources == tuple((unit.start + k, unit.start + k + 1) for k in range(len(unit.text)))
        for unit in original
    )
    assert [unit.story for unit in original][-2:] == [True, True]

    after = side_units(extracted, text, "result", result)
    saved = docx_result_bytes(path, document=extracted, source_text=text, processing=result)
    assert [unit.text for unit in after] == [
        unit.text for unit in docx_units(Document(io.BytesIO(saved)))
    ]
    for finding in result.applied_findings:
        code = result.text[finding.output_start : finding.output_end]
        unit = next(u for u in after if u.start <= finding.original_start < u.end)
        at = unit.text.index(code)
        covered = unit.sources[at : at + len(code)]
        # The replacement text covers exactly the same span, without gaps.
        assert covered[0][0] == finding.original_start
        assert covered[-1][1] == finding.original_end
        assert all(a[1] == b[0] for a, b in zip(covered, covered[1:], strict=False))


# -- Verorten im PDF ----------------------------------------------------------


def _convert(engine_path: Path, payload: bytes) -> bytes:
    return docx_to_pdf(OfficeEngine(engine_path, "probe", "1"), payload)


@pytest.mark.parametrize("side", ["original", "result"])
def test_paged_layout_finds_every_place_across_lines_pages_and_stories(
    tmp_path: Path, side: str
) -> None:
    payload = _letter()
    path = _write(tmp_path, payload)
    extracted = _extract(payload)
    result = _result(extracted)
    processing = result if side == "result" else None
    docx = docx_result_bytes(
        path, document=extracted, source_text=extracted.text, processing=processing
    )
    pdf = _convert(_probe_engine(tmp_path), docx)
    units = side_units(extracted, extracted.text, side, processing)  # type: ignore[arg-type]

    layout = paged_layout(pdf, units, extracted.text, max_image_pixels=MAX_PIXELS)

    assert len(layout["pages"]) >= 2
    assert layout["unplaced"] == []
    assert {page["unit"] for page in layout["pages"]} == {"pt"}
    for finding in result.applied_findings:
        words = _words_in(layout, finding.original_start, finding.original_end)
        assert words, finding.finding_id
        # In the result the code spans the full name.
        if side == "result":
            assert min(word[5] for word in words) == finding.original_start
            assert max(word[6] for word in words) == finding.original_end
    # Reading order: starts and ends increase, no overlap.
    words = layout["words"]
    assert all(a[6] <= b[5] for a, b in zip(words, words[1:], strict=False))
    # The long paragraph spans two pages.
    long = next(
        s
        for s in docx_unit_segments(extracted)
        if "Seitengrenze" in extracted.text[s.start : s.end]
    )
    assert len({word[0] for word in _words_in(layout, long.start, long.end)}) == 2
    # The header contains words only on the first page but appears on all pages.
    head = next(
        s for s in docx_unit_segments(extracted) if s.source_ref.startswith("part:word/header")
    )
    assert {word[0] for word in _words_in(layout, head.start, head.end)} == {0}
    # Each box is rendered on ink.
    document = fitz.open(stream=pdf, filetype="pdf")
    for word in words[:20]:
        rect = fitz.Rect(word[1:5])
        assert document[word[0]].get_textbox(rect).strip(), word


def test_paged_layout_names_units_it_cannot_find(tmp_path: Path) -> None:
    payload = _letter()
    path = _write(tmp_path, payload)
    extracted = _extract(payload)
    docx = docx_result_bytes(path, document=extracted, source_text=extracted.text, processing=None)
    pdf = _convert(_probe_engine(tmp_path, drop="Fuellzeile 2"), docx)
    units = side_units(extracted, extracted.text, "original", None)

    layout = paged_layout(pdf, units, extracted.text, max_image_pixels=MAX_PIXELS)

    missing = next(
        s
        for s in docx_unit_segments(extracted)
        if extracted.text[s.start : s.end] == "Fuellzeile 2"
    )
    assert layout["unplaced"] == [[missing.start, missing.end]]
    assert _words_in(layout, missing.start, missing.end) == []
    # Units after the missing line are still found.
    after = next(
        s
        for s in docx_unit_segments(extracted)
        if extracted.text[s.start : s.end] == "Fuellzeile 3"
    )
    assert _words_in(layout, after.start, after.end)


def test_paged_layout_reads_ligatures_and_soft_hyphens(tmp_path: Path) -> None:
    document = Document()
    document.add_paragraph("Pro\ufb01\u00adtabel")
    buffer = io.BytesIO()
    document.save(buffer)
    extracted = _extract(buffer.getvalue())
    pdf = fitz.open()
    page = pdf.new_page(width=300, height=200)
    page.insert_text((20, 100), "Profitabel", fontsize=11)
    units = side_units(extracted, extracted.text, "original", None)

    layout = paged_layout(pdf.tobytes(), units, extracted.text, max_image_pixels=MAX_PIXELS)

    assert layout["unplaced"] == []
    assert [word[5:7] for word in layout["words"]] == [[0, len(extracted.text)]]


# -- Engine ------------------------------------------------------------------


def test_engine_is_found_and_asked_once(probe: Path) -> None:
    engine = office_engine()
    assert engine == OfficeEngine(probe, "probe", "1")
    assert office_engine() is engine


def test_engine_with_another_protocol_does_not_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(engine_module.OFFICE_ENGINE_ENV, str(_probe_engine(tmp_path, protocol=2)))
    monkeypatch.setattr(engine_module, "bundled_assets_root", lambda: None)
    office_engine.cache_clear()
    try:
        assert office_engine() is None
    finally:
        office_engine.cache_clear()


def test_frozen_sidecar_takes_the_engine_only_from_the_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    monkeypatch.setattr(engine_module, "bundled_assets_root", lambda: assets)
    monkeypatch.setattr(engine_module, "is_frozen", lambda: True)
    monkeypatch.setenv(engine_module.OFFICE_ENGINE_ENV, str(_probe_engine(tmp_path)))
    assert engine_executable() is None

    bundled = assets / "office-engine" / "unname-office-engine"
    bundled.parent.mkdir()
    bundled.write_bytes(b"")
    assert engine_executable() == bundled


def test_engine_failure_says_nothing_about_the_document(probe: Path) -> None:
    engine = OfficeEngine(probe, "probe", "1")
    with pytest.raises(OfficeEngineError) as raised:
        docx_to_pdf(engine, b"kaputt")
    assert "Code 4" in str(raised.value)
    assert "kaputt" not in str(raised.value)


def test_engine_output_must_be_a_pdf(tmp_path: Path) -> None:
    liar = tmp_path / "unname-office-engine"
    liar.write_text(f"#!{sys.executable}\nimport sys\nsys.stdout.write('kein pdf')\n")
    liar.chmod(liar.stat().st_mode | stat.S_IXUSR)
    with pytest.raises(OfficeEngineError, match="kein brauchbares PDF"):
        docx_to_pdf(OfficeEngine(liar, "x", "1"), b"docx")


def test_engine_runs_without_the_session_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = tmp_path / "unname-office-engine"
    spy.write_text(
        textwrap.dedent(
            f"""\
            #!{sys.executable}
            import json, os, sys
            sys.stdin.buffer.read()
            sys.stdout.write("%PDF-" + json.dumps(sorted(os.environ)))
            """
        )
    )
    spy.chmod(spy.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PRESIDIO_TOKEN_GEHEIM", "x")
    output = docx_to_pdf(OfficeEngine(spy, "x", "1"), b"docx")
    keys = json.loads(output[len(b"%PDF-") :])
    assert "PRESIDIO_TOKEN_GEHEIM" not in keys
    assert set(keys) <= set(engine_module._ENVIRONMENT_KEYS) | {
        "__CF_USER_TEXT_ENCODING",
        "LC_CTYPE",
    }


# -- Endpoints ---------------------------------------------------------------


def _apply(client: TestClient, job: str) -> dict[str, Any]:
    masking = {"method": "reversible", "style": "codes", "categories": ["names"]}
    response = client.post(f"/jobs/{job}/apply", headers=AUTH, json={"masking": masking})
    assert response.status_code == 200, response.text
    return response.json()


def test_flow_layout_offers_paged_only_with_an_engine(
    api: tuple[TestClient, PhaseOneServices],  # noqa: F811
    tmp_path: Path,
    no_engine: None,
) -> None:
    client, _ = api
    job = _submit(client, _write(tmp_path, _letter()), _DOCX)
    layout = client.get(f"/jobs/{job}/layout", headers=AUTH).json()
    assert layout["kind"] == "flow" and layout["paged"] is False
    assert client.get(f"/jobs/{job}/layout/paged?side=original", headers=AUTH).status_code == 404


def test_api_serves_paged_pages_for_original_and_result(
    api: tuple[TestClient, PhaseOneServices],  # noqa: F811
    tmp_path: Path,
    probe: Path,
) -> None:
    client, services = api
    job = _submit(client, _write(tmp_path, _letter()), _DOCX)
    layout = client.get(f"/jobs/{job}/layout", headers=AUTH).json()
    assert layout["paged"] is True
    text = client.get(f"/jobs/{job}/findings", headers=AUTH).json()["text"]

    original = client.get(f"/jobs/{job}/layout/paged?side=original", headers=AUTH)
    assert original.status_code == 200, original.text
    assert original.headers["cache-control"] == "no-store"
    body = original.json()
    assert body["side"] == "original" and body["revision"] == 0
    assert body["engine"] == {"name": "probe", "version": "1"}
    assert body["unplaced"] == []
    names = [i for i in range(len(text)) if text.startswith("MUSTERMANN", i)]
    assert names and all(_words_in(body, i, i + 10) for i in names)

    # No result before the first apply.
    assert client.get(f"/jobs/{job}/layout/paged", headers=AUTH).status_code == 409
    applied = _apply(client, job)
    result = client.get(f"/jobs/{job}/layout/paged?side=result", headers=AUTH).json()
    assert result["revision"] == services.jobs().get_processing_snapshot(job).revision
    assert result["unplaced"] == []
    for finding in applied["applied_findings"]:
        assert _words_in(result, finding["original_start"], finding["original_end"])

    page = client.get(
        f"/jobs/{job}/layout/paged/0?side=result&revision={result['revision']}&scale=1",
        headers=AUTH,
    )
    assert page.status_code == 200 and page.headers["content-type"] == "image/png"
    assert page.headers["cache-control"] == "no-store"
    width = result["pages"][0]["width"]
    assert fitz.Pixmap(page.content).width == round(width)

    # New state: the old page image stays cached, a foreign state does not.
    _apply(client, job)
    stale = client.get(
        f"/jobs/{job}/layout/paged/1?side=result&revision={result['revision']}&scale=1",
        headers=AUTH,
    )
    assert stale.status_code in {200, 409}
    assert (
        client.get(
            f"/jobs/{job}/layout/paged/0?side=result&revision=9999&scale=1", headers=AUTH
        ).status_code
        == 409
    )
    assert client.get(f"/jobs/{job}/layout/paged?side=original").status_code == 401
    assert client.delete(f"/jobs/{job}", headers=AUTH).status_code == 204
    assert services.job_memory().size == 0


def test_paged_view_is_only_for_word(
    api: tuple[TestClient, PhaseOneServices],  # noqa: F811
    corpus: dict[str, Path],  # noqa: F811
    probe: Path,
) -> None:
    client, _ = api
    job = _submit(client, corpus["lieferschein.pdf"], "application/pdf")
    assert client.get(f"/jobs/{job}/layout", headers=AUTH).json()["paged"] is False
    assert client.get(f"/jobs/{job}/layout/paged?side=original", headers=AUTH).status_code == 422


# -- Real Engine ------------------------------------------------------------


def _built_engine() -> Path | None:
    from presidio_sidecar.resources import repository_root

    path = repository_root() / "office-engine" / "target" / "release" / "unname-office-engine"
    return path if path.is_file() else None


@pytest.mark.skipif(_built_engine() is None, reason="office-engine ist nicht gebaut.")
def test_built_engine_places_every_code_of_the_corpus_letter(
    corpus: dict[str, Path],  # noqa: F811
) -> None:
    path = corpus["brief.docx"]
    extracted = _extract(path.read_bytes())
    result = layout_corpus.result_for(extracted, layout_corpus.LETTER_PLACES)
    docx = docx_result_bytes(
        path, document=extracted, source_text=extracted.text, processing=result
    )
    engine = OfficeEngine(_built_engine(), "built", "?")  # type: ignore[arg-type]
    pdf = docx_to_pdf(engine, docx)
    units = side_units(extracted, extracted.text, "result", result)

    layout = paged_layout(pdf, units, extracted.text, max_image_pixels=MAX_PIXELS)

    for finding in result.applied_findings:
        assert _words_in(layout, finding.original_start, finding.original_end), finding.finding_id
