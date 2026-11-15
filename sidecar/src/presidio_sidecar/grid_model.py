"""Table model for Excel and CSV.

The review view displays tables as a grid: column widths, row heights,
merged cells, frozen panes, fonts, fills, borders, and number formats
from the file itself. For each cell, it also stores its range in the
extraction source text – the view inserts segments there, and the export
(``export_xlsx``: exactly one cell per replacement) modifies it.

- **Layout map** (``kind = "grid"``): per sheet, the dimensions and
  features, plus deduplicated styles. Cells are not included directly,
  but retrieved sheet- and row-wise via ``GET /jobs/{id}/layout/sheets/{i}?rows=a-b``.
- **Display:** the formatted value (``data_only``, i.e., the last
  calculated value of a formula). The view shows the extracted text
  as soon as a cell has a value – this is the text the export
  modifies. Formatting happens only on retrieval, in the view's language.
- **CSV:** the same model from the ``CSV_CELL`` segments, without styles;
  a field's quotes do not belong to the display.

For more than ``GRID_CELL_LIMIT`` cells per sheet, no model is created;
the file then appears as text.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Final, Literal

from .extraction import DocumentFormat, ExtractedDocument, SegmentKind
from .layout import LayoutError, Utf16Offsets

#: Maximum number of cells (with value or style) per sheet.
GRID_CELL_LIMIT: Final = 200_000
#: Rows per block in the cache; requests are rounded to blocks.
GRID_BLOCK_ROWS: Final = 500
#: Width of an unspecified column (8.43 characters) and height of a row (15 pt), in px.
DEFAULT_COLUMN_PX: Final = 64
DEFAULT_ROW_PX: Final = 20

Language = Literal["de", "en"]
#: ``[Zeile, Spalte, Art, Wert, Zahlenformat, Stil, start, end]`` – roh, vor dem Formatieren.
RawCell = list[Any]
#: ``[line, column, view, style, start, end]`` – this is how a cell extends.
GridCell = tuple[int, int, str, int, int, int]

_GRID_FORMATS: Final = {DocumentFormat.XLSX, DocumentFormat.CSV}
_XLSX_CELL_KINDS: Final = {SegmentKind.XLSX_CELL, SegmentKind.XLSX_FORMULA}
_SAFE_FONT: Final = re.compile(r"^[A-Za-z0-9 \-]{1,48}$")
_NUMBER_LIKE: Final = re.compile(r"^[+-]?(\d{1,3}([.,'  ]\d{3})*|\d+)([.,]\d+)?\s?%?$")


@dataclass(frozen=True, slots=True)
class GridModel:
    """Layout map (without cells) and the raw cells per sheet, sorted by row."""

    layout: dict[str, Any]
    cells: tuple[tuple[RawCell, ...], ...]


def is_grid_document(document: ExtractedDocument | None) -> bool:
    return document is not None and document.format in _GRID_FORMATS


def build_grid(job_id: str, document: ExtractedDocument, source_path: Path) -> GridModel | None:
    """Table model for an XLSX or CSV; ``None`` for sheets that are too large."""

    if document.format is DocumentFormat.XLSX:
        return _xlsx_grid(job_id, document, source_path)
    if document.format is DocumentFormat.CSV:
        return _csv_grid(job_id, document)
    raise LayoutError("Diese Datei ist keine Tabelle.")


def grid_rows(
    model_cells: Sequence[RawCell],
    first: int,
    last: int,
    language: Language,
) -> list[GridCell]:
    """Cells of rows ``first..last`` (inclusive), formatted for the view."""

    return [
        (row, column, display_value(kind, value, number_format, language), style, start, end)
        for row, column, kind, value, number_format, style, start, end in model_cells
        if first <= row <= last
    ]


def cell_blocks(cells: Sequence[RawCell]) -> dict[int, list[RawCell]]:
    """Cells grouped by block (``GRID_BLOCK_ROWS`` rows) – as they are stored in the cache."""

    blocks: dict[int, list[RawCell]] = {}
    for cell in cells:
        blocks.setdefault(cell[0] // GRID_BLOCK_ROWS, []).append(cell)
    return blocks


# -- Stile -------------------------------------------------------------------


class _Styles:
    """Deduplicated styles; index 0 is the empty default style."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = [{}]
        self._index: dict[str, int] = {"{}": 0}

    def add(self, style: dict[str, Any]) -> int:
        key = repr(sorted(style.items()))
        if not style:
            return 0
        index = self._index.get(key)
        if index is None:
            index = len(self.items)
            self.items.append(style)
            self._index[key] = index
        return index


def _xlsx_style(cell: Any, kind: str) -> dict[str, Any]:
    style: dict[str, Any] = {}
    font = cell.font
    if font is not None:
        if font.b:
            style["bold"] = True
        if font.i:
            style["italic"] = True
        if font.u and font.u != "none":
            style["underline"] = True
        if font.sz and float(font.sz) != 11.0:
            style["size"] = round(float(font.sz), 1)
        color = _color(font.color)
        if color and color != "#000000":
            style["color"] = color
        name = str(font.name or "")
        if name and name != "Calibri" and _SAFE_FONT.match(name):
            style["font"] = name
    fill = cell.fill
    if fill is not None and getattr(fill, "fill_type", None) == "solid":
        color = _color(fill.fgColor)
        if color and color != "#FFFFFF":
            style["fill"] = color
    border: dict[str, list[str]] = {}
    for side in ("top", "right", "bottom", "left"):
        line = getattr(cell.border, side, None) if cell.border is not None else None
        if line is not None and line.style:
            border[side] = [str(line.style), _color(line.color) or "#000000"]
    if border:
        style["border"] = border
    alignment = cell.alignment
    horizontal = getattr(alignment, "horizontal", None) or "general"
    align = _ALIGN.get(horizontal)
    if align is None:
        # "Standard" as in Excel: numbers and dates right-aligned, booleans centered.
        align = {"n": "right", "d": "right", "b": "center"}.get(kind, "left")
    if align != "left":
        style["align"] = align
    vertical = getattr(alignment, "vertical", None) or "bottom"
    valign = {"top": "top", "center": "middle", "distributed": "middle", "justify": "top"}.get(
        vertical, "bottom"
    )
    if valign != "bottom":
        style["valign"] = valign
    if getattr(alignment, "wrap_text", False):
        style["wrap"] = True
    return style


_ALIGN: Final = {
    "left": "left",
    "center": "center",
    "centerContinuous": "center",
    "right": "right",
    "justify": "justify",
    "distributed": "justify",
    "fill": "left",
}


def _color(color: Any) -> str | None:
    """Color as ``#RRGGBB``; the model does not support theme colors (``theme``)."""

    if color is None:
        return None
    kind = getattr(color, "type", None)
    value: Any = None
    if kind == "rgb":
        value = color.rgb
    elif kind == "indexed":
        from openpyxl.styles.colors import COLOR_INDEX

        index = int(color.indexed)
        value = COLOR_INDEX[index] if 0 <= index < len(COLOR_INDEX) else None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9A-Fa-f]{6,8}", value):
        return None
    return "#" + value[-6:].upper()


# -- Excel -------------------------------------------------------------------


def _xlsx_grid(job_id: str, document: ExtractedDocument, source_path: Path) -> GridModel | None:
    try:
        from openpyxl import load_workbook

        # Normal mode: merged cells and dimensions are missing in ``read_only`` mode.
        workbook = load_workbook(source_path, read_only=False, data_only=True, keep_links=False)
    except Exception as error:
        raise LayoutError("Die XLSX-Datei laesst sich nicht mehr oeffnen.") from error

    offsets = Utf16Offsets(document.text)
    titles: list[tuple[int, int] | None] = []
    #: cell address → (extracted text, UTF-16 range).
    ranges: dict[str, tuple[str, int, int]] = {}
    for segment in document.segments:
        if segment.kind is SegmentKind.XLSX_SHEET_TITLE:
            titles.append((offsets(segment.start), offsets(segment.end)))
        elif segment.kind in _XLSX_CELL_KINDS:
            ranges[segment.source_ref] = (
                document.text[segment.start : segment.end],
                offsets(segment.start),
                offsets(segment.end),
            )

    try:
        worksheets = list(workbook.worksheets)
        if len(worksheets) != len(titles):
            raise LayoutError("Die XLSX-Datei passt nicht zur Extraktion.")
        styles = _Styles()
        sheets: list[dict[str, Any]] = []
        all_cells: list[tuple[RawCell, ...]] = []
        for index, worksheet in enumerate(worksheets):
            sheet = _xlsx_sheet(job_id, index, worksheet, titles[index], ranges, styles)
            if sheet is None:
                return None
            meta, cells = sheet
            sheets.append(meta)
            all_cells.append(cells)
    finally:
        workbook.close()

    layout = {
        "job_id": job_id,
        "kind": "grid",
        "offset_encoding": "utf-16",
        "format": "xlsx",
        "sheets": sheets,
        "styles": styles.items,
    }
    return GridModel(layout=layout, cells=tuple(all_cells))


def _xlsx_sheet(
    job_id: str,
    index: int,
    worksheet: Any,
    title_range: tuple[int, int] | None,
    ranges: dict[str, tuple[str, int, int]],
    styles: _Styles,
) -> tuple[dict[str, Any], tuple[RawCell, ...]] | None:
    stored = getattr(worksheet, "_cells", {})
    if len(stored) > GRID_CELL_LIMIT:
        return None
    merges = [
        [area.min_row - 1, area.min_col - 1, area.max_row - 1, area.max_col - 1]
        for area in worksheet.merged_cells.ranges
    ]
    cells: list[RawCell] = []
    rows = 0
    cols = 0
    for (row, column), cell in sorted(stored.items()):
        kind, value, number_format = _xlsx_value(cell)
        source, start, end = ranges.get(f"{worksheet.title}!{cell.coordinate}", ("", -1, -1))
        if kind == "" and source:
            # A formula without a stored value: the view displays the formula.
            kind, value = "s", source
        style = styles.add(_xlsx_style(cell, kind))
        if kind == "" and style == 0:
            continue
        cells.append([row - 1, column - 1, kind, value, number_format, style, start, end])
        rows = max(rows, row)
        cols = max(cols, column)
    for _, _, max_row, max_col in merges:
        rows = max(rows, max_row + 1)
        cols = max(cols, max_col + 1)

    widths, hidden_cols = _xlsx_columns(worksheet, cols)
    default_height = _row_px(getattr(worksheet.sheet_format, "defaultRowHeight", None))
    row_heights: dict[int, int] = {}
    hidden_rows: list[int] = []
    for row, dimension in worksheet.row_dimensions.items():
        if not 1 <= row <= rows:
            continue
        if dimension.hidden:
            hidden_rows.append(row - 1)
        if dimension.height is not None:
            height = _row_px(dimension.height)
            if height != default_height:
                row_heights[row - 1] = height

    meta = {
        "index": index,
        "name": worksheet.title,
        "name_range": list(title_range) if title_range else None,
        "hidden": worksheet.sheet_state != "visible",
        "conditional": len(worksheet.conditional_formatting) > 0,
        "rows": rows,
        "cols": cols,
        "frozen": _frozen(worksheet),
        "col_widths": widths,
        "default_row_height": default_height,
        "row_heights": row_heights,
        "hidden_rows": sorted(hidden_rows),
        "hidden_cols": hidden_cols,
        "merges": merges,
        "cells": f"/jobs/{job_id}/layout/sheets/{index}",
    }
    return meta, tuple(cells)


def _xlsx_value(cell: Any) -> tuple[str, Any, str]:
    """``(type, value, number format)``; type ``""`` for empty cells."""

    value = cell.value
    number_format = str(cell.number_format or "General")
    if value is None:
        return "", "", number_format
    if isinstance(value, bool):
        return "b", value, number_format
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return "s", str(value), number_format
        return "n", value, number_format
    if isinstance(value, (datetime, date, time)):
        return "d", value.isoformat(), number_format
    if isinstance(value, timedelta):
        return "s", str(value), number_format
    if cell.data_type == "e":
        return "e", str(value), number_format
    return "s", str(value), number_format


def _xlsx_columns(worksheet: Any, cols: int) -> tuple[list[int], list[int]]:
    default = getattr(worksheet.sheet_format, "defaultColWidth", None)
    default_px = _column_px(default) if default else DEFAULT_COLUMN_PX
    widths = [default_px] * cols
    hidden: set[int] = set()
    for dimension in worksheet.column_dimensions.values():
        low = int(dimension.min or 0)
        high = int(dimension.max or 0)
        if low < 1:
            continue
        for column in range(low, min(high, cols) + 1):
            if dimension.width and dimension.customWidth is not False:
                widths[column - 1] = _column_px(dimension.width)
            if dimension.hidden:
                hidden.add(column - 1)
    return widths, sorted(hidden)


def _column_px(width: float) -> int:
    """Column width in pixels follows Excel's Calibri 11 standard: 7 px for digits plus
    a 5 px margin.
    px margin.
    """

    return max(0, int(float(width) * 7 + 5))


def _row_px(points: float | None) -> int:
    if points is None:
        return DEFAULT_ROW_PX
    return max(0, round(float(points) * 4 / 3))


def _frozen(worksheet: Any) -> list[int]:
    anchor = worksheet.freeze_panes
    if not anchor:
        return [0, 0]
    from openpyxl.utils.cell import column_index_from_string, coordinate_from_string

    letters, row = coordinate_from_string(anchor)
    return [max(0, row - 1), max(0, column_index_from_string(letters) - 1)]


# -- CSV ---------------------------------------------------------------------


def _csv_grid(job_id: str, document: ExtractedDocument) -> GridModel | None:
    segments = [segment for segment in document.segments if segment.kind is SegmentKind.CSV_CELL]
    if not segments:
        return None
    if len(segments) > GRID_CELL_LIMIT:
        return None
    offsets = Utf16Offsets(document.text)
    styles = _Styles()
    right = styles.add({"align": "right"})
    cells: list[RawCell] = []
    longest: dict[int, int] = {}
    rows = 0
    cols = 0
    for segment in segments:
        row, column = _csv_position(segment.source_ref)
        raw = document.text[segment.start : segment.end]
        start, end = segment.start, segment.end
        display = raw
        if len(raw) >= 2 and raw[0] == raw[-1] == '"':
            display = raw[1:-1].replace('""', '"')
            if '""' not in raw[1:-1]:
                # Without doubled quotation marks, the display matches the source text.
                start, end = start + 1, end - 1
        cells.append(
            [
                row,
                column,
                "s",
                display,
                "@",
                right if _NUMBER_LIKE.match(display.strip()) else 0,
                offsets(start),
                offsets(end),
            ]
        )
        longest[column] = max(longest.get(column, 0), min(40, len(display)))
        rows = max(rows, row + 1)
        cols = max(cols, column + 1)
    cells.sort(key=lambda cell: (cell[0], cell[1]))
    sheet = {
        "index": 0,
        "name": document.source_name,
        "name_range": None,
        "hidden": False,
        "conditional": False,
        "rows": rows,
        "cols": cols,
        "frozen": [0, 0],
        "col_widths": [
            min(320, max(48, longest.get(column, 0) * 7 + 12)) for column in range(cols)
        ],
        "default_row_height": DEFAULT_ROW_PX,
        "row_heights": {},
        "hidden_rows": [],
        "hidden_cols": [],
        "merges": [],
        "cells": f"/jobs/{job_id}/layout/sheets/0",
    }
    layout = {
        "job_id": job_id,
        "kind": "grid",
        "offset_encoding": "utf-16",
        "format": "csv",
        "sheets": [sheet],
        "styles": styles.items,
    }
    return GridModel(layout=layout, cells=(tuple(cells),))


def _csv_position(source_ref: str) -> tuple[int, int]:
    match = re.fullmatch(r"row:(\d+)/column:(\d+)", source_ref)
    if match is None:
        raise LayoutError("Eine CSV-Zelle hat keine Position.")
    return int(match.group(1)) - 1, int(match.group(2)) - 1


_SEPARATORS: Final[dict[str, tuple[str, str]]] = {"de": (",", "."), "en": (".", ",")}
_BOOLEANS: Final[dict[str, tuple[str, str]]] = {"de": ("WAHR", "FALSCH"), "en": ("TRUE", "FALSE")}
_MONTHS: Final[dict[str, tuple[str, ...]]] = {
    "de": (
        "Januar",
        "Februar",
        "März",
        "April",
        "Mai",
        "Juni",
        "Juli",
        "August",
        "September",
        "Oktober",
        "November",
        "Dezember",
    ),
    "en": (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
}
_DAYS: Final[dict[str, tuple[str, ...]]] = {
    "de": ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"),
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
}
#: Built-in date formats that Excel displays in the system's language.
_LOCAL_DATES: Final[dict[str, dict[str, str]]] = {
    "mm-dd-yy": {"de": "dd.mm.yyyy", "en": "m/d/yyyy"},
    "m/d/yy h:mm": {"de": "dd.mm.yyyy hh:mm", "en": "m/d/yyyy h:mm"},
    "d-mmm-yy": {"de": "dd. mmm yy", "en": "d-mmm-yy"},
}


def display_value(kind: str, value: Any, number_format: str, language: Language) -> str:
    """Anzeigetext einer Zelle; Unbekanntes erscheint als Rohwert."""

    if kind in {"", "s", "e"}:
        return str(value)
    if kind == "b":
        yes, no = _BOOLEANS[language]
        return yes if value else no
    try:
        if kind == "d":
            return format_date(_parse_iso(str(value)), number_format, language)
        if kind == "n":
            return format_number(float(value), number_format, language)
    except (ValueError, ArithmeticError):
        pass
    return str(value)


def _parse_iso(value: str) -> datetime:
    if "T" in value or " " in value:
        return datetime.fromisoformat(value)
    if ":" in value:
        return datetime.combine(date(1900, 1, 1), time.fromisoformat(value))
    return datetime.combine(date.fromisoformat(value), time())


def _sections(number_format: str) -> list[str]:
    """Abschnitte (positiv; negativ; null; Text), Anfuehrungszeichen beachtet."""

    sections: list[str] = []
    current = ""
    quoted = False
    escaped = False
    for character in number_format:
        if escaped:
            current += character
            escaped = False
            continue
        if character == "\\":
            current += character
            escaped = True
            continue
        if character == '"':
            quoted = not quoted
        if character == ";" and not quoted:
            sections.append(current)
            current = ""
            continue
        current += character
    sections.append(current)
    return sections


def _tokens(section: str) -> list[tuple[str, str]]:
    """``("lit", Text)`` for literals, ``("fmt", char)`` for placeholders."""

    tokens: list[tuple[str, str]] = []
    index = 0
    while index < len(section):
        character = section[index]
        if character == '"':
            end = section.find('"', index + 1)
            end = len(section) if end < 0 else end
            tokens.append(("lit", section[index + 1 : end]))
            index = end + 1
            continue
        if character == "\\" and index + 1 < len(section):
            tokens.append(("lit", section[index + 1]))
            index += 2
            continue
        if character == "_" and index + 1 < len(section):
            tokens.append(("lit", " "))
            index += 2
            continue
        if character == "*" and index + 1 < len(section):
            index += 2
            continue
        if character == "[":
            end = section.find("]", index + 1)
            end = len(section) if end < 0 else end
            inner = section[index + 1 : end]
            # ``[$€-de-AT]`` → “€”; colors, conditions and ``[$-407]`` are dropped.
            if inner.startswith("$"):
                symbol = inner[1:].split("-", 1)[0]
                if symbol:
                    tokens.append(("lit", symbol))
            elif re.fullmatch(r"h+|m+|s+", inner, flags=re.IGNORECASE):
                tokens.append(("fmt", inner))
            index = end + 1
            continue
        tokens.append(("fmt", character))
        index += 1
    return tokens


def format_number(value: float, number_format: str, language: Language) -> str:
    sections = _sections(number_format)
    section = sections[0]
    negative = value < 0
    if negative and len(sections) >= 2 and sections[1].strip():
        section, value = sections[1], -value
        negative = False
    elif value == 0 and len(sections) >= 3 and sections[2].strip():
        section = sections[2]
    if section.strip().lower() in {"general", "standard", ""}:
        return _general(value, language)
    if section.strip() == "@":
        return _general(value, language)
    tokens = _tokens(section)
    placeholders = "".join(text for kind, text in tokens if kind == "fmt")
    if not re.search(r"[0#?]", placeholders):
        # A section containing only text (such as "–" for null) displays exactly that
        # text.
        return "".join(_literal(kind, text) for kind, text in tokens)
    if re.search(r"[eE][+-]|\?/|#/|0/", placeholders):
        return _general(value, language)

    percent = placeholders.count("%")
    magnitude = abs(value) * (100**percent)
    # The numeric part spans from the first to the last placeholder.
    digits = [i for i, (kind, text) in enumerate(tokens) if kind == "fmt" and text in "0#?.,"]
    first = next(i for i in digits if tokens[i][1] in "0#?")
    last = max(i for i in digits if tokens[i][1] in "0#?.,")
    number = "".join(text for _, text in tokens[first : last + 1] if text in "0#?.,")
    while number.endswith(","):  # Thousands scaling "0,," divides by 1000 per comma.
        number = number[:-1]
        magnitude /= 1000
    integer_part, _, fraction_part = number.partition(".")
    decimals = sum(1 for character in fraction_part if character in "0#?")
    required = fraction_part.count("0")
    grouping = "," in integer_part
    minimum = integer_part.count("0")

    rounded = Decimal(repr(magnitude)).quantize(Decimal(1).scaleb(-decimals), ROUND_HALF_UP)
    whole, _, fraction = f"{rounded:f}".partition(".")
    whole = whole.lstrip("0")
    whole = whole.rjust(minimum, "0")
    fraction = fraction.ljust(decimals, "0")
    while len(fraction) > required and fraction.endswith("0"):
        fraction = fraction[:-1]
    decimal_mark, group_mark = _SEPARATORS[language]
    if grouping and len(whole) > 3:
        groups = []
        while len(whole) > 3:
            groups.insert(0, whole[-3:])
            whole = whole[:-3]
        whole = group_mark.join([whole, *groups])
    text = whole + (decimal_mark + fraction if fraction or "." in number and required else "")

    prefix = "".join(_literal(kind, text_) for kind, text_ in tokens[:first])
    suffix = "".join(_literal(kind, text_) for kind, text_ in tokens[last + 1 :])
    sign = "-" if negative and rounded != 0 else ""
    return f"{sign}{prefix}{text}{suffix}"


def _literal(kind: str, text: str) -> str:
    if kind == "lit":
        return text
    return text if text not in "0#?.," else ""


def _general(value: float, language: Language) -> str:
    if float(value).is_integer() and abs(value) < 1e15:
        return str(int(value))
    text = f"{value:.10g}"
    if "e" in text:
        return text.replace(".", _SEPARATORS[language][0]).upper()
    return text.replace(".", _SEPARATORS[language][0])


def format_date(moment: datetime, number_format: str, language: Language) -> str:
    section = _sections(number_format)[0]
    pattern = _LOCAL_DATES.get(section.strip().lower(), {}).get(language, section)
    if pattern.strip().lower() in {"general", "standard", "", "@"}:
        pattern = _LOCAL_DATES["mm-dd-yy"][language]
        if moment.time() != time():
            pattern += " hh:mm"
    tokens = _date_tokens(_tokens(pattern))
    twelve = any(kind == "ampm" for kind, _ in tokens)
    parts: list[str] = []
    for kind, text in tokens:
        if kind == "lit":
            parts.append(text)
        elif kind == "ampm":
            parts.append("AM" if moment.hour < 12 else "PM")
        else:
            parts.append(_date_part(kind, text, moment, language, twelve))
    return "".join(parts)


def _date_tokens(tokens: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Group placeholders (``yyyy``, ``mm`` …); ``m`` after ``h`` or before ``s``
    denotes minutes.
    minutes.
    """

    grouped: list[tuple[str, str]] = []
    flat = list(tokens)
    index = 0
    while index < len(flat):
        kind, text = flat[index]
        if kind == "lit":
            grouped.append(("lit", text))
            index += 1
            continue
        if (
            text.upper() in {"A", "AM/PM"}
            and "".join(t for _, t in flat[index : index + 5]).upper() == "AM/PM"
        ):
            grouped.append(("ampm", "AM/PM"))
            index += 5
            continue
        letter = text.lower()[:1]
        if letter in "ymdhs" and len(set(text.lower())) == 1 and len(text) > 1:
            grouped.append((letter, text.lower()))  # from ``[hh]``
            index += 1
            continue
        if letter in "ymdhs":
            run = letter
            index += 1
            while (
                index < len(flat) and flat[index][0] == "fmt" and flat[index][1].lower() == letter
            ):
                run += letter
                index += 1
            grouped.append((letter, run))
            continue
        if text in {".", "0"} and grouped and grouped[-1][0] == "s":
            grouped.append(("lit", text if text == "." else ""))
            index += 1
            continue
        grouped.append(("lit", text))
        index += 1
    # Minutes: ``m`` immediately after hours or before seconds.
    fields = [i for i, (kind, _) in enumerate(grouped) if kind in "ymdhs"]
    for position, i in enumerate(fields):
        if grouped[i][0] != "m" or len(grouped[i][1]) > 2:
            continue
        before = grouped[fields[position - 1]][0] if position > 0 else ""
        after = grouped[fields[position + 1]][0] if position + 1 < len(fields) else ""
        if before == "h" or after == "s":
            grouped[i] = ("minute", grouped[i][1])
    return grouped


def _date_part(kind: str, text: str, moment: datetime, language: Language, twelve: bool) -> str:
    size = len(text)
    if kind == "y":
        return f"{moment.year:04d}" if size > 2 else f"{moment.year % 100:02d}"
    if kind == "m":
        if size >= 4:
            return _MONTHS[language][moment.month - 1]
        if size == 3:
            return _MONTHS[language][moment.month - 1][:3]
        return f"{moment.month:02d}" if size == 2 else str(moment.month)
    if kind == "d":
        if size >= 4:
            return _DAYS[language][moment.weekday()]
        if size == 3:
            return _DAYS[language][moment.weekday()][: 2 if language == "de" else 3]
        return f"{moment.day:02d}" if size == 2 else str(moment.day)
    if kind == "h":
        hour = moment.hour % 12 or 12 if twelve else moment.hour
        return f"{hour:02d}" if size >= 2 else str(hour)
    if kind == "minute":
        return f"{moment.minute:02d}" if size >= 2 else str(moment.minute)
    if kind == "s":
        return f"{moment.second:02d}" if size >= 2 else str(moment.second)
    return text


__all__ = [
    "GRID_BLOCK_ROWS",
    "GRID_CELL_LIMIT",
    "GridCell",
    "GridModel",
    "build_grid",
    "cell_blocks",
    "display_value",
    "format_date",
    "format_number",
    "grid_rows",
    "is_grid_document",
]
