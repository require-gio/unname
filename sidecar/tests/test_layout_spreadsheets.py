"""Excel and CSV in the review view (layout plan).

- The table model tracks column widths, row heights, merged cells,
  frozen panes, hidden rows/columns, styles and number formats; cells are
  provided sheet-by-sheet.
- Each extracted cell appears exactly once in the model with its UTF-16
  range in the source, including sheet names.
- Cell comparison: the saved file stores each replacement in the same
  cell the view shows; all other cells stay unchanged.
- Large sheets (10 000 rows) are loaded in chunks, fast enough for
  scrolling; beyond the cell range the file is shown as text.
"""

from __future__ import annotations

import csv
import io
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from presidio_sidecar import grid_model
from presidio_sidecar.document_workflow import export_processed_document
from presidio_sidecar.extraction import DocumentExtractor, SegmentKind
from presidio_sidecar.grid_model import build_grid, format_date, format_number, grid_rows
from presidio_sidecar.layout import build_layout
from presidio_sidecar.services import PhaseOneServices
from test_layout_map import (  # noqa: F401
    AUTH,
    _submit,
    api,
    corpus,
    layout_corpus,
)

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: Sample positions – same as in the view fixtures (``grid.spec.ts``).
SPANS = layout_corpus.GRID_PLACES


def _extract(path: Path) -> Any:
    return DocumentExtractor().extract_bytes(path.read_bytes(), filename=path.name)


def _rows(grid: Any, sheet: int, language: str = "de") -> dict[tuple[int, int], Any]:
    cells = grid_rows(grid.cells[sheet], 0, 10**6, language)  # type: ignore[arg-type]
    return {(cell[0], cell[1]): cell for cell in cells}


# -- Model ------------------------------------------------------------------


def test_xlsx_grid_has_sizes_merges_and_formats(corpus: dict[str, Path]) -> None:  # noqa: F811
    path = corpus["kunden.xlsx"]
    grid = build_grid("j", _extract(path), path)
    assert grid is not None
    customers_sheet, notes_sheet = grid.layout["sheets"]

    assert grid.layout["kind"] == "grid" and grid.layout["format"] == "xlsx"
    assert (customers_sheet["name"], customers_sheet["rows"], customers_sheet["cols"]) == (
        "Kunden",
        5,
        7,
    )
    assert customers_sheet["frozen"] == [2, 0]
    assert customers_sheet["merges"] == [[0, 0, 0, 5]]
    assert customers_sheet["col_widths"][:3] == [64, 173, 64]  # B: 24 characters
    assert customers_sheet["hidden_cols"] == [6]
    assert customers_sheet["row_heights"] == {0: 32}  # 24 pt
    assert customers_sheet["conditional"] is True
    assert customers_sheet["cells"] == "/jobs/j/layout/sheets/0"

    cells = _rows(grid, 0)
    styles = grid.layout["styles"]
    assert styles[0] == {}
    assert styles[cells[(0, 0)][3]] == {"bold": True, "size": 14.0, "align": "center"}
    assert styles[cells[(1, 1)][3]] == {
        "bold": True,
        "fill": "#F2F2F2",
        "border": {"bottom": ["thin", "#1F1846"]},
    }
    # Numbers, percentages and dates as in Excel, right-aligned.
    assert [cells[(2, column)][2] for column in range(6)] == [
        "1",
        "Max Mustermann",
        "Wien",
        "1.234,50 €",
        "12,5%",
        "01.03.2024",
    ]
    assert styles[cells[(2, 3)][3]] == {"align": "right"}
    english = _rows(grid, 0, "en")
    assert english[(4, 3)][2] == "15,890.75 €" and english[(3, 4)][2] == "3.1%"

    # Second sheet: merged cell with wrap; formula without stored value.
    assert notes_sheet["merges"] == [[0, 0, 1, 2]]
    notes = _rows(grid, 1)
    assert styles[notes[(0, 0)][3]] == {"valign": "top", "wrap": True}
    assert notes[(3, 0)][2] == '="Kunde: "&Kunden!B3'


def test_every_extracted_cell_and_sheet_name_has_its_range(
    corpus: dict[str, Path],  # noqa: F811
) -> None:
    path = corpus["kunden.xlsx"]
    document = _extract(path)
    grid = build_grid("j", document, path)
    assert grid is not None
    by_range: dict[tuple[int, int], tuple[str, int, int]] = {}
    for sheet in grid.layout["sheets"]:
        for row, column, _, _, start, end in grid_rows(grid.cells[sheet["index"]], 0, 10**6, "de"):
            if start >= 0:
                assert (start, end) not in by_range
                by_range[(start, end)] = (sheet["name"], row, column)

    from openpyxl.utils import get_column_letter

    titles = [s for s in document.segments if s.kind is SegmentKind.XLSX_SHEET_TITLE]
    assert [sheet["name_range"] for sheet in grid.layout["sheets"]] == [
        [s.start, s.end] for s in titles
    ]
    cells = [
        s for s in document.segments if s.kind in {SegmentKind.XLSX_CELL, SegmentKind.XLSX_FORMULA}
    ]
    assert len(cells) == len(by_range)
    for segment in cells:
        name, row, column = by_range[(segment.start, segment.end)]
        assert segment.source_ref == f"{name}!{get_column_letter(column + 1)}{row + 1}"


def test_csv_grid_shows_fields_without_quotes(tmp_path: Path) -> None:
    path = tmp_path / "liste.csv"
    path.write_text(
        'Name;Ort;Betrag\n"Hofer, Maria";Wien;12,50\n"Sag ""Hallo""";"Graz";3\n',
        encoding="utf-8",
    )
    document = _extract(path)
    grid = build_grid("j", document, path)
    assert grid is not None
    (sheet,) = grid.layout["sheets"]
    assert (sheet["rows"], sheet["cols"], sheet["name"]) == (3, 3, "liste.csv")
    cells = _rows(grid, 0)
    hofer = cells[(1, 0)]
    assert hofer[2] == "Hofer, Maria"
    # The range is inside the quotes: displayed text equals source text.
    assert document.text[hofer[4] : hofer[5]] == "Hofer, Maria"
    graz = cells[(2, 1)]
    assert graz[2] == "Graz" and document.text[graz[4] : graz[5]] == "Graz"
    # Double quotes: displayed text is unescaped, range covers the whole cell.
    hello_cell = cells[(2, 0)]
    assert (
        hello_cell[2] == 'Sag "Hallo"'
        and document.text[hello_cell[4] : hello_cell[5]] == '"Sag ""Hallo"""'
    )
    styles = grid.layout["styles"]
    assert styles[cells[(1, 2)][3]] == {"align": "right"}
    assert styles[cells[(1, 1)][3]] == {}
    assert sheet["col_widths"][0] > sheet["col_widths"][2]


def test_layout_map_is_grid_for_xlsx_and_csv(corpus: dict[str, Path]) -> None:  # noqa: F811
    for name in ("kunden.xlsx", "kunden.csv"):
        path = corpus[name]
        layout = build_layout("j", _extract(path), path, max_image_pixels=10**8)
        assert layout["kind"] == "grid"
        assert "cells" not in layout and all(isinstance(s["cells"], str) for s in layout["sheets"])


def test_sheets_over_the_cell_limit_fall_back_to_text(
    corpus: dict[str, Path],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(grid_model, "GRID_CELL_LIMIT", 10)
    for name in ("kunden.xlsx", "kunden.csv"):
        path = corpus[name]
        layout = build_layout("j", _extract(path), path, max_image_pixels=10**8)
        assert layout == {"job_id": "j", "kind": "text", "offset_encoding": "utf-16"}


# -- number formats -----------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "number_format", "de", "en"),
    [
        (1234.5, "General", "1234,5", "1234.5"),
        (3.0, "General", "3", "3"),
        (1234.5, "#,##0.00", "1.234,50", "1,234.50"),
        (1234567.891, "#,##0", "1.234.568", "1,234,568"),
        (0.5, "0%", "50%", "50%"),
        (0.125, "0.0%", "12,5%", "12.5%"),
        (-12.5, "#,##0.00;[Red]-#,##0.00", "-12,50", "-12.50"),
        (-12.5, "#,##0.00;(#,##0.00)", "(12,50)", "(12.50)"),
        (0.0, '0.00;-0.00;"–"', "–", "–"),
        (7.0, "000", "007", "007"),
        (0.25, "#.##", ",25", ".25"),
        (19.99, '"EUR "#,##0.00', "EUR 19,99", "EUR 19.99"),
        (1500.0, "#,##0.00 [$€-de-AT]", "1.500,00 €", "1,500.00 €"),
        (1500.0, "[$$-409]#,##0.00", "$1.500,00", "$1,500.00"),
        (2500000.0, '#,##0.0,,"M"', "2,5M", "2.5M"),
        (12345.678, "0.00E+00", "12345,678", "12345.678"),
    ],
)
def test_number_formats(value: float, number_format: str, de: str, en: str) -> None:
    assert format_number(value, number_format, "de") == de
    assert format_number(value, number_format, "en") == en


@pytest.mark.parametrize(
    ("number_format", "de", "en"),
    [
        ("mm-dd-yy", "05.03.2024", "3/5/2024"),
        ("DD.MM.YYYY", "05.03.2024", "05.03.2024"),
        ("d. mmmm yyyy", "5. März 2024", "5. March 2024"),
        ("dddd, d.m.yy", "Dienstag, 5.3.24", "Tuesday, 5.3.24"),
        ("yyyy-mm-dd hh:mm:ss", "2024-03-05 14:07:09", "2024-03-05 14:07:09"),
        ("h:mm AM/PM", "2:07 PM", "2:07 PM"),
        ("[$-407]mmm yy", "Mär 24", "Mar 24"),
    ],
)
def test_date_formats(number_format: str, de: str, en: str) -> None:
    moment = datetime(2024, 3, 5, 14, 7, 9)
    assert format_date(moment, number_format, "de") == de
    assert format_date(moment, number_format, "en") == en


# -- view = file -----------------------------------------------------------


def _expected(text: str, start: int, end: int, document_text: str) -> str:
    """Cell text after inspection: each position in the range is replaced by its code."""

    result = text
    for span in sorted(SPANS, key=lambda s: -layout_corpus.place_range(document_text, s)[0]):
        if span["id"] == "f_graz" and "Graz" not in text:
            continue
        s, e = layout_corpus.place_range(document_text, span)
        if start <= s and e <= end:
            result = result[: s - start] + layout_corpus.smoke_code(span) + result[e - start :]
    return result


def test_saved_xlsx_carries_each_replacement_in_the_cell_the_grid_shows(
    corpus: dict[str, Path],  # noqa: F811
    tmp_path: Path,
) -> None:
    path = corpus["kunden.xlsx"]
    document = _extract(path)
    grid = build_grid("j", document, path)
    assert grid is not None
    target = tmp_path / "kunden_geschuetzt.xlsx"
    export_processed_document(
        path,
        target,
        document=document,
        source_text=document.text,
        processing=layout_corpus.result_for(document, SPANS),
    )
    saved = load_workbook(target)
    changed = 0
    for sheet in grid.layout["sheets"]:
        worksheet = saved.worksheets[sheet["index"]]
        for row, column, _, _, start, end in grid_rows(grid.cells[sheet["index"]], 0, 10**6, "de"):
            if start < 0:
                continue
            source = document.text[start:end]
            expected = _expected(source, start, end, document.text)
            value = worksheet.cell(row=row + 1, column=column + 1).value
            original = load_workbook(path).worksheets[sheet["index"]].cell(row + 1, column + 1)
            if expected == source:
                assert value == original.value, (sheet["name"], row, column)
            else:
                changed += 1
                assert value == expected, (sheet["name"], row, column)
    assert changed == 4


def test_saved_csv_carries_each_replacement_in_the_cell_the_grid_shows(
    corpus: dict[str, Path],  # noqa: F811
    tmp_path: Path,
) -> None:
    path = corpus["kunden.csv"]
    document = _extract(path)
    grid = build_grid("j", document, path)
    assert grid is not None
    spans = SPANS[:3]
    target = tmp_path / "kunden_geschuetzt.csv"
    export_processed_document(
        path,
        target,
        document=document,
        source_text=document.text,
        processing=layout_corpus.result_for(document, spans),
    )
    rows = list(csv.reader(io.StringIO(target.read_text(encoding="utf-8")), delimiter=";"))
    codes = {str(s["value"]): layout_corpus.smoke_code(s) for s in spans}
    for row, column, display, _, start, end in grid_rows(grid.cells[0], 0, 10**6, "de"):
        assert document.text[start:end] == display
        assert rows[row][column] == codes.get(display, display)


# -- Interface -----------------------------------------------------------


def test_api_serves_grid_and_rows(
    api: tuple[TestClient, PhaseOneServices],  # noqa: F811
    corpus: dict[str, Path],  # noqa: F811
) -> None:
    client, services = api
    job = _submit(client, corpus["kunden.xlsx"], _XLSX)

    response = client.get(f"/jobs/{job}/layout", headers=AUTH)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    layout = response.json()
    assert layout["kind"] == "grid" and len(layout["sheets"]) == 2
    assert layout["sheets"][0]["cells"] == f"/jobs/{job}/layout/sheets/0"
    assert layout["styles"][0]["align"] == "left"

    rows = client.get(f"/jobs/{job}/layout/sheets/0?rows=2-3", headers=AUTH)
    assert rows.status_code == 200 and rows.headers["cache-control"] == "no-store"
    body = rows.json()
    assert (body["first"], body["last"]) == (2, 3)
    assert {cell[0] for cell in body["cells"]} == {2, 3}
    assert [cell[2] for cell in body["cells"] if cell[:2] == [2, 3]] == ["1.234,50 €"]
    english = client.get(f"/jobs/{job}/layout/sheets/0?rows=2-2&lang=en", headers=AUTH).json()
    assert [cell[2] for cell in english["cells"] if cell[1] == 3] == ["1,234.50 €"]
    # After the last sheet end: empty but no error.
    tail = client.get(f"/jobs/{job}/layout/sheets/1?rows=400-900", headers=AUTH).json()
    assert tail["cells"] == []

    # Shifted: blocks are recomputed.
    services.job_memory().forget(job)
    again = client.get(f"/jobs/{job}/layout/sheets/0?rows=2-3", headers=AUTH).json()
    assert again["cells"] == body["cells"]

    assert client.get(f"/jobs/{job}/layout/sheets/0").status_code == 401
    assert client.get(f"/jobs/{job}/layout/sheets/7", headers=AUTH).status_code == 404
    assert client.get(f"/jobs/{job}/layout/sheets/0?rows=x", headers=AUTH).status_code == 422
    assert client.get(f"/jobs/{job}/layout/sheets/0?rows=5-1", headers=AUTH).status_code == 422
    too_many = client.get(f"/jobs/{job}/layout/sheets/0?rows=0-5000", headers=AUTH)
    assert too_many.status_code == 422
    assert client.get(f"/jobs/{job}/layout/sheets/0?lang=fr", headers=AUTH).status_code == 422
    assert client.get("/jobs/nope/layout/sheets/0", headers=AUTH).status_code == 404


def test_api_rows_need_a_grid(
    api: tuple[TestClient, PhaseOneServices],  # noqa: F811
    corpus: dict[str, Path],  # noqa: F811
) -> None:
    client, _ = api
    job = _submit(client, corpus["lieferschein.pdf"], "application/pdf")
    assert client.get(f"/jobs/{job}/layout/sheets/0", headers=AUTH).status_code == 422


def _large_xlsx(path: Path, rows: int) -> None:
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("Gross")
    sheet.append(["Nr", "Name", "Ort", "Betrag", "Datum", "Notiz", "Kennung", "Status"])
    for row in range(1, rows):
        sheet.append(
            [
                row,
                f"Kunde {row}",
                "Wien",
                row * 1.25,
                datetime(2024, 1, 1 + row % 28),
                "offen",
                f"K-{row:05d}",
                "aktiv",
            ]
        )
    workbook.save(path)


def test_large_sheet_comes_in_fast_slices(
    api: tuple[TestClient, PhaseOneServices],  # noqa: F811
    tmp_path: Path,
) -> None:
    client, _ = api
    path = tmp_path / "gross.xlsx"
    _large_xlsx(path, 10_000)
    job = _submit(client, path, _XLSX)

    started = time.perf_counter()
    layout = client.get(f"/jobs/{job}/layout", headers=AUTH).json()
    built = time.perf_counter() - started
    (sheet,) = layout["sheets"]
    assert (sheet["rows"], sheet["cols"]) == (10_000, 8)

    slices = []
    for first in range(0, 10_000, 500):
        started = time.perf_counter()
        body = client.get(
            f"/jobs/{job}/layout/sheets/0?rows={first}-{first + 499}", headers=AUTH
        ).json()
        slices.append(time.perf_counter() - started)
        assert len(body["cells"]) == 500 * 8
        assert body["cells"][0][:2] == [first, 0]
    print(
        f"\n[leistung] Raster 10 000 Zeilen: Modell {built:.2f} s, "
        f"Ausschnitt max {max(slices) * 1000:.0f} ms"
    )
    assert built < 15
    # A slice must arrive in sync with scrolling. One may land on a garbage
    # collection or a busy CI runner: once 251 ms while the others took 16 ms.
    *others, slowest = sorted(slices)
    assert others[-1] < 0.25
    assert slowest < 1.0
